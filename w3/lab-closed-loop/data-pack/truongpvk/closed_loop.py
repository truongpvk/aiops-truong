import sys
import time
import json
import logging
import argparse
import subprocess
import threading
from collections import deque
from pathlib import Path
from urllib.parse import urljoin

import requests
import yaml
from prometheus_client import start_http_server, Counter, Gauge

# Configure Logging
class JSONFormatter(logging.Formatter):
    def format(self, record):
        log_record = {
            "ts": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "event_type": getattr(record, "event_type", "LOG"),
        }
        if hasattr(record, "extra_data"):
            log_record.update(record.extra_data)
        log_record["message"] = record.getMessage()
        return json.dumps(log_record)

logger = logging.getLogger("orchestrator")
handler = logging.StreamHandler(sys.stdout)
formatter = JSONFormatter()
formatter.datefmt = "%Y-%m-%dT%H:%M:%SZ"
handler.setFormatter(formatter)
logger.addHandler(handler)
logger.setLevel(logging.INFO)

def log_event(level, event_type, msg="", **kwargs):
    extra = {"event_type": event_type, "extra_data": kwargs}
    logger.log(level, msg, extra=extra)

# Metrics
ACTION_COUNTER = Counter("closed_loop_actions_total", "Total actions", ["service", "runbook", "outcome"])
CIRCUIT_BREAKER_GAUGE = Gauge("closed_loop_circuit_breaker_state", "Circuit breaker state (1=open)", ["service"])
BLAST_RADIUS_GAUGE = Gauge("closed_loop_blast_radius_remaining", "Actions remaining in window", ["service"])
MUTEX_GAUGE = Gauge("closed_loop_mutex_locked", "Mutex state (1=locked)", ["service"])
VERIFY_STATUS_GAUGE = Gauge("closed_loop_verify_status", "Verify status (0=fail, 1=pass, 2=in-progress)", ["service", "runbook"])

# State Tracking
class OrchestratorState:
    def __init__(self):
        self.action_history = deque()
        self.service_restarts = {}
        self.consecutive_failures = {}
        self.service_locks = {}
        self.meta_lock = threading.Lock()

    def get_lock(self, service):
        with self.meta_lock:
            if service not in self.service_locks:
                self.service_locks[service] = threading.Lock()
            return self.service_locks[service]

    def record_action(self, service, now):
        self.action_history.append((now, service))
        if service not in self.service_restarts:
            self.service_restarts[service] = deque()
        self.service_restarts[service].append(now)

    def clean_history(self, now):
        while self.action_history and now - self.action_history[0][0] > 60:
            self.action_history.popleft()
        for svc, history in self.service_restarts.items():
            while history and now - history[0] > 3600:
                history.popleft()

    def check_blast_radius(self, config, service, now):
        self.clean_history(now)
        max_min = config["blast_radius"].get("max_actions_per_minute", 3)
        max_hour = config["blast_radius"].get("max_restarts_per_service_per_hour", 5)
        
        if len(self.action_history) >= max_min:
            return False, "Max actions per minute exceeded"
        
        svc_history = self.service_restarts.get(service, [])
        if len(svc_history) >= max_hour:
            return False, f"Max restarts per hour exceeded for {service}"
            
        return True, ""

state = OrchestratorState()

def fetch_alerts(alertmanager_url):
    try:
        url = f"{alertmanager_url}/api/v2/alerts?active=true&silenced=false&inhibited=false"
        resp = requests.get(url, timeout=5)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        log_event(logging.ERROR, "ALERTMANAGER_FETCH_ERROR", str(e))
        return []

def run_script(script, service, dry_run=False, timeout_s=30):
    cmd = ["bash", script, "--service", service]
    if dry_run:
        cmd.append("--dry-run")
    log_event(logging.INFO, "RUNBOOK_EXEC", script=script, service=service, dry_run=dry_run)
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
        log_event(logging.INFO, "RUNBOOK_RESULT", script=script, service=service, returncode=res.returncode, stdout=res.stdout.strip())
        return res.returncode == 0
    except subprocess.TimeoutExpired:
        log_event(logging.ERROR, "RUNBOOK_TIMEOUT", script=script, service=service)
        return False
    except Exception as e:
        log_event(logging.ERROR, "RUNBOOK_ERROR", script=script, service=service, error=str(e))
        return False

