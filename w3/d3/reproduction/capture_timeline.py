#!/usr/bin/env python3
"""
capture_timeline.py — Record event timeline with UTC timestamps.
Polls Prometheus metrics and container events to build timeline.json.

Usage:
    python capture_timeline.py --duration 600 --out timeline.json
"""

import argparse
import json
import time
import urllib.request
from datetime import datetime, timezone


def get_utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def query_prometheus(query, prom_url="http://localhost:9090"):
    """Query Prometheus instant query API."""
    try:
        url = f"{prom_url}/api/v1/query?query={urllib.parse.quote(query)}"
        with urllib.request.urlopen(url, timeout=5) as resp:
            data = json.loads(resp.read())
            if data["status"] == "success" and data["data"]["result"]:
                return float(data["data"]["result"][0]["value"][1])
    except Exception:
        pass
    return None


def check_edge_health(edge_url="http://localhost:8080"):
    """Check if edge server responds to health endpoint."""
    try:
        req = urllib.request.Request(f"{edge_url}/health", method="GET")
        with urllib.request.urlopen(req, timeout=3) as resp:
            return resp.status == 200
    except Exception:
        return False


def main():
    parser = argparse.ArgumentParser(description="Capture outage timeline")
    parser.add_argument("--duration", type=int, default=600, help="Capture duration in seconds")
    parser.add_argument("--out", type=str, default="timeline.json", help="Output file")
    parser.add_argument("--interval", type=int, default=5, help="Poll interval in seconds")
    args = parser.parse_args()

    events = []
    start_time = time.time()
    prev_healthy = None
    prev_waf_status = None
    prev_cpu_high = None

    print(f"[+] Capturing timeline for {args.duration}s, polling every {args.interval}s...")

    while time.time() - start_time < args.duration:
        ts = get_utc_now()

        # Check health
        healthy = check_edge_health()
        if prev_healthy is not None and healthy != prev_healthy:
            events.append({
                "timestamp": ts,
                "event": "edge_server_health_changed",
                "detail": f"healthy={healthy}",
                "source": "healthcheck"
            })
        prev_healthy = healthy

        # Check WAF status
        waf = query_prometheus("waf_evil_regex_deployed")
        if waf is not None and prev_waf_status is not None and waf != prev_waf_status:
            events.append({
                "timestamp": ts,
                "event": "waf_rule_status_changed",
                "detail": f"evil_regex_deployed={'true' if waf == 1 else 'false'}",
                "source": "prometheus"
            })
        prev_waf_status = waf

        # Check CPU
        cpu = query_prometheus("edge_cpu_usage_percent")
        cpu_high = cpu is not None and cpu > 80
        if prev_cpu_high is not None and cpu_high != prev_cpu_high:
            events.append({
                "timestamp": ts,
                "event": "cpu_usage_spike" if cpu_high else "cpu_usage_recovered",
                "detail": f"cpu_percent={cpu:.1f}" if cpu else "unknown",
                "source": "prometheus"
            })
        prev_cpu_high = cpu_high

        # Check error rate
        error_rate = query_prometheus('rate(http_requests_total{status="503"}[30s])')
        if error_rate and error_rate > 0.1:
            events.append({
                "timestamp": ts,
                "event": "high_error_rate",
                "detail": f"503_rate={error_rate:.4f}/s",
                "source": "prometheus"
            })

        time.sleep(args.interval)

    # Write output
    output = {
        "incident": "cloudflare_waf_regex_2019_reproduction",
        "capture_start": datetime.fromtimestamp(start_time, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "capture_duration_seconds": args.duration,
        "total_events": len(events),
        "events": events
    }

    with open(args.out, "w") as f:
        json.dump(output, f, indent=2)

    print(f"[✓] Captured {len(events)} events → {args.out}")


if __name__ == "__main__":
    main()
