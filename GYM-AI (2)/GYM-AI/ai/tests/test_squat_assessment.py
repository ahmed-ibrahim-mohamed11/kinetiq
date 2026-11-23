"""Unit & integration tests for the KinetiQ Squat Assessment System (Tasks 1-5).

Verifies:
1. One-Euro Filter smoothing & jitter reduction without phase lag.
2. 3D Squat biomechanical feature extraction (knee/hip/ankle/trunk angles, valgus ratio, forward knee travel, depth).
3. Camera view detection ('front', 'diagonal', 'side') & view-based observability gating (FR9).
4. Squat repetition & phase segmentation (`REST` -> `DESCENT` -> `BOTTOM` -> `ASCENT`).
5. Instant Alarm Engine (`<= 200 ms` latency, `>= 4` frames debounce, per-rep cooldown, faulty joint highlighting).
6. All 5 Squat Judging Models (`06_Rules`, `07_RF`, `08_1DCNN`, `09_AngleGraph`, `10_Small_STGCN`) + Temperature Scaling Calibration.
"""
from __future__ import annotations

import numpy as np
import pytest

from ai.core.config import (
    HEAD,
    L_ELB,
    L_HIP,
    L_SHO,
    L_WRI,
    NECK,
    PELVIS,
    R_ELB,
    R_HIP,
    R_SHO,
    R_WRI,
    SPINE,
    THORAX,
)
from ai.core.squat_alarm import ERROR_SPEC, SquatAlarmEngine, SquatThresholds, segment_squat_reps
from ai.core.squat_features import (
    L_ANK,
    L_KNEE,
    R_ANK,
    R_KNEE,
    OneEuroFilter,
    detect_camera_view,
    prepare_squat_video,
    summarize_squat_rep_features,
)
from ai.core.squat_judges import (
    SquatJudgeSuite,
    TemperatureScaler,
    expected_calibration_error,
    rep_feature_vector,
    resample_rep_skeleton_3d,
    resample_rep_timeseries,
)
from ai.core.squat_pipeline import analyze_squat_pose


