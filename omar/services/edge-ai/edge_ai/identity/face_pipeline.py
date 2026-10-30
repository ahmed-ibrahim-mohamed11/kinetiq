"""
Face detection + embedding for enrollment and entrance identification.

* `HaarFaceDetector` — OpenCV's bundled frontal-face cascade. No download, no
  license issue, adequate for cooperative enrollment photos and a frontal
  entrance camera at short range. A stronger ONNX face detector (e.g. SCRFD)
  can replace it behind the same `FaceDetector` protocol after Phase 3 benchmarks.
* `OnnxFaceEmbedder` — ArcFace-style ONNX: input [1,3,112,112] normalised
  (x-127.5)/128, output [1,512], L2-normalised.
* `NoLivenessDetector` — explicit placeholder: returns None so events carry
  `presentation_attack_score: null` instead of a fabricated score.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from edge_ai.inference.session import OnnxSession, l2_normalize, preprocess_crop


@dataclass(frozen=True)
class FaceBox:
    box: tuple[float, float, float, float]
    score: float


class FaceDetector(Protocol):
    def detect(self, image: Any) -> list[FaceBox]: ...


class LivenessDetector(Protocol):
    def score(self, image: Any, face: FaceBox) -> float | None: ...


class NoLivenessDetector:
    """No PAD model selected yet (Phase 3 gate). Always None — never a guessed value."""

    def score(self, image: Any, face: FaceBox) -> float | None:
        return None


class HaarFaceDetector:
    def __init__(self, min_size_px: int = 60, scale_factor: float = 1.1, min_neighbors: int = 5) -> None:
        import cv2
        import cv2.data as cv2_data  # explicit: the stubs do not expose `cv2.data` as a module attribute

        self._cv2 = cv2
        self._cascade = cv2.CascadeClassifier(cv2_data.haarcascades + "haarcascade_frontalface_default.xml")
        if self._cascade.empty():
            raise RuntimeError("OpenCV haarcascade_frontalface_default.xml not found")
        self.min_size_px = min_size_px
        self.scale_factor = scale_factor
        self.min_neighbors = min_neighbors
        self.model_name = "opencv-haar-frontalface"

    def detect(self, image: Any) -> list[FaceBox]:
        gray = self._cv2.cvtColor(image, self._cv2.COLOR_BGR2GRAY)
        faces = self._cascade.detectMultiScale(
            gray,
            scaleFactor=self.scale_factor,
            minNeighbors=self.min_neighbors,
            minSize=(self.min_size_px, self.min_size_px),
        )
        out = []
        for x, y, w, h in faces:
            # Haar gives no confidence; use relative size as a weak proxy so larger (closer) faces rank first.
            out.append(FaceBox((float(x), float(y), float(x + w), float(y + h)), min(1.0, w / 200.0)))
        out.sort(key=lambda f: f.score, reverse=True)
        return out


class YuNetFaceDetector:
    """OpenCV FaceDetectorYN (YuNet) — a far stronger detector than Haar for entrance cameras.

    The operator supplies the ONNX file (Apache-2.0, from the OpenCV model zoo); it is not bundled.
    """

    def __init__(
        self, model_path: str, score_threshold: float = 0.7, nms_threshold: float = 0.3, top_k: int = 50
    ) -> None:
        import cv2

        self._cv2 = cv2
        self._det = cv2.FaceDetectorYN.create(model_path, "", (320, 320), score_threshold, nms_threshold, top_k)
        self.model_name = "yunet:" + str(model_path).replace("\\", "/").rsplit("/", 1)[-1]

    def detect(self, image: Any) -> list[FaceBox]:
        h, w = image.shape[:2]
        self._det.setInputSize((w, h))
        _, faces = self._det.detect(image)
        if faces is None:
            return []
        out = [
            FaceBox((float(x), float(y), float(x + bw), float(y + bh)), float(score))
            for x, y, bw, bh, *_rest, score in faces
        ]
        out.sort(key=lambda f: f.score, reverse=True)
        return out


class OnnxFaceEmbedder:
    def __init__(
        self, model_path: str, input_size: tuple[int, int] = (112, 112), providers: list[str] | None = None
    ) -> None:
        self._s = OnnxSession(model_path, providers)
        self.model_name = self._s.model_name
        self.input_size = input_size

    def embed(self, face_image: Any) -> list[float]:
        h, w = face_image.shape[:2]
        blob = preprocess_crop(face_image, (0.0, 0.0, float(w), float(h)), self.input_size, normalize="arcface")
        if blob is None:
            raise ValueError("empty face image")
        return l2_normalize(self._s.run(blob)[0])

    def embed_box(self, image: Any, face: FaceBox, pad_ratio: float = 0.15) -> list[float] | None:
        blob = preprocess_crop(image, face.box, self.input_size, normalize="arcface", pad_ratio=pad_ratio)
        return None if blob is None else l2_normalize(self._s.run(blob)[0])
