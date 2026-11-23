"""
Member ↔ equipment association from camera zones.

A person "uses" equipment when their foot point stays inside the equipment zone
polygon for ZONE_ENTER_DWELL_SECONDS. Usage ends when they are outside for
ZONE_EXIT_GRACE_SECONDS (or the track is lost). Brief occlusions therefore do
not split one usage into many.

association_confidence combines: fraction of recent samples inside the zone,
tracking confidence, and whether another identity is contesting the same zone
(two people near one machine → lower confidence for both).
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Literal

from edge_ai.constants import ZONE_ENTER_DWELL_SECONDS, ZONE_EXIT_GRACE_SECONDS
from edge_ai.geometry import point_in_polygon


@dataclass(frozen=True)
class ZoneSpec:
    zone_id: str
    camera_id: str
    equipment_id: str
    exercise_slug: str | None
    polygon: tuple[tuple[float, float], ...]


@dataclass(frozen=True)
class ZoneEvent:
    kind: Literal["usage_started", "usage_ended"]
    identity: str
    zone: ZoneSpec
    t: float
    confidence: float
    reason: Literal["left_zone", "track_lost", "timeout"] | None = None


@dataclass
class _Occupancy:
    first_inside_t: float
    last_inside_t: float
    active: bool = False
    samples: deque[bool] = field(default_factory=lambda: deque(maxlen=30))
    conf_samples: deque[float] = field(default_factory=lambda: deque(maxlen=30))


@dataclass
class ZoneAssociator:
    zones: list[ZoneSpec]
    enter_dwell_s: float = ZONE_ENTER_DWELL_SECONDS
    exit_grace_s: float = ZONE_EXIT_GRACE_SECONDS
    _occ: dict[tuple[str, str], _Occupancy] = field(default_factory=dict)  # (identity, zone_id)

    def zones_for_camera(self, camera_id: str) -> list[ZoneSpec]:
        return [z for z in self.zones if z.camera_id == camera_id]

    def observe(
        self, identity: str, camera_id: str, foot_xy: tuple[float, float], t: float, tracking_conf: float
    ) -> list[ZoneEvent]:
        events: list[ZoneEvent] = []
        for z in self.zones_for_camera(camera_id):
            inside = point_in_polygon(foot_xy, z.polygon)
            key = (identity, z.zone_id)
            occ = self._occ.get(key)
            if inside:
                if occ is None:
                    occ = _Occupancy(first_inside_t=t, last_inside_t=t)
                    self._occ[key] = occ
                occ.last_inside_t = t
                occ.samples.append(True)
                occ.conf_samples.append(tracking_conf)
                if not occ.active and t - occ.first_inside_t >= self.enter_dwell_s:
                    occ.active = True
                    events.append(ZoneEvent("usage_started", identity, z, occ.first_inside_t, self._confidence(key)))
            elif occ is not None:
                occ.samples.append(False)
                occ.conf_samples.append(tracking_conf)
                if occ.active and t - occ.last_inside_t >= self.exit_grace_s:
                    events.append(
                        ZoneEvent(
                            "usage_ended", identity, z, occ.last_inside_t, self._confidence(key), reason="left_zone"
                        )
                    )
                    del self._occ[key]
                elif not occ.active and t - occ.last_inside_t >= self.exit_grace_s:
                    del self._occ[key]  # walked through without dwelling
        return events

    def track_lost(self, identity: str, t: float) -> list[ZoneEvent]:
        events: list[ZoneEvent] = []
        for key in [k for k in self._occ if k[0] == identity]:
            occ = self._occ.pop(key)
            if occ.active:
                z = next(z for z in self.zones if z.zone_id == key[1])
                events.append(
                    ZoneEvent(
                        "usage_ended",
                        identity,
                        z,
                        t,
                        self._confidence_from(occ, identity, z.zone_id),
                        reason="track_lost",
                    )
                )
        return events

    def active_zone(self, identity: str) -> ZoneSpec | None:
        for (ident, zid), occ in self._occ.items():
            if ident == identity and occ.active:
                return next(z for z in self.zones if z.zone_id == zid)
        return None

    def _confidence(self, key: tuple[str, str]) -> float:
        occ = self._occ[key]
        return self._confidence_from(occ, key[0], key[1])

    def _confidence_from(self, occ: _Occupancy, identity: str, zone_id: str) -> float:
        inside_ratio = sum(occ.samples) / len(occ.samples) if occ.samples else 0.0
        mean_track = sum(occ.conf_samples) / len(occ.conf_samples) if occ.conf_samples else 0.0
        contested = any(ident != identity and zid == zone_id and o.active for (ident, zid), o in self._occ.items())
        conf = 0.6 * inside_ratio + 0.4 * mean_track
        if contested:
            conf *= 0.6
        return round(max(0.0, min(1.0, conf)), 4)
