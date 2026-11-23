from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class Detection:
    """A person detection in image pixels: [x1, y1, x2, y2] and a score in [0, 1]."""

    box: tuple[float, float, float, float]
    score: float
    class_id: int = 0  # 0 = person for COCO-trained detectors

    @property
    def width(self) -> float:
        return self.box[2] - self.box[0]

    @property
    def height(self) -> float:
        return self.box[3] - self.box[1]

    @property
    def area(self) -> float:
        return max(0.0, self.width) * max(0.0, self.height)


class Detector(Protocol):
    """Pluggable detector. Implementations: OnnxPersonDetector, (later) TensorRT/DeepStream."""

    model_name: str

    def detect(self, image: Any) -> list[Detection]: ...
