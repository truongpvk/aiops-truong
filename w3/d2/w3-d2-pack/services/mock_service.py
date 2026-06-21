#!/usr/bin/env python3
"""Generic mock service for chaos engineering lab.

Configured via environment variables:
    SERVICE_NAME    — name of this service (e.g., payment-svc)
    SERVICE_PORT    — port to listen on (default 8080)
    DOWNSTREAM      — comma-separated list of downstream services (host:port)
    TOPOLOGY_ROLE   — role hint: frontend|gateway|app|db|infra
"""
import os
import time
import random
import threading
import logging
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import json

try:
    from prometheus_client import (
        Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST
    )
    HAS_PROM = True
except ImportError:
    HAS_PROM = False

SERVICE_NAME = os.environ.get("SERVICE_NAME", "unknown-svc")
SERVICE_PORT = int(os.environ.get("SERVICE_PORT", "8080"))
DOWNSTREAM = [d.strip() for d in os.environ.get("DOWNSTREAM", "").split(",") if d.strip()]
TOPOLOGY_ROLE = os.environ.get("TOPOLOGY_ROLE", "app")

logging.basicConfig(level=logging.INFO, format=f"[{SERVICE_NAME}] %(message)s")
log = logging.getLogger(SERVICE_NAME)

# ── Prometheus metrics ───────────────────────────────────────────
if HAS_PROM:
    REQUEST_COUNT = Counter(
        "http_requests_total",
        "Total HTTP requests",
        ["service", "method", "endpoint", "status"],
    )
    REQUEST_DURATION = Histogram(
        "http_request_duration_seconds",
        "HTTP request duration",
        ["service", "method", "endpoint"],
        buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
    )
    ERROR_RATE = Counter(
        "http_errors_total",
        "Total HTTP errors",
        ["service", "status"],
    )
    ACTIVE_REQUESTS = Gauge(
        "http_active_requests",
        "Currently active requests",
        ["service"],
    )
    UP_GAUGE = Gauge(
        "service_up",
        "Whether the service is up",
        ["service"],
    )
    UP_GAUGE.labels(service=SERVICE_NAME).set(1)

# ── Simple downstream caller ────────────────────────────────────
import urllib.request
import urllib.error


def call_downstream(host_port: str, path: str = "/process") -> tuple[int, str]:
    """Call a downstream service, return (status_code, body)."""
    url = f"http://{host_port}{path}"
    try:
        req = urllib.request.Request(url, method="GET")
        req.add_header("X-Request-From", SERVICE_NAME)
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, str(e)
    except Exception as e:
        return 503, str(e)


# ── Handler ──────────────────────────────────────────────────────
class ServiceHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # suppress default logging

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        start = time.time()

        if path == "/health":
            self._respond(200, {"status": "ok", "service": SERVICE_NAME})
            return

        if path == "/metrics" and HAS_PROM:
            data = generate_latest()
            self.send_response(200)
            self.send_header("Content-Type", CONTENT_TYPE_LATEST)
            self.end_headers()
            self.wfile.write(data)
            return

        if path in ("/process", "/checkout/health", "/", "/api"):
            self._handle_process(parsed)
            return

        self._respond(404, {"error": "not found"})

    def do_POST(self):
        self.do_GET()

    def _handle_process(self, parsed):
        start = time.time()
        status = 200
        if HAS_PROM:
            ACTIVE_REQUESTS.labels(service=SERVICE_NAME).inc()

        try:
            # Simulate baseline processing time
            base_latency = {
                "frontend": 0.005,
                "gateway": 0.008,
                "app": 0.015,
                "db": 0.020,
                "infra": 0.010,
            }.get(TOPOLOGY_ROLE, 0.01)
            jitter = random.uniform(0, base_latency * 0.5)
            time.sleep(base_latency + jitter)

            # Call downstream services
            downstream_results = {}
            for ds in DOWNSTREAM:
                ds_status, ds_body = call_downstream(ds)
                downstream_results[ds] = ds_status
                if ds_status >= 500:
                    status = 502  # propagate failure

            duration = time.time() - start
            body = {
                "service": SERVICE_NAME,
                "status": "ok" if status == 200 else "degraded",
                "latency_ms": round(duration * 1000, 1),
                "downstream": downstream_results,
            }

            if HAS_PROM:
                REQUEST_COUNT.labels(
                    service=SERVICE_NAME, method="GET",
                    endpoint="/process", status=str(status)
                ).inc()
                REQUEST_DURATION.labels(
                    service=SERVICE_NAME, method="GET", endpoint="/process"
                ).observe(duration)
                if status >= 400:
                    ERROR_RATE.labels(service=SERVICE_NAME, status=str(status)).inc()

            self._respond(status, body)
        finally:
            if HAS_PROM:
                ACTIVE_REQUESTS.labels(service=SERVICE_NAME).dec()

    def _respond(self, status: int, body: dict):
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


# ── Background traffic generator (simulates load) ───────────────
def background_traffic():
    """Generate internal traffic between services for realistic metrics."""
    time.sleep(5)  # let server start
    while True:
        try:
            for ds in DOWNSTREAM:
                if random.random() < 0.3:  # 30% chance each cycle
                    call_downstream(ds, "/process")
        except Exception:
            pass
        time.sleep(random.uniform(2, 8))


# ── Main ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    log.info(f"Starting {SERVICE_NAME} on :{SERVICE_PORT} (role={TOPOLOGY_ROLE})")
    log.info(f"Downstream: {DOWNSTREAM}")

    # Start background traffic
    if DOWNSTREAM:
        t = threading.Thread(target=background_traffic, daemon=True)
        t.start()

    server = HTTPServer(("0.0.0.0", SERVICE_PORT), ServiceHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Shutting down")
        server.shutdown()
