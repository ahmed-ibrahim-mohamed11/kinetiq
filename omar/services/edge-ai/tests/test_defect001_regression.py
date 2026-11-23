"""Regression tests for DEFECT-001 ensuring BiomechanicalSquatEngine and orchestrator live person state API compatibility."""

from edge_ai.pose.base import Keypoint, Pose
from edge_ai.reps.biomechanical_squat import BiomechanicalSquatEngine, SquatRepResult, SquatPhase
from edge_ai.reps.set_segmenter import SetSegmenter


def test_squat_rep_result_properties():
    res = SquatRepResult(
        rep_number=1,
        started_at=10.0,
        completed_at=12.0,
        duration_s=2.0,
        verdict="correct",
        min_knee_angle_deg=88.5,
        max_trunk_lean_deg=15.2,
        min_valgus_ratio=0.92,
        primary_error=None,
        detected_errors=[],
        confidence=0.95,
    )
    assert res.is_valid is True
    assert res.quality == "correct"
    assert res.form_verdict == "RIGHT FORM"
    assert res.primary_feedback == "Good form"

    res_fault = SquatRepResult(
        rep_number=2,
        started_at=13.0,
        completed_at=15.0,
        duration_s=2.0,
        verdict="incorrect",
        min_knee_angle_deg=120.0,
        max_trunk_lean_deg=45.0,
        min_valgus_ratio=0.70,
        primary_error="shallow_squat",
        detected_errors=["shallow_squat", "excessive_trunk_lean"],
        confidence=0.88,
    )
    assert res_fault.is_valid is False
    assert res_fault.quality == "incorrect"
    assert res_fault.form_verdict == "WRONG FORM"
    assert res_fault.primary_feedback == "shallow_squat"


def test_biomechanical_squat_engine_properties():
    eng = BiomechanicalSquatEngine()
    # Initially no person
    assert eng.current_knee_angle is None
    assert eng.current_trunk_angle is None
    assert eng.active_errors == []
    assert eng.rep_history == []

    # Update with standing posture
    pose = Pose(
        keypoints=(
            Keypoint(100, 50, 0.9), Keypoint(95, 45, 0.9), Keypoint(105, 45, 0.9),
            Keypoint(90, 50, 0.9), Keypoint(110, 50, 0.9),
            Keypoint(80, 70, 0.95), Keypoint(120, 70, 0.95),
            Keypoint(75, 105, 0.9), Keypoint(125, 105, 0.9),
            Keypoint(75, 140, 0.9), Keypoint(125, 140, 0.9),
            Keypoint(82, 140, 0.95), Keypoint(118, 140, 0.95),
            Keypoint(82, 220, 0.95), Keypoint(118, 220, 0.95),
            Keypoint(82, 300, 0.95), Keypoint(118, 300, 0.95),
        )
    )
    eng.update(pose, t=1.0)
    assert eng.phase == SquatPhase.STANDING_READY
    assert eng.current_knee_angle is not None
    assert eng.current_knee_angle >= 170.0
    assert eng.current_trunk_angle is not None
    assert isinstance(eng.active_errors, list)
    assert isinstance(eng.rep_history, list)


def test_set_segmenter_index_access():
    seg = SetSegmenter(activity_local_id="act-1")
    assert seg.set_index == 0
    assert seg._set_index == 0

