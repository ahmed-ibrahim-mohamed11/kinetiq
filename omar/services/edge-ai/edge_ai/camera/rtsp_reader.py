"""
Threaded RTSP reader with automatic reconnection and measured FPS.

Design
------
* One daemon thread per camera decodes frames continuously and keeps only the
  latest frame (`latest()`), so inference never falls behind the live stream —
  dropping frames is preferable to growing latency.
* Reconnection uses exponential backoff (1s → 30s) and emits status changes
  through a callback so the pipeline can send `camera.status` events.
* OpenCV is imported lazily so the rest of the package (and unit tests) do not
  need it installed.
"""

from __future__ import annotations

import contextlib
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol

import structlog

from edge_ai.config import redact_url

log = structlog.get_logger(__name__)


class StreamStatus(str, Enum):
    ONLINE = "online"
    RECONNECTING = "reconnecting"
    OFFLINE = "offline"
    DEGRADED = "degraded"


@dataclass
class Frame:
    image: Any  # numpy ndarray (H, W, 3) BGR
    captured_at: float  # time.time() wall clock
    monotonic: float  # time.monotonic()
    frame_index: int


class FrameSource(Protocol):
    """Anything that yields frames: RtspReader, a video file, or a synthetic generator for tests."""

    def latest(self) -> Frame | None: ...
    def status(self) -> StreamStatus: ...
    def measured_fps(self) -> float: ...
    def start(self) -> None: ...
    def stop(self) -> None: ...


def parse_webcam_url(url: str) -> int | None:
    """`webcam://<index>` (local USB/laptop camera, development only) → device index, else None."""
    if not url.lower().startswith("webcam://"):
        return None
    rest = url.split("://", 1)[1].strip("/")
    if not rest.isdigit():
        raise ValueError(f"webcam URL must be webcam://<device index>, got {url!r}")
    return int(rest)


