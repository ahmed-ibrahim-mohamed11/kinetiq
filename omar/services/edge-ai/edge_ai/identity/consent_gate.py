"""
Consent gate: the edge keeps a local cache of granted consents per member (pulled
from the cloud, refreshed periodically) and refuses biometric processing for
anyone not in it. The cloud enforces the same rule in `ingest_events`; defence
in depth means a stale cache can never *grant* access, only deny it.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ConsentGate:
    cache_path: Path
    max_age_seconds: float = 24 * 3600
    _granted: dict[str, dict[str, str]] = field(default_factory=dict)  # member_id -> {consent_type: consent_record_id}
    _loaded_at: float = 0.0

    def load(self) -> None:
        if self.cache_path.exists():
            data = json.loads(self.cache_path.read_text(encoding="utf-8"))
            self._granted = data.get("granted", {})
            self._loaded_at = data.get("loaded_at", 0.0)

    def replace(self, granted: dict[str, dict[str, str]]) -> None:
        self._granted = granted
        self._loaded_at = time.time()
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(json.dumps({"granted": granted, "loaded_at": self._loaded_at}), encoding="utf-8")

    @property
    def stale(self) -> bool:
        # `>=` so that max_age_seconds=0 always means stale regardless of clock resolution.
        return time.time() - self._loaded_at >= self.max_age_seconds

    def consent_record_for(self, member_id: str, consent_type: str) -> str | None:
        """Returns the consent_record_id when the member has a granted consent of that type."""
        if self.stale:
            return None  # fail closed
        return self._granted.get(member_id, {}).get(consent_type)

    def allows_face(self, member_id: str) -> str | None:
        return self.consent_record_for(member_id, "biometric_face_identification")

    def allows_tracking(self, member_id: str) -> str | None:
        return self.consent_record_for(member_id, "cctv_workout_tracking")
