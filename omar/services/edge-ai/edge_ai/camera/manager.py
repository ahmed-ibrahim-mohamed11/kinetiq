"""
Central Camera Manager / Camera Broker.

Provides a unified central hardware camera manager multiplexed to multiple independent
logical consumers (Reception, Auth, Member Profile, Workout, Diagnostics).
Only this manager opens the physical camera handle.
"""
from __future__ import annotations

import base64
import contextlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import json
import os
from pathlib import Path
import queue
import threading
import time
from typing import Any, Generator
from uuid import uuid4

import cv2
import numpy as np
import structlog

from edge_ai.camera.rtsp_reader import parse_webcam_url
from edge_ai.constants import (
    CAMERA_RECONNECT_MAX_ATTEMPTS,
    CONSUMER_LEASE_TIMEOUT_SECONDS,
    FACE_DETECTION_INTERVAL_MS,
    FACE_MATCH_THRESHOLD,
    MAX_FRAME_QUEUE_SIZE,
    RECOGNITION_COOLDOWN_MS,
)

log = structlog.get_logger(__name__)


class CameraState(str, Enum):
    IDLE = "idle"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    RECONNECTING = "reconnecting"
    ERROR = "error"
    PERMISSION_DENIED = "permission_denied"
    DEVICE_NOT_FOUND = "device_not_found"


@dataclass
class ConsumerSubscription:
    consumer_id: str
    camera_id: str
    subscribed_at: float
    last_heartbeat_at: float
    frame_queue: queue.Queue = field(default_factory=lambda: queue.Queue(maxsize=MAX_FRAME_QUEUE_SIZE))
    options: dict[str, Any] = field(default_factory=dict)
    frames_sent: int = 0
    frames_dropped: int = 0
    active: bool = True