def verify_service(prometheus_url, service, baseline_data):
    thresholds = baseline_data.get("verify_thresholds", {})
    timeout = thresholds.get("verify_timeout_seconds", 60)
    interval = thresholds.get("verify_poll_interval_seconds", 10)
    min_samples = thresholds.get("verify_min_samples", 3)
    p99_max = thresholds.get("latency_p99_max_ms", 500)
    up_req = thresholds.get("up_required", 1)

    queries = baseline_data.get("prometheus_queries", {})
    latency_query = queries.get("latency_p99", "").replace("{service}", service)
    up_query = queries.get("up", "").replace("{service}", service)

    samples_passed = 0
    start_time = time.time()
    sample_idx = 1

    log_event(logging.INFO, "VERIFY_START", service=service, timeout_s=timeout)

    while time.time() - start_time < timeout:
        time.sleep(interval)
        try:
            lat_res = requests.get(f"{prometheus_url}/api/v1/query", params={"query": latency_query}, timeout=5).json()
            up_res = requests.get(f"{prometheus_url}/api/v1/query", params={"query": up_query}, timeout=5).json()

            lat_val = float(lat_res["data"]["result"][0]["value"][1]) if lat_res.get("data", {}).get("result") else 0
            up_val = float(up_res["data"]["result"][0]["value"][1]) if up_res.get("data", {}).get("result") else 0

            lat_ok = lat_val <= p99_max
            up_ok = up_val >= up_req

            log_event(logging.INFO, "VERIFY_SAMPLE", sample=sample_idx, latency_p99_ms=lat_val, up=up_val, latency_ok=lat_ok, up_ok=up_ok)

            if lat_ok and up_ok:
                samples_passed += 1
                if samples_passed >= min_samples:
                    log_event(logging.INFO, "VERIFY_PASS", service=service, samples=samples_passed)
                    return True
            else:
                samples_passed = 0
        except Exception as e:
            log_event(logging.WARNING, "VERIFY_QUERY_ERROR", service=service, error=str(e))
        
        sample_idx += 1

    log_event(logging.WARNING, "VERIFY_FAIL", service=service, samples=sample_idx-1)
    return False

