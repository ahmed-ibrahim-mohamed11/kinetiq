"""Tests for BiomechanicalSquatEngine validating genuine joint geometry and false-rep rejection."""

import math
import pytest
from edge_ai.pose.base import Keypoint, Pose
from edge_ai.reps.biomechanical_squat import BiomechanicalSquatEngine, SquatPhase


def make_pose_at_knee_angle(knee_angle_deg: float, trunk_lean_deg: float = 0.0, hip_x: float = 240.0) -> Pose:
    """Construct an anatomically valid COCO-17 Pose with exact knee angle."""
    hip_y = 180.0
    thigh_len = 80.0
    shin_len = 80.0

    # True geometric flexion
    alpha = math.radians(180.0 - knee_angle_deg)
    thigh_a = alpha * 0.55
    shin_a = -alpha * 0.45

    knee_x = hip_x + thigh_len * math.sin(thigh_a)
    knee_y = hip_y + thigh_len * math.cos(thigh_a)

    ankle_x = knee_x + shin_len * math.sin(shin_a)
    ankle_y = knee_y + shin_len * math.cos(shin_a)

    # Shoulder position: trunk lean relative to vertical
    torso_len = 70.0
    trunk_rad = math.radians(trunk_lean_deg)
    sho_x = hip_x + torso_len * math.sin(trunk_rad)
    sho_y = hip_y - torso_len * math.cos(trunk_rad)

    return Pose(
        keypoints=(
            Keypoint(sho_x, sho_y - 20, 0.9),  # 0 nose
            Keypoint(sho_x - 5, sho_y - 25, 0.9), # 1 left_eye
            Keypoint(sho_x + 5, sho_y - 25, 0.9), # 2 right_eye
            Keypoint(sho_x - 10, sho_y - 20, 0.9), # 3 left_ear
            Keypoint(sho_x + 10, sho_y - 20, 0.9), # 4 right_ear
            Keypoint(sho_x - 20, sho_y, 0.95), # 5 left_shoulder
            Keypoint(sho_x + 20, sho_y, 0.95), # 6 right_shoulder
            Keypoint(sho_x - 25, sho_y + 35, 0.9), # 7 left_elbow
            Keypoint(sho_x + 25, sho_y + 35, 0.9), # 8 right_elbow
            Keypoint(sho_x - 25, sho_y + 70, 0.9), # 9 left_wrist
            Keypoint(sho_x + 25, sho_y + 70, 0.9), # 10 right_wrist
            Keypoint(hip_x - 18, hip_y, 0.95), # 11 left_hip
            Keypoint(hip_x + 18, hip_y, 0.95), # 12 right_hip
            Keypoint(knee_x - 18, knee_y, 0.95), # 13 left_knee
            Keypoint(knee_x + 18, knee_y, 0.95), # 14 right_knee
            Keypoint(ankle_x - 18, ankle_y, 0.95), # 15 left_ankle
            Keypoint(ankle_x + 18, ankle_y, 0.95), # 16 right_ankle
        )
    )


def test_exact_4_squats_count_exactly_4_reps():
    """Four clean physical squat cycles must produce exactly 4 counted reps."""
    engine = BiomechanicalSquatEngine()
    t = 0.0

    # Trainee stands upright for 1s
    for _ in range(10):
        t += 0.1
        engine.update(make_pose_at_knee_angle(175.0), t)
    assert engine.phase == SquatPhase.STANDING_READY
    assert engine.completed_reps == 0

    reps_completed = []

    # Perform 4 distinct squat cycles
    for rep_i in range(4):
        # 1. Descent: 175° -> 90° over 1.2s
        for angle in [160.0, 140.0, 120.0, 100.0, 90.0]:
            t += 0.2
            res = engine.update(make_pose_at_knee_angle(angle), t)
            if res: reps_completed.append(res)
        assert engine.phase == SquatPhase.BOTTOM_CONFIRMED

        # 2. Hold bottom briefly
        t += 0.1
        engine.update(make_pose_at_knee_angle(90.0), t)

        # 3. Ascent: 90° -> 175° over 1.2s
        for angle in [105.0, 125.0, 145.0, 160.0, 175.0]:
            t += 0.2
            res = engine.update(make_pose_at_knee_angle(angle), t)
            if res: reps_completed.append(res)

        # 4. Stand between reps (1.0s refractory)
        for _ in range(5):
            t += 0.2
            res = engine.update(make_pose_at_knee_angle(175.0), t)
            if res: reps_completed.append(res)

    assert engine.completed_reps == 4
    assert engine.valid_reps == 4
    assert engine.imperfect_reps == 0
    assert len(reps_completed) == 4
    assert [r.rep_number for r in reps_completed] == [1, 2, 3, 4]


def test_seated_desk_operator_produces_zero_reps():
    """A seated person whose knees stay bent at 90° with slight torso jitter must never count a squat."""
    engine = BiomechanicalSquatEngine()
    t = 0.0

    # 5 seconds of seated desk movement (knee static around 90-95°, upper body leaning)
    for _ in range(50):
        t += 0.1
        lean = 5.0 + 10.0 * math.sin(t * 2)
        knee = 92.0 + 3.0 * math.cos(t)
        res = engine.update(make_pose_at_knee_angle(knee, trunk_lean_deg=lean), t)
        assert res is None

    assert engine.completed_reps == 0
    assert engine.rejected_cycles == 0