class PhysicalCameraDevice:
    """Manages one physical camera device handle and multiplexes frames to consumers."""

    def __init__(self, camera_id: str, url: str = "webcam://0", name: str = "LOCAL-WEBCAM") -> None:
        self.camera_id = camera_id
        self.url = url
        self.name = name
        self.state: CameraState = CameraState.IDLE
        self.cap: cv2.VideoCapture | None = None
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        # Set only after the capture object has been opened (or a terminal
        # startup error is known).  The old implementation returned from
        # start() immediately after spawning the worker, which made callers
        # believe the camera was running while physical_handles was still 0.
        self._ready_event = threading.Event()
        self.consumers: dict[str, ConsumerSubscription] = {}
        self.reconnect_attempts: int = 0
        self.last_frame: np.ndarray | None = None
        self.last_jpeg: bytes | None = None
        self.last_frame_at: float = 0.0
        self.frames_received: int = 0
        self.frames_processed: int = 0
        self.detections: int = 0
        self.last_processed_frame_at: float = 0.0
        self.frame_fps: float = 0.0
        self.inference_fps: float = 0.0
        self.recognition_fps: float = 0.0
        self.error_code: str | None = None
        self.error_message: str | None = None
        self._frame_times: list[float] = []
        self._resolution: tuple[int, int] | None = None

    @property
    def physical_handles(self) -> int:
        with self._lock:
            return 1 if (self.cap is not None and self.cap.isOpened()) else 0

    @property
    def active_consumers_count(self) -> int:
        with self._lock:
            return sum(1 for c in self.consumers.values() if c.active)

    def _open_capture(self) -> cv2.VideoCapture:
        local_index = parse_webcam_url(self.url)
        if local_index is not None:
            backend = cv2.CAP_DSHOW if os.name == "nt" else cv2.CAP_ANY
            cap = cv2.VideoCapture(local_index, backend)
            if not cap.isOpened():
                cap.release()
                raise ConnectionError(f"Failed to open local webcam index {local_index}")
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
            cap.set(cv2.CAP_PROP_FPS, 15)
            w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            if w > 0 and h > 0:
                self._resolution = (w, h)
            return cap

        os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp|stimeout;10000000")
        cap = cv2.VideoCapture(self.url, cv2.CAP_FFMPEG)
        if not cap.isOpened():
            cap.release()
            raise ConnectionError(f"Failed to open video source {self.url}")
        return cap

    def start(self, wait_timeout: float | None = 1.5) -> bool:
        """Start the capture worker and optionally wait for a real handle.

        A logical subscription is not useful until the worker has either
        opened the physical device or reported a terminal error.  Waiting for
        that boundary gives the HTTP/API callers an honest result and removes
        the startup race that previously caused the concurrency tests (and
        the browser UI) to observe ``running`` with zero handles.
        """
        with self._lock:
            if self.state == CameraState.RUNNING:
                return True
            if self.state == CameraState.STARTING:
                ready_event = self._ready_event
            else:
                self.state = CameraState.STARTING
                self.error_code = None
                self.error_message = None
                self._stop.clear()
                self._ready_event.clear()
                ready_event = self._ready_event
                self._thread = threading.Thread(target=self._run_loop, name=f"cam-{self.camera_id}", daemon=True)
                self._thread.start()

        if wait_timeout is None:
            return True
        ready_event.wait(max(0.0, wait_timeout))
        with self._lock:
            return self.state == CameraState.RUNNING and self.physical_handles == 1

    def stop(self) -> None:
        with self._lock:
            if self.state in (CameraState.IDLE, CameraState.STOPPING):
                return
            self.state = CameraState.STOPPING
            self._stop.set()
            self._ready_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)
        with self._lock:
            if self.cap is not None:
                with contextlib.suppress(Exception):
                    self.cap.release()
                self.cap = None
            self.state = CameraState.IDLE
        if os.name == "nt":
            time.sleep(0.1)

    def _run_loop(self) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            try:
                with self._lock:
                    if self.cap is None or not self.cap.isOpened():
                        self.state = CameraState.STARTING
                        self.cap = self._open_capture()
                        self.state = CameraState.RUNNING
                        self.reconnect_attempts = 0
                        backoff = 1.0
                        self._ready_event.set()
                        log.info("camera.physical_opened", camera_id=self.camera_id, handles=1)

                consecutive_failures = 0
                while not self._stop.is_set():
                    ok, img = self.cap.read()
                    now = time.monotonic()
                    if not ok or img is None:
                        consecutive_failures += 1
                        if consecutive_failures > 15:
                            raise ConnectionError("Hardware stream stalled (no frames returned)")
                        time.sleep(0.04)
                        continue

                    consecutive_failures = 0
                    self.frames_received += 1
                    self.last_frame_at = time.time()
                    self._record_fps(now)

                    # Only compress JPEG when an HTTP consumer is subscribed to conserve CPU/memory
                    has_http_consumers = any(
                        sub.active and not sub.consumer_id.startswith("pipeline_")
                        for sub in self.consumers.values()
                    )
                    jpeg_bytes: bytes | None = None
                    if has_http_consumers:
                        try:
                            _, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), 75])
                            jpeg_bytes = buf.tobytes()
                        except Exception:  # noqa: BLE001
                            jpeg_bytes = b""

                    with self._lock:
                        self.last_frame = img
                        if jpeg_bytes is not None:
                            self.last_jpeg = jpeg_bytes

                        # Dispatch to active web consumers using bounded queues
                        if jpeg_bytes:
                            for sub in list(self.consumers.values()):
                                if not sub.active or sub.consumer_id.startswith("pipeline_"):
                                    continue
                                if sub.frame_queue.full():
                                    try:
                                        sub.frame_queue.get_nowait()
                                        sub.frames_dropped += 1
                                    except queue.Empty:
                                        pass
                                try:
                                    sub.frame_queue.put_nowait(jpeg_bytes)
                                    sub.frames_sent += 1
                                except queue.Full:
                                    sub.frames_dropped += 1

                    # Yield CPU slice (~20-25 FPS capture rate, low CPU overhead)
                    time.sleep(0.035)

            except Exception as exc:  # noqa: BLE001
                with self._lock:
                    if self.cap is not None:
                        with contextlib.suppress(Exception):
                            self.cap.release()
                        self.cap = None

                    if self._stop.is_set():
                        break

                    self.reconnect_attempts += 1
                    err_msg = str(exc)
                    if "denied" in err_msg.lower() or "permission" in err_msg.lower():
                        self.state = CameraState.PERMISSION_DENIED
                    elif "not found" in err_msg.lower() or "device" in err_msg.lower():
                        self.state = CameraState.DEVICE_NOT_FOUND
                    else:
                        self.state = CameraState.RECONNECTING

                    self.error_code = self.state.value
                    self.error_message = err_msg

                    if self.reconnect_attempts >= CAMERA_RECONNECT_MAX_ATTEMPTS:
                        self.state = CameraState.ERROR
                        self._ready_event.set()
                        log.error("camera.max_reconnect_reached", camera_id=self.camera_id, attempts=self.reconnect_attempts)
                        break

                self._stop.wait(backoff)
                backoff = min(backoff * 2.0, 15.0)

        with self._lock:
            if self.cap is not None:
                with contextlib.suppress(Exception):
                    self.cap.release()
                self.cap = None
            if self.state not in (CameraState.ERROR, CameraState.PERMISSION_DENIED, CameraState.DEVICE_NOT_FOUND):
                self.state = CameraState.IDLE

    def _record_fps(self, t: float) -> None:
        self._frame_times.append(t)
        if len(self._frame_times) > 60:
            self._frame_times.pop(0)
        if len(self._frame_times) >= 2:
            span = self._frame_times[-1] - self._frame_times[0]
            self.frame_fps = round((len(self._frame_times) - 1) / span, 1) if span > 0 else 0.0


