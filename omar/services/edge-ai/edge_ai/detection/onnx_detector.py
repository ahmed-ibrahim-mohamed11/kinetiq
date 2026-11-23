"""
Generic ONNX Runtime person detector adapter.

Supports the common "YOLO-style" export layout: input NCHW float32 [1,3,H,W] in
0..1, output [1, N, 4+C] or [1, 4+C, N] with cx,cy,w,h + class scores. This
covers YOLOv5/8/11 and RT-DETR exports after their standard post-processing is
baked in or applied here. The model file is NOT bundled: Phase 2 benchmarks
decide which weights to ship, and docs/architecture/model-selection.md records
license and measured latency for each candidate.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import numpy.typing as npt

from .base import Detection


def letterbox(image: Any, size: tuple[int, int]) -> tuple[Any, float, tuple[float, float]]:
    """Resize with unchanged aspect ratio and pad to `size` (w, h). Returns (img, scale, (pad_x, pad_y))."""
    import cv2

    h, w = image.shape[:2]
    tw, th = size
    scale = min(tw / w, th / h)
    nw, nh = int(round(w * scale)), int(round(h * scale))
    resized = cv2.resize(image, (nw, nh), interpolation=cv2.INTER_LINEAR)
    canvas = np.full((th, tw, 3), 114, dtype=np.uint8)
    px, py = (tw - nw) / 2, (th - nh) / 2
    canvas[int(py) : int(py) + nh, int(px) : int(px) + nw] = resized
    return canvas, scale, (px, py)


def nms(boxes: npt.NDArray[Any], scores: npt.NDArray[Any], iou_thresh: float) -> list[int]:
    """Pure-numpy NMS. boxes: [N,4] xyxy."""
    if boxes.size == 0:
        return []
    x1, y1, x2, y2 = boxes[:, 0], boxes[:, 1], boxes[:, 2], boxes[:, 3]
    areas = np.maximum(0, x2 - x1) * np.maximum(0, y2 - y1)
    order = scores.argsort()[::-1]
    keep: list[int] = []
    while order.size > 0:
        i = int(order[0])
        keep.append(i)
        if order.size == 1:
            break
        rest = order[1:]
        xx1 = np.maximum(x1[i], x1[rest])
        yy1 = np.maximum(y1[i], y1[rest])
        xx2 = np.minimum(x2[i], x2[rest])
        yy2 = np.minimum(y2[i], y2[rest])
        inter = np.maximum(0, xx2 - xx1) * np.maximum(0, yy2 - yy1)
        iou = inter / (areas[i] + areas[rest] - inter + 1e-9)
        order = rest[iou <= iou_thresh]
    return keep


class OnnxPersonDetector:
    def __init__(
        self,
        model_path: str,
        input_size: tuple[int, int] = (640, 640),
        conf_threshold: float = 0.35,
        iou_threshold: float = 0.5,
        person_class_id: int = 0,
        providers: list[str] | None = None,
    ) -> None:
        import onnxruntime as ort  # lazy

        self.model_name = model_path.rsplit("/", 1)[-1]
        self.input_size = input_size
        self.conf_threshold = conf_threshold
        self.iou_threshold = iou_threshold
        self.person_class_id = person_class_id
        providers = providers or ["CUDAExecutionProvider", "CPUExecutionProvider"]
        self.session = ort.InferenceSession(model_path, providers=providers)
        self.input_name = self.session.get_inputs()[0].name
        self.active_provider = self.session.get_providers()[0]

    def detect(self, image: Any) -> list[Detection]:
        img, scale, (px, py) = letterbox(image, self.input_size)
        blob = img[:, :, ::-1].astype(np.float32) / 255.0  # BGR→RGB
        blob = np.transpose(blob, (2, 0, 1))[None]
        out = self.session.run(None, {self.input_name: blob})[0]
        return self.postprocess(out, scale, (px, py), image.shape[1], image.shape[0])

    def postprocess(
        self, out: npt.NDArray[Any], scale: float, pad: tuple[float, float], img_w: int, img_h: int
    ) -> list[Detection]:
        pred = np.squeeze(out, axis=0)
        if pred.ndim != 2:
            return []
        # Accept [N, 4+C] or [4+C, N]. Channel counts are small (5..~128); N is the anchor count.
        rows, cols = pred.shape
        channels_first = (rows <= 128 and cols > rows) or (rows in (5, 6, 84, 85) and cols not in (5, 6, 84, 85))
        if channels_first:
            pred = pred.T
        boxes_cxcywh = pred[:, :4]
        cls_scores = pred[:, 4:]
        if cls_scores.shape[1] <= self.person_class_id:
            return []
        scores = cls_scores[:, self.person_class_id]
        mask = scores >= self.conf_threshold
        if not np.any(mask):
            return []
        b = boxes_cxcywh[mask]
        s = scores[mask]
        xyxy = np.stack(
            [b[:, 0] - b[:, 2] / 2, b[:, 1] - b[:, 3] / 2, b[:, 0] + b[:, 2] / 2, b[:, 1] + b[:, 3] / 2], axis=1
        )
        # undo letterbox
        px, py = pad
        xyxy[:, [0, 2]] = (xyxy[:, [0, 2]] - px) / scale
        xyxy[:, [1, 3]] = (xyxy[:, [1, 3]] - py) / scale
        xyxy[:, [0, 2]] = np.clip(xyxy[:, [0, 2]], 0, img_w)
        xyxy[:, [1, 3]] = np.clip(xyxy[:, [1, 3]], 0, img_h)
        keep = nms(xyxy, s, self.iou_threshold)
        return [Detection(tuple(map(float, xyxy[i])), float(s[i]), self.person_class_id) for i in keep]  # type: ignore[arg-type]