def process_alert(alert, config, baseline_data):
    labels = alert.get("labels", {})
    alertname = labels.get("alertname", "")
    service = labels.get("service") or labels.get("job") or "unknown"
    severity = labels.get("severity", "")

    log_event(logging.INFO, "ALERT_DETECTED", alertname=alertname, service=service, severity=severity)

    # Decide
    runbook = config.get("runbook_map", {}).get(alertname)
    if not runbook:
        return

    # Hallucination Defense (Stress 6)
    registry = config.get("runbook_registry", [])
    if runbook not in registry:
        log_event(logging.ERROR, "DECISION_VALIDATION_FAILED", bad_runbook=runbook, alertname=alertname, raw_decision=runbook, action="escalate_no_auto_action")
        return

    log_event(logging.INFO, "DECIDE_RUNBOOK", alertname=alertname, service=service, runbook=runbook)

    # Blast-radius
    now = time.time()
    ok, reason = state.check_blast_radius(config, service, now)
    if not ok:
        log_event(logging.WARNING, "BLAST_RADIUS_EXCEEDED", service=service, reason=reason)
        return
    log_event(logging.INFO, "BLAST_RADIUS_OK", service=service)

    # Lock (Stress 5)
    lock = state.get_lock(service)
    if not lock.acquire(blocking=False):
        log_event(logging.WARNING, "SERVICE_LOCK_BUSY", service=service, message="Service is already being processed")
        return
    
    MUTEX_GAUGE.labels(service=service).set(1)

    try:
        # Circuit Breaker Check
        if state.consecutive_failures.get(service, 0) >= 3:
            log_event(logging.ERROR, "CIRCUIT_BREAKER_HALT", service=service, message="Circuit open due to >=3 failures")
            CIRCUIT_BREAKER_GAUGE.labels(service=service).set(1)
            return

        # Dry-Run
        if not run_script(runbook, service, dry_run=True):
            return
        log_event(logging.INFO, "DRY_RUN_PASS", runbook=runbook, service=service)

        # Multi-step deploy (Stress 4)
        multi_steps = config.get("multi_step_map", {}).get(alertname, [])
        completed_steps = []
        if multi_steps:
            state.record_action(service, time.time())
            for step in multi_steps:
                if not run_script(step, service, dry_run=False):
                    log_event(logging.ERROR, "TRANSACTIONAL_STEP_FAIL", step=step, service=service, completed_before_failure=completed_steps)
                    rollback_steps = config.get("multi_step_rollback_map", {}).get(alertname, [])
                    for rb_step in reversed(rollback_steps[:len(completed_steps)]):
                        log_event(logging.WARNING, "TRANSACTIONAL_ROLLBACK_STEP", step=rb_step, service=service)
                        run_script(rb_step, service, dry_run=False)
                    log_event(logging.INFO, "TRANSACTIONAL_ROLLBACK_COMPLETE", service=service, rolled_back=list(reversed(rollback_steps[:len(completed_steps)])))
                    state.consecutive_failures[service] = state.consecutive_failures.get(service, 0) + 1
                    return
                completed_steps.append(step)
            return

        # Act
        state.record_action(service, time.time())
        if not run_script(runbook, service, dry_run=False):
            state.consecutive_failures[service] = state.consecutive_failures.get(service, 0) + 1
            return
        
        log_event(logging.INFO, "ACTION_EXECUTED", runbook=runbook, service=service)
        VERIFY_STATUS_GAUGE.labels(service=service, runbook=runbook).set(2)

        # Verify
        verify_ok = verify_service(config["prometheus_url"], service, baseline_data)

        if verify_ok:
            VERIFY_STATUS_GAUGE.labels(service=service, runbook=runbook).set(1)
            ACTION_COUNTER.labels(service=service, runbook=runbook, outcome="success").inc()
            log_event(logging.INFO, "ACTION_SUCCESS", alertname=alertname, service=service, runbook=runbook)
            state.consecutive_failures[service] = 0
            CIRCUIT_BREAKER_GAUGE.labels(service=service).set(0)
            return
        
        # Rollback
        VERIFY_STATUS_GAUGE.labels(service=service, runbook=runbook).set(0)
        log_event(logging.WARNING, "ROLLBACK_TRIGGERED", service=service, rollback_runbook=runbook)
        run_script(runbook, service, dry_run=False)
        log_event(logging.INFO, "ROLLBACK_EXECUTED", service=service, rollback_runbook=runbook)
        
        state.consecutive_failures[service] = state.consecutive_failures.get(service, 0) + 1
        ACTION_COUNTER.labels(service=service, runbook=runbook, outcome="rollback").inc()
        
    finally:
        lock.release()
        MUTEX_GAUGE.labels(service=service).set(0)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args()

    with open(args.config) as f:
        config = yaml.safe_load(f)

    baseline_path = Path(args.config).parent / config.get("baseline_path", "../data/baseline.json")
    with open(baseline_path) as f:
        baseline_data = json.load(f)

    start_http_server(8000)
    log_event(logging.INFO, "ORCHESTRATOR_START")

    seen_alerts = set()
    poll_interval = config.get("poll_interval_seconds", 15)

    while True:
        alerts = fetch_alerts(config["alertmanager_url"])
        for alert in alerts:
            fp = alert.get("fingerprint", "")
            if fp and fp in seen_alerts:
                continue
            if fp:
                seen_alerts.add(fp)

            # Process in thread for concurrency
            t = threading.Thread(target=process_alert, args=(alert, config, baseline_data))
            t.start()

        if len(seen_alerts) > 1000:
            seen_alerts.clear()

        time.sleep(poll_interval)

if __name__ == "__main__":
    main()
