"""
Top-down RTMPose adapter (OpenMMLab RTMPose, Apache-2.0) for the SimCC ONNX exports.

Reference model: rtmpose-m body7 256x192 from the OpenMMLab ONNX SDK zip
(end2end.onnx: input ``input`` 1x3x256x192, outputs ``simcc_x`` 1x17x384 and ``simcc_y`` 1x17x512).

Per requested box (normally a tracker box from the person detector):
1. centre/scale from the box, scale × ``padding`` (1.25), then widened or heightened to the
   model aspect ratio (w/h = 192/256) so the person is not distorted;
2. affine warp of that region to the model input (border value 0), BGR→RGB,
   ``(x - mean) / std`` with the ImageNet statistics on the 0..255 scale;
3. SimCC decode: ``x = argmax(simcc_x) / split_ratio``, ``y = argmax(simcc_y) / split_ratio``,
   score = the smaller of the two maxima (as mmpose / rtmlib ``get_simcc_maximum``: a keypoint is
   only as confident as its least confident axis), clipped to [0, 1];
4. map back to frame pixels: ``kp / input_size * scale + centre - scale / 2``.

COCO-17 keypoint order, so edge_ai/pose/angles.py works unchanged.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import numpy.typing as npt

from edge_ai.detection.onnx_detector import static_input_size
from edge_ai.inference.session import OnnxSession

from .base import Keypoint, Pose

RTMPOSE_MEAN = np.array([123.675, 116.28, 103.53], dtype=np.float32)
RTMPOSE_STD = np.array([58.395, 57.12, 57.375], dtype=np.float32)

Box = tuple[float, float, float, float]


def box_to_center_scale(box: Box, input_size: tuple[int, int], padding: float = 1.25) -> tuple[Any, Any]:
    """xyxy box → (centre [2], scale [2]) padded and fixed to the model aspect ratio (input_size = (w, h))."""
    x1, y1, x2, y2 = box
    center = np.array([(x1 + x2) / 2.0, (y1 + y2) / 2.0], dtype=np.float32)
    w, h = (x2 - x1) * padding, (y2 - y1) * padding
    aspect = input_size[0] / input_size[1]
    if w > h * aspect:
        h = w / aspect
    else:
        w = h * aspect
    return center, np.array([w, h], dtype=np.float32)


def crop_warp_matrix(center: Any, scale: Any, input_size: tuple[int, int]) -> npt.NDArray[np.float32]:
    """2x3 affine matrix mapping the (centre, scale) region of the frame onto the model input."""
    sx = input_size[0] / float(scale[0])
    sy = input_size[1] / float(scale[1])
    ox = float(center[0]) - float(scale[0]) / 2.0
    oy = float(center[1]) - float(scale[1]) / 2.0
    return np.array([[sx, 0.0, -ox * sx], [0.0, sy, -oy * sy]], dtype=np.float32)


def decode_simcc(
    simcc_x: npt.NDArray[Any], simcc_y: npt.NDArray[Any], split_ratio: float = 2.0
) -> tuple[npt.NDArray[np.float32], npt.NDArray[np.float32]]:
    """[K, Wx], [K, Wy] → (locations [K, 2] in model-input pixels, scores [K])."""
    sx = np.asarray(simcc_x, dtype=np.float32).reshape(-1, np.shape(simcc_x)[-1])
    sy = np.asarray(simcc_y, dtype=np.float32).reshape(-1, np.shape(simcc_y)[-1])
    locs = np.stack([np.argmax(sx, axis=1), np.argmax(sy, axis=1)], axis=-1).astype(np.float32) / split_ratio
    scores = np.minimum(np.amax(sx, axis=1), np.amax(sy, axis=1)).astype(np.float32)
    return locs, scores


class RtmPoseOnnx:
    """Top-down pose: one crop per requested box → one Pose (or None when the box has no area)."""

    def __init__(
        self,
        model_path: str,
        input_size: tuple[int, int] | None = None,
        padding: float = 1.25,
        split_ratio: float = 2.0,
        providers: list[str] | None = None,
        session: OnnxSession | None = None,
    ) -> None:
        """input_size (w, h): None = the model's static input size, else 192×256."""
        self._s = session or OnnxSession(model_path, providers)
        self.model_name = self._s.model_name
        self.input_size = input_size or static_input_size(self._s.input_shape) or (192, 256)
        self.padding = padding
        self.split_ratio = split_ratio
        names = self._s.output_names
        self._ix = names.index("simcc_x") if "simcc_x" in names else 0
        self._iy = names.index("simcc_y") if "simcc_y" in names else 1

    @property
    def providers(self) -> list[str]:
        return list(self._s.session.get_providers())

    def preprocess(self, image: Any, box: Box) -> tuple[npt.NDArray[np.float32], Any, Any] | None:
        import cv2

        h_img, w_img = image.shape[:2]
        x1, y1, x2, y2 = max(0.0, box[0]), max(0.0, box[1]), min(float(w_img), box[2]), min(float(h_img), box[3])
        if x2 - x1 < 2 or y2 - y1 < 2:  # no area inside the frame
            return None
        center, scale = box_to_center_scale(box, self.input_size, self.padding)
        m = crop_warp_matrix(center, scale, self.input_size)
        crop = cv2.warpAffine(image, m, self.input_size, flags=cv2.INTER_LINEAR, borderValue=(0, 0, 0))
        rgb = crop[:, :, ::-1].astype(np.float32)
        blob = np.ascontiguousarray(np.transpose((rgb - RTMPOSE_MEAN) / RTMPOSE_STD, (2, 0, 1))[None])
        return blob.astype(np.float32), center, scale

    def postprocess(self, outputs: list[npt.NDArray[Any]], center: Any, scale: Any) -> Pose:
        simcc_x = np.asarray(outputs[self._ix])
        simcc_y = np.asarray(outputs[self._iy])
        if simcc_x.ndim == 3:  # [1, K, W] → [K, W]
            simcc_x, simcc_y = simcc_x[0], simcc_y[0]
        locs, scores = decode_simcc(simcc_x, simcc_y, self.split_ratio)
        size = np.array(self.input_size, dtype=np.float32)
        frame_xy = locs / size * scale + center - scale / 2.0
        return Pose(
            tuple(
                Keypoint(float(x), float(y), float(max(0.0, min(1.0, s))))
                for (x, y), s in zip(frame_xy, scores, strict=True)
            )
        )

    def estimate(self, image: Any, boxes: list[Box]) -> list[Pose | None]:
        result: list[Pose | None] = []
        for box in boxes:
            prep = self.preprocess(image, box)
            if prep is None:
                result.append(None)
                continue
            blob, center, scale = prep
            result.append(self.postprocess(self._s.run(blob), center, scale))
        return result
