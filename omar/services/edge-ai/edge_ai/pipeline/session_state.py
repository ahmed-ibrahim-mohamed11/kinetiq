"""
Per-global-identity workout state: the glue between zone events, rep counters
and the event stream. Owns local ids (usage/activity/set) that the cloud uses
for idempotent upserts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal
from uuid import UUID, uuid4

from edge_ai.constants import TRACKING_VERIFIED_THRESHOLD
from edge_ai.contracts.events_v1 import (
    EquipmentUsageEndedPayload,
    EquipmentUsageStartedPayload,
    ExerciseActivityEndedPayload,
    ExerciseActivityStartedPayload,
    RepCountedPayload,
    SetCompletedPayload,
    SetStartedPayload,
)
from edge_ai.events.builder import EventBuilder
from edge_ai.fusion.equipment_association import ZoneEvent
from edge_ai.fusion.rep_dedup import RepDeduplicator
from edge_ai.pose.angles import extract_signal
from edge_ai.pose.base import Pose
from edge_ai.reps.biomechanical_squat import BiomechanicalSquatEngine
from edge_ai.reps.profiles import RepProfile
from edge_ai.reps.set_segmenter import SetSegmenter
from edge_ai.reps.state_machine import RepCounter, RepEvent


@dataclass
class ActiveUsage:
    usage_local_id: str
    activity_local_id: str
    equipment_id: str
    zone_id: str
    camera_id: str
    exercise_id: UUID
    profile: RepProfile
    started_at: float
    exercise_slug: str = ""
    # one rep counter per observing camera; dedup merges them
    counters: dict[str, RepCounter] = field(default_factory=dict)
    squat_engines: dict[str, BiomechanicalSquatEngine] = field(default_factory=dict)
    segmenter: SetSegmenter | None = None
    active_seconds: float = 0.0
    last_motion_t: float | None = None


@dataclass
class IdentityWorkoutState:
    global_id: str
    member_id: str | None
    emitter: EventBuilder
    dedup: RepDeduplicator
    exercise_ids: dict[str, UUID]
    profiles: dict[str, RepProfile]
    usage: ActiveUsage | None = None
    tracking_confidence: float = 1.0
    member_name: str | None = None
    live_squat_engine: BiomechanicalSquatEngine = field(default_factory=BiomechanicalSquatEngine)
    _last_live_pose_t: float = -1.0
    _last_live_squat_rep: object | None = None

    # ── zone events → usage/activity lifecycle ───────────────────────────────
    def on_zone_event(self, ev: ZoneEvent) -> None:
        if ev.kind == "usage_started":
            if self.usage is not None:
                if self.usage.equipment_id == ev.zone.equipment_id:
                    return  # second camera sees the same machine → same usage
                self._end_usage(ev.t, "left_zone")
            slug = ev.zone.exercise_slug
            exercise_id = self.exercise_ids.get(slug or "")
            profile = self.profiles.get(slug or "")
            if exercise_id is None or profile is None:
                return  # unknown exercise mapping: occupancy still recorded below without activity
            usage_id = f"u-{uuid4().hex[:12]}"
            activity_id = f"a-{uuid4().hex[:12]}"
            self.usage = ActiveUsage(
                usage_id,
                activity_id,
                ev.zone.equipment_id,
                ev.zone.zone_id,
                ev.zone.camera_id,
                exercise_id,
                profile,
                ev.t,
                exercise_slug=slug or "",
            )
            self.usage.segmenter = SetSegmenter(activity_local_id=activity_id)
            self.emitter.emit(
                "equipment.usage.started",
                EquipmentUsageStartedPayload(
                    usage_local_id=usage_id,
                    global_track_id=self.global_id,
                    member_id=UUID(self.member_id) if self.member_id else None,
                    equipment_id=UUID(ev.zone.equipment_id),
                    zone_id=UUID(ev.zone.zone_id),
                    camera_id=UUID(ev.zone.camera_id),
                    association_confidence=ev.confidence,
                ),
                ev.t,
            )
            # Exercise classification v1 = equipment default exercise with the zone association confidence.
            # Phase 5 replaces this with a temporal classifier; the event shape stays identical.
            self.emitter.emit(
                "exercise.activity.started",
                ExerciseActivityStartedPayload(
                    activity_local_id=activity_id,
                    usage_local_id=usage_id,
                    exercise_id=exercise_id,
                    classification_confidence=min(0.9, ev.confidence),
                    classifier_model_version_id=None,
                ),
                ev.t,
            )
        elif ev.kind == "usage_ended" and self.usage is not None and self.usage.equipment_id == ev.zone.equipment_id:
            # Only end when NO camera still sees the person at this machine.
            self._end_usage(ev.t, ev.reason or "left_zone")

    def _end_usage(self, t: float, reason: Literal["left_zone", "track_lost", "checkout", "timeout"]) -> None:
        u = self.usage
        if u is None:
            return
        if u.segmenter:
            closed = u.segmenter.close(t)
            if closed:
                self._emit_set_event(closed)
        self.emitter.emit(
            "exercise.activity.ended",
            ExerciseActivityEndedPayload(
                activity_local_id=u.activity_local_id, active_seconds=round(u.active_seconds, 2)
            ),
            t,
        )
        self.emitter.emit(
            "equipment.usage.ended", EquipmentUsageEndedPayload(usage_local_id=u.usage_local_id, reason=reason), t
        )
        self.dedup.forget(self.global_id, u.equipment_id)
        self.usage = None

    # ── pose signal → reps → sets ────────────────────────────────────────────
    def on_signal(self, camera_id: str, value: float, confidence: float, t: float) -> None:
        u = self.usage
        if u is None or u.segmenter is None:
            return
        counter = u.counters.get(camera_id)
        if counter is None:
            counter = RepCounter(profile=u.profile)
            u.counters[camera_id] = counter
        rep = counter.update(value, confidence, t)
        # Active time: accumulate while the signal is moving (any camera observing motion).
        if u.last_motion_t is not None and counter.state.value not in ("unknown", "start"):
            u.active_seconds += max(0.0, min(1.0, t - u.last_motion_t))
        u.last_motion_t = t
        if rep is None:
            return
        if not self.dedup.observe(self.global_id, u.equipment_id, camera_id, rep.completed_at, rep.confidence):
            return  # another camera already counted this physical rep
        u.segmenter.tracking_verified = self.tracking_confidence >= TRACKING_VERIFIED_THRESHOLD
        for ev in u.segmenter.on_rep(rep):
            self._emit_set_event(ev, camera_id=camera_id)

    def update_live_pose(self, pose: Pose, t: float) -> None:
        if abs(t - self._last_live_pose_t) > 1e-6:
            self._last_live_squat_rep = self.live_squat_engine.update(pose, t)
            self._last_live_pose_t = t

    def on_pose(self, camera_id: str, pose: Pose, t: float) -> None:
        self.update_live_pose(pose, t)
        u = self.usage
        if u is None or u.segmenter is None:
            return

        # Check if current exercise is a squat requiring full biomechanical analysis
        is_squat = "squat" in u.exercise_slug.lower() or "squat" in u.profile.signal.lower()
        if is_squat:
            engine = u.squat_engines.get(camera_id)
            if engine is None:
                # Share live_squat_engine with the primary observing camera
                if not u.squat_engines:
                    engine = self.live_squat_engine
                else:
                    engine = BiomechanicalSquatEngine()
                u.squat_engines[camera_id] = engine

            if engine is self.live_squat_engine and abs(t - self._last_live_pose_t) <= 1e-6:
                squat_rep = self._last_live_squat_rep
            else:
                squat_rep = engine.update(pose, t)

            if u.last_motion_t is not None and engine.phase.value not in (
                "no_person",
                "standing_ready",
                "insufficient_visibility",
                "seated_idle",
            ):
                u.active_seconds += max(0.0, min(1.0, t - u.last_motion_t))
            u.last_motion_t = t

            if squat_rep is not None:
                if not self.dedup.observe(self.global_id, u.equipment_id, camera_id, squat_rep.completed_at, squat_rep.confidence):  # type: ignore[attr-defined]
                    return
                u.segmenter.tracking_verified = self.tracking_confidence >= TRACKING_VERIFIED_THRESHOLD
                rep = RepEvent(
                    rep_index=squat_rep.rep_number,  # type: ignore[attr-defined]
                    started_at=squat_rep.started_at,  # type: ignore[attr-defined]
                    completed_at=squat_rep.completed_at,  # type: ignore[attr-defined]
                    duration_s=squat_rep.duration_s,  # type: ignore[attr-defined]
                    confidence=squat_rep.confidence,  # type: ignore[attr-defined]
                    peak_value=squat_rep.min_knee_angle_deg,  # type: ignore[attr-defined]
                )
                for ev in u.segmenter.on_rep(rep):
                    self._emit_set_event(ev, camera_id=camera_id)
            return

        # Default: scalar angle extraction and hysteresis counter
        val, conf = extract_signal(u.profile.signal, pose)
        self.on_signal(camera_id, val, conf, t)

    def tick(self, now: float) -> None:
        u = self.usage
        if u and u.segmenter:
            closed = u.segmenter.tick(now)
            if closed:
                self._emit_set_event(closed)

    def _emit_set_event(self, ev, camera_id: str | None = None) -> None:  # type: ignore[no-untyped-def]
        u = self.usage
        if u is None:
            return
        if ev.kind == "set_started":
            self.emitter.emit(
                "set.started",
                SetStartedPayload(
                    set_local_id=ev.set_local_id,
                    activity_local_id=u.activity_local_id,
                    set_index=ev.set_index,
                    rest_before_seconds=ev.rest_before_seconds,
                ),
                ev.t,
            )
        elif ev.kind == "rep" and ev.rep is not None:
            self.emitter.emit(
                "rep.counted",
                RepCountedPayload(
                    set_local_id=ev.set_local_id,
                    rep_index=ev.rep_count,
                    camera_id=UUID(camera_id or u.camera_id),
                    confidence=ev.rep.confidence,
                ),
                ev.t,
            )
        elif ev.kind == "set_completed":
            self.emitter.emit(
                "set.completed",
                SetCompletedPayload(
                    set_local_id=ev.set_local_id, rep_count=ev.rep_count, confidence=ev.confidence, verified=ev.verified
                ),
                ev.t,
            )

    def on_track_lost(self, t: float) -> None:
        if self.usage is not None:
            self._end_usage(t, "track_lost")
