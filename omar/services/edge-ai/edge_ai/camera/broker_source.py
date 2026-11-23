"""
BrokerFrameSource: Adapter allowing Edge AI Pipeline to consume frames
from CentralCameraManager without opening a separate cv2.VideoCapture handle.
"""
from __future__ import annotations

import time
from typing import Any

from edge_ai.camera.manager import CameraState, CentralCameraManager
from edge_ai.camera.rtsp_reader import Frame, FrameSource, StreamStatus


class BrokerFrameSource:
    """FrameSource that delegates hardware ownership exclusively to CentralCameraManager."""

    def __init__(self, camera_id: str, url: str = "webcam://0", name: str = "LOCAL-CAMERA") -> None:
        self.camera_id = camera_id
        self.url = url
        self.name = name
        self.consumer_id = f"pipeline_{camera_id}"
        self._frame_index = 0
        self._last_frame: Frame | None = None
        self._started = False

    def start(self) -> None:
        mgr = CentralCameraManager.get_instance()
        mgr.register_camera(self.camera_id, url=self.url, name=self.name)
        mgr.start_camera(self.camera_id, self.consumer_id)
        self._started = True

    def stop(self) -> None:
        mgr = CentralCameraManager.get_instance()
        mgr.stop_camera(self.camera_id, self.consumer_id)
        self._started = False

    def status(self) -> StreamStatus:
        mgr = CentralCameraManager.get_instance()
        st = mgr.get_camera_status(self.camera_id)
        cam_state = st.get("state")
        if cam_state in (CameraState.RUNNING.value, CameraState.RUNNING, "running"):
            return StreamStatus.ONLINE
        if cam_state in (CameraState.STARTING.value, CameraState.RECONNECTING.value, "starting", "reconnecting"):
            return StreamStatus.RECONNECTING
        return StreamStatus.OFFLINE

    def measured_fps(self) -> float:
        mgr = CentralCameraManager.get_instance()
        st = mgr.get_camera_status(self.camera_id)
        return float(st.get("frame_fps", 0.0))

    def latest(self) -> Frame | None:
        mgr = CentralCameraManager.get_instance()
        dev = mgr._get_or_create_device(self.camera_id)
        with dev._lock:
            if dev.last_frame is None:
                return None
            if self._last_frame is None or self._last_frame.captured_at != dev.last_frame_at:
                self._frame_index += 1
                self._last_frame = Frame(
                    image=dev.last_frame,
                    captured_at=dev.last_frame_at,
                    monotonic=time.monotonic(),
                    frame_index=self._frame_index,
                )
            return self._last_frame

