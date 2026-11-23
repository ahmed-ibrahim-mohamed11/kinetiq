"""
Cross-camera identity fusion.

Each camera's tracker emits *local* tracks. The registry maps them to *global*
identities using:

  1. Appearance: cosine similarity of re-identification embeddings (when a ReID
     model is loaded; embeddings are optional so the pipeline degrades
     gracefully to spatial/temporal cues only).
  2. Space: optional floor-plan position (via camera homography) — two people
     cannot be 8 metres apart at the same instant.
  3. Time: a global identity that vanished more than TRACK_REASSOCIATE_SECONDS
     ago is not a re-association candidate.

Safety rule (from the spec): never merge on an ambiguous match. If the best and
second-best candidates are closer than `ambiguity_margin`, the local track gets a
NEW global identity with identity_confidence 0 and an `identity_ambiguous`
uncertainty flag. A wrong merge attributes reps to the wrong member, which is
worse than a split track.

Member binding happens only at the entrance (face/QR) and is carried by the
global identity; floor cameras never identify faces.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from uuid import uuid4

from edge_ai.constants import IDENTITY_BIND_THRESHOLD, TRACK_REASSOCIATE_SECONDS


@dataclass
class LocalObservation:
    camera_id: str
    local_track_id: int
    t: float
    embedding: list[float] | None = None
    floor_xy: tuple[float, float] | None = None
    tracking_confidence: float = 1.0


@dataclass
class GlobalIdentity:
    global_id: str
    created_at: float
    last_seen_at: float
    member_id: str | None = None
    identity_confidence: float = 0.0
    embedding: list[float] | None = None
    floor_xy: tuple[float, float] | None = None
    # (camera_id, local_track_id) currently bound
    local_tracks: set[tuple[str, int]] = field(default_factory=set)
    ambiguous: bool = False

    def bind_member(self, member_id: str, confidence: float) -> bool:
        if confidence < IDENTITY_BIND_THRESHOLD:
            return False
        if self.member_id is not None and self.member_id != member_id:
            # Two different members claimed on one body — refuse and flag.
            self.ambiguous = True
            return False
        self.member_id = member_id
        self.identity_confidence = max(self.identity_confidence, confidence)
        return True


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na > 0 and nb > 0 else 0.0


@dataclass
class GlobalIdentityRegistry:
    appearance_threshold: float = 0.55
    ambiguity_margin: float = 0.08
    max_floor_distance_m: float = 3.0
    reassociate_seconds: float = TRACK_REASSOCIATE_SECONDS
    embedding_momentum: float = 0.9

    _identities: dict[str, GlobalIdentity] = field(default_factory=dict)
    _local_to_global: dict[tuple[str, int], str] = field(default_factory=dict)
    merges_refused: int = 0

    # ── queries ──────────────────────────────────────────────────────────────
    def get(self, global_id: str) -> GlobalIdentity | None:
        return self._identities.get(global_id)

    def global_for_local(self, camera_id: str, local_track_id: int) -> GlobalIdentity | None:
        gid = self._local_to_global.get((camera_id, local_track_id))
        return self._identities.get(gid) if gid else None

    def active(self, now: float | None = None) -> list[GlobalIdentity]:
        now = now if now is not None else time.time()
        return [g for g in self._identities.values() if now - g.last_seen_at <= self.reassociate_seconds]

    # ── core ─────────────────────────────────────────────────────────────────
    def observe(self, obs: LocalObservation) -> tuple[GlobalIdentity, bool]:
        """Associate a local observation. Returns (identity, is_new_identity)."""
        key = (obs.camera_id, obs.local_track_id)
        gid = self._local_to_global.get(key)
        if gid and gid in self._identities:
            g = self._identities[gid]
            self._touch(g, obs)
            return g, False

        candidates = self._score_candidates(obs)
        if candidates:
            best_score, best = candidates[0]
            second = candidates[1][0] if len(candidates) > 1 else -1.0
            if best_score >= self.appearance_threshold and best_score - second >= self.ambiguity_margin:
                best.local_tracks.add(key)
                self._local_to_global[key] = best.global_id
                self._touch(best, obs)
                return best, False
            if best_score >= self.appearance_threshold:
                self.merges_refused += 1
                g = self._new_identity(obs, ambiguous=True)
                return g, True
        g = self._new_identity(obs, ambiguous=False)
        return g, True

    def end_local_track(self, camera_id: str, local_track_id: int) -> GlobalIdentity | None:
        key = (camera_id, local_track_id)
        gid = self._local_to_global.pop(key, None)
        if not gid:
            return None
        g = self._identities.get(gid)
        if g:
            g.local_tracks.discard(key)
        return g

    def expire(self, now: float) -> list[GlobalIdentity]:
        """Drop identities unseen for longer than the re-association window. Returns those dropped."""
        gone = [
            g
            for g in self._identities.values()
            if now - g.last_seen_at > self.reassociate_seconds and not g.local_tracks
        ]
        for g in gone:
            self._identities.pop(g.global_id, None)
        return gone

    # ── internals ────────────────────────────────────────────────────────────
    def _new_identity(self, obs: LocalObservation, ambiguous: bool) -> GlobalIdentity:
        g = GlobalIdentity(
            global_id=f"g-{uuid4().hex[:12]}",
            created_at=obs.t,
            last_seen_at=obs.t,
            embedding=list(obs.embedding) if obs.embedding else None,
            floor_xy=obs.floor_xy,
            ambiguous=ambiguous,
        )
        g.local_tracks.add((obs.camera_id, obs.local_track_id))
        self._identities[g.global_id] = g
        self._local_to_global[(obs.camera_id, obs.local_track_id)] = g.global_id
        return g

    def _touch(self, g: GlobalIdentity, obs: LocalObservation) -> None:
        g.last_seen_at = max(g.last_seen_at, obs.t)
        if obs.embedding:
            if g.embedding is None:
                g.embedding = list(obs.embedding)
            else:
                m = self.embedding_momentum
                g.embedding = [m * a + (1 - m) * b for a, b in zip(g.embedding, obs.embedding, strict=False)]
        if obs.floor_xy:
            g.floor_xy = obs.floor_xy

    def _score_candidates(self, obs: LocalObservation) -> list[tuple[float, GlobalIdentity]]:
        scored: list[tuple[float, GlobalIdentity]] = []
        for g in self._identities.values():
            if obs.t - g.last_seen_at > self.reassociate_seconds:
                continue
            # The same camera cannot see one person as two simultaneous tracks.
            if any(cam == obs.camera_id for cam, _ in g.local_tracks):
                continue
            # Spatial gate
            if obs.floor_xy and g.floor_xy:
                d = math.dist(obs.floor_xy, g.floor_xy)
                if d > self.max_floor_distance_m + 1.5 * (obs.t - g.last_seen_at):  # allow ~1.5 m/s walking
                    continue
            if obs.embedding and g.embedding:
                score = cosine(obs.embedding, g.embedding)
            elif obs.floor_xy and g.floor_xy:
                d = math.dist(obs.floor_xy, g.floor_xy)
                score = max(0.0, 1.0 - d / self.max_floor_distance_m) * 0.7  # spatial-only evidence is capped
            else:
                continue  # no evidence → never associate
            scored.append((score, g))
        scored.sort(key=lambda s: s[0], reverse=True)
        return scored
