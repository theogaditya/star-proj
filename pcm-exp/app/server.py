#!/usr/bin/env python3
"""
server.py — CPU-intensive HTTP server with Prometheus metrics endpoint.

On each request to /:
  - Burns CPU for ~50ms (busy loop)
  - Increments http_requests_total counter
  - Returns 200 OK

Exposes /metrics in Prometheus exposition format.
"""

import time
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

# ── Prometheus metrics (manual implementation, no external deps) ──────────────

REQUESTS_TOTAL = 0
REQUESTS_LOCK = threading.Lock()


def increment_requests():
    global REQUESTS_TOTAL
    with REQUESTS_LOCK:
        REQUESTS_TOTAL += 1


def get_requests_total():
    with REQUESTS_LOCK:
        return REQUESTS_TOTAL


# ── CPU burn ──────────────────────────────────────────────────────────────────

def burn_cpu(duration_ms=50):
    """Burn CPU for approximately duration_ms milliseconds."""
    end = time.monotonic() + (duration_ms / 1000.0)
    while time.monotonic() < end:
        _ = sum(i * i for i in range(100))


# ── HTTP Handler ──────────────────────────────────────────────────────────────

class AppHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/metrics":
            self._serve_metrics()
        elif self.path == "/healthz":
            self._send_response(200, "ok\n")
        else:
            self._serve_request()

    def _serve_request(self):
        increment_requests()
        burn_cpu(50)
        self._send_response(200, "OK\n")

    def _serve_metrics(self):
        total = get_requests_total()
        body = (
            "# HELP http_requests_total Total number of HTTP requests received.\n"
            "# TYPE http_requests_total counter\n"
            f"http_requests_total {total}\n"
        )
        self._send_response(200, body, content_type="text/plain; version=0.0.4")

    def _send_response(self, code, body, content_type="text/plain"):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.end_headers()
        self.wfile.write(body.encode())

    def log_message(self, format, *args):
        # Suppress per-request logs to reduce noise
        pass


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = 8080
    server = HTTPServer(("0.0.0.0", port), AppHandler)
    print(f"[cpu-http-app] listening on :{port}")
    print(f"  GET /         → burn CPU + count request")
    print(f"  GET /metrics  → Prometheus metrics")
    print(f"  GET /healthz  → health check")
    server.serve_forever()
