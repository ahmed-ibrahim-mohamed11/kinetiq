"""
Generic ONNX Runtime person detector adapter.

Two output layouts, chosen with `layout`:

* ``auto`` (default, unchanged behaviour): the common "YOLO-style" export. Input NCHW
  float32 [1,3,H,W] RGB in 0..1, centred letterbox, output [1, N, 4+C] or [1, 4+C, N] with
  cx,cy,w,h + class scores in input pixels. Covers YOLOv5/8/11 and RT-DETR exports whose
  decode is baked into the graph.
* ``yolox``: the official Megvii YOLOX ONNX exports (yolox_nano/tiny/s/m/l/x.onnx, Apache-2.0).
  Input [1,3,H,W] **BGR 0..255 without normalisation**, image resized with unchanged aspect
  ratio and pasted at the **top-left** of a canvas filled with 114 (YOLOX ``preproc``). Output
  [1, N, 5+C] RAW: per anchor point (x, y, log w, log h, objectness, class scores) where objectness
  and class scores are already sigmoid-activated. Decode with strides 8/16/32:
  ``xy = (out_xy + grid) * stride``, ``wh = exp(out_wh) * stride``; ``score = obj * cls``.

The model file is NOT bundled: benchmarks decide which weights to ship, and
docs/architecture/model-selection.md records license and measured latency for each candidate.
"""

from __future__ import annotations

import os
from typing import Any, Literal

import numpy as np
import numpy.typing as npt

from .base import Detection

DetectorLayout = Literal["auto", "yolox"]
DETECTOR_LAYOUTS: tuple[str, ...] = ("auto", "yolox")
YOLOX_STRIDES: tuple[int, ...] = (8, 16, 32)


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


def letterbox_top_left(image: Any, size: tuple[int, int]) -> tuple[Any, float]:
    """YOLOX ``preproc``: resize with unchanged aspect ratio, paste at the top-left of a 114-filled (w, h) canvas."""
    import cv2

    h, w = image.shape[:2]
    tw, th = size
    scale = min(tw / w, th / h)
    nw, nh = min(tw, int(w * scale)), min(th, int(h * scale))
    resized = cv2.resize(image, (nw, nh), interpolation=cv2.INTER_LINEAR)
    canvas = np.full((th, tw, 3), 114, dtype=np.uint8)
    canvas[:nh, :nw] = resized
    return canvas, scale


def yolox_grids(input_size: tuple[int, int], strides: tuple[int, ...] = YOLOX_STRIDES) -> tuple[Any, Any]:
    """Anchor-point grid (x, y) and per-point stride, in the order YOLOX concatenates its head outputs."""
    tw, th = input_size
    grids: list[npt.NDArray[Any]] = []
    expanded: list[npt.NDArray[Any]] = []
    for stride in strides:
        hs, ws = th // stride, tw // stride
        xv, yv = np.meshgrid(np.arange(ws), np.arange(hs))
        grid = np.stack((xv, yv), axis=2).reshape(-1, 2)
        grids.append(grid)
        expanded.append(np.full((grid.shape[0], 1), stride))
    return np.concatenate(grids, 0).astype(np.float32), np.concatenate(expanded, 0).astype(np.float32)


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


def static_input_size(shape: tuple[Any, ...]) -> tuple[int, int] | None:
    """(w, h) from a static NCHW input shape, None when H or W is dynamic."""
    if len(shape) == 4 and isinstance(shape[2], int) and isinstance(shape[3], int) and shape[2] > 0 and shape[3] > 0:
        return shape[3], shape[2]
    return None


