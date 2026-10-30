"""
Turn a Pose into the scalar signal a rep profile tracks (e.g. mean elbow angle).

Returns (value, confidence). confidence = min keypoint score among the joints
used; NaN value when the joints are not visible so the state machine can hold
its state instead of guessing.
"""

from __future__ import annotations

import math
from collections.abc import Callable

from edge_ai.geometry import joint_angle

from .base import Pose

Signal = tuple[float, float]


def _angle(pose: Pose, a: str, b: str, c: str, min_score: float) -> Signal:
    ka, kb, kc = pose.kp(a), pose.kp(b), pose.kp(c)
    conf = min(ka.score, kb.score, kc.score)
    if conf < min_score:
        return math.nan, conf
    return joint_angle((ka.x, ka.y), (kb.x, kb.y), (kc.x, kc.y)), conf


def _mean_sided(pose: Pose, joints: tuple[str, str, str], min_score: float) -> Signal:
    """Average left/right angles; fall back to whichever side is visible."""
    a, b, c = joints
    left = _angle(pose, f"left_{a}", f"left_{b}", f"left_{c}", min_score)
    right = _angle(pose, f"right_{a}", f"right_{b}", f"right_{c}", min_score)
    vals = [v for v in (left, right) if not math.isnan(v[0])]
    if not vals:
        return math.nan, max(left[1], right[1])
    return sum(v for v, _ in vals) / len(vals), min(c for _, c in vals)


def elbow_angle_mean(pose: Pose, min_score: float = 0.3) -> Signal:
    return _mean_sided(pose, ("shoulder", "elbow", "wrist"), min_score)


def knee_angle_mean(pose: Pose, min_score: float = 0.3) -> Signal:
    return _mean_sided(pose, ("hip", "knee", "ankle"), min_score)


def hip_angle_mean(pose: Pose, min_score: float = 0.3) -> Signal:
    return _mean_sided(pose, ("shoulder", "hip", "knee"), min_score)


SIGNAL_EXTRACTORS: dict[str, Callable[[Pose], Signal]] = {
    "elbow_angle_mean": elbow_angle_mean,
    "knee_angle_mean": knee_angle_mean,
    "hip_angle_mean": hip_angle_mean,
}


def extract_signal(name: str, pose: Pose) -> Signal:
    fn = SIGNAL_EXTRACTORS.get(name)
    if fn is None:
        return math.nan, 0.0
    return fn(pose)
