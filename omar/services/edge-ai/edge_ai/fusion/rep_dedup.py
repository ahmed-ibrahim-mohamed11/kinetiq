"""
Global repetition deduplication across cameras.

Two cameras watching the same leg press will each run a rep counter on the same
person. Both see the same physical rep within a few hundred milliseconds. The
deduplicator admits the first observation for a (global_identity, equipment)
pair and suppresses any other observation inside REP_DEDUP_WINDOW_MS.

It also tracks which camera is the *primary* observer (highest recent
confidence) so set bookkeeping follows one consistent source, with automatic
failover when the primary stops reporting.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from edge_ai.constants import REP_DEDUP_WINDOW_MS


@dataclass
class _Key:
    identity: str
    equipment_id: str

    def __hash__(self) -> int:
        return hash((self.identity, self.equipment_id))


@dataclass
class RepDeduplicator:
    window_ms: int = REP_DEDUP_WINDOW_MS
    primary_timeout_s: float = 3.0
    _last_rep_t: dict[tuple[str, str], float] = field(default_factory=dict)
    _primary: dict[tuple[str, str], tuple[str, float, float]] = field(default_factory=dict)  # camera, conf, last_t
    suppressed: int = 0
    admitted: int = 0

    def observe(self, identity: str, equipment_id: str, camera_id: str, t: float, confidence: float) -> bool:
        """Return True if this rep observation should be counted."""
        key = (identity, equipment_id)
        last = self._last_rep_t.get(key)
        # Primary election: keep current primary while it keeps reporting; otherwise fail over.
        prim = self._primary.get(key)
        if (
            prim is None
            or t - prim[2] > self.primary_timeout_s
            or (confidence > prim[1] + 0.15 and camera_id != prim[0])
        ):
            self._primary[key] = (camera_id, confidence, t)
        elif prim[0] == camera_id:
            self._primary[key] = (camera_id, 0.8 * prim[1] + 0.2 * confidence, t)

        if last is not None and (t - last) * 1000.0 < self.window_ms:
            self.suppressed += 1
            return False
        self._last_rep_t[key] = t
        self.admitted += 1
        return True

    def primary_camera(self, identity: str, equipment_id: str) -> str | None:
        p = self._primary.get((identity, equipment_id))
        return p[0] if p else None

    def forget(self, identity: str, equipment_id: str | None = None) -> None:
        keys = [k for k in self._last_rep_t if k[0] == identity and (equipment_id is None or k[1] == equipment_id)]
        for k in keys:
            self._last_rep_t.pop(k, None)
            self._primary.pop(k, None)
