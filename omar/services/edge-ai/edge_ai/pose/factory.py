"""
Pick the pose adapter by configuration.

kind:
* ``yolo_pose`` — bottom-up YoloPoseOnnx (one pass per frame, poses matched to boxes by IoU);
* ``rtmpose``   — top-down RtmPoseOnnx (one crop per box, SimCC outputs);
* ``auto``      — inspect the model outputs: two [1, 17, W] SimCC heads → rtmpose, else yolo_pose.

None = ``$EDGE_POSE_KIND`` or ``auto``. The ONNX session is opened once and handed to the adapter.
"""

from __future__ import annotations

import os
from typing import Any

from edge_ai.inference.session import OnnxSession

from .base import PoseEstimator
from .onnx_pose import YoloPoseOnnx
from .rtmpose import RtmPoseOnnx

POSE_KINDS: tuple[str, ...] = ("auto", "yolo_pose", "rtmpose")


def detect_pose_kind(session: OnnxSession) -> str:
    """'rtmpose' when the model has SimCC x/y heads, otherwise 'yolo_pose'."""
    names = session.output_names
    if "simcc_x" in names and "simcc_y" in names:
        return "rtmpose"
    outs = session.session.get_outputs()
    if len(outs) == 2 and all(len(o.shape) == 3 and o.shape[1] == 17 for o in outs):
        return "rtmpose"
    return "yolo_pose"


def create_pose_estimator(
    model_path: str,
    kind: str | None = None,
    providers: list[str] | None = None,
    **kwargs: Any,
) -> PoseEstimator:
    chosen = (kind or os.environ.get("EDGE_POSE_KIND") or "auto").strip().lower().replace("-", "_")
    if chosen not in POSE_KINDS:
        raise ValueError(f"unknown pose kind {chosen!r}; expected one of {POSE_KINDS}")
    session = OnnxSession(model_path, providers)
    if chosen == "auto":
        chosen = detect_pose_kind(session)
    if chosen == "rtmpose":
        return RtmPoseOnnx(model_path, session=session, **kwargs)
    return YoloPoseOnnx(model_path, session=session, **kwargs)
