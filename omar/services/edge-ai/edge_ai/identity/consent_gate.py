"""
Consent gate: the edge keeps a local cache of granted consents per member (pulled
from the cloud, refreshed periodically) and refuses biometric processing for
anyone not in it. The cloud enforces the same rule in `ingest_events`; defence
in depth means a stale cache can never *grant* access, only deny it.

Staleness is measured from the moment the CLOUD generated the snapshot
(`generated_at`), not from when the edge last applied it: re-applying a cached
snapshot while offline must never make it look fresh again.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

BIOMETRIC = "biometric_face_identification"
TRACKING = "cctv_workout_tracking"


@dataclass
class ConsentGate:
    # Optional legacy plaintext cache. The production stack passes None: the consents travel in the
    # (encrypted) device-sync snapshot cache instead, so no plaintext roster sits on disk.
    cache_path: Path | None
    max_age_seconds: float = 24 * 3600
    _granted: dict[str, dict[str, str]] = field(default_factory=dict)  # member_id -> {consent_type: consent_record_id}
    _expires: dict[str, dict[str, float]] = field(default_factory=dict)  # member_id -> {consent_type: epoch seconds}
    _generated_at: float = 0.0

    def load(self) -> None:
        if self.cache_path is None or not self.cache_path.exists():
            return
        data = json.loads(self.cache_path.read_text(encoding="utf-8"))
        self._granted = data.get("granted", {})
        self._expires = data.get("expires", {})
        # Older files only carried `loaded_at`; it is the best (upper-bound) age information they have.
        self._generated_at = float(data.get("generated_at", data.get("loaded_at", 0.0)))

    def replace(
        self,
        granted: dict[str, dict[str, str]],
        generated_at: float | None = None,
        expires: dict[str, dict[str, float]] | None = None,
    ) -> None:
        """Install a consent snapshot. `generated_at` = when the cloud produced it (defaults to now)."""
        now = time.time()
        self._granted = granted
        self._expires = expires or {}
        # A device clock running behind the server must not push freshness into the future.
        self._generated_at = min(now, generated_at) if generated_at is not None else now
        if self.cache_path is not None:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            self.cache_path.write_text(
                json.dumps({"granted": granted, "expires": self._expires, "generated_at": self._generated_at}),
                encoding="utf-8",
            )

    def clear(self) -> None:
        """Deny everything (e.g. the device was retired)."""
        self._granted = {}
        self._expires = {}
        self._generated_at = 0.0

    @property
    def generated_at(self) -> float:
        return self._generated_at

    @property
    def stale(self) -> bool:
        # `>=` so that max_age_seconds=0 always means stale regardless of clock resolution.
        return time.time() - self._generated_at >= self.max_age_seconds

    def consent_record_for(self, member_id: str, consent_type: str) -> str | None:
        """Returns the consent_record_id when the member has a granted, unexpired consent of that type."""
        if self.stale:
            return None  # fail closed
        record = self._granted.get(member_id, {}).get(consent_type)
        if record is None:
            return None
        expires_at = self._expires.get(member_id, {}).get(consent_type)
        if expires_at is not None and expires_at <= time.time():
            return None  # expired while the device was offline
        return record

    def allows_face(self, member_id: str) -> str | None:
        return self.consent_record_for(member_id, BIOMETRIC)

    def allows_tracking(self, member_id: str) -> str | None:
        return self.consent_record_for(member_id, TRACKING)

    def any_face_identification_allowed(self) -> bool:
        """False when no member could pass the gate: callers then skip face processing entirely."""
        if self.stale:
            return False
        return any(self.allows_face(m) is not None and self.allows_tracking(m) is not None for m in self._granted)
