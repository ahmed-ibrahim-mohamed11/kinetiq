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
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import structlog
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

log = structlog.get_logger(__name__)


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
    """Encrypted template rows + a `generation` counter bumped on every enroll/delete.

    Several processes share the file (`edge-ai run` and the `enroll`/`revoke` CLI), so matchers
    compare the generation before each match and reload when another process changed the store.
    Deletion never needs the key: refs are selected and deleted by id, so an undecryptable row
    (key change, restored backup, corruption) can always be removed. `secure_delete` zeroes freed
    content and the WAL is truncated after each delete, so deleted ciphertext does not linger in
    the database or WAL files.
    """

    def __init__(self, db_path: Path, key: bytes, key_id: str | None = None) -> None:
        if len(key) != 32:
            raise ValueError("AES-256-GCM key must be 32 bytes")
        self._aes = AESGCM(key)
        self.key_id = key_id or hashlib.sha256(key).hexdigest()[:16]
        self.db_path = db_path
        self.undecryptable = 0  # rows skipped by the last all_templates() call
        self._lock = threading.RLock()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(db_path), check_same_thread=False)
        self._db.execute("pragma secure_delete=on")
        self._db.execute("pragma journal_mode=wal")
        self._db.execute("pragma synchronous=full")
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
        self._db.execute("create table if not exists store_meta (key text primary key, value integer not null)")
        self._db.execute("insert or ignore into store_meta(key, value) values ('generation', 0)")
        self._db.commit()

    # ── change tracking ─────────────────────────────────────────────────────
    def generation(self) -> int:
        with self._lock:
            row = self._db.execute("select value from store_meta where key = 'generation'").fetchone()
        return int(row[0]) if row else 0

    def _bump(self) -> None:
        self._db.execute("update store_meta set value = value + 1 where key = 'generation'")

    def _scrub(self) -> None:
        """Move WAL frames into the (secure_delete-zeroed) database and truncate the WAL file."""
        try:
            self._db.execute("pragma wal_checkpoint(truncate)")
        except sqlite3.Error:  # pragma: no cover — busy reader in another process; next delete retries
            pass

    # ── crypto ───────────────────────────────────────────────────────────────
    def _encrypt(self, vector: list[float], aad: bytes) -> tuple[bytes, bytes]:
        nonce = os.urandom(12)
        plaintext = json.dumps(vector, separators=(",", ":")).encode()
        return nonce, self._aes.encrypt(nonce, plaintext, aad)

    def _decrypt(self, nonce: bytes, ciphertext: bytes, aad: bytes) -> list[float]:
        return list(json.loads(self._aes.decrypt(nonce, ciphertext, aad)))

    # ── enrollment ───────────────────────────────────────────────────────────
    @staticmethod
    def new_template_ref() -> str:
        return f"tpl-{hashlib.sha256(os.urandom(16)).hexdigest()[:20]}"

    def enroll(
        self,
        member_id: str,
        consent_record_id: str,
        embedding: list[float],
        model_name: str,
        template_ref: str | None = None,
    ) -> str:
        """Store a template; `template_ref` lets the caller queue its event before the row exists."""
        if not consent_record_id:
            raise PermissionError("biometric enrollment requires a consent record")
        template_ref = template_ref or self.new_template_ref()
        aad = f"{template_ref}|{member_id}".encode()
        nonce, ct = self._encrypt(embedding, aad)
        with self._lock:
            self._db.execute(
                "insert into templates values (?,?,?,?,?,?,?,?,null)",
                (template_ref, member_id, consent_record_id, self.key_id, nonce, ct, model_name, time.time()),
            )
            self._bump()
            self._db.commit()
        return template_ref

    # ── deletion (never decrypts) ────────────────────────────────────────────
    def revoke_member(self, member_id: str) -> int:
        """Consent withdrawn: delete ciphertext immediately (not just flag)."""
        with self._lock:
            cur = self._db.execute("delete from templates where member_id = ?", (member_id,))
            if cur.rowcount:
                self._bump()
            self._db.commit()
            self._scrub()
        return cur.rowcount

    def delete_refs(self, refs: list[str]) -> int:
        """Delete exactly these template refs (a member's other templates are untouched)."""
        if not refs:
            return 0
        total = 0
        with self._lock:
            for ref in refs:
                total += self._db.execute("delete from templates where template_ref = ?", (ref,)).rowcount
            if total:
                self._bump()
            self._db.commit()
            self._scrub()
        return total

    def refs_for_member(self, member_id: str) -> list[str]:
        with self._lock:
            rows = self._db.execute(
                "select template_ref from templates where member_id = ? order by enrolled_at", (member_id,)
            ).fetchall()
        return [r[0] for r in rows]

    def list_refs(self) -> list[tuple[str, str, float]]:
        """(template_ref, member_id, enrolled_at) for every row, without decrypting anything."""
        with self._lock:
            rows = self._db.execute("select template_ref, member_id, enrolled_at from templates").fetchall()
        return [(r[0], r[1], float(r[2])) for r in rows]

    def count(self) -> int:
        with self._lock:
            return int(self._db.execute("select count(*) from templates where revoked_at is null").fetchone()[0])

    # ── reading ──────────────────────────────────────────────────────────────
    def _readable_rows(self) -> tuple[list[tuple[str, str, list[float]]], list[tuple[str, str]]]:
        with self._lock:
            rows = self._db.execute(
                "select template_ref, member_id, key_id, nonce, ciphertext from templates where revoked_at is null"
            ).fetchall()
        ok: list[tuple[str, str, list[float]]] = []
        bad: list[tuple[str, str]] = []
        for ref, member_id, key_id, nonce, ct in rows:
            if key_id != self.key_id:
                bad.append((ref, member_id))
                continue
            try:
                ok.append((ref, member_id, self._decrypt(nonce, ct, f"{ref}|{member_id}".encode())))
            except (InvalidTag, ValueError):
                bad.append((ref, member_id))
        return ok, bad

    def all_templates(self) -> list[tuple[str, str, list[float]]]:
        """Decryptable templates. Rows written under another key or corrupted are skipped and counted
        in `undecryptable` (see `undecryptable_refs` / `edge-ai revoke --undecryptable`)."""
        ok, bad = self._readable_rows()
        self.undecryptable = len(bad)
        if bad:
            log.warning("face_store.undecryptable_templates", count=len(bad))
        return ok

    def undecryptable_refs(self) -> list[tuple[str, str]]:
        """(template_ref, member_id) of rows the current key cannot decrypt."""
        return self._readable_rows()[1]

    def close(self) -> None:
        with self._lock:
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
        self._generation = -1

    def refresh(self) -> None:
        self._generation = self.store.generation()
        self._cache = self.store.all_templates()

    def match(self, embedding: list[float]) -> MatchResult:
        # One indexed select per match: picks up enrolments/deletions made by any process at once.
        if self._cache is None or self.store.generation() != self._generation:
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
