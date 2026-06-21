#!/usr/bin/env python3
"""AIOps Pipeline — FastAPI service for anomaly detection, correlation, and RCA.

Endpoints:
    GET  /alerts?since=<ts>                → list anomaly alerts
    POST /correlate {window}               → cluster alerts into incidents  
    POST /rca {window_start, window_end}   → root cause analysis

Pulls metrics from Prometheus, compares against baseline, uses topology-aware
analysis for RCA.
"""
import os
import time
import json
import math
import logging
import statistics
import threading
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Query
from pydantic import BaseModel
import requests

logging.basicConfig(level=logging.INFO, format="[aiops] %(message)s")
log = logging.getLogger("aiops")

app = FastAPI(title="AIOps Pipeline", version="1.0.0")

PROM_URL = os.environ.get("PROMETHEUS_URL", "http://prometheus:9090")
BASELINE_PATH = os.environ.get("BASELINE_PATH", "/data/baseline.json")

# ── Service Topology ─────────────────────────────────────────────
TOPOLOGY = {
    "frontend":         {"downstream": ["api-gateway"], "tier": 0},
    "api-gateway":      {"downstream": ["payment-svc", "inventory-svc", "notification-svc", "checkout-svc"], "tier": 1},
    "checkout-svc":     {"downstream": ["payment-svc", "inventory-svc"], "tier": 2},
    "payment-svc":      {"downstream": ["payment-db"], "tier": 2},
    "inventory-svc":    {"downstream": ["inventory-db"], "tier": 2},
    "notification-svc": {"downstream": [], "tier": 2},
    "auth-svc":         {"downstream": [], "tier": 2},
    "payment-db":       {"downstream": [], "tier": 3},
    "inventory-db":     {"downstream": [], "tier": 3},
    "cache-svc":        {"downstream": [], "tier": 3},
    "log-collector":    {"downstream": [], "tier": 3},
    "dns-resolver":     {"downstream": [], "tier": 3},
}

# ── Alert Store (in-memory) ──────────────────────────────────────
alert_store: list[dict] = []
alert_lock = threading.Lock()

# ── Baseline cache ───────────────────────────────────────────────
baseline_cache: dict = {}


def load_baseline():
    """Load baseline.json if available."""
    global baseline_cache
    try:
        if Path(BASELINE_PATH).exists():
            with open(BASELINE_PATH) as f:
                baseline_cache = json.load(f)
            log.info(f"Loaded baseline with {len(baseline_cache.get('metrics', {}))} metric series")
    except Exception as e:
        log.warning(f"Could not load baseline: {e}")


# ── Prometheus Queries ───────────────────────────────────────────
METRIC_QUERIES = {
    "latency_p99": 'histogram_quantile(0.99, rate(http_request_duration_seconds_bucket{{service="{svc}"}}[1m]))',
    "request_rate": 'rate(http_requests_total{{service="{svc}"}}[1m])',
    "error_rate": 'rate(http_errors_total{{service="{svc}"}}[1m])',
    "error_ratio": 'rate(http_errors_total{{service="{svc}"}}[1m]) / (rate(http_requests_total{{service="{svc}"}}[1m]) > 0)',
    "active_requests": 'http_active_requests{{service="{svc}"}}',
    "cpu_usage": 'rate(container_cpu_usage_seconds_total{{name=~".*{svc}.*"}}[1m])',
    "memory_usage": 'container_memory_usage_bytes{{name=~".*{svc}.*"}}',
}

# Infra-specific queries (no service label)
INFRA_QUERIES = {
    "service_up": 'service_up{{service="{svc}"}}',
}


def prom_query(query: str) -> list[dict]:
    """Execute instant Prometheus query."""
    try:
        r = requests.get(
            f"{PROM_URL}/api/v1/query",
            params={"query": query},
            timeout=5,
        )
        r.raise_for_status()
        return r.json().get("data", {}).get("result", [])
    except Exception as e:
        log.debug(f"Prom query failed: {query[:60]}... → {e}")
        return []


def prom_query_range(query: str, start: int, end: int, step: int = 15) -> list[dict]:
    """Execute range Prometheus query."""
    try:
        r = requests.get(
            f"{PROM_URL}/api/v1/query_range",
            params={"query": query, "start": start, "end": end, "step": step},
            timeout=10,
        )
        r.raise_for_status()
        return r.json().get("data", {}).get("result", [])
    except Exception:
        return []


# ── Anomaly Detection ────────────────────────────────────────────
# Thresholds (tuned for the mock services)
THRESHOLDS = {
    "latency_p99": {"absolute": 0.3, "relative_factor": 3.0},    # > 300ms or 3x baseline
    "error_rate": {"absolute": 0.05, "relative_factor": 5.0},     # > 5% or 5x baseline
    "error_ratio": {"absolute": 0.1, "relative_factor": 3.0},     # > 10% errors
    "active_requests": {"absolute": 50, "relative_factor": 5.0},  # queue buildup
    "service_up": {"absolute_below": 1.0},                         # service down
}


