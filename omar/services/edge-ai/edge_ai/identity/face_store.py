"""
Edge-only encrypted biometric template store + matcher.

* Templates (face embeddings) are encrypted with AES-256-GCM using a device key
  (EDGE_BIOMETRIC_KEY_B64) and stored in SQLite. The cloud only ever receives
  the opaque `template_ref` and `key_id`.
* Enrollment requires a consent_record_id; the store refuses to save otherwise.
* Matching is 1:N cosine similarity with a margin check: if the best and second
  best are too close, the result is "ambiguous" and no identity is returned —
  the member is asked to use QR/NFC instead.
* The embedding model itself (e.g. an ArcFace-family ONNX) is pluggable through
  `FaceEmbedder`; this file contains no model weights.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class FaceEmbedder(Protocol):
    model_name: str

    def embed(self, face_image: Any) -> list[float]: ...

    def liveness_score(self, face_image: Any) -> float:
        """Presentation-attack detection: 0 = certainly spoof, 1 = certainly live."""
        ...


@dataclass(frozen=True)
class MatchResult:
    member_id: str | None
    confidence: float
    ambiguous: bool
    template_ref: str | None


class EncryptedTemplateStore:
    def __init__(self, db_path: Path, key: bytes, key_id: str | None = None) -> None:
        if len(key) != 32:
            raise ValueError("AES-256-GCM key must be 32 bytes")
        self._aes = AESGCM(key)
        self.key_id = key_id or hashlib.sha256(key).hexdigest()[:16]
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(db_path), check_same_thread=False)
        self._db.execute("pragma journal_mode=wal")
        self._db.execute(
            """create table if not exists templates (
                 template_ref text primary key,
                 member_id text not null,
                 consent_record_id text not null,
                 key_id text not null,
                 nonce blob not null,
                 ciphertext blob not null,
                 model_name text not null,
                 enrolled_at real not null,
                 revoked_at real
               )"""
        )
        self._db.execute("create index if not exists idx_templates_member on templates(member_id)")
        self._db.commit()

    # ── crypto ───────────────────────────────────────────────────────────────
    def _encrypt(self, vector: list[float], aad: bytes) -> tuple[bytes, bytes]:
        nonce = os.urandom(12)
        plaintext = json.dumps(vector, separators=(",", ":")).encode()
        return nonce, self._aes.encrypt(nonce, plaintext, aad)

    def _decrypt(self, nonce: bytes, ciphertext: bytes, aad: bytes) -> list[float]:
        return list(json.loads(self._aes.decrypt(nonce, ciphertext, aad)))

    # ── enrollment ───────────────────────────────────────────────────────────
    def enroll(self, member_id: str, consent_record_id: str, embedding: list[float], model_name: str) -> str:
        if not consent_record_id:
            raise PermissionError("biometric enrollment requires a consent record")
        template_ref = f"tpl-{hashlib.sha256(os.urandom(16)).hexdigest()[:20]}"
        aad = f"{template_ref}|{member_id}".encode()
        nonce, ct = self._encrypt(embedding, aad)
        self._db.execute(
            "insert into templates values (?,?,?,?,?,?,?,?,null)",
            (template_ref, member_id, consent_record_id, self.key_id, nonce, ct, model_name, time.time()),
        )
        self._db.commit()
        return template_ref

    def revoke_member(self, member_id: str) -> int:
        """Consent withdrawn: delete ciphertext immediately (not just flag)."""
        cur = self._db.execute("delete from templates where member_id = ?", (member_id,))
        self._db.commit()
        return cur.rowcount

    def count(self) -> int:
        return int(self._db.execute("select count(*) from templates where revoked_at is null").fetchone()[0])

    def all_templates(self) -> list[tuple[str, str, list[float]]]:
        rows = self._db.execute(
            "select template_ref, member_id, nonce, ciphertext from templates where revoked_at is null"
        ).fetchall()
        out = []
        for ref, member_id, nonce, ct in rows:
            out.append((ref, member_id, self._decrypt(nonce, ct, f"{ref}|{member_id}".encode())))
        return out

    def close(self) -> None:
        self._db.close()


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


class FaceMatcher:
    """1:N matcher over the encrypted store. Thresholds are calibrated per model in Phase 3 on
    real enrollment data; defaults are deliberately conservative."""

    def __init__(
        self, store: EncryptedTemplateStore, match_threshold: float = 0.62, ambiguity_margin: float = 0.06
    ) -> None:
        self.store = store
        self.match_threshold = match_threshold
        self.ambiguity_margin = ambiguity_margin
        self._cache: list[tuple[str, str, list[float]]] | None = None

    def refresh(self) -> None:
        self._cache = self.store.all_templates()

    def match(self, embedding: list[float]) -> MatchResult:
        if self._cache is None:
            self.refresh()
        assert self._cache is not None
        scored = sorted(((_cosine(embedding, vec), ref, member) for ref, member, vec in self._cache), reverse=True)
        if not scored:
            return MatchResult(None, 0.0, False, None)
        best, ref, member = scored[0]
        second = scored[1][0] if len(scored) > 1 else -1.0
        # Different-member near-tie → ambiguous. Same member enrolled twice is fine.
        if (
            len(scored) > 1
            and scored[1][2] != member
            and best - second < self.ambiguity_margin
            and best >= self.match_threshold
        ):
            return MatchResult(None, round(best, 4), True, None)
        if best < self.match_threshold:
            return MatchResult(None, round(best, 4), False, None)
        return MatchResult(member, round(best, 4), False, ref)