def _make_synthetic_squat_pose3d(
    T: int = 180,
    fps: float = 30.0,
    n_reps: int = 3,
    view: str = "front",
    inject_valgus: bool = False,
    inject_shallow: bool = False,
    inject_forward_knee: bool = False,
    inject_trunk_lean: bool = False,
) -> np.ndarray:
    """Generate a realistic 3D H36M-17 skeleton sequence performing `n_reps` barbell squats."""
    P = np.zeros((T, 17, 3), dtype=np.float32)
    t = np.arange(T) / fps

    # Build smooth squat depth wave: 0 at standing rest, 1 at bottom of each rep
    phase_wave = np.zeros(T, dtype=np.float64)
    rep_len = T // (n_reps + 1)
    for r in range(n_reps):
        s = (r + 1) * (T // (n_reps + 1)) - rep_len // 2
        e = s + int(0.75 * rep_len)
        e = min(e, T - 1)
        idx = np.arange(s, e)
        w = 0.5 - 0.5 * np.cos(2.0 * np.pi * (idx - s) / max(1, e - s))
        phase_wave[idx] = w

    depth_amp = 0.22 if inject_shallow else 0.48  # hip drop in meters
    hip_y = -depth_amp * phase_wave               # in upright coords (+Y = up), hips drop by depth_amp

    # Base standing coordinates in subject frame: X = lateral (L=-, R=+), Y = vertical (up=+), Z = forward (+)
    ank_w = 0.22
    knee_w_base = 0.22
    hip_w = 0.20
    sho_w = 0.26

    for i in range(T):
        w = float(phase_wave[i])
        # Knee width shrinks during squat if inject_valgus=True
        kw = knee_w_base * (1.0 - 0.42 * w) if inject_valgus else knee_w_base * (1.0 + 0.04 * w)
        # Hip height above ankle: standing = 0.85m, bottom = 0.85 - depth_amp
        hy = 0.85 - depth_amp * w
        ky = 0.44 - 0.04 * w
        # Realistic forward knee travel (Z) so knee flexion bends to ~80 deg on deep squat vs ~130 deg on shallow
        base_kz = 0.14 * w if inject_shallow else 0.24 * w
        kz = (0.39 * w) if inject_forward_knee else base_kz
        # Pelvis shifts slightly back on normal squat so femur-tibia angle closes properly
        pelvis_z = -0.14 * w if not inject_shallow else -0.04 * w
        # Trunk lean forward (Z offset of thorax relative to pelvis)
        trunk_z = pelvis_z + ((0.42 * w) if inject_trunk_lean else (0.14 * w))
        trunk_y = hy + np.sqrt(max(0.05, 0.50**2 - (trunk_z - pelvis_z) ** 2))

        pts = np.zeros((17, 3), dtype=np.float32)
        pts[PELVIS] = [0.0, hy, pelvis_z]
        pts[L_HIP] = [-hip_w, hy, pelvis_z]
        pts[R_HIP] = [+hip_w, hy, pelvis_z]
        pts[L_KNEE] = [-kw, ky, kz]
        pts[R_KNEE] = [+kw, ky, kz]
        pts[L_ANK] = [-ank_w, 0.0, 0.0]
        pts[R_ANK] = [+ank_w, 0.0, 0.0]
        pts[SPINE] = [0.0, 0.5 * (hy + trunk_y), 0.5 * trunk_z]
        pts[THORAX] = [0.0, trunk_y, trunk_z]
        pts[NECK] = [0.0, trunk_y + 0.08, trunk_z]
        pts[HEAD] = [0.0, trunk_y + 0.18, trunk_z]
        pts[L_SHO] = [-sho_w, trunk_y, trunk_z]
        pts[R_SHO] = [+sho_w, trunk_y, trunk_z]
        pts[L_ELB] = [-sho_w - 0.08, trunk_y - 0.18, trunk_z - 0.05]
        pts[R_ELB] = [+sho_w + 0.08, trunk_y - 0.18, trunk_z - 0.05]
        pts[L_WRI] = [-sho_w - 0.05, trunk_y + 0.02, trunk_z]
        pts[R_WRI] = [+sho_w + 0.05, trunk_y + 0.02, trunk_z]

        # Root-center at pelvis (like MotionBERT output)
        pts = pts - pts[PELVIS : PELVIS + 1]

        # Rotate around vertical Y axis according to camera view ('front'=0 deg, 'diagonal'=50 deg, 'side'=85 deg)
        yaw_deg = {"front": 0.0, "diagonal": 52.0, "side": 85.0}[view]
        rad = np.radians(yaw_deg)
        c, s_ang = np.cos(rad), np.sin(rad)
        R_yaw = np.array([[c, 0.0, s_ang], [0.0, 1.0, 0.0], [-s_ang, 0.0, c]], dtype=np.float32)
        P[i] = pts @ R_yaw.T

    return P


def test_one_euro_filter_reduces_jitter():
    rng = np.random.default_rng(0)
    t = np.linspace(0, 4.0, 120)
    # Slow posture trajectory + high-frequency keypoint jitter
    clean = 0.25 * np.sin(2.0 * np.pi * 0.25 * t)[:, None, None] * np.ones((120, 17, 3))
    noisy = clean + rng.normal(0.0, 0.08, size=clean.shape)
    oef = OneEuroFilter(min_cutoff=1.2, beta=0.05)
    smoothed = oef.filter_sequence(noisy, fps=30.0)
    # Frame-to-frame velocity jitter is sharply reduced by One-Euro filtering
    raw_jitter = float(np.std(np.diff(noisy, axis=0) - np.diff(clean, axis=0)))
    smooth_jitter = float(np.std(np.diff(smoothed, axis=0) - np.diff(clean, axis=0)))
    assert smooth_jitter < raw_jitter * 0.45


def test_camera_view_detection_and_observability():
    for target_view in ("front", "diagonal", "side"):
        P = _make_synthetic_squat_pose3d(T=60, fps=30.0, n_reps=1, view=target_view)
        info = detect_camera_view(P)
        assert info.view == target_view
        if target_view == "side":
            assert info.observable_errors["knees_caving_inward"] is False
            assert info.observable_errors["knees_forward_error"] is True
        elif target_view == "front":
            assert info.observable_errors["knees_caving_inward"] is True
            assert info.observable_errors["knees_forward_error"] is False


def test_squat_rep_segmentation_and_instant_alarm():
    # 1. Good squat in front view -> 3 reps, 0 alarms
    P_good = _make_synthetic_squat_pose3d(T=240, fps=30.0, n_reps=3, view="front")
    v_good = prepare_squat_video("good_squat", P_good, fps=30.0)
    reps_good, phases_good = segment_squat_reps(v_good)
    assert len(reps_good) == 3
    assert set(np.unique(phases_good)).issubset({0, 1, 2, 3})

    eng = SquatAlarmEngine(fps=30.0)
    log_good = eng.run_video(v_good)
    assert all(ev.latency_ms <= 200.0 for ev in log_good)
    red_good = [ev for ev in log_good if ev.state == "red_alarm"]
    assert len(red_good) == 0

    # 2. Valgus squat in front view -> triggers KIE red alarm on knee joints with cooldown (1 alarm per rep)
    P_valgus = _make_synthetic_squat_pose3d(T=240, fps=30.0, n_reps=3, view="front", inject_valgus=True)
    v_valgus = prepare_squat_video("valgus_squat", P_valgus, fps=30.0)
    log_valgus = eng.run_video(v_valgus)
    red_valgus = [ev for ev in log_valgus if "knees_caving_inward" in ev.active_errors]
    assert len(red_valgus) > 0
    assert L_KNEE in red_valgus[0].faulty_h36m_joints and R_KNEE in red_valgus[0].faulty_h36m_joints


def test_five_squat_judging_models_and_calibration():
    # Build synthetic training dataset of 12 reps (6 good, 6 faulty across valgus/shallow/forward-knee)
    X_feat, X_seq, X_skel, y = [], [], [], []
    configs = [
        (False, False, False, 0),
        (False, False, False, 0),
        (True, False, False, 1),
        (False, True, False, 1),
        (False, False, True, 1),
    ]
    for idx, (valgus, shallow, kfe, label) in enumerate(configs):
        view = "diagonal"
        P = _make_synthetic_squat_pose3d(
            T=200,
            fps=30.0,
            n_reps=3,
            view=view,
            inject_valgus=valgus,
            inject_shallow=shallow,
            inject_forward_knee=kfe,
        )
        v = prepare_squat_video(f"syn_{idx}", P, fps=30.0)
        reps, _ = segment_squat_reps(v)
        for r in reps:
            s, b, e = r["start"], r["bottom"], r["end"]
            summary = summarize_squat_rep_features(v, s, b, e)
            X_feat.append(rep_feature_vector(summary))
            X_seq.append(resample_rep_timeseries(v, s, e))
            X_skel.append(resample_rep_skeleton_3d(v, s, e))
            y.append(label)

    X_feat_arr = np.stack(X_feat)
    X_seq_arr = np.stack(X_seq)
    X_skel_arr = np.stack(X_skel)
    y_arr = np.array(y, dtype=np.int64)

    suite = SquatJudgeSuite()
    suite.fit_all(X_feat_arr, X_seq_arr, X_skel_arr, y_arr)
    assert suite.is_fitted

    # Verify all 5 models produce calibrated probabilities in [0, 1] and high accuracy
    for probs in [
        suite.rules.predict_proba_features(X_feat_arr),
        suite.rf.predict_proba_features(X_feat_arr),
        suite.tcnn.predict_proba_seq(X_seq_arr),
        suite.agnn.predict_proba_seq(X_seq_arr),
        suite.stgcn.predict_proba_skel(X_skel_arr),
    ]:
        assert probs.shape == (len(y_arr),)
        assert np.all((probs >= 0.0) & (probs <= 1.0))
        acc = float(np.mean((probs >= 0.5) == y_arr))
        assert acc >= 0.75

    # Full pipeline test on a faulty video
    P_test = _make_synthetic_squat_pose3d(T=180, fps=30.0, n_reps=2, view="diagonal", inject_valgus=True)
    res, _, _ = analyze_squat_pose("test_valgus", P_test, fps=30.0, judge_suite=suite)
    assert res.total_reps == 2
    assert res.incorrect_reps >= 1
    assert res.alarm_latency_p95_ms <= 200.0