class OnnxPersonDetector:
    def __init__(
        self,
        model_path: str,
        input_size: tuple[int, int] | None = None,
        conf_threshold: float = 0.30,
        iou_threshold: float = 0.5,
        person_class_id: int = 0,
        providers: list[str] | None = None,
        layout: DetectorLayout = "auto",
    ) -> None:
        """input_size (w, h): None = the model's static input size, else 640×640."""
        import onnxruntime as ort  # lazy

        if layout not in DETECTOR_LAYOUTS:
            raise ValueError(f"unknown detector layout {layout!r}; expected one of {DETECTOR_LAYOUTS}")
        self.layout: DetectorLayout = layout
        self.model_name = str(model_path).replace("\\", "/").rsplit("/", 1)[-1]
        self.conf_threshold = conf_threshold
        self.iou_threshold = iou_threshold
        self.person_class_id = person_class_id
        providers = providers or ["CUDAExecutionProvider", "CPUExecutionProvider"]
        available = ort.get_available_providers()
        chosen = [p for p in providers if p in available] or ["CPUExecutionProvider"]
        so = ort.SessionOptions()
        so.log_severity_level = 3
        so.intra_op_num_threads = 2
        so.inter_op_num_threads = 1
        so.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        self.session = ort.InferenceSession(str(model_path), sess_options=so, providers=chosen)
        self.input_name = self.session.get_inputs()[0].name
        self.active_provider = self.session.get_providers()[0]
        self.input_size = input_size or static_input_size(tuple(self.session.get_inputs()[0].shape)) or (640, 640)
        self._grids: tuple[Any, Any] | None = None

    @property
    def providers(self) -> list[str]:
        return list(self.session.get_providers())

    def detect(self, image: Any) -> list[Detection]:
        if self.layout == "yolox":
            img, scale = letterbox_top_left(image, self.input_size)
            blob = np.ascontiguousarray(np.transpose(img.astype(np.float32), (2, 0, 1))[None])  # BGR, 0..255
            out = self.session.run(None, {self.input_name: blob})[0]
            return self.postprocess_yolox(out, scale, image.shape[1], image.shape[0])
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
        return self._finish(boxes_cxcywh, scores, scale, pad, img_w, img_h)

    def postprocess_yolox(self, out: npt.NDArray[Any], scale: float, img_w: int, img_h: int) -> list[Detection]:
        """Grid-decode a raw YOLOX head output [1, N, 5+C] (input-pixel space, top-left padding)."""
        pred = np.asarray(out, dtype=np.float32)
        if pred.ndim == 3:
            pred = pred[0]
        if pred.ndim != 2 or pred.shape[1] < 6 + self.person_class_id:
            raise ValueError(f"YOLOX output must be [1, N, 5+C], got {tuple(np.shape(out))}")
        if self._grids is None:
            self._grids = yolox_grids(self.input_size)
        grids, strides = self._grids
        if pred.shape[0] != grids.shape[0]:
            raise ValueError(
                f"YOLOX output has {pred.shape[0]} anchor points but input {self.input_size[0]}x{self.input_size[1]} "
                f"implies {grids.shape[0]}: wrong input size, or the model was exported with decode_in_inference"
            )
        xy = (pred[:, :2] + grids) * strides
        wh = np.exp(np.minimum(pred[:, 2:4], 50.0)) * strides  # clamp avoids overflow on garbage logits
        scores = pred[:, 4] * pred[:, 5 + self.person_class_id]
        return self._finish(np.concatenate([xy, wh], axis=1), scores, scale, (0.0, 0.0), img_w, img_h)

    def _finish(
        self,
        boxes_cxcywh: npt.NDArray[Any],
        scores: npt.NDArray[Any],
        scale: float,
        pad: tuple[float, float],
        img_w: int,
        img_h: int,
    ) -> list[Detection]:
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


def create_detector(
    model_path: str,
    layout: str | None = None,
    providers: list[str] | None = None,
    **kwargs: Any,
) -> OnnxPersonDetector:
    """Build the detector; layout None = $EDGE_DETECTOR_LAYOUT or 'auto'."""
    chosen = (layout or os.environ.get("EDGE_DETECTOR_LAYOUT") or "auto").strip().lower()
    if chosen not in DETECTOR_LAYOUTS:
        raise ValueError(f"unknown detector layout {chosen!r}; expected one of {DETECTOR_LAYOUTS}")
    return OnnxPersonDetector(model_path, providers=providers, layout=chosen, **kwargs)  # type: ignore[arg-type]
