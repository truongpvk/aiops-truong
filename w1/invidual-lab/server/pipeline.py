"""
AIOps W1 Individual Lab — Streaming Anomaly Pipeline
=====================================================
Stateful streaming pipeline:
  - Rolling mean/std z-score per metric (score BEFORE append)
  - Hard absolute thresholds as fallback (immune to baseline poisoning)
  - Isolation Forest on feature vectors (warmup=100, retrain every 50)
  - Trend-based root-cause classification via streak counters
  - Alert suppression with 300s cooldown per fault type
"""

import asyncio
import json
import re
import statistics
import time
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request

from state.streaming_state import StreamingState, METRIC_FIELDS

# ── Paths ──────────────────────────────────────────────────────────────
ALERTS_FILE = Path("alerts.jsonl")

# ── Global state (instantiated ONCE at startup) ────────────────────────
state = StreamingState()


# ── Rolling Z-score ────────────────────────────────────────────────────
def rolling_zscore(window, value: float) -> float:
    """
    Z-score of value against rolling window mean/std.
    Score BEFORE appending current sample.
    Requires at least 20 samples to be meaningful.
    """
    if len(window) < 20:
        return 0.0
    mu  = statistics.mean(window)
    std = statistics.pstdev(window) or 1e-9
    return abs(value - mu) / std


# ── Isolation Forest ───────────────────────────────────────────────────
def _feature_vector(metrics: dict) -> list[float]:
    return [
        metrics.get("memory_usage_bytes", 0)    / 2_000_000_000,
        metrics.get("cpu_usage_percent", 0)      / 100,
        metrics.get("http_requests_per_sec", 0)  / 500,
        metrics.get("http_p99_latency_ms", 0)    / 3000,
        metrics.get("http_5xx_rate", 0)          / 100,
        metrics.get("jvm_gc_pause_ms_avg", 0)    / 300,
        metrics.get("queue_depth", 0)            / 250,
        metrics.get("upstream_timeout_rate", 0)  / 100,
    ]


def maybe_train_iso(st: StreamingState):
    try:
        from sklearn.ensemble import IsolationForest
    except ImportError:
        return
    n = len(st.feature_vectors)
    if n < st.WARMUP:
        return
    if st.iso_model is None or st.iso_samples_since_train >= st.RETRAIN_EVERY:
        st.iso_model = IsolationForest(
            n_estimators=100, contamination=0.05, random_state=42, n_jobs=1,
        ).fit(list(st.feature_vectors))
        st.iso_samples_since_train = 0


def iso_score(st: StreamingState, vec: list[float]) -> float:
    if st.iso_model is None:
        return 0.0
    raw = st.iso_model.decision_function([vec])[0]
    return max(0.0, min(1.0, 0.5 - raw))


# ── Log helpers ────────────────────────────────────────────────────────
def simple_template(message: str) -> str:
    return re.sub(r"\d+(\.\d+)?", "<N>", message)


def recent_error_rate(st: StreamingState, window: int = 50) -> float:
    recent = list(st.log_history)[-window:]
    if not recent:
        return 0.0
    bad = sum(1 for l in recent if l.get("level") in ("ERROR", "FATAL"))
    return bad / len(recent)


# ── Trend counters ─────────────────────────────────────────────────────
def update_trend_counters(st: StreamingState, metrics: dict):
    history = list(st.metric_history)

    # Establish baseline RPS from first 50 samples
    if st.baseline_rps is None and len(history) >= 50:
        st.baseline_rps = statistics.mean(
            m["http_requests_per_sec"] for m in history[:50]
        )

    if len(history) < 2:
        return

    prev = history[-1]

    # Memory rising streak
    if metrics["memory_usage_bytes"] > prev["memory_usage_bytes"]:
        st.memory_rising_streak += 1
    else:
        st.memory_rising_streak = max(0, st.memory_rising_streak - 2)

    # Timeout rising streak
    if metrics["upstream_timeout_rate"] > prev["upstream_timeout_rate"] * 1.05:
        st.timeout_rising_streak += 1
    else:
        st.timeout_rising_streak = max(0, st.timeout_rising_streak - 1)

    # RPS spike streak
    baseline = st.baseline_rps or 120.0
    if metrics["http_requests_per_sec"] > baseline * 3:
        st.rps_spike_streak += 1
    else:
        st.rps_spike_streak = max(0, st.rps_spike_streak - 1)


