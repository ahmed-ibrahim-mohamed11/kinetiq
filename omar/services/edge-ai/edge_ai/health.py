"""Tiny HTTP server: GET /healthz (JSON) and GET /metrics (Prometheus text). stdlib only."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


def _prom(health: dict[str, Any]) -> str:
    lines = [
        "# TYPE edge_queue_depth gauge",
        f"edge_queue_depth {health.get('queue_depth', 0)}",
        "# TYPE edge_frames_processed_total counter",
        f"edge_frames_processed_total {health.get('frames_processed', 0)}",
        "# TYPE edge_avg_latency_ms gauge",
        f"edge_avg_latency_ms {health.get('avg_latency_ms') or 0}",
        "# TYPE edge_p95_latency_ms gauge",
        f"edge_p95_latency_ms {health.get('p95_latency_ms') or 0}",
        "# TYPE edge_identities_active gauge",
        f"edge_identities_active {health.get('identities_active', 0)}",
        "# TYPE edge_reps_admitted_total counter",
        f"edge_reps_admitted_total {health.get('reps_admitted', 0)}",
        "# TYPE edge_reps_suppressed_total counter",
        f"edge_reps_suppressed_total {health.get('reps_suppressed_duplicates', 0)}",
        "# TYPE edge_identity_merges_refused_total counter",
        f"edge_identity_merges_refused_total {health.get('merges_refused', 0)}",
        "# TYPE edge_camera_online gauge",
    ]
    for cam_id, c in (health.get("cameras") or {}).items():
        online = 1 if c.get("status") == "online" else 0
        lines.append(f'edge_camera_online{{camera_id="{cam_id}",name="{c.get("name", "")}"}} {online}')
        lines.append(f'edge_camera_fps{{camera_id="{cam_id}"}} {c.get("fps", 0)}')
    return "\n".join(lines) + "\n"


def serve_health(port: int, health_fn: Callable[[], dict[str, Any]]) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            if self.path.startswith("/healthz"):
                body = json.dumps(health_fn(), default=str).encode()
                self._send(200 if health_fn().get("status") == "ok" else 503, body, "application/json")
            elif self.path.startswith("/metrics"):
                self._send(200, _prom(health_fn()).encode(), "text/plain; version=0.0.4")
            else:
                self._send(404, b"not found", "text/plain")

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("content-type", ctype)
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_: Any) -> None:  # silence default stderr logging
            return

    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)  # noqa: S104 — container-internal; firewall at host
    threading.Thread(target=server.serve_forever, name="health", daemon=True).start()
    return server
