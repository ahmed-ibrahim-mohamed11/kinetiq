"""
Cloud → edge configuration sync (device-sync Edge Function).

The edge pulls a signed snapshot of its branch configuration and the consent
registry. The snapshot is cached on disk so the device keeps working through
internet outages; the ConsentGate reads the cached consents and fails closed
when the cache is older than its max age.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx
import structlog
from pydantic import BaseModel, Field

from edge_ai.config import CameraConfig
from edge_ai.identity.consent_gate import ConsentGate
from edge_ai.reps.profiles import RepProfile

log = structlog.get_logger(__name__)


class SyncExercise(BaseModel):
    id: UUID
    slug: str
    name: str
    rep_profile: dict[str, Any]


class SyncMember(BaseModel):
    id: UUID
    member_code: str
    full_name: str


class SyncSnapshot(BaseModel):
    schema_version: int = 1
    generated_at: datetime
    device_id: UUID
    branch_id: UUID
    cameras: list[CameraConfig] = Field(default_factory=list)
    equipment: list[dict[str, Any]] = Field(default_factory=list)
    exercises: list[SyncExercise] = Field(default_factory=list)
    consents: dict[str, dict[str, str]] = Field(default_factory=dict)
    members: list[SyncMember] = Field(default_factory=list)
    revoked_template_refs: list[str] = Field(default_factory=list)
    active_template_refs: list[str] = Field(default_factory=list)

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


class DeviceSyncClient:
    def __init__(
        self,
        sync_url: str,
        device_id: str,
        device_secret: str,
        cache_path: Path,
        client: httpx.Client | None = None,
        timeout_s: float = 15.0,
    ) -> None:
        self.sync_url = sync_url
        self.device_id = device_id
        self.device_secret = device_secret
        self.cache_path = cache_path
        self._client = client or httpx.Client(timeout=timeout_s)
        self.last_success_at: float | None = None
        self.last_error: str | None = None

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
        if resp.status_code != 200:
            self.last_error = f"server {resp.status_code}: {resp.text[:200]}"
            log.warning("device_sync.rejected", status=resp.status_code)
            return None
        snapshot = SyncSnapshot.model_validate(resp.json())
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(json.dumps(resp.json()), encoding="utf-8")
        self.last_success_at = time.time()
        self.last_error = None
        return snapshot

    def load_cached(self) -> SyncSnapshot | None:
        if not self.cache_path.exists():
            return None
        try:
            return SyncSnapshot.model_validate(json.loads(self.cache_path.read_text(encoding="utf-8")))
        except ValueError as exc:
            log.warning("device_sync.cache_invalid", error=str(exc))
            return None

    def fetch_or_cached(self) -> SyncSnapshot | None:
        return self.fetch() or self.load_cached()

    @staticmethod
    def apply_consents(snapshot: SyncSnapshot, gate: ConsentGate) -> None:
        gate.replace(snapshot.consents)
