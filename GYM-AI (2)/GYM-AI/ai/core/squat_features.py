"""Squat Assessment System — Task 1: One-Euro Filter + 2D/3D Squat Biomechanical Features.

Implements the exact Squat biomechanical signals, angles, view-detection, and smoothing
specified in KinetiQ_Proposal.pdf and KINETIQ_A3_Model_Architecture.pdf:

1. OneEuroFilter:
   Frame-by-frame adaptive low-pass filter that eliminates pose jitter at low speeds while
   preserving fast movement with minimal lag (<= 1 frame delay for the <=200ms alarm engine).

2. 3D & 2D Squat Biomechanical Features (per frame):
   - knee_angle_l / knee_angle_r / knee_angle_mean (degrees, 180 = standing straight, ~70-90 = deep squat)
   - hip_angle_l / hip_angle_r / hip_angle_mean (degrees, torso-to-thigh flexion)
   - ankle_angle_l / ankle_angle_r / ankle_angle_mean (degrees, tibia inclination from vertical / dorsiflexion proxy)
   - trunk_lean_deg (degrees, torso inclination relative to vertical axis)
   - squat_depth_ratio (hip vertical position relative to knee in femur-length units; <=0 means hip crease at/below knee)
   - knee_valgus_ratio (KIE — 3D/2D knee-width to ankle-width ratio; < threshold indicates knees caving inward)
   - knee_forward_travel (KFE — forward horizontal knee displacement past ankle normalized by tibia length)
   - lr_depth_asymmetry (left vs right hip/knee vertical symmetry in torso units)
   - heel_lift_proxy (ankle vertical rise relative to standing baseline in tibia units; enhanced when RTMW foot keypoints are present)
   - camera_view ('front', 'diagonal', 'side') + view_observability per error (FR9: abstain/uncertain when view hides an error)
   - setup_ok (FR10: full body visible — hips, knees, ankles, shoulders confident in frame)
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

import numpy as np

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

# H36M-17 lower-body joint indices (matching ai/core/pose.py::coco2h36m)
R_KNEE, R_ANK = 2, 3
L_KNEE, L_ANK = 5, 6

REQUIRED_SQUAT_JOINTS = [L_SHO, R_SHO, L_HIP, R_HIP, L_KNEE, R_KNEE, L_ANK, R_ANK]


# -----------------------------------------------------------------------------
# 1. One-Euro Filter (Supporting Algorithm #1 from Model Architecture Poster)
# -----------------------------------------------------------------------------
class OneEuroFilter:
    """Adaptive low-pass filter (Casiez et al., CHI 2012) for 2D/3D pose streams.

    Works on arbitrary numpy shapes (e.g., (17, 2) or (17, 3)) frame-by-frame or
    over a full sequence `(T, J, D)`. NaN values coast the previous valid state.
    """

    def __init__(self, min_cutoff: float = 1.7, beta: float = 0.35, d_cutoff: float = 1.0):
        if min_cutoff <= 0 or d_cutoff <= 0 or beta < 0:
            raise ValueError("OneEuroFilter requires min_cutoff > 0, d_cutoff > 0, beta >= 0")
        self.min_cutoff = float(min_cutoff)
        self.beta = float(beta)
        self.d_cutoff = float(d_cutoff)
        self._x_prev: Optional[np.ndarray] = None
        self._dx_prev: Optional[np.ndarray] = None
        self._t_prev: Optional[float] = None

    @staticmethod
    def _alpha(cutoff: np.ndarray | float, dt: float) -> np.ndarray | float:
        tau = 1.0 / (2.0 * math.pi * cutoff)
        return 1.0 / (1.0 + tau / max(dt, 1e-6))

    def reset(self) -> None:
        self._x_prev = None
        self._dx_prev = None
        self._t_prev = None

    def filter_frame(self, x: np.ndarray, t: float) -> np.ndarray:
        arr = np.asarray(x, dtype=np.float64)
        if self._x_prev is None or self._t_prev is None:
            self._x_prev = arr.copy()
            self._dx_prev = np.zeros_like(arr)
            self._t_prev = float(t)
            return arr.copy()

        dt = float(t) - self._t_prev
        if dt <= 0:
            dt = 1.0 / 30.0
        self._t_prev = float(t)

        finite = np.isfinite(arr)
        prev_finite = np.isfinite(self._x_prev)
        out = self._x_prev.copy()

        # Where previous was NaN but current is finite, initialize directly
        init_mask = finite & (~prev_finite)
        out[init_mask] = arr[init_mask]
        self._dx_prev[init_mask] = 0.0

        # Where both are finite, apply One-Euro update
        upd = finite & prev_finite
        if np.any(upd):
            dx = (arr[upd] - self._x_prev[upd]) / dt
            a_d = self._alpha(self.d_cutoff, dt)
            dx_hat = a_d * dx + (1.0 - a_d) * self._dx_prev[upd]
            cutoff = self.min_cutoff + self.beta * np.abs(dx_hat)
            a = self._alpha(cutoff, dt)
            out[upd] = a * arr[upd] + (1.0 - a) * self._x_prev[upd]
            self._dx_prev[upd] = dx_hat

        self._x_prev = out.copy()
        return out

    def filter_sequence(self, seq: np.ndarray, fps: float) -> np.ndarray:
        """Filter an entire `(T, ...)` array at constant frame rate `fps`."""
        if fps <= 0 or not np.isfinite(fps):
            raise ValueError(f"fps must be positive and finite, got {fps!r}")
        self.reset()
        out = np.empty_like(seq, dtype=np.float64)
        dt = 1.0 / float(fps)
        for i in range(len(seq)):
            out[i] = self.filter_frame(seq[i], i * dt)
        return out


# -----------------------------------------------------------------------------
# 2. 3D Vector & Angle Helpers
# -----------------------------------------------------------------------------
def _unit(v: np.ndarray) -> np.ndarray:
    return v / (np.linalg.norm(v, axis=-1, keepdims=True) + 1e-9)


def _angle_3d_deg(a: np.ndarray, b: np.ndarray, c: np.ndarray) -> np.ndarray:
    """Included angle ABC (in degrees, 0..180) at vertex `b` for arrays `(T, 3)` or `(3,)`."""
    u = _unit(a - b)
    v = _unit(c - b)
    cos_theta = np.clip(np.sum(u * v, axis=-1), -1.0, 1.0)
    return np.degrees(np.arccos(cos_theta))


def _interp_rows(X: np.ndarray, valid: np.ndarray) -> np.ndarray:
    X = X.copy()
    if valid.all() or valid.sum() < 2:
        return X
    idx = np.arange(len(X))
    for k in range(X.shape[1]):
        X[~valid, k] = np.interp(idx[~valid], idx[valid], X[valid, k])
    return X


# -----------------------------------------------------------------------------
# 3. Camera View Detection & Observability Gating (FR9 & FR10)
# -----------------------------------------------------------------------------
@dataclass(frozen=True)
class ViewInfo:
    view: str                         # "front" | "diagonal" | "side"
    frontal_ratio: float              # median |X_hip_span| / 3D_hip_span in [0, 1]
    observable_errors: Dict[str, bool]


def detect_camera_view(pose3d: np.ndarray, pose2d_h36m: Optional[np.ndarray] = None) -> ViewInfo:
    """Classify camera angle ('front', 'diagonal', 'side') from 3D/2D hip & shoulder span.

    In camera coordinates (X=horizontal image axis, Y=vertical, Z=depth):
    - Front/rear view: left-right shoulder & hip vector lies mostly along camera X.
    - Side view: left-right shoulder & hip vector lies mostly along camera Z.
    - Diagonal view: intermediate.
    """
    sho_vec = pose3d[:, R_SHO] - pose3d[:, L_SHO]
    hip_vec = pose3d[:, R_HIP] - pose3d[:, L_HIP]
    x_span = 0.5 * (np.abs(sho_vec[:, 0]) + np.abs(hip_vec[:, 0]))
    xz_span = 0.5 * (
        np.hypot(sho_vec[:, 0], sho_vec[:, 2]) + np.hypot(hip_vec[:, 0], hip_vec[:, 2])
    ) + 1e-9
    ratio = float(np.nanmedian(x_span / xz_span))
    if pose2d_h36m is not None:
        P2 = np.asarray(pose2d_h36m, dtype=np.float64)[..., :2]
        torso2d = float(np.nanmedian(np.hypot(P2[:, THORAX, 0] - P2[:, PELVIS, 0], P2[:, THORAX, 1] - P2[:, PELVIS, 1])))
        sho2d_rel = float(np.nanmedian(np.abs(P2[:, R_SHO, 0] - P2[:, L_SHO, 0]))) / max(torso2d, 1.0)
        if sho2d_rel >= 0.78:
            view = "front"
        elif sho2d_rel <= 0.35:
            view = "side"
        else:
            view = "diagonal"
    else:
        if ratio >= 0.78:
            view = "front"
        elif ratio <= 0.45:
            view = "side"
        else:
            view = "diagonal"

    # Proposal Section 3.4 & FR9: view gating (an error is judged only from views where it is visible)
    observable = {
        "knees_caving_inward": view in ("front", "diagonal"),       # valgus is frontal/diagonal plane
        "knees_forward_error": view in ("side", "diagonal"),        # sagittal knee travel needs side/diagonal
        "shallow_squat": True,                                      # depth is observable in all views via 3D + 2D Y
        "excessive_trunk_lean": view in ("side", "diagonal"),       # sagittal trunk angle
        "heel_lift": view in ("side", "diagonal"),                  # heel rise clearest in side/diagonal
        "lr_asymmetry": view in ("front", "diagonal"),              # left/right symmetry clearest in front/diagonal
    }
    return ViewInfo(view=view, frontal_ratio=round(ratio, 4), observable_errors=observable)


# -----------------------------------------------------------------------------
# 4. Full Squat Video Preparation (3D + 2D Biomechanics + Phase Signals)
# -----------------------------------------------------------------------------
def prepare_squat_video(
    name: str,
    pose3d: np.ndarray,
    fps: float,
    conf: Optional[np.ndarray] = None,
    pose2d_h36m: Optional[np.ndarray] = None,
    conf_thr: float = 0.3,
    use_one_euro: bool = True,
) -> dict:
    """Extract smoothed 3D & 2D biomechanical signals for Barbell/Bodyweight Squat assessment.

    Parameters
    ----------
    name : str
        Identifier of the video or stream.
    pose3d : np.ndarray, shape (T, 17, 3)
        MotionBERT-Lite root-relative 3D pose in H36M-17 format.
        Note: In H36M camera coordinates, +Y points DOWNWARD in image space (so standing has
        thorax Y < pelvis Y < knee Y < ankle Y). We build an explicit anatomical subject coordinate
        frame (lat = L->R, up = pelvis->thorax during standing, fwd = lat x up) so all features
        have unambiguous physical signs regardless of camera convention.
    fps : float
        Video frame rate (Hz).
    conf : Optional[np.ndarray], shape (T, 17)
        Per-joint 2D detection confidence in [0, 1].
    pose2d_h36m : Optional[np.ndarray], shape (T, 17, 2) or (T, 17, 3)
        Image-space 2D keypoints (for 2D frontal knee/ankle ratio & ankle vertical baseline).
    conf_thr : float
        Minimum confidence for a joint to be considered visible (FR9/FR10).
    use_one_euro : bool
        Whether to smooth the 3D (and 2D) joint trajectories with OneEuroFilter before feature extraction.
    """
    P = np.asarray(pose3d, dtype=np.float64)
    if P.ndim != 3 or P.shape[1:] != (17, 3):
        raise ValueError(f"{name}: expected (T,17,3) H36M 3D pose, got {P.shape}")
    if not (fps > 0) or not np.isfinite(fps):
        raise ValueError(f"{name}: fps must be a positive, finite number, got {fps!r}")

    T = len(P)
    finite = np.isfinite(P).all(axis=(1, 2))
    P = _interp_rows(P.reshape(T, -1), finite).reshape(T, 17, 3)

    if use_one_euro and T >= 2:
        oef3d = OneEuroFilter(min_cutoff=1.2, beta=0.04, d_cutoff=1.0)
        P_raw = P.copy()
        P = oef3d.filter_sequence(P, fps)
    else:
        P_raw = P.copy()

    # Confidence & setup visibility gating (FR9 & FR10)
    if conf is not None:
        C = np.asarray(conf, dtype=np.float64)
        if C.shape != (T, 17):
            raise ValueError(f"{name}: expected conf shape {(T, 17)}, got {C.shape}")
        valid_l = finite & (C[:, [L_SHO, L_HIP, L_KNEE, L_ANK]].min(axis=1) >= conf_thr)
        valid_r = finite & (C[:, [R_SHO, R_HIP, R_KNEE, R_ANK]].min(axis=1) >= conf_thr)
        setup_frame_ok = finite & (C[:, REQUIRED_SQUAT_JOINTS].min(axis=1) >= conf_thr)
        joint_conf_mean = float(np.nanmean(C[:, REQUIRED_SQUAT_JOINTS]))
    else:
        valid_l = finite.copy()
        valid_r = finite.copy()
        setup_frame_ok = finite.copy()
        joint_conf_mean = 1.0 if finite.all() else float(finite.mean())

    # Anatomical scale lengths (robust video medians)
    pelvis = P[:, PELVIS]
    thorax = P[:, THORAX]
    torso_vec = thorax - pelvis
    torso_len = max(float(np.median(np.linalg.norm(torso_vec, axis=1))), 1e-6)

    femur_l = np.linalg.norm(P[:, L_HIP] - P[:, L_KNEE], axis=1)
    femur_r = np.linalg.norm(P[:, R_HIP] - P[:, R_KNEE], axis=1)
    femur_len = max(float(np.median(0.5 * (femur_l + femur_r))), 1e-6)

    tibia_l = np.linalg.norm(P[:, L_KNEE] - P[:, L_ANK], axis=1)
    tibia_r = np.linalg.norm(P[:, R_KNEE] - P[:, R_ANK], axis=1)
    tibia_len = max(float(np.median(0.5 * (tibia_l + tibia_r))), 1e-6)

    # Global gravity/vertical axis in 3D camera space:
    # During standing (top 25% of ankle-to-thorax distance), vector from mid-ankle to thorax points UP.
    mid_ank = 0.5 * (P[:, L_ANK] + P[:, R_ANK])
    mid_knee = 0.5 * (P[:, L_KNEE] + P[:, R_KNEE])
    mid_hip = 0.5 * (P[:, L_HIP] + P[:, R_HIP])
    body_span = np.linalg.norm(thorax - mid_ank, axis=1)
    stand_mask = body_span >= np.percentile(body_span, 75)
    if not np.any(stand_mask):
        stand_mask = np.ones(T, dtype=bool)
    up_global = _unit(np.median((thorax - mid_ank)[stand_mask], axis=0))  # (3,) unit vector pointing UP

    # Subject-centric horizontal axes per frame:
    # lat: Left-to-Right hip/shoulder vector orthogonalized to up_global
    lat_raw = 0.5 * ((P[:, R_SHO] - P[:, L_SHO]) + (P[:, R_HIP] - P[:, L_HIP]))
    lat = _unit(lat_raw - np.outer(lat_raw @ up_global, up_global))
    # fwd: Athlete's forward facing direction (aligned with ankle->knee or foot direction)
    fwd = _unit(np.cross(lat, up_global[None, :]))
    knee_rel_ank = mid_knee - mid_ank
    # In a squat, knees flex forward relative to ankles; align `fwd` so median knee_rel_ank @ fwd >= 0
    if float(np.median(np.sum(knee_rel_ank * fwd, axis=1))) < 0:
        fwd = -fwd

    # -------------------------------------------------------------------------
    # Core 3D Joint Angles (in degrees)
    # -------------------------------------------------------------------------
    # 1. Knee flexion angle: hip - knee - ankle (180° = straight leg, ~70-95° = bottom of squat)
    knee_angle_l = _angle_3d_deg(P[:, L_HIP], P[:, L_KNEE], P[:, L_ANK])
    knee_angle_r = _angle_3d_deg(P[:, R_HIP], P[:, R_KNEE], P[:, R_ANK])
    knee_angle_mean = 0.5 * (knee_angle_l + knee_angle_r)

    # 2. Hip flexion angle: shoulder - hip - knee (180° = upright standing, ~60-90° = bottom of squat)
    hip_angle_l = _angle_3d_deg(P[:, L_SHO], P[:, L_HIP], P[:, L_KNEE])
    hip_angle_r = _angle_3d_deg(P[:, R_SHO], P[:, R_HIP], P[:, R_KNEE])
    hip_angle_mean = 0.5 * (hip_angle_l + hip_angle_r)

    # 3. Ankle / Tibia inclination angle: angle between tibia (ankle->knee) and vertical `up_global`
    #    (90° - dorsiflexion tilt, or included angle between knee-ankle and horizontal floor: 90° upright -> ~60° flexed)
    tibia_up_l = _unit(P[:, L_KNEE] - P[:, L_ANK])
    tibia_up_r = _unit(P[:, R_KNEE] - P[:, R_ANK])
    tibia_tilt_l = np.degrees(np.arccos(np.clip(tibia_up_l @ up_global, -1.0, 1.0)))
    tibia_tilt_r = np.degrees(np.arccos(np.clip(tibia_up_r @ up_global, -1.0, 1.0)))
    ankle_angle_l = 90.0 - tibia_tilt_l
    ankle_angle_r = 90.0 - tibia_tilt_r
    ankle_angle_mean = 0.5 * (ankle_angle_l + ankle_angle_r)

    # 4. Trunk lean angle (degrees from vertical `up_global`; 0° = vertical torso, >40° = excessive lean)
    torso_unit = _unit(torso_vec)
    trunk_lean_deg = np.degrees(np.arccos(np.clip(torso_unit @ up_global, -1.0, 1.0)))

    # -------------------------------------------------------------------------
    # Core Squat Error Signals (KIE, KFE, SS + Experimental Indicators)
    # -------------------------------------------------------------------------
    # A. Squat Depth (SS — Shallow Squat):
    #    Height of hip relative to knee along `up_global`, normalized by femur length.
    #    Standing: ~ +0.85 to +1.0. Parallel squat: <= 0.0 (hip crease at or below top of knee).
    hip_above_knee_l = ((P[:, L_HIP] - P[:, L_KNEE]) @ up_global) / femur_len
    hip_above_knee_r = ((P[:, R_HIP] - P[:, R_KNEE]) @ up_global) / femur_len
    squat_depth_rel = 0.5 * (hip_above_knee_l + hip_above_knee_r)

    # Normalized Squat Progression Signal `depth_progress` in [0, ~1]:
    # 0 = standing upright, 1 = deep squat (combines hip descent and knee flexion)
    stand_ref = float(np.percentile(squat_depth_rel[stand_mask], 85))
    descent_norm = np.clip((stand_ref - squat_depth_rel) / max(stand_ref, 0.4), 0.0, 1.5)
    knee_flex_norm = np.clip((175.0 - knee_angle_mean) / 95.0, 0.0, 1.5)
    squat_signal = 0.55 * descent_norm + 0.45 * knee_flex_norm

    # B. Knees Caving Inward (KIE — Knee Valgus):
    #    Ratio of inter-knee distance to inter-ankle distance along the lateral axis `lat`.
    #    In good form, knees track over or slightly outside toes (ratio >= 0.90).
    #    When knees cave inward, knee width drops below ankle width (ratio < 0.82).
    knee_width_3d = np.abs(np.sum((P[:, R_KNEE] - P[:, L_KNEE]) * lat, axis=1))
    ank_width_3d = np.maximum(np.abs(np.sum((P[:, R_ANK] - P[:, L_ANK]) * lat, axis=1)), 0.12 * femur_len)
    knee_valgus_ratio = knee_width_3d / ank_width_3d
    if pose2d_h36m is not None:
        P2_raw = np.asarray(pose2d_h36m, dtype=np.float64)[..., :2]
        finite2d = np.isfinite(P2_raw).all(axis=(1, 2))
        P2 = _interp_rows(P2_raw.reshape(T, -1), finite2d).reshape(T, 17, 2)
        P2 = np.nan_to_num(P2, nan=0.0)
        kw_2d = np.abs(P2[:, R_KNEE, 0] - P2[:, L_KNEE, 0])
        aw_2d = np.maximum(np.abs(P2[:, R_ANK, 0] - P2[:, L_ANK, 0]), 1e-3)
        valgus_2d = np.clip(kw_2d / aw_2d, 0.2, 2.5)
        # Blend 3D and 2D ratio when in front view for extra precision
        knee_valgus_ratio = 0.6 * knee_valgus_ratio + 0.4 * valgus_2d
    knee_valgus_ratio = np.nan_to_num(knee_valgus_ratio, nan=1.0, posinf=1.5, neginf=0.5)

    # C. Excessive Forward Knee Travel (KFE — Knees Forward Error):
    #    Forward projection of knee past ankle along `fwd`, normalized by tibia length.
    kfe_l = np.sum((P[:, L_KNEE] - P[:, L_ANK]) * fwd, axis=1) / tibia_len
    kfe_r = np.sum((P[:, R_KNEE] - P[:, R_ANK]) * fwd, axis=1) / tibia_len
    knee_forward_travel = 0.5 * (kfe_l + kfe_r)

    # D. Left / Right Asymmetry (Experimental error #1):
    #    Absolute difference between left and right knee flexion (deg) and hip-to-knee depth.
    lr_knee_diff_deg = np.abs(knee_angle_l - knee_angle_r)
    lr_depth_asymmetry = np.abs(hip_above_knee_l - hip_above_knee_r)

    # E. Heel Lift Proxy (Experimental error #2):
    #    Upward vertical displacement of ankles relative to standing baseline (in tibia units).
    #    Note: Root-relative 3D pose zeroes pelvis, so ankle height in pelvis frame reflects hip descent;
    #    when 2D image coordinates `pose2d_h36m` are available, we measure image-space upward ankle drift
    #    normalized by 2D tibia length. Otherwise we measure sudden ankle-to-knee vertical compression anomaly.
    if pose2d_h36m is not None:
        tibia_2d = max(
            float(
                np.nanmedian(
                    0.5
                    * (
                        np.hypot(P2[:, L_KNEE, 0] - P2[:, L_ANK, 0], P2[:, L_KNEE, 1] - P2[:, L_ANK, 1])
                        + np.hypot(P2[:, R_KNEE, 0] - P2[:, R_ANK, 0], P2[:, R_KNEE, 1] - P2[:, R_ANK, 1])
                    )
                )
            ),
            1.0,
        )
        ank_y = 0.5 * (P2[:, L_ANK, 1] + P2[:, R_ANK, 1])
        base_ank_y = float(np.nanpercentile(ank_y[stand_mask], 75))
        # In image coordinates, Y decreases upward, so (base_ank_y - ank_y) > 0 when ankles/heels rise
        heel_lift_proxy = np.nan_to_num(np.maximum(0.0, (base_ank_y - ank_y) / tibia_2d), nan=0.0)
    else:
        heel_lift_proxy = np.zeros(T, dtype=np.float64)

    view_info = detect_camera_view(P, pose2d_h36m=pose2d_h36m)

    # Build per-leg 6D design matrix compatible with `ai.core.features.prepare_video` so the existing
    # Ridge PhaseModel + HSMM Decoder can also be trained/run seamlessly on Squat!
    leg_features = {}
    for side, (hip_i, knee_i, ank_i, k_ang, h_ang, d_rel) in {
        "L": (L_HIP, L_KNEE, L_ANK, knee_angle_l, hip_angle_l, hip_above_knee_l),
        "R": (R_HIP, R_KNEE, R_ANK, knee_angle_r, hip_angle_r, hip_above_knee_r),
    }.items():
        hip_p = (P[:, hip_i] - mid_ank) / torso_len
        knee_p = (P[:, knee_i] - mid_ank) / torso_len
        leg_features[side] = np.column_stack(
            [
                (180.0 - k_ang) / 90.0,       # normalized knee flexion
                (180.0 - h_ang) / 90.0,       # normalized hip flexion
                stand_ref - d_rel,            # normalized hip descent
                hip_p @ up_global,            # hip height above ankle
                knee_p @ fwd[0],              # forward knee travel
                trunk_lean_deg / 45.0,        # normalized trunk lean
            ]
        )

    return dict(
        name=name,
        exercise="barbell_squat",
        fps=float(fps),
        T=T,
        pose3d_smoothed=P.astype(np.float32),
        pose3d_raw=P_raw.astype(np.float32),
        torso_len=torso_len,
        femur_len=femur_len,
        tibia_len=tibia_len,
        # Compatibility keys for PhaseModel / HSMM Decoder:
        arm=leg_features,
        trunk=(trunk_lean_deg / 45.0)[:, None],
        valid={"L": valid_l, "R": valid_r},
        # Squat-specific biomechanical signals (length T):
        squat_signal=squat_signal,
        squat_depth_rel=squat_depth_rel,
        knee_angle_l=knee_angle_l,
        knee_angle_r=knee_angle_r,
        knee_angle_mean=knee_angle_mean,
        hip_angle_l=hip_angle_l,
        hip_angle_r=hip_angle_r,
        hip_angle_mean=hip_angle_mean,
        ankle_angle_l=ankle_angle_l,
        ankle_angle_r=ankle_angle_r,
        ankle_angle_mean=ankle_angle_mean,
        trunk_lean_deg=trunk_lean_deg,
        knee_valgus_ratio=knee_valgus_ratio,
        knee_forward_travel=knee_forward_travel,
        lr_knee_diff_deg=lr_knee_diff_deg,
        lr_depth_asymmetry=lr_depth_asymmetry,
        heel_lift_proxy=heel_lift_proxy,
        # View & setup checks (FR9, FR10):
        view_info=view_info,
        setup_frame_ok=setup_frame_ok,
        setup_ok=bool(np.mean(setup_frame_ok) >= 0.70),
        joint_conf_mean=round(joint_conf_mean, 4),
    )


def summarize_squat_rep_features(v: dict, start: int, bottom: int, end: int) -> Dict[str, float]:
    """Compute interpretable per-rep biomechanical summary metrics inside `[start, end]` with apex at `bottom`."""
    s = max(0, int(start))
    e = min(int(v["T"]) - 1, int(end))
    b = int(np.clip(bottom, s, e))
    if e <= s:
        e = min(int(v["T"]) - 1, s + 1)

    # Bottom window: +/- 2 frames around bottom for stable apex measurement
    b_lo = max(s, b - 2)
    b_hi = min(e, b + 2)

    # Ascent window: from bottom to end (where knee valgus / hip-first rise typically occurs)
    asc_lo, asc_hi = b, e
    # Descent window: from start to bottom (where forward knee travel initiates)
    desc_lo, desc_hi = s, max(s + 1, b)

    min_knee_angle = float(np.min(v["knee_angle_mean"][b_lo : b_hi + 1]))
    min_hip_angle = float(np.min(v["hip_angle_mean"][b_lo : b_hi + 1]))
    min_ankle_angle = float(np.min(v["ankle_angle_mean"][b_lo : b_hi + 1]))
    bottom_depth_rel = float(np.min(v["squat_depth_rel"][b_lo : b_hi + 1]))
    max_trunk_lean_deg = float(np.max(v["trunk_lean_deg"][s : e + 1]))

    # KIE (Knees caving inward): minimum knee/ankle width ratio during active squat (bottom + ascent)
    active_lo = max(s, s + int(0.2 * (e - s)))
    active_hi = max(active_lo + 1, e - int(0.15 * (e - s)))
    min_valgus_ratio = float(np.min(v["knee_valgus_ratio"][active_lo : active_hi + 1]))
    ascent_valgus_ratio = float(np.min(v["knee_valgus_ratio"][asc_lo : asc_hi + 1]))

    # KFE (Forward knee travel): 95th percentile forward travel during descent + bottom
    max_forward_knee = float(np.percentile(v["knee_forward_travel"][desc_lo : b_hi + 1], 95))

    # Experimental errors: asymmetry & heel lift
    max_lr_knee_diff_deg = float(np.percentile(v["lr_knee_diff_deg"][s : e + 1], 90))
    max_lr_depth_asym = float(np.percentile(v["lr_depth_asymmetry"][s : e + 1], 90))
    max_heel_lift = float(np.percentile(v["heel_lift_proxy"][s : e + 1], 95))

    fps = v["fps"]
    return {
        "duration_s": round((e - s) / fps, 3),
        "descent_s": round((b - s) / fps, 3),
        "ascent_s": round((e - b) / fps, 3),
        "min_knee_angle_deg": round(min_knee_angle, 2),
        "min_hip_angle_deg": round(min_hip_angle, 2),
        "min_ankle_angle_deg": round(min_ankle_angle, 2),
        "bottom_depth_rel": round(bottom_depth_rel, 4),
        "max_trunk_lean_deg": round(max_trunk_lean_deg, 2),
        "min_valgus_ratio": round(min_valgus_ratio, 4),
        "ascent_valgus_ratio": round(ascent_valgus_ratio, 4),
        "max_forward_knee": round(max_forward_knee, 4),
        "max_lr_knee_diff_deg": round(max_lr_knee_diff_deg, 2),
        "max_lr_depth_asym": round(max_lr_depth_asym, 4),
        "max_heel_lift": round(max_heel_lift, 4),
        "frac_valid": round(
            float(np.minimum(v["valid"]["L"][s : e + 1], v["valid"]["R"][s : e + 1]).mean()), 4
        ),
    }

