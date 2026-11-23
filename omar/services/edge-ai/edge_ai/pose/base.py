from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

# COCO-17 keypoint order used by most pose models (RTMPose, YOLO-pose, MoveNet w/ remap).
COCO_KEYPOINTS: tuple[str, ...] = (
    "nose",
    "left_eye",
    "right_eye",
    "left_ear",
    "right_ear",
    "left_shoulder",
    "right_shoulder",
    "left_elbow",
    "right_elbow",
    "left_wrist",
    "right_wrist",
    "left_hip",
    "right_hip",
    "left_knee",
    "right_knee",
    "left_ankle",
    "right_ankle",
)
KP = {name: i for i, name in enumerate(COCO_KEYPOINTS)}


@dataclass(frozen=True)
class Keypoint:
    x: float
    y: float
    score: float


@dataclass(frozen=True)
class Pose:
    """17 COCO keypoints in image pixels for one person crop."""

    keypoints: tuple[Keypoint, ...]

    def kp(self, name: str) -> Keypoint:
        return self.keypoints[KP[name]]

    def visible(self, name: str, min_score: float = 0.3) -> bool:
        return self.kp(name).score >= min_score


class PoseEstimator(Protocol):
    model_name: str

    def estimate(self, image: Any, boxes: list[tuple[float, float, float, float]]) -> list[Pose | None]: ...
