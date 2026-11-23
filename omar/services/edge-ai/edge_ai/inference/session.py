from __future__ import annotations

from typing import Any

import numpy as np
import numpy.typing as npt

IMAGENET_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
IMAGENET_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)


class OnnxSession:
    """Thin wrapper: provider selection (CUDA → CPU), input/output names, run()."""

    def __init__(self, model_path: str, providers: list[str] | None = None) -> None:
        import onnxruntime as ort  # lazy

        self.model_path = model_path
        self.model_name = str(model_path).replace("\\", "/").rsplit("/", 1)[-1]
        available = ort.get_available_providers()
        wanted = providers or [
            "CUDAExecutionProvider",
            "TensorrtExecutionProvider",
            "DmlExecutionProvider",
            "CPUExecutionProvider",
        ]
        chosen = [p for p in wanted if p in available] or ["CPUExecutionProvider"]
        so = ort.SessionOptions()
        so.log_severity_level = 3
        # Limit ONNX Runtime threads to prevent CPU thread starvation and high CPU load
        import os
        cpu_threads = min(2, max(1, (os.cpu_count() or 4) // 2))
        so.intra_op_num_threads = cpu_threads
        so.inter_op_num_threads = 1
        so.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        self.session = ort.InferenceSession(model_path, sess_options=so, providers=chosen)
        self.input_name = self.session.get_inputs()[0].name
        self.input_shape = tuple(self.session.get_inputs()[0].shape)
        self.output_names = [o.name for o in self.session.get_outputs()]
        self.active_provider = self.session.get_providers()[0]

    def run(self, blob: npt.NDArray[np.float32]) -> list[npt.NDArray[Any]]:
        return list(self.session.run(None, {self.input_name: blob}))


def preprocess_crop(
    image: Any,
    box: tuple[float, float, float, float],
    size: tuple[int, int],
    *,
    normalize: str = "imagenet",
    pad_ratio: float = 0.0,
) -> npt.NDArray[np.float32] | None:
    """Crop `box` (xyxy) from a BGR image, resize to (w, h), RGB, NCHW float32.

    normalize: 'imagenet' → (x/255 - mean)/std ; 'arcface' → (x - 127.5)/128 ; 'unit' → x/255.
    Returns None when the box has no area inside the image.
    """
    import cv2

    h_img, w_img = image.shape[:2]
    x1, y1, x2, y2 = box
    if pad_ratio > 0:
        pw, ph = (x2 - x1) * pad_ratio, (y2 - y1) * pad_ratio
        x1, y1, x2, y2 = x1 - pw, y1 - ph, x2 + pw, y2 + ph
    xi1, yi1 = max(0, int(x1)), max(0, int(y1))
    xi2, yi2 = min(w_img, int(x2)), min(h_img, int(y2))
    if xi2 - xi1 < 2 or yi2 - yi1 < 2:
        return None
    crop = image[yi1:yi2, xi1:xi2]
    crop = cv2.resize(crop, size, interpolation=cv2.INTER_LINEAR)
    rgb = crop[:, :, ::-1].astype(np.float32)
    if normalize == "imagenet":
        rgb = (rgb / 255.0 - IMAGENET_MEAN) / IMAGENET_STD
    elif normalize == "arcface":
        rgb = (rgb - 127.5) / 128.0
    else:
        rgb = rgb / 255.0
    return np.ascontiguousarray(np.transpose(rgb, (2, 0, 1))[None]).astype(np.float32)


def l2_normalize(vec: npt.NDArray[Any]) -> list[float]:
    v = np.asarray(vec, dtype=np.float32).reshape(-1)
    n = float(np.linalg.norm(v))
    return (v / n).tolist() if n > 0 else v.tolist()