def classify_fault(st: StreamingState, metrics: dict) -> str | None:
    if st.memory_rising_streak >= 20:
        return "memory_leak"
    if st.timeout_rising_streak >= 10:
        return "dependency_timeout"
    if st.rps_spike_streak >= 5 and st.baseline_rps and \
            metrics["http_requests_per_sec"] > st.baseline_rps * 3:
        return "traffic_spike"
    return None


# ── Alert writer ───────────────────────────────────────────────────────
def fire_alert(timestamp: str, alert_type: str, severity: str, message: str) -> dict:
    alert = {"timestamp": timestamp, "type": alert_type,
             "severity": severity, "message": message}
    with open(ALERTS_FILE, "a") as f:
        f.write(json.dumps(alert) + "\n")
    print(f"[ALERT] {severity.upper()} {alert_type}: {message}")
    return alert


# ── Core detection ─────────────────────────────────────────────────────
def detect_anomalies(st: StreamingState, payload: dict) -> list[dict]:
    metrics   = payload["metrics"]
    logs      = payload["logs"]
    timestamp = payload["timestamp"]
    fired: list[dict] = []

    n_history = len(st.metric_history)

    # ── 1. Rolling z-scores (score BEFORE append) ──────────────────────
    zs: dict[str, float] = {}
    for field in METRIC_FIELDS:
        val = metrics.get(field, 0)
        zs[field] = rolling_zscore(st.zscore_windows[field], val)  # score first
        st.zscore_windows[field].append(val)                        # then append

    # ── 2. Isolation Forest ────────────────────────────────────────────
    vec = _feature_vector(metrics)
    iso_anomaly = iso_score(st, vec)
    st.feature_vectors.append(vec)
    st.iso_samples_since_train += 1
    maybe_train_iso(st)

    # ── 3. Trend counters ──────────────────────────────────────────────
    update_trend_counters(st, metrics)

    # ── 4. Log history ─────────────────────────────────────────────────
    for log in logs:
        st.log_history.append(log)
        tmpl = simple_template(log.get("message", ""))
        st.template_freq[tmpl] = st.template_freq.get(tmpl, 0) + 1
        st.template_first_seen.setdefault(tmpl, timestamp)
        st.template_last_seen[tmpl] = timestamp

    # ── 5. Append metrics AFTER scoring ───────────────────────────────
    st.metric_history.append(metrics)
    st.timestamp_history.append(timestamp)

    # ── 6. Warmup guard — no alerts for first 20 samples ──────────────
    if n_history < 20:
        return fired

    # ── Derived values ─────────────────────────────────────────────────
    mem_util = metrics["memory_usage_bytes"] / metrics.get("memory_limit_bytes", 2e9)
    baseline = st.baseline_rps or 120.0

    # ── 7a. memory_leak ────────────────────────────────────────────────
    # Rolling z-score OR hard threshold (immune to baseline poisoning)
    memory_leak_score = sum([
        zs["memory_usage_bytes"]    > 3.0,
        zs["jvm_gc_pause_ms_avg"]   > 3.0,
        st.memory_rising_streak     > 15,
        mem_util                    > 0.70,          # hard: >70% of 2GB limit
        metrics["jvm_gc_pause_ms_avg"] > 30,         # hard: GC >30ms
    ])
    if memory_leak_score >= 2 and st.can_fire_alert("memory_leak"):
        sev = "critical" if mem_util > 0.80 or st.memory_rising_streak > 25 else "warning"
        msg = (
            f"Memory usage growing abnormally, utilisation={mem_util*100:.1f}%, "
            f"streak={st.memory_rising_streak}, gc_pause={metrics['jvm_gc_pause_ms_avg']:.1f}ms, "
            f"mem_zscore={zs['memory_usage_bytes']:.2f}"
        )
        fired.append(fire_alert(timestamp, "memory_leak", sev, msg))
        st.record_alert_fire("memory_leak")

    # ── 7b. traffic_spike ──────────────────────────────────────────────
    spike_score = sum([
        zs["http_requests_per_sec"]  > 3.0,
        zs["queue_depth"]            > 3.0,
        zs["http_p99_latency_ms"]    > 3.0,
        st.rps_spike_streak          >= 3,
        metrics["http_requests_per_sec"] > baseline * 3,   # hard: 3x baseline
        metrics["queue_depth"]           > 50,              # hard: queue >50
    ])
    if spike_score >= 2 and st.can_fire_alert("traffic_spike"):
        sev = "critical" if zs["http_requests_per_sec"] > 5.0 or metrics["queue_depth"] > 100 else "warning"
        msg = (
            f"Traffic spike detected, RPS={metrics['http_requests_per_sec']:.0f} "
            f"({metrics['http_requests_per_sec']/baseline:.1f}x baseline), "
            f"queue={metrics['queue_depth']}, rps_zscore={zs['http_requests_per_sec']:.2f}"
        )
        fired.append(fire_alert(timestamp, "traffic_spike", sev, msg))
        st.record_alert_fire("traffic_spike")

    # ── 7c. dependency_timeout ─────────────────────────────────────────
    timeout_score = sum([
        zs["upstream_timeout_rate"]  > 3.0,
        zs["http_5xx_rate"]          > 3.0,
        zs["http_p99_latency_ms"]    > 3.0,
        st.timeout_rising_streak     >= 8,
        metrics["upstream_timeout_rate"] > 10,   # hard: >10%
        metrics["http_5xx_rate"]         > 10,   # hard: >10%
    ])
    if timeout_score >= 2 and st.can_fire_alert("dependency_timeout"):
        sev = "critical" if metrics["upstream_timeout_rate"] > 20 else "warning"
        msg = (
            f"Upstream dependency timeout cascade, "
            f"timeout_rate={metrics['upstream_timeout_rate']:.1f}%, "
            f"5xx={metrics['http_5xx_rate']:.1f}%, "
            f"latency={metrics['http_p99_latency_ms']:.0f}ms, "
            f"timeout_zscore={zs['upstream_timeout_rate']:.2f}"
        )
        fired.append(fire_alert(timestamp, "dependency_timeout", sev, msg))
        st.record_alert_fire("dependency_timeout")

    # ── 7d. Isolation Forest catch-all ─────────────────────────────────
    if not fired and iso_anomaly > 0.65 and n_history > st.WARMUP:
        ft = classify_fault(st, metrics) or "traffic_spike"
        iso_key = f"iso_{ft}"
        if st.can_fire_alert(iso_key):
            msg = (
                f"Isolation Forest anomaly score={iso_anomaly:.2f}, "
                f"suspected={ft}, mem_util={mem_util*100:.1f}%, "
                f"rps_zscore={zs['http_requests_per_sec']:.2f}"
            )
            fired.append(fire_alert(timestamp, ft, "warning", msg))
            st.record_alert_fire(iso_key)

    # ── Record alerts ──────────────────────────────────────────────────
    for a in fired:
        st.alert_history.append(a)

    return fired


