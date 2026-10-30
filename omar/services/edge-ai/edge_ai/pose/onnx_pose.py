"""
Bottom-up pose adapter for YOLO-pose style ONNX exports.

Output layout: [1, 56, N] or [1, N, 56] = cx, cy, w, h, person_conf, then 17 × (x, y, score)
in letterboxed input coordinates. The adapter runs once per frame, undoes the letterbox,
applies NMS, and assigns each requested track box the pose whose person box overlaps it
most (IoU ≥ `match_iou`). Unmatched boxes get None so the rep counter holds its state.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from edge_ai.detection.onnx_detector import letterbox, nms
from edge_ai.geometry import iou
from edge_ai.inference.session import OnnxSession

from .base import Keypoint, Pose


class YoloPoseOnnx:
    def __init__(
        self,
        model_path: str,
        input_size: tuple[int, int] = (640, 640),
        conf_threshold: float = 0.4,
        iou_threshold: float = 0.5,
        match_iou: float = 0.3,
        providers: list[str] | None = None,
    ) -> None:
        self._s = OnnxSession(model_path, providers)
        self.model_name = self._s.model_name
        self.input_size = input_size
        self.conf_threshold = conf_threshold
        self.iou_threshold = iou_threshold
        self.match_iou = match_iou

    def estimate(self, image: Any, boxes: list[tuple[float, float, float, float]]) -> list[Pose | None]:
        if not boxes:
            return []
        img, scale, (px, py) = letterbox(image, self.input_size)
        blob = np.ascontiguousarray(np.transpose(img[:, :, ::-1].astype(np.float32) / 255.0, (2, 0, 1))[None])
        out = self._s.run(blob)[0]
        persons = self.postprocess(out, scale, (px, py), image.shape[1], image.shape[0])
        result: list[Pose | None] = []
        for box in boxes:
            best, best_iou = None, 0.0
            for pbox, pose in persons:
                v = iou(box, pbox)
                if v > best_iou:
                    best, best_iou = pose, v
            result.append(best if best_iou >= self.match_iou else None)
        return result

    def postprocess(
        self, out: Any, scale: float, pad: tuple[float, float], img_w: int, img_h: int
    ) -> list[tuple[tuple[float, float, float, float], Pose]]:
        pred = np.squeeze(np.asarray(out), axis=0)
        if pred.ndim != 2:
            return []
        if pred.shape[0] == 56 and pred.shape[1] != 56:
            pred = pred.T
        if pred.shape[1] < 56:
            return []
        conf = pred[:, 4]
        mask = conf >= self.conf_threshold
        if not np.any(mask):
            return []
        p = pred[mask]
        b = p[:, :4]
        xyxy = np.stack(
            [b[:, 0] - b[:, 2] / 2, b[:, 1] - b[:, 3] / 2, b[:, 0] + b[:, 2] / 2, b[:, 1] + b[:, 3] / 2], axis=1
        )
        keep = nms(xyxy, p[:, 4], self.iou_threshold)
        px, py = pad
        persons: list[tuple[tuple[float, float, float, float], Pose]] = []
        for i in keep:
            bx = xyxy[i].copy()
            bx[[0, 2]] = np.clip((bx[[0, 2]] - px) / scale, 0, img_w)
            bx[[1, 3]] = np.clip((bx[[1, 3]] - py) / scale, 0, img_h)
            kps = p[i, 5 : 5 + 51].reshape(17, 3)
            keypoints = tuple(
                Keypoint(float((k[0] - px) / scale), float((k[1] - py) / scale), float(max(0.0, min(1.0, k[2]))))
                for k in kps
            )
            persons.append(((float(bx[0]), float(bx[1]), float(bx[2]), float(bx[3])), Pose(keypoints)))
        return persons
