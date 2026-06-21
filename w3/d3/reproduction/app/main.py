"""
Cloudflare WAF Regex Reproduction — Edge Server
Simulates a Cloudflare edge server with WAF regex middleware.
When EVIL_REGEX_DEPLOYED=true, all incoming requests are matched against
an evil regex with nested quantifiers, causing catastrophic backtracking
and CPU saturation (reproducing the 2019-07-02 Cloudflare outage).
"""

import os
import re
import time
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from prometheus_client import Counter, Histogram, Gauge, generate_latest

# --- Metrics ---
REQUEST_COUNT = Counter("http_requests_total", "Total HTTP requests", ["method", "path", "status"])
REQUEST_LATENCY = Histogram("http_request_duration_seconds", "Request latency", ["method", "path"])
CPU_USAGE = Gauge("edge_cpu_usage_percent", "Simulated CPU usage percent")
WAF_STATUS = Gauge("waf_evil_regex_deployed", "1 if evil regex is deployed, 0 otherwise")
WAF_MATCH_DURATION = Histogram("waf_regex_match_duration_seconds", "Time spent in WAF regex matching")

# --- Evil regex (simplified from Cloudflare incident) ---
EVIL_REGEX = r'(?:(?:\"|\d|.*)+(?:.*=.*))'
SAFE_REGEX = r'^[a-zA-Z0-9\s]+$'

# --- Global state ---
evil_regex_deployed = os.environ.get("EVIL_REGEX_DEPLOYED", "false").lower() == "true"


class WAFHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        start = time.time()

        if self.path == "/health":
            self._respond(200, "OK")
            return

        if self.path == "/metrics":
            data = generate_latest()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(data)
            return

        # WAF check
        body = ""
        if "Content-Length" in self.headers:
            body = self.rfile.read(int(self.headers["Content-Length"])).decode()

        waf_start = time.time()
        if evil_regex_deployed:
            WAF_STATUS.set(1)
            try:
                # This will cause catastrophic backtracking on adversarial input
                re.match(EVIL_REGEX, body, re.DOTALL)
            except Exception:
                pass
        else:
            WAF_STATUS.set(0)
            re.match(SAFE_REGEX, body)
        waf_elapsed = time.time() - waf_start
        WAF_MATCH_DURATION.observe(waf_elapsed)

        # Simulate CPU metric based on regex time
        if waf_elapsed > 1.0:
            CPU_USAGE.set(min(100, waf_elapsed * 10))
        else:
            CPU_USAGE.set(5.0)

        elapsed = time.time() - start
        if elapsed > 5.0:
            # Server effectively hung — return 503
            self._respond(503, "Service Unavailable — WAF timeout")
            REQUEST_COUNT.labels(method="GET", path=self.path, status="503").inc()
        else:
            self._respond(200, f"OK — processed in {elapsed:.4f}s")
            REQUEST_COUNT.labels(method="GET", path=self.path, status="200").inc()

        REQUEST_LATENCY.labels(method="GET", path=self.path).observe(elapsed)

    def do_POST(self):
        self.do_GET()

    def _respond(self, code, msg):
        self.send_response(code)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(msg.encode())

    def log_message(self, format, *args):
        # Suppress default access logs
        pass


def enable_evil_regex():
    """Background thread: listens for SIGUSR1 or file flag to enable evil regex."""
    global evil_regex_deployed
    flag_file = "/tmp/evil_regex_flag"
    while True:
        if os.path.exists(flag_file):
            evil_regex_deployed = True
            print("[WAF] Evil regex DEPLOYED — catastrophic backtracking enabled", flush=True)
        time.sleep(1)


if __name__ == "__main__":
    port = 8080
    print(f"[Edge Server] Starting on port {port}", flush=True)
    print(f"[WAF] Evil regex deployed: {evil_regex_deployed}", flush=True)

    # Start flag watcher in background
    t = threading.Thread(target=enable_evil_regex, daemon=True)
    t.start()

    server = HTTPServer(("0.0.0.0", port), WAFHandler)
    server.serve_forever()
