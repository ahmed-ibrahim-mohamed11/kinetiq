"""
Entrance identification stage.

For each confirmed track on an entrance camera whose foot point is inside an
entrance zone, crop the head region, detect a face, embed it, and match it
against the encrypted store. On a confident, unambiguous match for a member
with granted consents, bind the global identity (→ `identity.checkin`).

Guard rails:
* at most one attempt per track per `retry_seconds`, and at most `max_attempts`;
* no face is detected or embedded while the consent gate is closed for everyone;
* consent gate checked BEFORE the match result is used (and again server-side);
* ambiguous matches are never bound; they count toward `ambiguous` for metrics;
* a track that is already bound is skipped.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from edge_ai.identity.consent_gate import ConsentGate
from edge_ai.identity.face_pipeline import FaceDetector, LivenessDetector, NoLivenessDetector
from edge_ai.identity.face_store import FaceMatcher


class FaceEmbedderLike:  # structural, for typing without importing ONNX
    model_name: str

    def embed_box(self, image: Any, face: Any, pad_ratio: float = 0.15) -> list[float] | None: ...  # pragma: no cover


@dataclass
class IdentificationOutcome:
    member_id: str | None
    confidence: float
    consent_record_id: str | None
    pad_score: float | None
    reason: str  # matched | no_face | no_match | ambiguous | no_consent | liveness_failed | gate_closed


@dataclass
class EntranceIdentifier:
    detector: FaceDetector
    embedder: Any
    matcher: FaceMatcher
    consent: ConsentGate
    liveness: LivenessDetector = field(default_factory=NoLivenessDetector)
    liveness_threshold: float = 0.5
    retry_seconds: float = 1.0
    max_attempts: int = 6
    head_fraction: float = 0.45  # top part of the person box where the face is
    _attempts: dict[tuple[str, int], list[float]] = field(default_factory=dict)
    stats: dict[str, int] = field(
        default_factory=lambda: {
            "matched": 0,
            "no_face": 0,
            "no_match": 0,
            "ambiguous": 0,
            "no_consent": 0,
            "liveness_failed": 0,
            "gate_closed": 0,
        }
    )

    def should_try(self, camera_id: str, local_track_id: int, now: float | None = None) -> bool:
        now = now if now is not None else time.time()
        hist = self._attempts.setdefault((camera_id, local_track_id), [])
        if len(hist) >= self.max_attempts:
            return False
        if hist and now - hist[-1] < self.retry_seconds:
            return False
        hist.append(now)
        return True

    def forget(self, camera_id: str, local_track_id: int) -> None:
        self._attempts.pop((camera_id, local_track_id), None)

    def identify(self, image: Any, person_box: tuple[float, float, float, float]) -> IdentificationOutcome:
        # 1:N matching needs an embedding before the match says who it is, so the per-member consent
        # check necessarily follows the match. When NOBODY could pass the gate (stale cache, no
        # consenting member) there is no reason to detect or embed a face at all.
        if not self.consent.any_face_identification_allowed():
            return self._out(None, 0.0, None, None, "gate_closed")
        x1, y1, x2, y2 = person_box
        head = (x1, y1, x2, y1 + (y2 - y1) * self.head_fraction)
        hx1, hy1, hx2, hy2 = (max(0, int(v)) for v in head)
        crop = image[hy1:hy2, hx1:hx2]
        if crop.size == 0:
            return self._out(None, 0.0, None, None, "no_face")
        faces = self.detector.detect(crop)
        if not faces:
            return self._out(None, 0.0, None, None, "no_face")
        face = faces[0]
        pad = self.liveness.score(crop, face)
        if pad is not None and pad < self.liveness_threshold:
            return self._out(None, 0.0, None, pad, "liveness_failed")
        emb = self.embedder.embed_box(crop, face)
        if emb is None:
            return self._out(None, 0.0, None, pad, "no_face")
        match = self.matcher.match(emb)
        if match.ambiguous:
            return self._out(None, match.confidence, None, pad, "ambiguous")
        if match.member_id is None:
            return self._out(None, match.confidence, None, pad, "no_match")
        consent_id = self.consent.allows_face(match.member_id)
        if consent_id is None or self.consent.allows_tracking(match.member_id) is None:
            return self._out(None, match.confidence, None, pad, "no_consent")
        return self._out(match.member_id, match.confidence, consent_id, pad, "matched")

    def _out(
        self, member: str | None, conf: float, consent: str | None, pad: float | None, reason: str
    ) -> IdentificationOutcome:
        self.stats[reason] = self.stats.get(reason, 0) + 1
        return IdentificationOutcome(member, conf, consent, pad, reason)