@dataclass
class RtspReader:
    url: str  # may include credentials; NEVER logged (use redact_url)
    name: str = "camera"
    reconnect_min_s: float = 1.0
    reconnect_max_s: float = 30.0
    read_timeout_s: float = 10.0
    on_status: Callable[[StreamStatus, str | None], None] | None = None
    transport: str = "tcp"

    _thread: threading.Thread | None = field(default=None, init=False, repr=False)
    _stop: threading.Event = field(default_factory=threading.Event, init=False, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)
    _latest: Frame | None = field(default=None, init=False, repr=False)
    _status: StreamStatus = field(default=StreamStatus.OFFLINE, init=False)
    _frame_times: deque[float] = field(default_factory=lambda: deque(maxlen=60), init=False, repr=False)
    _frame_index: int = field(default=0, init=False)
    _last_error: str | None = field(default=None, init=False)
    resolution: tuple[int, int] | None = field(default=None, init=False)

    # ── public API ───────────────────────────────────────────────────────────
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name=f"rtsp-{self.name}", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=self.read_timeout_s + 2)

    def latest(self) -> Frame | None:
        with self._lock:
            return self._latest

    def status(self) -> StreamStatus:
        return self._status

    def last_error(self) -> str | None:
        return self._last_error

    def measured_fps(self) -> float:
        with self._lock:
            if len(self._frame_times) < 2:
                return 0.0
            span = self._frame_times[-1] - self._frame_times[0]
            return (len(self._frame_times) - 1) / span if span > 0 else 0.0

    # ── internals ────────────────────────────────────────────────────────────
    def _set_status(self, status: StreamStatus, error: str | None = None) -> None:
        if status != self._status or error != self._last_error:
            self._status = status
            self._last_error = error
            log.info("camera.status", camera=self.name, status=status.value, error=error, url=redact_url(self.url))
            if self.on_status:
                try:
                    self.on_status(status, error)
                except Exception:  # noqa: BLE001 — never let a callback kill the reader
                    log.exception("camera.status_callback_failed", camera=self.name)

    def _open(self) -> Any:
        import os

        import cv2  # lazy

        local_index = parse_webcam_url(self.url)
        if local_index is not None:
            # Local webcam (development): `webcam://0` → device index 0. DirectShow on Windows opens
            # USB/laptop cameras quickly; other platforms use OpenCV's default backend.
            backend = cv2.CAP_DSHOW if os.name == "nt" else cv2.CAP_ANY
            cap = cv2.VideoCapture(local_index, backend)
            if not cap.isOpened():
                cap.release()
                raise ConnectionError(f"failed to open local webcam {local_index}")
            # Request 640x480 @ 15fps to conserve CPU/memory during local edge inference
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
            cap.set(cv2.CAP_PROP_FPS, 15)
            w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            if w > 0 and h > 0:
                self.resolution = (w, h)
            return cap

        # Force TCP so packet loss produces delays instead of corrupt frames; 10s socket timeout.
        os.environ.setdefault(
            "OPENCV_FFMPEG_CAPTURE_OPTIONS",
            f"rtsp_transport;{self.transport}|stimeout;{int(self.read_timeout_s * 1_000_000)}",
        )
        cap = cv2.VideoCapture(self.url, cv2.CAP_FFMPEG)
        if not cap.isOpened():
            cap.release()
            raise ConnectionError("VideoCapture failed to open stream")
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        if w > 0 and h > 0:
            self.resolution = (w, h)
        return cap

    def _run(self) -> None:
        backoff = self.reconnect_min_s
        while not self._stop.is_set():
            cap = None
            try:
                self._set_status(StreamStatus.RECONNECTING if self._frame_index else StreamStatus.OFFLINE)
                cap = self._open()
                self._set_status(StreamStatus.ONLINE)
                backoff = self.reconnect_min_s
                last_ok = time.monotonic()
                consecutive_failures = 0
                while not self._stop.is_set():
                    ok, img = cap.read()
                    now_m = time.monotonic()
                    if not ok or img is None:
                        consecutive_failures += 1
                        if now_m - last_ok > self.read_timeout_s or consecutive_failures > 30:
                            raise ConnectionError("stream stalled (no frames)")
                        time.sleep(0.02)
                        continue
                    consecutive_failures = 0
                    last_ok = now_m
                    self._frame_index += 1
                    frame = Frame(image=img, captured_at=time.time(), monotonic=now_m, frame_index=self._frame_index)
                    with self._lock:
                        self._latest = frame
                        self._frame_times.append(now_m)
                    if self.measured_fps() and self.measured_fps() < 3.0 and self._frame_index > 60:
                        self._set_status(StreamStatus.DEGRADED, "measured fps below 3")
                    elif self._status == StreamStatus.DEGRADED and self.measured_fps() >= 5.0:
                        self._set_status(StreamStatus.ONLINE)
            except Exception as exc:  # noqa: BLE001 — any decode/network failure → reconnect
                self._set_status(StreamStatus.RECONNECTING, str(exc)[:200])
                self._stop.wait(backoff)
                backoff = min(backoff * 2, self.reconnect_max_s)
            finally:
                if cap is not None:
                    with contextlib.suppress(Exception):
                        cap.release()
        self._set_status(StreamStatus.OFFLINE)


class SyntheticFrameSource:
    """Deterministic frame source for tests/benchmarks: emits a black frame at a fixed rate."""

    def __init__(self, width: int = 640, height: int = 360, fps: float = 10.0) -> None:
        self.width, self.height, self.fps = width, height, fps
        self._idx = 0
        self._started = False

    def start(self) -> None:
        self._started = True

    def stop(self) -> None:
        self._started = False

    def status(self) -> StreamStatus:
        return StreamStatus.ONLINE if self._started else StreamStatus.OFFLINE

    def measured_fps(self) -> float:
        return self.fps

    def latest(self) -> Frame | None:
        import numpy as np

        self._idx += 1
        now = time.time()
        return Frame(np.zeros((self.height, self.width, 3), dtype=np.uint8), now, time.monotonic(), self._idx)