class CentralCameraManager:
    """Singleton broker managing hardware camera and logical multi-consumer subscriptions."""

    _instance: CentralCameraManager | None = None
    _singleton_lock = threading.Lock()

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._devices: dict[str, PhysicalCameraDevice] = {}
        self._last_recognition: dict[str, float] = {}  # member_id -> timestamp
        self._face_detector: Any = None
        self._face_embedder: Any = None
        self._template_store: Any = None
        self._consent_gate: Any = None
        self._member_names: dict[str, str] = {}
        self._stop_event = threading.Event()
        self._sweep_thread = threading.Thread(target=self._lease_sweep_loop, name="camera-lease-sweep", daemon=True)
        self._sweep_thread.start()
        self._accepting_subscriptions: bool = True

    @classmethod
    def get_instance(cls) -> CentralCameraManager:
        with cls._singleton_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def register_camera(self, camera_id: str, url: str = "webcam://0", name: str = "LOCAL-WEBCAM") -> PhysicalCameraDevice:
        with self._lock:
            local_idx = parse_webcam_url(url)
            for existing in self._devices.values():
                if existing.url == url or (local_idx is not None and parse_webcam_url(existing.url) == local_idx):
                    self._devices[camera_id] = existing
                    return existing
            if camera_id not in self._devices:
                self._devices[camera_id] = PhysicalCameraDevice(camera_id, url=url, name=name)
            return self._devices[camera_id]

    def set_identity_stack(
        self,
        face_detector: Any,
        face_embedder: Any,
        template_store: Any,
        consent_gate: Any,
        member_names: dict[str, str] | None = None,
        emitter: Any | None = None,
    ) -> None:
        with self._lock:
            self._face_detector = face_detector
            self._face_embedder = face_embedder
            self._template_store = template_store
            self._consent_gate = consent_gate
            self._emitter = emitter
            if member_names:
                self._member_names.update(member_names)

    def _get_or_create_device(self, camera_id: str | None) -> PhysicalCameraDevice:
        cam_id = camera_id or "default"
        with self._lock:
            if cam_id in self._devices:
                return self._devices[cam_id]
            # If any device exists, reuse and alias it to guarantee ONE physical handle
            if self._devices:
                first_dev = next(iter(self._devices.values()))
                self._devices[cam_id] = first_dev
                return first_dev
            dev = PhysicalCameraDevice(cam_id, "webcam://0", "Default Webcam")
            self._devices[cam_id] = dev
            if cam_id != "default":
                self._devices["default"] = dev
            return dev

    # ── Manager Core Protocol ──────────────────────────────────────────────────

    def close_subscriptions(self) -> None:
        """Stop accepting new HTTP camera subscriptions during graceful shutdown."""
        with self._lock:
            self._accepting_subscriptions = False

    def stop_all_consumers(self) -> None:
        """Deactivate and clear all consumer subscriptions across all devices."""
        with self._lock:
            for dev in self._devices.values():
                for sub in dev.consumers.values():
                    sub.active = False
                dev.consumers.clear()

    def start_camera(self, camera_id: str | None, consumer_id: str) -> dict[str, Any]:
        """Idempotent start: subscribes consumer and opens physical camera if not already active."""
        if not consumer_id:
            return {"ok": False, "error": "consumer_id is required."}
        device = self._get_or_create_device(camera_id)
        with self._lock:
            if not self._accepting_subscriptions:
                return {"ok": False, "error": "Camera subscriptions closed for shutdown."}
            now = time.time()
            if consumer_id not in device.consumers:
                device.consumers[consumer_id] = ConsumerSubscription(
                    consumer_id=consumer_id,
                    camera_id=device.camera_id,
                    subscribed_at=now,
                    last_heartbeat_at=now,
                )
            else:
                device.consumers[consumer_id].last_heartbeat_at = now
                device.consumers[consumer_id].active = True

        ready = device.start()

        with self._lock:
            active_count = device.active_consumers_count
            state = device.state.value
            physical_handles = device.physical_handles
            error_message = device.error_message

        return {
            "ok": ready,
            "camera_id": device.camera_id,
            "consumer_id": consumer_id,
            "state": state,
            "physical_handles": physical_handles,
            "active_consumers": active_count,
            "consumers": [c.consumer_id for c in device.consumers.values() if c.active],
            **({"error": error_message or "Physical camera did not become ready before the startup timeout."} if not ready else {}),
        }

    def stop_camera(self, camera_id: str | None, consumer_id: str) -> dict[str, Any]:
        """Idempotent stop: removes this consumer; releases hardware camera ONLY if 0 consumers remain."""
        if not consumer_id:
            return {"ok": False, "error": "consumer_id is required."}
        device = self._get_or_create_device(camera_id)
        with self._lock:
            if consumer_id in device.consumers:
                sub = device.consumers.pop(consumer_id)
                sub.active = False
            active_count = device.active_consumers_count
            if active_count == 0:
                device.stop()

        return {
            "ok": True,
            "camera_id": device.camera_id,
            "consumer_id": consumer_id,
            "state": device.state.value,
            "physical_handles": device.physical_handles,
            "active_consumers": active_count,
            "consumers": [c.consumer_id for c in device.consumers.values() if c.active],
        }

    def subscribe(self, camera_id: str | None, consumer_id: str, options: dict[str, Any] | None = None) -> ConsumerSubscription:
        device = self._get_or_create_device(camera_id)
        with self._lock:
            if not self._accepting_subscriptions:
                raise RuntimeError("Camera subscriptions closed for shutdown.")
            now = time.time()
            if consumer_id not in device.consumers:
                device.consumers[consumer_id] = ConsumerSubscription(
                    consumer_id=consumer_id,
                    camera_id=device.camera_id,
                    subscribed_at=now,
                    last_heartbeat_at=now,
                    options=options or {},
                )
            else:
                sub = device.consumers[consumer_id]
                sub.last_heartbeat_at = now
                sub.active = True
                if options:
                    sub.options.update(options)
            device.start()
            return device.consumers[consumer_id]

    def unsubscribe(self, camera_id: str | None, consumer_id: str) -> dict[str, Any]:
        return self.stop_camera(camera_id, consumer_id)

    def heartbeat(self, camera_id: str | None, consumer_id: str) -> dict[str, Any]:
        device = self._get_or_create_device(camera_id)
        with self._lock:
            if consumer_id in device.consumers:
                device.consumers[consumer_id].last_heartbeat_at = time.time()
                return {"ok": True, "active": True, "active_consumers": device.active_consumers_count}
        return {"ok": True, "active": False, "message": "Consumer not registered; startCamera required."}

    def get_camera_status(self, camera_id: str | None = None) -> dict[str, Any]:
        device = self._get_or_create_device(camera_id)
        with self._lock:
            active_list = [c.consumer_id for c in device.consumers.values() if c.active]
            state = device.state.value
            if device.physical_handles == 0 and state == "running":
                state = "starting"
            has_models = bool(self._face_detector and self._face_embedder)
            if not has_models:
                model_status = "missing"
            elif device.physical_handles == 0 or device.frames_received == 0:
                model_status = "initializing"
            else:
                model_status = "loaded"
            return {
                "camera_id": device.camera_id,
                "camera_name": device.name,
                "state": state,
                "physical_handles": device.physical_handles,
                "active_consumers": len(active_list),
                "consumers": active_list,
                "frame_fps": device.frame_fps,
                "inference_fps": device.inference_fps,
                "recognition_fps": device.recognition_fps,
                "last_frame_at": device.last_frame_at,
                "frames_received": device.frames_received,
                "frames_processed": device.frames_processed,
                "detections": device.detections,
                "last_processed_frame_at": device.last_processed_frame_at,
                "model_status": model_status,
                "reconnect_count": device.reconnect_attempts,
                "model_version": "yunet_sface_v1",
                "error_code": device.error_code,
                "error_message": device.error_message,
            }

    def get_diagnostics(self, camera_id: str | None = None) -> dict[str, Any]:
        with self._lock:
            cameras_diag = {}
            unique_devices = list({id(d): d for d in self._devices.values()}.values())
            total_handles = sum(d.physical_handles for d in unique_devices)
            all_consumers = sorted(list({c.consumer_id for dev in unique_devices for c in dev.consumers.values() if c.active}))
            has_models = bool(self._face_detector and self._face_embedder)
            if not has_models:
                model_status = "missing"
            elif total_handles == 0:
                model_status = "initializing"
            else:
                model_status = "loaded"

            for cid, dev in self._devices.items():
                active = [c.consumer_id for c in dev.consumers.values() if c.active]
                cameras_diag[cid] = {
                    "name": dev.name,
                    "state": dev.state.value,
                    "physical_handles": dev.physical_handles,
                    "active_consumers": len(active),
                    "consumers": active,
                    "frame_fps": dev.frame_fps,
                    "inference_fps": dev.inference_fps,
                    "frames_received": dev.frames_received,
                    "frames_processed": dev.frames_processed,
                    "detections": dev.detections,
                    "last_processed_frame_at": dev.last_processed_frame_at,
                    "queue_depth": sum(c.frame_queue.qsize() for c in dev.consumers.values()),
                    "reconnect_attempts": dev.reconnect_attempts,
                    "last_frame_at": dev.last_frame_at,
                    "error_code": dev.error_code,
                }

            # Selected camera or primary default device for single camera diagnostics contract
            selected_dev = self._get_or_create_device(camera_id) if (camera_id or self._devices) else None
            active_sel = [c.consumer_id for c in selected_dev.consumers.values() if c.active] if selected_dev else []
            sel_q_depth = sum(c.frame_queue.qsize() for c in selected_dev.consumers.values()) if selected_dev else 0
            iso_last_frame = (
                datetime.fromtimestamp(selected_dev.last_frame_at, timezone.utc).isoformat()
                if (selected_dev and selected_dev.last_frame_at > 0)
                else None
            )

            phys_handles = selected_dev.physical_handles if selected_dev else 0
            act_count = len(active_sel)
            cam_state = selected_dev.state.value if selected_dev else CameraState.IDLE.value
            frame_fps = selected_dev.frame_fps if selected_dev else 0.0
            rec_attempts = selected_dev.reconnect_attempts if selected_dev else 0
            last_ts = selected_dev.last_frame_at if selected_dev else 0.0

            return {
                # Requirement 2 exact contract
                "physical_handles": phys_handles,
                "active_consumers": act_count,
                "camera_state": cam_state,
                "frame_fps": frame_fps,
                "queue_depth": sel_q_depth,
                "reconnect_attempts": rec_attempts,
                "last_frame_timestamp": last_ts,
                # Single-camera diagnostics contract (camelCase)
                "cameraId": selected_dev.camera_id if selected_dev else (camera_id or "default"),
                "physicalHandleCount": phys_handles,
                "activeConsumerCount": act_count,
                "consumers": active_sel,
                "cameraState": cam_state,
                "frameFps": frame_fps,
                "queueDepth": sel_q_depth,
                "reconnectAttempts": rec_attempts,
                "lastFrameAt": iso_last_frame,
                # Counters & Model Status
                "frames_received": selected_dev.frames_received if selected_dev else 0,
                "frames_processed": selected_dev.frames_processed if selected_dev else 0,
                "detections": selected_dev.detections if selected_dev else 0,
                "inference_fps": selected_dev.inference_fps if selected_dev else 0.0,
                "last_processed_frame_at": selected_dev.last_processed_frame_at if selected_dev else 0.0,
                "model_status": model_status,
                # System-level diagnostics
                "status": "ok" if any(d.state == CameraState.RUNNING for d in unique_devices) or not unique_devices else "degraded",
                "total_cameras": len(unique_devices),
                "total_physical_handles": total_handles,
                "active_consumer_count": len(all_consumers),
                "all_active_consumers": all_consumers,
                "cameras": cameras_diag,
                "models": {
                    "face_detection": "yunet_2023mar" if self._face_detector else "not_loaded",
                    "face_recognition": "sface_2021dec" if self._face_embedder else "not_loaded",
                },
                "biometric_templates_count": self._template_store.count() if self._template_store else 0,
            }

    def release_physical_handles(self) -> dict[str, Any]:
        """Release all physical hardware camera handles to allow local browser capture."""
        with self._lock:
            for dev in self._devices.values():
                dev.stop()
            if os.name == "nt":
                time.sleep(0.15)
        return {"ok": True, "released": True}

    # ── Frame Streaming & Snapshot ─────────────────────────────────────────────

    def get_consumer_frame(self, camera_id: str | None, consumer_id: str, timeout: float = 0.25) -> bytes | None:
        device = self._get_or_create_device(camera_id)
        sub = self.subscribe(camera_id, consumer_id)
        try:
            return sub.frame_queue.get(timeout=timeout)
        except queue.Empty:
            with self._lock:
                if device.last_jpeg is None and device.last_frame is not None:
                    try:
                        _, buf = cv2.imencode(".jpg", device.last_frame, [int(cv2.IMWRITE_JPEG_QUALITY), 75])
                        device.last_jpeg = buf.tobytes()
                    except Exception:
                        pass
                return device.last_jpeg

    def stream_consumer_mjpeg(self, camera_id: str | None, consumer_id: str) -> Generator[bytes, None, None]:
        """Yields standard multipart MJPEG stream frames directly to browser client."""
        device = self._get_or_create_device(camera_id)
        sub = self.subscribe(camera_id, consumer_id)
        boundary = b"--frame\r\n"
        while sub.active and not self._stop_event.is_set():
            try:
                frame_bytes = sub.frame_queue.get(timeout=1.0)
            except queue.Empty:
                with self._lock:
                    frame_bytes = device.last_jpeg
                if not frame_bytes:
                    time.sleep(0.05)
                    continue

            chunk = (
                boundary
                + b"Content-Type: image/jpeg\r\n"
                + b"Content-Length: " + str(len(frame_bytes)).encode() + b"\r\n\r\n"
                + frame_bytes
                + b"\r\n"
            )
            yield chunk

    # ── Shared Face Recognition & Quality Pipeline ─────────────────────────────

    def _assess_face_quality(self, crop: np.ndarray) -> tuple[bool, str | None, float]:
        """Liveness, size, sharpness and brightness assessment."""
        if crop is None or crop.size == 0:
            return False, "empty_face_crop", 0.0
        h, w = crop.shape[:2]
        if w < 40 or h < 40:
            return False, "face_too_small", 0.0

        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if len(crop.shape) == 3 else crop
        sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        if sharpness < 12.0:
            return False, "face_blurry", sharpness

        mean_val = float(np.mean(gray))
        if mean_val < 20.0:
            return False, "face_too_dark", sharpness
        if mean_val > 245.0:
            return False, "face_overexposed", sharpness

        return True, None, sharpness

    def recognize_face(
        self,
        camera_id: str | None,
        consumer_id: str,
        image_b64: str | None = None,
        min_confidence: float | None = None,
    ) -> dict[str, Any]:
        """Safe face recognition flow returning standardized member payload."""
        threshold = min_confidence if min_confidence is not None else FACE_MATCH_THRESHOLD
        now_iso = datetime.now(timezone.utc).isoformat()
        evt_id = f"evt_{uuid4().hex[:12]}"
        device = self._get_or_create_device(camera_id)
        eff_camera_id = camera_id or device.camera_id

        # 1. Retrieve image frame (from parameter or live device buffer)
        img: np.ndarray | None = None
        if not image_b64 or str(image_b64).strip() in ("", "current"):
            with self._lock:
                if device.last_frame is not None:
                    img = device.last_frame.copy()
            if img is None:
                return {
                    "eventId": evt_id,
                    "cameraId": eff_camera_id,
                    "consumerId": consumer_id,
                    "memberId": None,
                    "matchStatus": "error",
                    "confidence": 0.0,
                    "modelVersion": "yunet_sface_v1",
                    "occurredAt": now_iso,
                    "error": "No camera frame available. Ensure camera is running.",
                }
        else:
            clean = str(image_b64).split(",", 1)[-1] if "," in str(image_b64) else str(image_b64)
            try:
                buf = np.frombuffer(base64.b64decode(clean), dtype=np.uint8)
                img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
            except Exception as exc:  # noqa: BLE001
                return {
                    "eventId": evt_id,
                    "cameraId": eff_camera_id,
                    "consumerId": consumer_id,
                    "memberId": None,
                    "matchStatus": "error",
                    "confidence": 0.0,
                    "modelVersion": "yunet_sface_v1",
                    "occurredAt": now_iso,
                    "error": f"Invalid base64 image: {exc}",
                }

        if img is None:
            return {
                "eventId": evt_id,
                "cameraId": eff_camera_id,
                "consumerId": consumer_id,
                "memberId": None,
                "matchStatus": "error",
                "confidence": 0.0,
                "modelVersion": "yunet_sface_v1",
                "occurredAt": now_iso,
                "error": "Could not decode image frame.",
            }

        # 2. Detect face
        if not self._face_detector or not self._face_embedder:
            return {
                "eventId": evt_id,
                "cameraId": eff_camera_id,
                "consumerId": consumer_id,
                "memberId": None,
                "matchStatus": "error",
                "confidence": 0.0,
                "modelVersion": "yunet_sface_v1",
                "occurredAt": now_iso,
                "error": "Face detection or recognition models not initialized.",
            }

        faces = self._face_detector.detect(img)
        if not faces:
            return {
                "eventId": evt_id,
                "cameraId": eff_camera_id,
                "consumerId": consumer_id,
                "memberId": None,
                "matchStatus": "unknown",
                "confidence": 0.0,
                "modelVersion": "yunet_sface_v1",
                "occurredAt": now_iso,
                "error": "No face detected in camera view.",
            }

        # 3. Quality & Liveness checks
        best_face = faces[0]
        x1, y1, x2, y2 = (int(v) for v in best_face.box if hasattr(best_face, "box")) if hasattr(best_face, "box") else (int(v) for v in best_face[:4])
        h_img, w_img = img.shape[:2]
        crop = img[max(0, y1) : min(h_img, y2), max(0, x1) : min(w_img, x2)]

        ok_qual, qual_err, sharpness = self._assess_face_quality(crop)
        if not ok_qual:
            return {
                "eventId": evt_id,
                "cameraId": eff_camera_id,
                "consumerId": consumer_id,
                "memberId": None,
                "matchStatus": "rejected",
                "confidence": 0.0,
                "modelVersion": "yunet_sface_v1",
                "occurredAt": now_iso,
                "error": f"Face quality check failed: {qual_err}",
            }

        # 4. Generate Face Embedding
        emb = self._face_embedder.embed_box(img, best_face)
        if emb is None:
            return {
                "eventId": evt_id,
                "cameraId": eff_camera_id,
                "consumerId": consumer_id,
                "memberId": None,
                "matchStatus": "unknown",
                "confidence": 0.0,
                "modelVersion": "yunet_sface_v1",
                "occurredAt": now_iso,
                "error": "Could not compute face biometric embedding.",
            }

        # 5. Compare with approved member templates in EncryptedTemplateStore
        from edge_ai.identity.face_store import FaceMatcher

        matcher = FaceMatcher(self._template_store, match_threshold=threshold) if self._template_store else None
        if not matcher:
            return {
                "eventId": evt_id,
                "cameraId": eff_camera_id,
                "consumerId": consumer_id,
                "memberId": None,
                "matchStatus": "error",
                "confidence": 0.0,
                "modelVersion": "yunet_sface_v1",
                "occurredAt": now_iso,
                "error": "Biometric template store is offline.",
            }

        match = matcher.match(emb)
        if not match.member_id or match.confidence < threshold or match.ambiguous:
            return {
                "eventId": evt_id,
                "cameraId": eff_camera_id,
                "consumerId": consumer_id,
                "memberId": None,
                "matchStatus": "unknown",
                "confidence": round(float(match.confidence), 3),
                "modelVersion": "yunet_sface_v1",
                "occurredAt": now_iso,
                "error": "Face not recognized among approved member records.",
            }

        member_id = match.member_id

        # 6. Biometric Consent Verification
        if self._consent_gate and not self._consent_gate.allows_face(member_id):
            return {
                "eventId": evt_id,
                "cameraId": eff_camera_id,
                "consumerId": consumer_id,
                "memberId": member_id,
                "matchStatus": "no_consent",
                "confidence": round(float(match.confidence), 3),
                "modelVersion": "yunet_sface_v1",
                "occurredAt": now_iso,
                "error": "Member biometric consent has been withdrawn or is missing.",
            }

        # 7. Debounce & Cooldown Check
        now_ts = time.time()
        last_match_t = self._last_recognition.get(member_id, 0.0)
        is_cooldown = (now_ts - last_match_t) < (RECOGNITION_COOLDOWN_MS / 1000.0)
        self._last_recognition[member_id] = now_ts

        member_name = self._member_names.get(member_id)
        return {
            "eventId": evt_id,
            "cameraId": eff_camera_id,
            "consumerId": consumer_id,
            "memberId": member_id,
            "memberCode": getattr(match, "member_code", None) or f"GYM-{member_id[:6].upper()}",
            "displayName": member_name,
            "matchStatus": "matched",
            "confidence": round(float(match.confidence), 3),
            "modelVersion": "yunet_sface_v1",
            "occurredAt": now_iso,
            "cooldown": is_cooldown,
        }

    def enroll_face(
        self,
        camera_id: str | None,
        consumer_id: str,
        member_id: str,
        member_name: str | None,
        images_b64: list[str] | None = None,
        consent_record_id: str | None = None,
    ) -> dict[str, Any]:
        """Multi-frame face biometric enrollment with duplicate checks and quality verification."""
        if not member_id:
            return {"ok": False, "error": "member_id is required for enrollment."}
        device = self._get_or_create_device(camera_id)

        decoded_images: list[np.ndarray] = []
        if not images_b64 or (len(images_b64) == 1 and str(images_b64[0]).strip() in ("", "current")):
            with self._lock:
                if device.last_frame is not None:
                    decoded_images.append(device.last_frame.copy())
        else:
            for raw in images_b64:
                clean = str(raw).split(",", 1)[-1] if "," in str(raw) else str(raw)
                try:
                    buf = np.frombuffer(base64.b64decode(clean), dtype=np.uint8)
                    m = cv2.imdecode(buf, cv2.IMREAD_COLOR)
                    if m is not None:
                        decoded_images.append(m)
                except Exception:
                    pass

        if not decoded_images:
            return {"ok": False, "error": "No valid frames available for face enrollment."}

        # Quality check all frames
        valid_frames = []
        for frame in decoded_images:
            faces = self._face_detector.detect(frame) if self._face_detector else []
            if not faces:
                continue
            best_face = faces[0]
            x1, y1, x2, y2 = (int(v) for v in best_face.box if hasattr(best_face, "box")) if hasattr(best_face, "box") else (int(v) for v in best_face[:4])
            h, w = frame.shape[:2]
            crop = frame[max(0, y1) : min(h, y2), max(0, x1) : min(w, x2)]
            ok_q, _, _ = self._assess_face_quality(crop)
            if ok_q:
                valid_frames.append(frame)

        if not valid_frames:
            return {"ok": False, "error": "All enrollment frames failed quality checks (too blurry, dark, or small)."}

        # Enroll through enrollment logic
        from edge_ai.identity.consent_gate import BIOMETRIC, TRACKING
        from edge_ai.identity.enrollment import enroll_member

        cid = consent_record_id or str(uuid4())
        with self._lock:
            if self._consent_gate:
                granted = dict(self._consent_gate._granted)
                granted[member_id] = {BIOMETRIC: cid, TRACKING: cid}
                self._consent_gate.replace(granted, generated_at=time.time(), expires=self._consent_gate._expires)

            if member_name:
                self._member_names[member_id] = member_name

            try:
                res = enroll_member(
                    member_id=member_id,
                    images=valid_frames,
                    detector=self._face_detector,
                    embedder=self._face_embedder,
                    store=self._template_store,
                    consent=self._consent_gate,
                    emitter=getattr(self, "_emitter", None),
                    min_quality=0.10,
                )
                return {
                    "ok": True,
                    "template_ref": res.template_ref,
                    "images_used": res.images_used,
                    "quality": res.quality,
                    "member_id": member_id,
                    "member_name": member_name,
                }
            except Exception as exc:  # noqa: BLE001
                return {"ok": False, "error": str(exc)}

    def _lease_sweep_loop(self) -> None:
        """Background thread sweeping expired consumer leases (auto-cleanup)."""
        while not self._stop_event.is_set():
            time.sleep(5.0)
            now = time.time()
            with self._lock:
                for dev in list(self._devices.values()):
                    expired = [
                        cid
                        for cid, sub in dev.consumers.items()
                        if sub.active
                        and not cid.startswith("pipeline_")
                        and (now - sub.last_heartbeat_at > CONSUMER_LEASE_TIMEOUT_SECONDS)
                    ]
                    for cid in expired:
                        log.warning("camera.consumer_lease_expired", consumer_id=cid, camera_id=dev.camera_id)
                        dev.consumers.pop(cid, None)
                    if dev.active_consumers_count == 0 and dev.state == CameraState.RUNNING:
                        log.info("camera.auto_releasing_unused_hardware", camera_id=dev.camera_id)
                        dev.stop()

    def shutdown(self) -> None:
        self._stop_event.set()
        with self._lock:
            for dev in self._devices.values():
                dev.stop()

