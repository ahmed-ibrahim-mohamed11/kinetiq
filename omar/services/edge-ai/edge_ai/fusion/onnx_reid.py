"""Person re-identification embedder (OSNet / fast-reid style ONNX: [1,3,256,128] → [1,D])."""

from __future__ import annotations

from typing import Any

from edge_ai.inference.session import OnnxSession, l2_normalize, preprocess_crop


class OnnxReidEmbedder:
    def __init__(
        self, model_path: str, input_size: tuple[int, int] = (128, 256), providers: list[str] | None = None
    ) -> None:
        self._s = OnnxSession(model_path, providers)
        self.model_name = self._s.model_name
        self.input_size = input_size  # (w, h)

    def embed(self, image: Any, box: tuple[float, float, float, float]) -> list[float] | None:
        blob = preprocess_crop(image, box, self.input_size, normalize="imagenet")
        if blob is None:
            return None
        out = self._s.run(blob)[0]
        return l2_normalize(out)

    def embed_many(self, image: Any, boxes: list[tuple[float, float, float, float]]) -> list[list[float] | None]:
        return [self.embed(image, b) for b in boxes]