def detect_anomalies_for_service(svc: str) -> list[dict]:
    """Check all metrics for a service, return list of anomalies."""
    anomalies = []
    now = int(time.time())

    for metric_name, query_tpl in {**METRIC_QUERIES, **INFRA_QUERIES}.items():
        query = query_tpl.format(svc=svc)
        results = prom_query(query)

        for r in results:
            try:
                val = float(r["value"][1])
            except (KeyError, IndexError, ValueError, TypeError):
                continue

            if math.isnan(val) or math.isinf(val):
                continue

            # Check thresholds
            thresh = THRESHOLDS.get(metric_name, {})
            is_anomaly = False
            reason = ""

            if "absolute_below" in thresh:
                if val < thresh["absolute_below"]:
                    is_anomaly = True
                    reason = f"{metric_name}={val:.4f} < {thresh['absolute_below']}"
            elif "absolute" in thresh:
                if val > thresh["absolute"]:
                    is_anomaly = True
                    reason = f"{metric_name}={val:.4f} > threshold {thresh['absolute']}"

            # Baseline comparison
            baseline_metrics = baseline_cache.get("metrics", {})
            for bq, bv in baseline_metrics.items():
                if svc in bq or metric_name in bq:
                    baseline_mean = bv.get("mean", 0)
                    if baseline_mean > 0 and "relative_factor" in thresh:
                        if val > baseline_mean * thresh["relative_factor"]:
                            is_anomaly = True
                            reason = f"{metric_name}={val:.4f} > {thresh['relative_factor']}x baseline ({baseline_mean:.4f})"

            if is_anomaly:
                anomalies.append({
                    "service": svc,
                    "metric": metric_name,
                    "value": round(val, 6),
                    "reason": reason,
                    "timestamp": now,
                })

    return anomalies


# ── Background Detector (runs every 10s) ────────────────────────
def detector_loop():
    """Continuously poll Prometheus and fire alerts."""
    load_baseline()
    time.sleep(10)  # wait for stack to stabilize
    log.info("Detector loop started")
    
    while True:
        try:
            for svc in TOPOLOGY:
                anomalies = detect_anomalies_for_service(svc)
                for a in anomalies:
                    alert = {
                        "id": f"{a['service']}_{a['metric']}_{a['timestamp']}",
                        "service": a["service"],
                        "metric": a["metric"],
                        "value": a["value"],
                        "reason": a["reason"],
                        "fire_ts": a["timestamp"],
                        "severity": "critical" if a["value"] > 1.0 else "warning",
                    }
                    with alert_lock:
                        # Deduplicate: don't fire same service+metric within 30s
                        recent = [
                            x for x in alert_store
                            if x["service"] == alert["service"]
                            and x["metric"] == alert["metric"]
                            and abs(x["fire_ts"] - alert["fire_ts"]) < 30
                        ]
                        if not recent:
                            alert_store.append(alert)
                            log.info(f"ALERT: {alert['service']}/{alert['metric']} = {alert['value']} ({alert['reason']})")
        except Exception as e:
            log.error(f"Detector error: {e}")

        time.sleep(10)


# Start detector in background
detector_thread = threading.Thread(target=detector_loop, daemon=True)
detector_thread.start()


# ── API Endpoints ────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok", "service": "aiops-pipeline"}


@app.get("/alerts")
def get_alerts(since: int = Query(0, description="Unix timestamp")):
    """Return alerts fired since the given timestamp."""
    with alert_lock:
        filtered = [a for a in alert_store if a["fire_ts"] >= since]
    return filtered


class CorrelateRequest(BaseModel):
    window: int = 300  # seconds


@app.post("/correlate")
def correlate(req: CorrelateRequest):
    """Cluster alerts into incident groups using topology + temporal correlation."""
    now = int(time.time())
    cutoff = now - req.window

    with alert_lock:
        recent_alerts = [a for a in alert_store if a["fire_ts"] >= cutoff]

    if not recent_alerts:
        return {"clusters": [], "total_alerts": 0}

    # Group by service
    by_service = defaultdict(list)
    for a in recent_alerts:
        by_service[a["service"]].append(a)

    # Build clusters using topology proximity
    clusters = []
    visited = set()

    for svc in sorted(by_service.keys(), key=lambda s: TOPOLOGY.get(s, {}).get("tier", 99)):
        if svc in visited:
            continue
        cluster_services = {svc}
        visited.add(svc)

        # Add topologically connected services that also have alerts
        topo = TOPOLOGY.get(svc, {})
        for ds in topo.get("downstream", []):
            if ds in by_service:
                cluster_services.add(ds)
                visited.add(ds)

        # Also check upstream
        for other_svc, other_topo in TOPOLOGY.items():
            if svc in other_topo.get("downstream", []) and other_svc in by_service:
                cluster_services.add(other_svc)
                visited.add(other_svc)

        cluster_alerts = []
        for cs in cluster_services:
            cluster_alerts.extend(by_service[cs])

        clusters.append({
            "cluster_id": len(clusters) + 1,
            "services": sorted(cluster_services),
            "alert_count": len(cluster_alerts),
            "earliest_ts": min(a["fire_ts"] for a in cluster_alerts),
            "latest_ts": max(a["fire_ts"] for a in cluster_alerts),
        })

    return {"clusters": clusters, "total_alerts": len(recent_alerts)}