# ── Periodic snapshot ──────────────────────────────────────────────────
async def periodic_snapshot():
    while True:
        await asyncio.sleep(60)
        async with state.lock:
            state.save_snapshot()
        print("[STATE] Snapshot saved.")


# ── FastAPI ────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    asyncio.create_task(periodic_snapshot())
    yield

app = FastAPI(lifespan=lifespan)


@app.post("/ingest")
async def ingest(request: Request):
    payload = await request.json()
    async with state.lock:
        fired = detect_anomalies(state, payload)
    return {"status": "ok", "alerts_fired": len(fired)}


@app.get("/health")
async def health():
    return {
        "status": "running",
        "metric_samples": len(state.metric_history),
        "alerts_total":   len(state.alert_history),
        "iso_model_ready": state.iso_model is not None,
        "memory_streak":  state.memory_rising_streak,
        "timeout_streak": state.timeout_rising_streak,
        "rps_spike_streak": state.rps_spike_streak,
        "baseline_rps":   state.baseline_rps,
    }


@app.get("/alerts")
async def get_alerts():
    return list(state.alert_history)


@app.get("/debug")
async def debug():
    zs = {}
    if state.metric_history:
        latest = list(state.metric_history)[-1]
        for field in METRIC_FIELDS:
            val = latest.get(field, 0)
            zs[field] = {
                "value": val,
                "rolling_zscore": round(rolling_zscore(state.zscore_windows[field], val), 2),
            }
    return {
        "n_samples": len(state.metric_history),
        "streaks": {
            "memory_rising":  state.memory_rising_streak,
            "timeout_rising": state.timeout_rising_streak,
            "rps_spike":      state.rps_spike_streak,
        },
        "baseline_rps": state.baseline_rps,
        "scores": zs,
    }


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")