def test_upper_body_vertical_bobbing_without_knee_flexion_produces_zero_reps():
    """Standing person nodding or bending forward (knees straight at 170°) produces 0 reps."""
    engine = BiomechanicalSquatEngine()
    t = 0.0

    for _ in range(30):
        t += 0.1
        lean = 25.0 * math.sin(t * 3) # intense trunk bowing
        res = engine.update(make_pose_at_knee_angle(172.0, trunk_lean_deg=lean), t)
        assert res is None

    assert engine.completed_reps == 0


def test_partial_shallow_squat_rejected():
    """A shallow squat that descends to 125° and returns without reaching bottom depth (<118°) is marked partial."""
    engine = BiomechanicalSquatEngine()
    t = 0.0

    # Stand
    for _ in range(5):
        t += 0.1
        engine.update(make_pose_at_knee_angle(175.0), t)

    # Shallow descent: 175° -> 125° -> 175°
    for angle in [160.0, 140.0, 125.0, 140.0, 160.0, 175.0]:
        t += 0.2
        res = engine.update(make_pose_at_knee_angle(angle), t)
        assert res is None

    assert engine.completed_reps == 0
    assert engine.partial_reps == 1


def test_insufficient_visibility_pauses_counting():
    """When knee and ankle landmarks are missing, engine pauses and produces no ghost reps."""
    engine = BiomechanicalSquatEngine()
    t = 0.0

    # Half pose: only head and shoulders visible (e.g. laptop webcam looking at desk)
    half_pose = Pose(
        keypoints=(
            Keypoint(240, 50, 0.9), Keypoint(235, 45, 0.9), Keypoint(245, 45, 0.9),
            Keypoint(230, 50, 0.9), Keypoint(250, 50, 0.9),
            Keypoint(200, 100, 0.9), Keypoint(280, 100, 0.9),
            Keypoint(0, 0, 0.0), Keypoint(0, 0, 0.0), Keypoint(0, 0, 0.0), Keypoint(0, 0, 0.0),
            Keypoint(0, 0, 0.0), Keypoint(0, 0, 0.0), Keypoint(0, 0, 0.0), Keypoint(0, 0, 0.0),
            Keypoint(0, 0, 0.0), Keypoint(0, 0, 0.0),
        )
    )

    for _ in range(20):
        t += 0.1
        res = engine.update(half_pose, t)
        assert res is None
        assert engine.phase == SquatPhase.INSUFFICIENT_VISIBILITY

    assert engine.completed_reps == 0


def test_seated_idle_phase_reached_and_recovers_on_standing():
    """Seated operator transitions to SEATED_IDLE after 1.5s and only enters STANDING_READY after standing."""
    engine = BiomechanicalSquatEngine()
    t = 0.0
    for _ in range(20):
        t += 0.1
        res = engine.update(make_pose_at_knee_angle(92.0), t)
        assert res is None
    assert engine.phase == SquatPhase.SEATED_IDLE
    assert engine.completed_reps == 0

    # Standing up transitions cleanly to STANDING_READY without counting a phantom rep
    for _ in range(5):
        t += 0.1
        res = engine.update(make_pose_at_knee_angle(175.0), t)
        assert res is None
    assert engine.phase == SquatPhase.STANDING_READY
    assert engine.completed_reps == 0


def test_identity_workout_state_dispatches_squat_slug_to_biomechanical_engine(tmp_path):
    """P0 regression test: IdentityWorkoutState.on_pose must route exercise_slug='squat' to BiomechanicalSquatEngine."""
    from uuid import uuid4
    from edge_ai.events.builder import EventBuilder
    from edge_ai.events.queue import PersistentEventQueue
    from edge_ai.fusion.equipment_association import ZoneSpec, ZoneEvent
    from edge_ai.fusion.rep_dedup import RepDeduplicator
    from edge_ai.pipeline.session_state import IdentityWorkoutState
    from edge_ai.reps.profiles import RepProfile

    cam_id = str(uuid4())
    eq_id = str(uuid4())
    zone_id = str(uuid4())
    ex_id = uuid4()

    q = PersistentEventQueue(tmp_path / "events.db")
    emitter = EventBuilder(
        queue=q,
        device_id=uuid4(),
        branch_id=uuid4(),
    )
    squat_profile = RepProfile(signal="knee_angle_mean", top_threshold=160.0, bottom_threshold=100.0)
    state = IdentityWorkoutState(
        global_id="g-test-1",
        member_id=None,
        emitter=emitter,
        dedup=RepDeduplicator(),
        exercise_ids={"squat": ex_id},
        profiles={"squat": squat_profile},
    )
    zone = ZoneSpec(
        zone_id=zone_id,
        camera_id=cam_id,
        equipment_id=eq_id,
        exercise_slug="squat",
        polygon=((0.0, 0.0), (500.0, 0.0), (500.0, 500.0), (0.0, 500.0)),
    )
    state.on_zone_event(ZoneEvent(kind="usage_started", identity="g-test-1", zone=zone, t=0.0, confidence=0.95))
    assert state.usage is not None
    assert state.usage.exercise_slug == "squat"

    t = 0.0
    for _ in range(5):
        t += 0.1
        state.on_pose(cam_id, make_pose_at_knee_angle(175.0), t)

    # Verify BiomechanicalSquatEngine was instantiated (and NOT generic RepCounter)
    assert cam_id in state.usage.squat_engines
    assert cam_id not in state.usage.counters