class RCARequest(BaseModel):
    window_start: int
    window_end: int


@app.post("/rca")
def root_cause_analysis(req: RCARequest):
    """Topology-aware + temporal-causal RCA.
    
    Strategy:
    1. Get alerts in window
    2. Find earliest-firing service (temporal causality)
    3. Prefer upstream services in topology (root cause propagates downstream)
    4. Weight by: earliest alert time + topology depth (deeper = more likely root)
    """
    with alert_lock:
        window_alerts = [
            a for a in alert_store
            if req.window_start <= a["fire_ts"] <= req.window_end
        ]

    if not window_alerts:
        return {"root_service": None, "confidence": 0.0, "evidence": [], "error": "no alerts in window"}

    # Score each service
    service_scores: dict[str, dict] = {}
    
    for a in window_alerts:
        svc = a["service"]
        if svc not in service_scores:
            service_scores[svc] = {
                "service": svc,
                "first_alert_ts": a["fire_ts"],
                "alert_count": 0,
                "metrics_affected": set(),
                "tier": TOPOLOGY.get(svc, {}).get("tier", 99),
            }
        score = service_scores[svc]
        score["alert_count"] += 1
        score["metrics_affected"].add(a["metric"])
        score["first_alert_ts"] = min(score["first_alert_ts"], a["fire_ts"])

    # RCA scoring:
    # - Earlier alert = more likely root (temporal causality)
    # - Higher tier (deeper in topology) = more likely root
    # - More metrics affected = stronger signal
    # - DON'T just pick noisiest (most alerts) — that's §7.3 anti-pattern
    
    earliest_ts = min(s["first_alert_ts"] for s in service_scores.values())
    latest_ts = max(s["first_alert_ts"] for s in service_scores.values())
    ts_range = max(latest_ts - earliest_ts, 1)

    scored = []
    for svc, info in service_scores.items():
        # Temporal score: earlier = higher (0-1)
        temporal = 1.0 - ((info["first_alert_ts"] - earliest_ts) / ts_range) if ts_range > 1 else 1.0
        
        # Topology score: deeper services (db, infra) more likely root
        topo_score = info["tier"] / 4.0  # normalize to 0-1

        # Metric breadth: more metrics = stronger signal, but capped
        metric_score = min(len(info["metrics_affected"]) / 3.0, 1.0)

        # DON'T reward high alert count (anti-pattern §7.3)
        # Instead, slightly penalize very high count (suggests retry storm, not root)
        count_penalty = 0.0
        if info["alert_count"] > 5:
            count_penalty = 0.2  # penalize noisy services

        # Check if this is downstream of another alerting service
        is_downstream = False
        for other_svc in service_scores:
            if other_svc != svc and svc in TOPOLOGY.get(other_svc, {}).get("downstream", []):
                is_downstream = True
                break

        downstream_penalty = 0.15 if is_downstream and len(service_scores) > 1 else 0.0

        total = (
            temporal * 0.35
            + topo_score * 0.25
            + metric_score * 0.25
            - count_penalty * 0.1
            - downstream_penalty
            + 0.15  # base
        )

        scored.append({
            "service": svc,
            "score": round(total, 4),
            "temporal_score": round(temporal, 3),
            "topo_score": round(topo_score, 3),
            "metric_score": round(metric_score, 3),
            "first_alert_ts": info["first_alert_ts"],
            "alert_count": info["alert_count"],
            "metrics": sorted(info["metrics_affected"]),
        })

    scored.sort(key=lambda x: x["score"], reverse=True)
    root = scored[0]

    # Confidence = score gap to second candidate
    confidence = root["score"]
    if len(scored) > 1:
        gap = root["score"] - scored[1]["score"]
        confidence = min(0.95, root["score"] + gap * 0.5)

    evidence = [
        f"Earliest alert at ts={root['first_alert_ts']} (temporal causality)",
        f"Topology tier={TOPOLOGY.get(root['service'], {}).get('tier', '?')}",
        f"Metrics affected: {', '.join(root['metrics'])}",
        f"Alert count: {root['alert_count']}",
    ]

    return {
        "root_service": root["service"],
        "confidence": round(confidence, 3),
        "evidence": evidence,
        "all_candidates": scored[:5],
    }


# ── Startup ──────────────────────────────────────────────────────
@app.on_event("startup")
def startup():
    load_baseline()
    log.info("AIOps Pipeline ready")
