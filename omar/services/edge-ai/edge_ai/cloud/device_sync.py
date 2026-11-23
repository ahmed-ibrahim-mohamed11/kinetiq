"""
Cloud → edge configuration sync (device-sync Edge Function).

The edge pulls a snapshot of its branch configuration and the consent registry.
The REQUEST is HMAC-signed with the device secret; the RESPONSE is not signed,
so its integrity relies on TLS (Settings refuses non-loopback http:// URLs).

The snapshot is cached on disk so the device keeps working through internet
outages. The cache holds only what the edge needs (no member roster) and is
encrypted with a key derived from the biometric key; without that key the
consents and template references are not cached at all. The ConsentGate measures
staleness from the snapshot's `generated_at`, so a cached snapshot never looks
fresher than the moment the cloud produced it.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx
import structlog
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from pydantic import BaseModel, Field, PrivateAttr, TypeAdapter, ValidationError, model_validator

from edge_ai.config import CameraConfig
from edge_ai.identity.consent_gate import BIOMETRIC, TRACKING, ConsentGate
from edge_ai.reps.profiles import RepProfile

log = structlog.get_logger(__name__)

# The only consent types the edge acts on (C6). Anything else in an older snapshot is dropped.
EDGE_CONSENT_TYPES = (BIOMETRIC, TRACKING)
_CACHE_MAGIC = b"SGC1"


class SyncExercise(BaseModel):
    id: UUID
    slug: str
    name: str
    rep_profile: dict[str, Any]


_DATETIME = TypeAdapter(datetime)


def _epoch(value: Any) -> float | None:
    """Epoch seconds of an ISO-8601 timestamp (or a number). Raises ValueError when malformed.

    Parsed with pydantic, not datetime.fromisoformat: Postgres trims trailing zeros of the
    fraction when it writes timestamptz to jsonb ('...41.15+02:00'), which Python 3.10's
    fromisoformat rejects.
    """
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        raise ValueError(f"invalid timestamp {value!r}")
    if isinstance(value, (int, float)):
        return float(value)
    try:
        dt = _DATETIME.validate_python(str(value))
    except ValidationError as exc:
        raise ValueError(f"invalid timestamp {str(value)[:40]!r}") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _consent_item(item: Any) -> tuple[str, str, str, float | None] | None:
    """(member_id, consent_type, consent_record_id, expires_epoch) of one C6 consent, None to skip it.

    A malformed item is skipped (fail closed: no consent for it) instead of failing the whole
    snapshot, so one bad row never stops catalogue, exclusion-zone and revocation updates.
    """
    if not isinstance(item, dict):
        log.warning("device_sync.bad_consent_item", error="not an object")
        return None
    ctype = str(item.get("consent_type", ""))
    if ctype not in EDGE_CONSENT_TYPES:
        return None
    member, record = item.get("member_id"), item.get("consent_record_id")
    if member in (None, "") or record in (None, ""):
        log.warning("device_sync.bad_consent_item", consent_type=ctype, error="member_id/consent_record_id missing")
        return None
    try:
        exp = _epoch(item.get("expires_at"))
    except ValueError as exc:
        log.warning("device_sync.bad_consent_item", consent_type=ctype, error=str(exc))
        return None
    return str(member), ctype, str(record), exp


class SyncSnapshot(BaseModel):
    schema_version: int = 1
    generated_at: datetime
    device_id: UUID
    branch_id: UUID
    cameras: list[CameraConfig] = Field(default_factory=list)
    equipment: list[dict[str, Any]] = Field(default_factory=list)
    exercises: list[SyncExercise] = Field(default_factory=list)
    # Normalised form: member_id -> {consent_type: consent_record_id} (+ optional expiry, epoch seconds).
    consents: dict[str, dict[str, str]] = Field(default_factory=dict)
    consent_expires: dict[str, dict[str, float]] = Field(default_factory=dict)
    revoked_template_refs: list[str] = Field(default_factory=list)
    # None = the server did not send the key → never reconcile local templates against it.
    active_template_refs: list[str] | None = None
    _from_cache: bool = PrivateAttr(default=False)

    @model_validator(mode="before")
    @classmethod
    def _normalise_consents(cls, data: Any) -> Any:
        """Accept C6 `[{member_id, consent_type, consent_record_id, expires_at}]` and the older
        `{member_id: {type: id}}` shape; keep only the consent types the edge uses."""
        if not isinstance(data, dict):
            return data
        raw = data.get("consents")
        granted: dict[str, dict[str, str]] = {}
        prior = data.get("consent_expires")
        expires: dict[str, dict[str, float]] = dict(prior) if isinstance(prior, dict) else {}
        if isinstance(raw, list):
            for item in raw:
                parsed = _consent_item(item)
                if parsed is None:
                    continue
                member, ctype, record, exp = parsed
                granted.setdefault(member, {})[ctype] = record
                if exp is not None:
                    expires.setdefault(member, {})[ctype] = exp
        elif isinstance(raw, dict):
            for member, types in raw.items():
                if not isinstance(types, dict):
                    continue
                kept = {t: str(v) for t, v in types.items() if t in EDGE_CONSENT_TYPES}
                if kept:
                    granted[str(member)] = kept
        else:
            return data
        out = {k: v for k, v in data.items() if k != "members"}  # never keep the roster (C6)
        out["consents"] = granted
        out["consent_expires"] = expires
        return out

    @property
    def from_cache(self) -> bool:
        return self._from_cache

    @property
    def generated_at_epoch(self) -> float:
        dt = self.generated_at if self.generated_at.tzinfo else self.generated_at.replace(tzinfo=timezone.utc)
        return dt.timestamp()

    def exercise_ids(self) -> dict[str, UUID]:
        return {e.slug: e.id for e in self.exercises}

    def profiles(self) -> dict[str, RepProfile]:
        out: dict[str, RepProfile] = {}
        for e in self.exercises:
            try:
                out[e.slug] = RepProfile.from_catalog(e.rep_profile)
            except ValueError as exc:
                log.warning("sync.bad_rep_profile", slug=e.slug, error=str(exc))
        return out


def sign_sync_request(secret: str, device_id: str, timestamp: str) -> str:
    canonical = f"{device_id}\n{timestamp}\nGET /device-sync"
    return hmac.new(secret.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256).hexdigest()


def derive_cache_key(biometric_key: bytes) -> bytes:
    """Domain-separated sub-key: the snapshot cache never shares a key with face templates."""
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=b"smartgym-edge/device-sync-cache/v1").derive(
        biometric_key
    )


class DeviceSyncClient:
    def __init__(
        self,
        sync_url: str,
        device_id: str,
        device_secret: str,
        cache_path: Path,
        client: httpx.Client | None = None,
        timeout_s: float = 15.0,
        cache_key: bytes | None = None,
    ) -> None:
        self.sync_url = sync_url
        self.device_id = device_id
        self.device_secret = device_secret
        self.cache_path = cache_path
        self._client = client or httpx.Client(timeout=timeout_s)
        self._aes = AESGCM(derive_cache_key(cache_key)) if cache_key is not None else None
        self.last_success_at: float | None = None
        self.last_error: str | None = None
        self.retired = False

    def fetch(self) -> SyncSnapshot | None:
        ts = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        headers = {
            "x-device-id": self.device_id,
            "x-device-timestamp": ts,
            "x-device-signature": sign_sync_request(self.device_secret, self.device_id, ts),
        }
        try:
            resp = self._client.get(self.sync_url, headers=headers)
        except httpx.HTTPError as exc:
            self.last_error = f"network: {exc}"
            log.warning("device_sync.network_error", error=str(exc))
            return None
        if resp.status_code == 403 and _error_code(resp) == "device_retired":
            self.retired = True
            self.last_error = "device_retired"
            log.error("device_sync.device_retired", msg="the cloud retired this device; consents are no longer served")
            return None
        if resp.status_code != 200:
            self.last_error = f"server {resp.status_code}: {resp.text[:200]}"
            log.warning("device_sync.rejected", status=resp.status_code)
            return None
        try:
            snapshot = SyncSnapshot.model_validate(resp.json())
        except (ValueError, TypeError) as exc:  # includes pydantic.ValidationError and bad JSON
            self.last_error = f"invalid snapshot: {str(exc)[:200]}"
            log.error("device_sync.invalid_snapshot", error=str(exc)[:500])
            return None
        self._write_cache(snapshot)
        self.retired = False
        self.last_success_at = time.time()
        self.last_error = None
        return snapshot

    # ── cache ────────────────────────────────────────────────────────────────
    def _cache_payload(self, snapshot: SyncSnapshot) -> dict[str, Any]:
        data = snapshot.model_dump(mode="json")
        if self._aes is None:
            # No key → no place to keep personal data safely: cache configuration only.
            data.update(consents={}, consent_expires={}, revoked_template_refs=[], active_template_refs=None)
        return data

    def _write_cache(self, snapshot: SyncSnapshot) -> None:
        raw = json.dumps(self._cache_payload(snapshot), separators=(",", ":")).encode("utf-8")
        if self._aes is not None:
            nonce = os.urandom(12)
            raw = _CACHE_MAGIC + nonce + self._aes.encrypt(nonce, raw, self.device_id.encode())
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.cache_path.with_name(self.cache_path.name + ".tmp")
        tmp.write_bytes(raw)
        os.replace(tmp, self.cache_path)

    def load_cached(self) -> SyncSnapshot | None:
        if not self.cache_path.exists():
            return None
        blob = self.cache_path.read_bytes()
        legacy = not blob.startswith(_CACHE_MAGIC)
        try:
            if legacy:
                text = blob.decode("utf-8")
            elif self._aes is None:
                log.warning("device_sync.cache_encrypted_no_key")
                return None
            else:
                n = len(_CACHE_MAGIC)
                text = self._aes.decrypt(blob[n : n + 12], blob[n + 12 :], self.device_id.encode()).decode("utf-8")
            snapshot = SyncSnapshot.model_validate(json.loads(text))
        except (ValueError, TypeError, InvalidTag) as exc:
            log.warning("device_sync.cache_invalid", error=str(exc)[:200])
            return None
        if legacy:
            # Plaintext cache from an older version (may hold the member roster): rewrite it minimised.
            self._write_cache(snapshot)
            if self._aes is None:
                snapshot = SyncSnapshot.model_validate(self._cache_payload(snapshot))
        snapshot._from_cache = True
        return snapshot

    def fetch_or_cached(self) -> SyncSnapshot | None:
        return self.fetch() or self.load_cached()

    @staticmethod
    def apply_consents(snapshot: SyncSnapshot, gate: ConsentGate) -> None:
        gate.replace(snapshot.consents, generated_at=snapshot.generated_at_epoch, expires=snapshot.consent_expires)


def _error_code(resp: httpx.Response) -> str | None:
    try:
        body = resp.json()
    except ValueError:
        return None
    return str(body.get("code")) if isinstance(body, dict) and body.get("code") is not None else None
