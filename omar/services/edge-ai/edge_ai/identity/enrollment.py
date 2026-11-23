"""
Member enrollment on the edge.

    edge-ai enroll --member <uuid> --image photo.jpg [--image photo2.jpg ...]

1. Pull (or load cached) device-sync snapshot → verify the member has a granted
   `biometric_face_identification` consent (fail closed).
2. Detect exactly one face per image, embed, average embeddings (L2-normalised).
3. Store the template encrypted (AES-256-GCM) in the local store.
4. Emit `biometric.enrolled` so the cloud registry records the reference.
Images are NOT retained; only the derived embedding is stored.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

import numpy as np

from edge_ai.contracts.events_v1 import BiometricEnrolledPayload, BiometricRevokedPayload
from edge_ai.events.builder import EventBuilder
from edge_ai.identity.consent_gate import ConsentGate
from edge_ai.identity.face_pipeline import FaceDetector
from edge_ai.identity.face_store import EncryptedTemplateStore


@dataclass(frozen=True)
class EnrollmentResult:
    template_ref: str
    images_used: int
    quality: float


class EnrollmentError(RuntimeError):
    pass


def _quality(faces_found: list[int], face_sizes: list[float]) -> float:
    """Simple, explainable quality: fraction of images with exactly one face × size adequacy."""
    if not faces_found:
        return 0.0
    single = sum(1 for n in faces_found if n == 1) / len(faces_found)
    size = min(1.0, (sum(face_sizes) / len(face_sizes)) / 160.0) if face_sizes else 0.0
    return round(single * size, 4)


def enroll_member(
    *,
    member_id: str,
    images: list[Any],
    detector: FaceDetector,
    embedder: Any,
    store: EncryptedTemplateStore,
    consent: ConsentGate,
    emitter: EventBuilder | None,
    min_quality: float = 0.5,
) -> EnrollmentResult:
    consent_id = consent.allows_face(member_id)
    if consent_id is None:
        raise EnrollmentError(
            "member has no granted biometric consent in the synced registry (or the registry is stale)"
        )
    if not images:
        raise EnrollmentError("at least one image is required")

    embeddings: list[list[float]] = []
    faces_found: list[int] = []
    face_sizes: list[float] = []
    for img in images:
        faces = detector.detect(img)
        faces_found.append(len(faces))
        if len(faces) != 1:
            continue  # exactly one face required per image
        f = faces[0]
        face_sizes.append(f.box[2] - f.box[0])
        emb = embedder.embed_box(img, f)
        if emb is not None:
            embeddings.append(emb)
    if not embeddings:
        raise EnrollmentError(f"no usable face found (faces per image: {faces_found})")
    quality = _quality(faces_found, face_sizes)
    if quality < min_quality:
        raise EnrollmentError(f"enrollment quality {quality} below {min_quality}; use clearer frontal photos")

    mean = np.mean(np.asarray(embeddings, dtype=np.float32), axis=0)
    norm = float(np.linalg.norm(mean))
    template = (mean / norm).tolist() if norm > 0 else mean.tolist()

    ref = store.enroll(member_id, consent_id, template, getattr(embedder, "model_name", "unknown"))
    if emitter is not None:
        emitter.emit(
            "biometric.enrolled",
            BiometricEnrolledPayload(
                member_id=UUID(member_id),
                consent_record_id=UUID(consent_id),
                template_ref=ref,
                key_id=store.key_id,
                model_name=getattr(embedder, "model_name", "unknown"),
                quality=quality,
            ),
        )
    return EnrollmentResult(ref, len(embeddings), quality)


RevocationReason = Literal["consent_withdrawn", "member_request", "staff_request", "key_rotation", "retired_device"]


def revoke_member(
    *, member_id: str, store: EncryptedTemplateStore, emitter: EventBuilder | None, reason: RevocationReason
) -> int:
    refs = [ref for ref, m, _ in store.all_templates() if m == member_id]
    deleted = store.revoke_member(member_id)
    if emitter is not None:
        for ref in refs:
            emitter.emit(
                "biometric.revoked", BiometricRevokedPayload(member_id=UUID(member_id), template_ref=ref, reason=reason)
            )
    return deleted


def apply_cloud_revocations(*, revoked_refs: list[str], store: EncryptedTemplateStore) -> int:
    """Delete templates the cloud marked revoked (consent withdrawn in the member app)."""
    local = {ref: member for ref, member, _ in store.all_templates()}
    n = 0
    for ref in revoked_refs:
        member = local.get(ref)
        if member:
            n += store.revoke_member(member)
    return n


def load_images(paths: list[Path]) -> list[Any]:
    import cv2

    out = []
    for p in paths:
        img = cv2.imread(str(p))
        if img is None:
            raise EnrollmentError(f"cannot read image {p}")
        out.append(img)
    return out
