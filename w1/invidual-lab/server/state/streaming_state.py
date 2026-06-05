"""
StreamingState — single shared instance across all requests.
Holds rolling windows, detector states, model caches, alert history.
Thread-safe via asyncio.Lock (used with 'async with state.lock').
"""

import asyncio
import pickle
import time
from collections import deque
from pathlib import Path

METRIC_WINDOW = 300
LOG_WINDOW = 1000
STATE_FILE = Path("state_snapshot.pkl")

METRIC_FIELDS = [
    "memory_usage_bytes",
    "cpu_usage_percent",
    "http_requests_per_sec",
    "http_p99_latency_ms",
    "http_5xx_rate",
    "jvm_gc_pause_ms_avg",
    "queue_depth",
    "upstream_timeout_rate",
]


class StreamingState:
    def __init__(self):
        self.lock = asyncio.Lock()

        # Rolling windows
        self.metric_history: deque = deque(maxlen=METRIC_WINDOW)   # list of metric dicts
        self.log_history: deque = deque(maxlen=LOG_WINDOW)          # list of log entry dicts
        self.timestamp_history: deque = deque(maxlen=METRIC_WINDOW) # ISO strings

        # EWMA state per metric: {"mean": float, "var": float}
        self.ewma_state: dict = {}

        # Z-score rolling window per metric (raw values for computing mean/std)
        self.zscore_windows: dict = {f: deque(maxlen=60) for f in METRIC_FIELDS}

        # Isolation Forest
        self.feature_vectors: deque = deque(maxlen=500)
        self.iso_model = None
        self.iso_samples_since_train = 0
        self.WARMUP = 100
        self.RETRAIN_EVERY = 50

        # Log / template tracking
        self.template_freq: dict = {}          # template_str -> count
        self.template_first_seen: dict = {}    # template_str -> timestamp
        self.template_last_seen: dict = {}     # template_str -> timestamp
        self.log_count_since_rebuild = 0
        self.tfidf_vectorizer = None
        self.baseline_corpus: list = []

        # Alert suppression: type -> last fire time (epoch)
        self.last_alert_time: dict = {}
        self.ALERT_COOLDOWN = 300  # seconds

        # Alert history
        self.alert_history: deque = deque(maxlen=200)

        # Root-cause trend tracking (consecutive-sample counters)
        self.memory_rising_streak = 0
        self.timeout_rising_streak = 0
        self.rps_spike_streak = 0

        # Baseline RPS (learned during warmup)
        self.baseline_rps = None

        self._try_restore()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _try_restore(self):
        if STATE_FILE.exists():
            try:
                with open(STATE_FILE, "rb") as f:
                    snap = pickle.load(f)
                self.ewma_state = snap.get("ewma_state", {})
                self.feature_vectors = snap.get("feature_vectors", deque(maxlen=500))
                self.iso_model = snap.get("iso_model", None)
                self.baseline_rps = snap.get("baseline_rps", None)
                self.template_freq = snap.get("template_freq", {})
                print("[STATE] Restored previous snapshot.")
            except Exception as e:
                print(f"[STATE] Could not restore snapshot: {e}")

    def save_snapshot(self):
        try:
            snap = {
                "ewma_state": self.ewma_state,
                "feature_vectors": self.feature_vectors,
                "iso_model": self.iso_model,
                "baseline_rps": self.baseline_rps,
                "template_freq": self.template_freq,
            }
            with open(STATE_FILE, "wb") as f:
                pickle.dump(snap, f)
        except Exception as e:
            print(f"[STATE] Snapshot save failed: {e}")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def can_fire_alert(self, alert_type: str) -> bool:
        now = time.time()
        last = self.last_alert_time.get(alert_type, 0)
        return (now - last) >= self.ALERT_COOLDOWN

    def record_alert_fire(self, alert_type: str):
        self.last_alert_time[alert_type] = time.time()