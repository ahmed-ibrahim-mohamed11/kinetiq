"""Real-Time Multi-Person Squat Assessment Session (Backed by SmartGymPlatformHub).

Replaces the legacy global-state / pixel-masking / fabricated-hip implementation with:
1. Top-Down Multi-Person Detection & Pose Estimation (`YOLOX + RTMPose-m` ONNX, fallback `YOLO11n-pose`).
2. ByteTrack Multi-Object Tracking (`IouTracker`) with isolated per-person `PersonTrackState`.
3. Explicit 9-State Biomechanical Squat State Machine (`BiomechanicalSquatStateMachine`) that:
   - Never fabricates missing hip/knee coordinates from shoulder coordinates.
   - Displays `INSUFFICIENT POSE VISIBILITY — COUNT PAUSED` when landmarks are insufficient.
   - Separates valid reps (`RIGHT`), imperfect reps (`WRONG`), partial reps, and rejected movements.
4. Multi-Camera Global Identity Fusion & Idempotent Event Persistence (`SmartGymPlatformHub`).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import os
import time
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np

from ai.core.smart_gym_engine import (
    SKELETON_EDGES,
    SmartGymPlatformHub,
    VisibilityStatus,
)

BODY_EDGES = list(SKELETON_EDGES)


@dataclass
class LiveRepRecord:
    rep_number: int
    verdict: str                    # "correct" | "incorrect"
    confidence: float
    primary_error: Optional[str]
    detected_errors: List[str]
    explanation: str
    min_knee_angle_deg: float
    depth_pct: float
    min_valgus_ratio: float
    max_trunk_lean_deg: float
    duration_s: float
    model_probabilities: Dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class LiveSquatSession:
    """Backward-compatible wrapper around `SmartGymPlatformHub` with per-person state isolation."""

    def __init__(
        self,
        device: str = "cpu",
        camera_id: str = "cam-a",
        hub: Optional[SmartGymPlatformHub] = None,
    ) -> None:
        self.device = device
        self.camera_id = camera_id
        self.hub = hub or SmartGymPlatformHub()
        self.last_telemetry: Dict[str, Any] = self.hub.get_camera_telemetry(camera_id)

    def reset(self, camera_id: Optional[str] = None) -> None:
        self.hub.reset_camera(camera_id or self.camera_id)
        self.last_telemetry = self.hub.get_camera_telemetry(camera_id or self.camera_id)

    def reset_counts(self, camera_id: Optional[str] = None) -> None:
        self.reset(camera_id)

    def lock_person_at(
        self,
        x_norm: float,
        y_norm: float,
        camera_id: Optional[str] = None,
    ) -> Optional[int]:
        cid = camera_id or self.camera_id
        cam = self.hub.cameras.get(cid, self.hub.cameras["cam-a"])
        H, W = (cam.last_frame_bgr.shape[:2]) if cam.last_frame_bgr is not None else (360, 480)
        return cam.select_target(
            mode="MANUAL",
            click_xy_norm=(float(x_norm), float(y_norm)),
            W=W,
            H=H,
        )

    def process_frame(
        self,
        frame_bgr: np.ndarray,
        camera_id: Optional[str] = None,
        timestamp: Optional[float] = None,
    ) -> Tuple[np.ndarray, Dict[str, Any]]:
        cid = camera_id or self.camera_id
        tel = self.hub.process_camera_frame(
            frame_bgr=frame_bgr,
            camera_id=cid,
            timestamp=timestamp,
        )
        self.last_telemetry = tel
        cam = self.hub.cameras[cid]
        annotated = cam.last_annotated_bgr if cam.last_annotated_bgr is not None else frame_bgr
        return annotated, tel

    def get_telemetry(self, camera_id: Optional[str] = None) -> Dict[str, Any]:
        return self.hub.get_camera_telemetry(camera_id or self.camera_id)

    def save_report_json(self, out_dir: Optional[str] = None) -> str:
        out_dir = out_dir or os.path.join(os.path.dirname(__file__), "..", "..", "outputs", "live_reports")
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, f"smart_gym_report_{int(time.time())}.json")
        payload = {
            "generated_at": time.time(),
            "diagnostics": self.hub.get_diagnostics(),
            "analytics": self.hub.get_analytics(),
            "cameras": {
                cid: self.hub.get_camera_telemetry(cid)
                for cid in self.hub.cameras.keys()
            },
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        return os.path.abspath(path)
