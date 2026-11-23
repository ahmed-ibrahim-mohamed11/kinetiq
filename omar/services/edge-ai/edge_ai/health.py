"""Tiny HTTP server: GET /healthz (JSON) and GET /metrics (Prometheus text). stdlib only."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

# C4 rejection codes plus the edge's own ('poison' = whole batch refused, 'unknown' = no code sent).
_REJECTION_CODES = frozenset(
    {
        "branch_mismatch",
        "no_consent",
        "unknown_reference",
        "invalid_value",
        "invalid_state",
        "internal",
        "poison",
        "unknown",
    }
)


def _label(value: Any) -> str:
    """Escape a Prometheus label value (backslash, double quote, newline)."""
    return str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


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
        "# TYPE edge_device_retired gauge",
        f"edge_device_retired {1 if health.get('device_retired') else 0}",
        "# TYPE edge_consent_cache_stale gauge",
        f"edge_consent_cache_stale {1 if health.get('consent_cache_stale') else 0}",
        "# TYPE edge_detections_excluded_total counter",
        f"edge_detections_excluded_total {health.get('detections_excluded', 0)}",
        "# TYPE edge_biometric_templates_undecryptable gauge",
        f"edge_biometric_templates_undecryptable {health.get('biometric_templates_undecryptable') or 0}",
        "# TYPE edge_dead_letter_depth gauge",
        f"edge_dead_letter_depth {(health.get('sync') or {}).get('dead_letter_depth', 0)}",
        "# TYPE edge_sync_rejected_total counter",
    ]
    by_code: dict[str, int] = {}
    for code, n in ((health.get("sync") or {}).get("rejections_by_code") or {}).items():
        key = code if code in _REJECTION_CODES else "other"  # server-supplied: never a free-form label
        by_code[key] = by_code.get(key, 0) + int(n)
    for code, n in sorted(by_code.items()):
        lines.append(f'edge_sync_rejected_total{{code="{code}"}} {n}')
    lines.append("# TYPE edge_camera_online gauge")
    for cam_id, c in (health.get("cameras") or {}).items():
        online = 1 if c.get("status") == "online" else 0
        cam = _label(cam_id)
        lines.append(f'edge_camera_online{{camera_id="{cam}",name="{_label(c.get("name", ""))}"}} {online}')
        lines.append(f'edge_camera_fps{{camera_id="{cam}"}} {c.get("fps", 0)}')
    return "\n".join(lines) + "\n"


def serve_health(
    port: int,
    health_fn: Callable[[], dict[str, Any]],
    pipeline: Any = None,
    readiness: Any = None,
) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("content-type", ctype)
            self.send_header("content-length", str(len(body)))
            self.send_header("access-control-allow-origin", "*")
            self.send_header("access-control-allow-methods", "GET, POST, OPTIONS")
            self.send_header("access-control-allow-headers", "content-type, authorization")
            self.send_header("connection", "close")
            self.end_headers()
            self.wfile.write(body)

        def address_string(self) -> str:
            # Prevent slow reverse DNS lookup on Windows
            return str(self.client_address[0])

        def do_OPTIONS(self) -> None:  # noqa: N802
            self.send_response(204)
            self.send_header("access-control-allow-origin", "*")
            self.send_header("access-control-allow-methods", "GET, POST, OPTIONS")
            self.send_header("access-control-allow-headers", "content-type, authorization")
            self.send_header("content-length", "0")
            self.end_headers()

        def do_GET(self) -> None:  # noqa: N802
            from urllib.parse import parse_qs, urlparse
            from edge_ai.camera.manager import CentralCameraManager

            manager = CentralCameraManager.get_instance()
            parsed_url = urlparse(self.path)
            q_params = parse_qs(parsed_url.query)
            cam_id = q_params.get("camera_id", [None])[0]
            cons_id = q_params.get("consumer_id", [f"consumer_{int(time.time()*1000)}"])[0]

            if self.path.startswith("/readyz"):
                if readiness is not None:
                    code, ready_data = readiness.ready_payload(pipeline, manager)
                    self._send(code, json.dumps(ready_data, default=str).encode(), "application/json")
                else:
                    health = health_fn()
                    is_ok = health.get("status") == "ok"
                    self._send(200 if is_ok else 503, json.dumps({"status": "ready" if is_ok else "not_ready", "ready": is_ok}).encode(), "application/json")
            elif self.path.startswith("/healthz"):
                # Evaluate once: the pipeline counters/state can change between
                # calls, which previously made the HTTP status disagree with
                # the JSON body seen by the browser.
                health = health_fn()
                body = json.dumps(health, default=str).encode()
                self._send(200 if health.get("status") in ("ok", "alive") else 503, body, "application/json")
            elif self.path.startswith("/metrics"):
                self._send(200, _prom(health_fn()).encode(), "text/plain; version=0.0.4")
            elif self.path.startswith("/api/camera/status"):
                status_data = manager.get_camera_status(cam_id)
                self._send(200, json.dumps(status_data).encode(), "application/json")
            elif self.path.startswith("/api/camera/diagnostics"):
                diag_data = manager.get_diagnostics(cam_id)
                self._send(200, json.dumps(diag_data).encode(), "application/json")
            elif self.path.startswith("/api/camera/frame"):
                frame_bytes = manager.get_consumer_frame(cam_id, cons_id)
                if frame_bytes:
                    self._send(200, frame_bytes, "image/jpeg")
                else:
                    self._send(404, b"no frame available", "text/plain")
            elif self.path.startswith("/api/camera/stream"):
                try:
                    self.send_response(200)
                    self.send_header("content-type", "multipart/x-mixed-replace; boundary=frame")
                    self.send_header("access-control-allow-origin", "*")
                    self.send_header("cache-control", "no-cache, no-store, must-revalidate")
                    self.send_header("pragma", "no-cache")
                    self.send_header("expires", "0")
                    self.end_headers()
                    for chunk in manager.stream_consumer_mjpeg(cam_id, cons_id):
                        self.wfile.write(chunk)
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass
                except Exception:
                    pass
            elif self.path.startswith("/live"):
                if pipeline is not None and hasattr(pipeline, "live_state"):
                    body = json.dumps(pipeline.live_state(), default=str).encode()
                    self._send(200, body, "application/json")
                else:
                    self._send(501, b'{"error":"live state not supported"}', "application/json")
            elif self.path.startswith("/camera/frame.jpg") or self.path.startswith("/frame.jpg"):
                frame_bytes = None
                if pipeline is not None and hasattr(pipeline, "latest_jpeg"):
                    frame_bytes = pipeline.latest_jpeg()
                if not frame_bytes:
                    frame_bytes = manager.get_consumer_frame(cam_id, cons_id)
                if frame_bytes:
                    self._send(200, frame_bytes, "image/jpeg")
                else:
                    self._send(404, b"no frame available", "text/plain")
            else:
                self._send(404, b"not found", "text/plain")

        def do_POST(self) -> None:  # noqa: N802
            from edge_ai.camera.manager import CentralCameraManager

            manager = CentralCameraManager.get_instance()
            content_length = int(self.headers.get("content-length", 0))
            payload = self.rfile.read(content_length) if content_length > 0 else b""
            data: dict[str, Any] = {}
            if payload:
                try:
                    data = json.loads(payload.decode("utf-8"))
                except Exception:
                    data = {}

            cam_id = data.get("camera_id")
            cons_id = data.get("consumer_id") or f"consumer_{int(time.time()*1000)}"

            if self.path.startswith("/api/camera/start"):
                res = manager.start_camera(cam_id, cons_id)
                self._send(200, json.dumps(res).encode(), "application/json")
            elif self.path.startswith("/api/camera/stop"):
                res = manager.stop_camera(cam_id, cons_id)
                self._send(200, json.dumps(res).encode(), "application/json")
            elif self.path.startswith("/api/camera/subscribe"):
                opts = data.get("options") or {}
                sub = manager.subscribe(cam_id, cons_id, opts)
                self._send(200, json.dumps({"ok": True, "consumer_id": sub.consumer_id, "camera_id": sub.camera_id}).encode(), "application/json")
            elif self.path.startswith("/api/camera/unsubscribe"):
                res = manager.unsubscribe(cam_id, cons_id)
                self._send(200, json.dumps(res).encode(), "application/json")
            elif self.path.startswith("/api/camera/heartbeat"):
                res = manager.heartbeat(cam_id, cons_id)
                self._send(200, json.dumps(res).encode(), "application/json")
            elif self.path.startswith("/api/camera/recognize"):
                img = data.get("image_b64") or data.get("image")
                min_conf = data.get("min_confidence")
                res = manager.recognize_face(cam_id, cons_id, img, min_conf)
                self._send(200, json.dumps(res).encode(), "application/json")
            elif self.path.startswith("/api/camera/enroll"):
                mem_id = str(data.get("member_id", ""))
                mem_name = data.get("member_name")
                raw_imgs = data.get("images_b64") or data.get("images", [])
                if isinstance(raw_imgs, str):
                    raw_imgs = [raw_imgs]
                cid = data.get("consent_record_id")
                res = manager.enroll_face(cam_id, cons_id, mem_id, mem_name, raw_imgs, cid)
                self._send(200 if res.get("ok") else 400, json.dumps(res).encode(), "application/json")
            elif self.path.startswith("/camera/release"):
                if pipeline is not None and hasattr(pipeline, "release_camera"):
                    res = pipeline.release_camera()
                    self._send(200, json.dumps(res).encode(), "application/json")
                else:
                    self._send(501, b'{"error":"release_camera not supported"}', "application/json")
            elif self.path.startswith("/camera/acquire"):
                if pipeline is not None and hasattr(pipeline, "acquire_camera"):
                    res = pipeline.acquire_camera()
                    self._send(200, json.dumps(res).encode(), "application/json")
                else:
                    self._send(501, b'{"error":"acquire_camera not supported"}', "application/json")
            elif self.path.startswith("/api/face/enroll"):
                if pipeline is not None and hasattr(pipeline, "enroll_face_base64"):
                    member_id = str(data.get("member_id", ""))
                    member_name = data.get("member_name")
                    raw_images = data.get("images", [])
                    if isinstance(raw_images, str):
                        raw_images = [raw_images]
                    consent_id = data.get("consent_record_id")
                    res = pipeline.enroll_face_base64(member_id, member_name, raw_images, consent_id)
                    code = 200 if res.get("ok") else 400
                    self._send(code, json.dumps(res).encode(), "application/json")
                else:
                    self._send(501, b'{"error":"enroll_face not supported"}', "application/json")
            elif self.path.startswith("/api/face/identify"):
                if pipeline is not None and hasattr(pipeline, "identify_face_base64"):
                    img_str = data.get("image") or (data.get("images", [None])[0] if isinstance(data.get("images"), list) else None)
                    if not img_str:
                        self._send(400, b'{"ok":false,"error":"Missing image in request body."}', "application/json")
                        return
                    min_conf = data.get("min_confidence")
                    res = pipeline.identify_face_base64(str(img_str), min_conf)
                    self._send(200, json.dumps(res).encode(), "application/json")
                else:
                    self._send(501, b'{"error":"identify_face not supported"}', "application/json")
            elif self.path.startswith("/api/workout/reset"):
                if pipeline is not None and hasattr(pipeline, "reset_workout"):
                    global_id = data.get("global_id")
                    res = pipeline.reset_workout(global_id)
                    self._send(200, json.dumps(res).encode(), "application/json")
                else:
                    self._send(501, b'{"error":"reset_workout not supported"}', "application/json")
            else:
                self._send(404, b"not found", "text/plain")

        def log_message(self, *_: Any) -> None:  # silence default stderr logging
            return

    class QueuedThreadingServer(ThreadingHTTPServer):
        request_queue_size = 64
        daemon_threads = True

    server = QueuedThreadingServer(("0.0.0.0", port), Handler)  # noqa: S104 — container-internal; firewall at host
    threading.Thread(target=server.serve_forever, name="health", daemon=True).start()
    return server
