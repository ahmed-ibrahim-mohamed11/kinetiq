"""
Single-camera multi-object tracker (SORT-style, dependency-free).

* Constant-velocity prediction on box centre + size.
* Greedy IoU matching in two passes (high-score detections first, then low-score
  ones — the key ByteTrack idea that recovers partially occluded people).
* Track lifecycle: TENTATIVE → CONFIRMED after `min_hits`; LOST after
  `max_age` missed frames; tracking_confidence decays while lost.

This tracker produces *local* ids. Cross-camera identity lives in
edge_ai.fusion.global_identity.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from edge_ai.detection.base import Detection
from edge_ai.geometry import iou


class TrackState(str, Enum):
    TENTATIVE = "tentative"
    CONFIRMED = "confirmed"
    LOST = "lost"


@dataclass
class Track:
    track_id: int
    box: tuple[float, float, float, float]
    score: float
    state: TrackState = TrackState.TENTATIVE
    hits: int = 1
    age: int = 1
    time_since_update: int = 0
    velocity: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    history: list[tuple[float, float, float, float]] = field(default_factory=list)
    first_seen_ts: float = 0.0
    last_seen_ts: float = 0.0

    @property
    def tracking_confidence(self) -> float:
        """1.0 when freshly updated; decays with missed frames and low hit ratio."""
        hit_ratio = min(1.0, self.hits / max(1, self.age))
        decay = max(0.0, 1.0 - 0.15 * self.time_since_update)
        return round(max(0.0, min(1.0, 0.5 * hit_ratio + 0.5 * self.score)) * decay, 4)

    def predict(self) -> tuple[float, float, float, float]:
        x1, y1, x2, y2 = self.box
        vx1, vy1, vx2, vy2 = self.velocity
        return (x1 + vx1, y1 + vy1, x2 + vx2, y2 + vy2)

    def update(self, det: Detection, ts: float) -> None:
        prev = self.box
        alpha = 0.6  # velocity smoothing
        new_v = tuple(det.box[i] - prev[i] for i in range(4))
        self.velocity = tuple(alpha * new_v[i] + (1 - alpha) * self.velocity[i] for i in range(4))  # type: ignore[assignment]
        self.box = det.box
        self.score = 0.7 * self.score + 0.3 * det.score
        self.hits += 1
        self.time_since_update = 0
        self.last_seen_ts = ts
        self.history.append(det.box)
        if len(self.history) > 60:
            self.history.pop(0)


class IouTracker:
    def __init__(
        self,
        iou_threshold: float = 0.3,
        low_iou_threshold: float = 0.2,
        high_score: float = 0.5,
        max_age: int = 30,
        min_hits: int = 3,
    ) -> None:
        self.iou_threshold = iou_threshold
        self.low_iou_threshold = low_iou_threshold
        self.high_score = high_score
        self.max_age = max_age
        self.min_hits = min_hits
        self._tracks: list[Track] = []
        self._next_id = 1

    @property
    def tracks(self) -> list[Track]:
        return [t for t in self._tracks if t.state != TrackState.LOST]

    @property
    def confirmed(self) -> list[Track]:
        return [t for t in self._tracks if t.state == TrackState.CONFIRMED]

    def update(self, detections: list[Detection], ts: float) -> list[Track]:
        # 1. predict
        for t in self._tracks:
            t.age += 1
            t.time_since_update += 1
            t.box = t.predict()

        active = [t for t in self._tracks if t.state != TrackState.LOST]
        high = [d for d in detections if d.score >= self.high_score]
        low = [d for d in detections if d.score < self.high_score]

        # 2. first association: high-score dets ↔ all active tracks
        unmatched_tracks, unmatched_high = self._associate(active, high, self.iou_threshold, ts)
        # 3. second association: low-score dets ↔ remaining tracks (occlusion recovery)
        unmatched_tracks, _ = self._associate(unmatched_tracks, low, self.low_iou_threshold, ts)

        # 4. lifecycle
        for t in self._tracks:
            if t.time_since_update == 0:
                if t.state == TrackState.TENTATIVE and t.hits >= self.min_hits:
                    t.state = TrackState.CONFIRMED
            elif t.time_since_update > self.max_age or (t.state == TrackState.TENTATIVE and t.time_since_update > 2):
                t.state = TrackState.LOST

        # 5. new tracks from unmatched high-score detections only (low-score noise never spawns tracks)
        for d in unmatched_high:
            self._tracks.append(
                Track(self._next_id, d.box, d.score, first_seen_ts=ts, last_seen_ts=ts, history=[d.box])
            )
            self._next_id += 1

        # 6. prune lost tracks after a grace window so fusion can still read their last state
        self._tracks = [
            t for t in self._tracks if not (t.state == TrackState.LOST and t.time_since_update > self.max_age * 2)
        ]
        return self.tracks

    def _associate(
        self, tracks: list[Track], dets: list[Detection], thresh: float, ts: float
    ) -> tuple[list[Track], list[Detection]]:
        if not tracks or not dets:
            return tracks, dets
        pairs: list[tuple[float, int, int]] = []
        for ti, t in enumerate(tracks):
            for di, d in enumerate(dets):
                v = iou(t.box, d.box)
                if v >= thresh:
                    pairs.append((v, ti, di))
        pairs.sort(reverse=True)
        used_t: set[int] = set()
        used_d: set[int] = set()
        for _, ti, di in pairs:
            if ti in used_t or di in used_d:
                continue
            tracks[ti].update(dets[di], ts)
            if tracks[ti].state == TrackState.LOST:
                tracks[ti].state = TrackState.TENTATIVE
            used_t.add(ti)
            used_d.add(di)
        return [t for i, t in enumerate(tracks) if i not in used_t], [d for i, d in enumerate(dets) if i not in used_d]
