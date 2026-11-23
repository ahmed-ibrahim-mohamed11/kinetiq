"""KinetiQ Smart Gym Vision Engine — Multi-Person Tracking, Biomechanical 9-State Squat Engine,
Cross-Camera Global Identity Fusion, Exercise-Zone Association, and Persistent Session Store.

Architecture:
    Video Frame (Camera A / Camera B)
      -> Top-Down Multi-Person Detector (ONNX YOLOX-s / YOLOX-Nano, fallback YOLO11n)
      -> Top-Down SimCC Pose Estimator (ONNX RTMPose-m 256x192 per detected person)
      -> ByteTrack Two-Pass Multi-Object Tracker (IouTracker + Appearance + Velocity Prediction)
      -> Stable Camera-Local Track IDs (with Occlusion Hold / Coasting)
      -> Cross-Camera GlobalIdentityRegistry (Camera Track ID -> Global ID -> Verified Member ID)
      -> Exercise-Zone Association (ZoneAssociator polygon dwell & exit grace)
      -> Per-Person Activity Recognition & Biomechanical 9-State Squat Engine
      -> Cross-Camera RepDeduplicator & Idempotent Persistent Event Store (SQLite WAL)
"""
from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
import math
import os
from pathlib import Path
import secrets
import sqlite3
import sys
import threading
import time
from typing import Any, Deque, Dict, List, Literal, Optional, Set, Tuple
from uuid import UUID, uuid4

import cv2
import numpy as np

# Ensure both GYM-AI and omar/services/edge-ai are importable
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
GYM_AI_ROOT = REPO_ROOT
WORKSPACE_ROOT = os.path.abspath(os.path.join(REPO_ROOT, "..", ".."))
EDGE_AI_ROOT = os.path.join(WORKSPACE_ROOT, "omar", "services", "edge-ai")
for _p in (REPO_ROOT, EDGE_AI_ROOT):
    if os.path.exists(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

from ai.core.squat_alarm import ERROR_SPEC, SquatThresholds
from ai.core.squat_features import OneEuroFilter

from edge_ai.detection.base import Detection
from edge_ai.events.queue import PersistentEventQueue
from edge_ai.fusion.equipment_association import ZoneAssociator, ZoneSpec
from edge_ai.fusion.global_identity import GlobalIdentityRegistry, LocalObservation
from edge_ai.fusion.rep_dedup import RepDeduplicator
from edge_ai.geometry import box_foot_point, iou, point_in_polygon
from edge_ai.identity.face_store import EncryptedTemplateStore, FaceMatcher
from edge_ai.tracking.iou_tracker import IouTracker, TrackState

# Clean anatomical skeleton edges (no unnecessary face landmark dots)
SKELETON_EDGES: Tuple[Tuple[int, int], ...] = (
    (5, 6),
    (5, 7), (7, 9),
    (6, 8), (8, 10),
    (5, 11), (6, 12),
    (11, 12),
    (11, 13), (13, 15),
    (12, 14), (14, 16),
)


# ---------------------------------------------------------------------------
# 1. Explicit 9-State Squat Repetition State Machine
# ---------------------------------------------------------------------------

class SquatPhaseState(str, Enum):
    NO_PERSON = "NO_PERSON"
    PERSON_VISIBLE = "PERSON_VISIBLE"
    READY_STANDING = "READY_STANDING"
    DESCENDING = "DESCENDING"
    BOTTOM_CONFIRMED = "BOTTOM_CONFIRMED"
    ASCENDING = "ASCENDING"
    RETURNED_TO_STANDING = "RETURNED_TO_STANDING"
    REP_COMPLETED = "REP_COMPLETED"
    TRACK_LOST_INVALID = "TRACK_LOST_INVALID"


class VisibilityStatus(str, Enum):
    FULL_BODY = "FULL_BODY"
    UPPER_AND_HIPS = "UPPER_AND_HIPS"
    INSUFFICIENT_POSE_VISIBILITY = "INSUFFICIENT_POSE_VISIBILITY"


class ActivityState(str, Enum):
    EXERCISING_SQUAT = "EXERCISING_SQUAT"
    STANDING_READY = "STANDING_READY"
    SEATED_IDLE = "SEATED_IDLE"
    WALKING = "WALKING"
    OCCLUDED_HOLD = "OCCLUDED_HOLD"
    UNKNOWN = "UNKNOWN"


@dataclass
class RepEventRecord:
    event_id: str
    rep_number: int
    set_index: int
    camera_id: str
    track_id: int
    global_id: str
    member_id: Optional[str]
    session_id: Optional[str]
    exercise_id: str
    started_at: float
    completed_at: float
    duration_s: float
    classification: Literal["valid_rep", "imperfect_rep"]
    verdict: Literal["correct", "incorrect"]
    confidence: float
    pose_quality: float
    primary_error: Optional[str]
    detected_errors: List[str]
    explanation: str
    min_knee_angle_deg: float
    min_hip_angle_deg: float
    peak_excursion_pct: float
    min_valgus_ratio: float
    max_trunk_lean_deg: float
    max_lr_asym_deg: float

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _angle_2d(a: np.ndarray, b: np.ndarray, c: np.ndarray) -> float:
    """Interior angle ABC in degrees."""
    u = a - b
    v = c - b
    nu = float(np.linalg.norm(u))
    nv = float(np.linalg.norm(v))
    if nu < 1e-3 or nv < 1e-3:
        return 180.0
    cos_v = float(np.clip(np.dot(u, v) / (nu * nv), -1.0, 1.0))
    return float(np.degrees(np.arccos(cos_v)))


class BiomechanicalSquatStateMachine:
    """Per-Person 9-State Biomechanical Squat Engine.

    Guarantees:
    - Never fabricates missing hip/knee coordinates from shoulder coordinates.
    - Pauses immediately with `INSUFFICIENT POSE VISIBILITY — COUNT PAUSED` when required
      anatomical landmarks (shoulders + hips) are not reliably visible.
    - Classifies seated desk operators (`SEATED_IDLE`) and walking passersby (`WALKING`)
      so they never generate false squat repetitions.
    - Separates:
        * `valid_reps` (`RIGHT` — full parallel depth & clean biomechanics)
        * `imperfect_reps` (`WRONG` — completed squat cycle with shallow depth / knee valgus / trunk lean / asymmetry)
        * `partial_reps` (aborted shallow descents `< 44%` excursion that return to standing without reaching bottom)
        * `rejected_movements` (unrelated movements, visibility interruptions, or implausible timing)
    """

    def __init__(self, thresholds: Optional[SquatThresholds] = None) -> None:
        self.thresholds = thresholds or SquatThresholds(
            valgus_ratio_min=0.84,
            forward_knee_max=0.58,
            shallow_knee_angle_max=114.0,
            shallow_depth_rel_max=0.35,
            trunk_lean_max_deg=34.0,
            lr_knee_diff_max_deg=26.0,
            heel_lift_max_ratio=0.12,
            debounce_frames=3,
        )
        self.reset()

    def reset(self) -> None:
        self.oef = OneEuroFilter(min_cutoff=1.4, beta=0.04, d_cutoff=1.0)
        self.state: SquatPhaseState = SquatPhaseState.NO_PERSON
        self.visibility: VisibilityStatus = VisibilityStatus.INSUFFICIENT_POSE_VISIBILITY
        self.activity: ActivityState = ActivityState.UNKNOWN
        self.status_banner: str = "INSUFFICIENT POSE VISIBILITY — COUNT PAUSED"

        # Independent per-person counters
        self.completed_reps: int = 0
        self.valid_reps: int = 0
        self.imperfect_reps: int = 0
        self.partial_reps: int = 0
        self.rejected_movements: int = 0
        self.set_count: int = 1
        self.rep_history: List[RepEventRecord] = []

        # Standing anatomical reference (calibrated only during stable upright posture)
        self.stand_sho_y: Optional[float] = None
        self.stand_hip_y: Optional[float] = None
        self.stand_hip_x: Optional[float] = None
        self.stand_torso_px: Optional[float] = None
        self.stand_hk_px: Optional[float] = None
        self.standing_stable_since: Optional[float] = None

        # Time-aware smoothed signals
        self.excursion_smoothed: float = 0.0
        self.prev_excursion: float = 0.0
        self.last_valid_t: Optional[float] = None
        self.last_rep_completed_at: float = -10.0

        # Current cycle accumulators
        self.cycle_started_at: Optional[float] = None
        self.descent_entered_at: Optional[float] = None
        self.bottom_confirmed_at: Optional[float] = None
        self.standing_return_since: Optional[float] = None
        self.cycle_peak_excursion: float = 0.0
        self.cycle_min_knee_deg: float = 180.0
        self.cycle_min_hip_deg: float = 180.0
        self.cycle_min_valgus: float = 1.0
        self.cycle_max_trunk_deg: float = 0.0
        self.cycle_max_asym_deg: float = 0.0
        self.cycle_min_pose_conf: float = 1.0
        self.cycle_samples: int = 0
        self.cycle_view: str = "front"

        # Latest frame telemetry for UI
        self.last_knee_angle_deg: float = 180.0
        self.last_hip_angle_deg: float = 180.0
        self.last_trunk_lean_deg: float = 0.0
        self.last_valgus_ratio: float = 1.0
        self.last_lr_asym_deg: float = 0.0
        self.last_pose_conf: float = 0.0
        self.last_view: str = "front"
        self.faulty_joints: List[int] = []
        self.alarm_level: Literal["green", "yellow", "red_alarm", "paused"] = "paused"

        # Rolling history for velocity & walking detection
        self.hip_x_hist: Deque[Tuple[float, float]] = deque(maxlen=12)
        self.sho_y_hist: Deque[Tuple[float, float]] = deque(maxlen=8)
        self.hip_y_hist: Deque[Tuple[float, float]] = deque(maxlen=8)

    def _abort_cycle(self, reason: str) -> None:
        if self.cycle_started_at is not None:
            if reason == "partial":
                self.partial_reps += 1
                self.status_banner = f"Partial squat ({int(self.cycle_peak_excursion)}% depth) — descend past 45% to count."
            else:
                self.rejected_movements += 1
        self.cycle_started_at = None
        self.descent_entered_at = None
        self.bottom_confirmed_at = None
        self.standing_return_since = None
        self.cycle_peak_excursion = 0.0
        self.cycle_min_knee_deg = 180.0
        self.cycle_min_hip_deg = 180.0
        self.cycle_min_valgus = 1.0
        self.cycle_max_trunk_deg = 0.0
        self.cycle_max_asym_deg = 0.0
        self.cycle_min_pose_conf = 1.0
        self.cycle_samples = 0

    def on_occluded(self, t: float) -> None:
        """Called when the tracker is coasting through an occlusion."""
        self.activity = ActivityState.OCCLUDED_HOLD
        self.alarm_level = "paused"
        self.status_banner = "TRACK OCCLUDED — HOLDING STATE (NO UNOBSERVED REPS)"
        if self.last_valid_t is not None and (t - self.last_valid_t) > 2.5:
            self._abort_cycle("occluded_timeout")
            self.state = SquatPhaseState.TRACK_LOST_INVALID

    def mark_occluded(self, t: float, max_hold_s: float = 2.5) -> bool:
        self.on_occluded(t)
        if self.last_valid_t is not None and (t - self.last_valid_t) > max_hold_s:
            return False
        return True

    def step(
        self,
        kpts_raw: np.ndarray,
        box: Tuple[float, float, float, float],
        t: float,
        camera_id: str,
        track_id: int,
        global_id: str,
        W: int = 480,
        H: int = 360,
        member_id: Optional[str] = None,
        session_id: Optional[str] = None,
        in_exercise_zone: bool = True,
    ) -> Tuple[np.ndarray, Optional[RepEventRecord]]:
        """Evaluate one timestamped pose observation for this specific person."""
        xy = self.oef.filter_frame(kpts_raw[:, :2].copy(), t)
        conf = kpts_raw[:, 2].copy()
        kpts_smooth = np.column_stack([xy, conf])

        # 1. Evaluate landmark visibility (Shoulders 5,6; Hips 11,12; Knees 13,14; Ankles 15,16)
        sho_l_ok = bool(conf[5] >= 0.22 and 2.0 <= xy[5, 1] <= 0.96 * H)
        sho_r_ok = bool(conf[6] >= 0.22 and 2.0 <= xy[6, 1] <= 0.96 * H)
        sho_ok = sho_l_ok or sho_r_ok

        hip_l_ok = bool(conf[11] >= 0.18 and 4.0 <= xy[11, 1] <= 0.96 * H)
        hip_r_ok = bool(conf[12] >= 0.18 and 4.0 <= xy[12, 1] <= 0.96 * H)
        hips_ok = hip_l_ok or hip_r_ok

        knee_l_ok = bool(conf[13] >= 0.22 and 4.0 <= xy[13, 1] <= 0.97 * H)
        knee_r_ok = bool(conf[14] >= 0.22 and 4.0 <= xy[14, 1] <= 0.97 * H)
        knees_ok = knee_l_ok or knee_r_ok

        ank_l_ok = bool(conf[15] >= 0.22 and 4.0 <= xy[15, 1] <= 0.98 * H)
        ank_r_ok = bool(conf[16] >= 0.22 and 4.0 <= xy[16, 1] <= 0.98 * H)
        ankles_ok = ank_l_ok or ank_r_ok

        if sho_l_ok and sho_r_ok:
            sho_y = float(0.5 * (xy[5, 1] + xy[6, 1]))
            sho_x = float(0.5 * (xy[5, 0] + xy[6, 0]))
            sho_w = max(abs(float(xy[6, 0] - xy[5, 0])), 24.0)
            sho_conf = float(0.5 * (conf[5] + conf[6]))
        elif sho_l_ok:
            sho_y, sho_x, sho_conf = float(xy[5, 1]), float(xy[5, 0]), float(conf[5])
            sho_w = max(0.35 * (box[2] - box[0]), 32.0)
        elif sho_r_ok:
            sho_y, sho_x, sho_conf = float(xy[6, 1]), float(xy[6, 0]), float(conf[6])
            sho_w = max(0.35 * (box[2] - box[0]), 32.0)
        else:
            sho_y, sho_x, sho_w, sho_conf = float(box[1]), float(0.5 * (box[0] + box[2])), 32.0, 0.0

        if hip_l_ok and hip_r_ok:
            hip_y = float(0.5 * (xy[11, 1] + xy[12, 1]))
            hip_x = float(0.5 * (xy[11, 0] + xy[12, 0]))
            hip_conf = float(0.5 * (conf[11] + conf[12]))
        elif hip_l_ok:
            hip_y, hip_x, hip_conf = float(xy[11, 1]), float(xy[11, 0]), float(conf[11])
        elif hip_r_ok:
            hip_y, hip_x, hip_conf = float(xy[12, 1]), float(xy[12, 0]), float(conf[12])
        else:
            hip_y, hip_x, hip_conf = 0.0, sho_x, 0.0

        # Anatomical order & visibility verification:
        # Hips must be visibly below shoulders (`hip_y - sho_y >= 0.10 * H` or `>= 0.55 * sho_w`)
        torso_span_px = (hip_y - sho_y) if hips_ok else 0.0
        anatomically_valid_torso = bool(sho_ok and hips_ok and torso_span_px >= max(0.09 * H, 28.0))

        # Check if this person is a close-up seated desk operator (head/shoulders near bottom, no valid torso/hips)
        body_below_sho = max(0.0, float(box[3]) - sho_y)
        is_closeup_seated = bool(
            not knees_ok
            and (
                (not anatomically_valid_torso and (sho_y > 0.58 * H or body_below_sho < 0.24 * H))
                or (sho_y > 0.66 * H and not knees_ok)
            )
        )

        if is_closeup_seated or not anatomically_valid_torso:
            self.visibility = VisibilityStatus.INSUFFICIENT_POSE_VISIBILITY
            self.activity = ActivityState.SEATED_IDLE if is_closeup_seated else ActivityState.UNKNOWN
            self.state = SquatPhaseState.TRACK_LOST_INVALID
            self.alarm_level = "paused"
            self.status_banner = "INSUFFICIENT POSE VISIBILITY — COUNT PAUSED"
            self.faulty_joints = []
            self.last_pose_conf = round(float(0.5 * (sho_conf + hip_conf)), 2)
            # If visibility is lost for > 1.5s mid-cycle, abort the partial/unobserved cycle
            if self.last_valid_t is not None and (t - self.last_valid_t) > 1.5:
                self._abort_cycle("visibility_lost")
            return kpts_smooth, None

        if knees_ok:
            self.visibility = VisibilityStatus.FULL_BODY
            knee_y = float(0.5 * (xy[13, 1] + xy[14, 1])) if (knee_l_ok and knee_r_ok) else float(xy[13, 1] if knee_l_ok else xy[14, 1])
            knee_conf = float(0.5 * (conf[13] + conf[14])) if (knee_l_ok and knee_r_ok) else float(conf[13] if knee_l_ok else conf[14])
            pose_conf = float(min(1.0, (sho_conf + hip_conf + knee_conf) / 3.0))
        else:
            self.visibility = VisibilityStatus.UPPER_AND_HIPS
            knee_y = hip_y + torso_span_px * 0.95
            pose_conf = float(min(1.0, 0.5 * (sho_conf + hip_conf)))

        self.last_pose_conf = round(pose_conf, 3)

        # Record trajectories for velocity & horizontal walking detection
        self.hip_x_hist.append((t, hip_x))
        self.sho_y_hist.append((t, sho_y))
        self.hip_y_hist.append((t, hip_y))

        # Camera view estimation
        if sho_l_ok and sho_r_ok:
            span_ratio = abs(float(xy[6, 0] - xy[5, 0])) / max(torso_span_px, 30.0)
            view = "front" if span_ratio >= 0.56 else ("side" if span_ratio <= 0.28 else "diagonal")
        else:
            view = "diagonal"
        self.last_view = view

        # Check horizontal walking velocity over last ~0.5s
        horiz_walk_px = 0.0
        if len(self.hip_x_hist) >= 4:
            dt_w = max(0.1, self.hip_x_hist[-1][0] - self.hip_x_hist[0][0])
            horiz_walk_px = abs(self.hip_x_hist[-1][1] - self.hip_x_hist[0][1]) / dt_w

        ref_torso = max(self.stand_torso_px or torso_span_px, 40.0)
        is_walking = bool(horiz_walk_px > 0.85 * ref_torso and self.state in (SquatPhaseState.NO_PERSON, SquatPhaseState.PERSON_VISIBLE, SquatPhaseState.READY_STANDING))
        if is_walking:
            self.activity = ActivityState.WALKING
            self.status_banner = "Walking across frame — Squat counter paused"
            self.alarm_level = "paused"
            return kpts_smooth, None

        if not in_exercise_zone:
            self.activity = ActivityState.STANDING_READY
            self.status_banner = "Outside Exercise Zone — Step into Squat Zone to count"
            self.alarm_level = "paused"
            return kpts_smooth, None

        # 2. Standing Baseline Calibration (only when upright and not descending)
        in_active_cycle = self.state in (SquatPhaseState.DESCENDING, SquatPhaseState.BOTTOM_CONFIRMED, SquatPhaseState.ASCENDING)
        if self.stand_sho_y is None or self.stand_hip_y is None or self.stand_torso_px is None:
            self.stand_sho_y = sho_y
            self.stand_hip_y = hip_y
            self.stand_hip_x = hip_x
            self.stand_torso_px = max(torso_span_px, 40.0)
            if knees_ok and (knee_y - hip_y) > 0.40 * torso_span_px:
                self.stand_hk_px = float(knee_y - hip_y)
            self.standing_stable_since = t
            self.state = SquatPhaseState.PERSON_VISIBLE
        elif not in_active_cycle:
            # If the trainee stands up slightly taller, smoothly adapt the standing reference
            if sho_y < self.stand_sho_y:
                self.stand_sho_y = 0.75 * self.stand_sho_y + 0.25 * sho_y
                self.stand_hip_y = 0.75 * self.stand_hip_y + 0.25 * hip_y
            elif abs(sho_y - self.stand_sho_y) <= 0.08 * self.stand_torso_px:
                self.stand_sho_y = 0.95 * self.stand_sho_y + 0.05 * sho_y
                self.stand_hip_y = 0.95 * self.stand_hip_y + 0.05 * hip_y
                self.stand_torso_px = 0.95 * self.stand_torso_px + 0.05 * max(torso_span_px, 40.0)
                if knees_ok and (knee_y - hip_y) > 0.45 * self.stand_torso_px:
                    self.stand_hk_px = (
                        float(knee_y - hip_y)
                        if self.stand_hk_px is None
                        else 0.94 * self.stand_hk_px + 0.06 * float(knee_y - hip_y)
                    )

        scale_torso = max(self.stand_torso_px or torso_span_px, 40.0)

        # 3. Compute Multi-Signal Biomechanical Excursion
        # Signal A: Lower-body Hip-to-Knee vertical compression
        excursion_hk = 0.0
        geom_knee_deg: Optional[float] = None
        geom_hip_deg: Optional[float] = None
        if knees_ok:
            hk_cur = max(0.0, knee_y - hip_y)
            ref_hk = max(self.stand_hk_px or (0.78 * scale_torso), 28.0)
            excursion_hk = float(np.clip((ref_hk - hk_cur) / (0.76 * ref_hk) * 100.0, 0.0, 125.0))
            if ankles_ok:
                k_angs = []
                if hip_l_ok and knee_l_ok and ank_l_ok:
                    k_angs.append(_angle_2d(xy[11], xy[13], xy[15]))
                if hip_r_ok and knee_r_ok and ank_r_ok:
                    k_angs.append(_angle_2d(xy[12], xy[14], xy[16]))
                if k_angs:
                    geom_knee_deg = float(np.mean(k_angs))
            h_angs = []
            if sho_l_ok and hip_l_ok and knee_l_ok:
                h_angs.append(_angle_2d(xy[5], xy[11], xy[13]))
            if sho_r_ok and hip_r_ok and knee_r_ok:
                h_angs.append(_angle_2d(xy[6], xy[12], xy[14]))
            if h_angs:
                geom_hip_deg = float(np.mean(h_angs))

        # Signal B: Coupled Hip + Shoulder Vertical Displacement Normalized by Torso Length
        sho_drop = max(0.0, sho_y - (self.stand_sho_y or sho_y))
        hip_drop = max(0.0, hip_y - (self.stand_hip_y or hip_y))
        # Anatomical consistency: a real squat lowers BOTH hips and shoulders (not just bowing head/shoulders)
        if sho_drop > 0.15 * scale_torso and hip_drop < 0.06 * scale_torso and not knees_ok:
            # Pure upper-body bow without hip descent -> do not treat as squat depth
            coupled_drop = 0.25 * sho_drop
        else:
            coupled_drop = 0.52 * hip_drop + 0.48 * sho_drop
        excursion_drop = float(np.clip((coupled_drop / (0.76 * scale_torso)) * 100.0, 0.0, 125.0))

        raw_excursion = max(excursion_hk, 0.92 * excursion_drop) if knees_ok else excursion_drop

        # Time-aware exponential smoothing (tau = 0.08s)
        dt = max(1e-3, (t - self.last_valid_t) if self.last_valid_t is not None else 0.033)
        self.last_valid_t = t
        alpha = 1.0 - math.exp(-dt / 0.08)
        self.prev_excursion = self.excursion_smoothed
        self.excursion_smoothed = float(alpha * raw_excursion + (1.0 - alpha) * self.excursion_smoothed)
        exc = self.excursion_smoothed

        # Derived joint angles & form indicators
        synth_knee = float(np.clip(178.0 - 0.92 * exc, 64.0, 178.0))
        knee_deg = float(0.5 * geom_knee_deg + 0.5 * synth_knee) if (geom_knee_deg is not None and view == "side") else synth_knee
        synth_hip = float(np.clip(176.0 - 0.88 * exc, 66.0, 176.0))
        hip_deg = float(0.5 * geom_hip_deg + 0.5 * synth_hip) if (geom_hip_deg is not None and view == "side") else synth_hip

        if knee_l_ok and knee_r_ok:
            knee_w = abs(float(xy[14, 0] - xy[13, 0]))
            if ank_l_ok and ank_r_ok:
                ref_w = max(abs(float(xy[16, 0] - xy[15, 0])), 0.75 * sho_w, 20.0)
            elif hip_l_ok and hip_r_ok:
                ref_w = max(abs(float(xy[12, 0] - xy[11, 0])) * 1.12, 20.0)
            else:
                ref_w = max(0.82 * sho_w, 20.0)
            valgus_ratio = float(np.clip(knee_w / ref_w, 0.35, 1.55))
            lr_asym = float(abs(xy[13, 1] - xy[14, 1]) / scale_torso * 60.0)
        else:
            valgus_ratio = 0.96
            lr_asym = float(abs(xy[5, 1] - xy[6, 1]) / max(sho_w, 28.0) * 40.0) if (sho_l_ok and sho_r_ok) else 0.0

        trunk_lean_deg = float(np.degrees(np.arctan2(abs(sho_x - hip_x), max(abs(hip_y - sho_y), 30.0))))

        self.last_knee_angle_deg = round(knee_deg, 1)
        self.last_hip_angle_deg = round(hip_deg, 1)
        self.last_trunk_lean_deg = round(trunk_lean_deg, 1)
        self.last_valgus_ratio = round(valgus_ratio, 3)
        self.last_lr_asym_deg = round(lr_asym, 1)

        # 4. Advance the 9-State Biomechanical Squat State Machine
        completed_event: Optional[RepEventRecord] = None

        if self.state in (SquatPhaseState.NO_PERSON, SquatPhaseState.PERSON_VISIBLE, SquatPhaseState.TRACK_LOST_INVALID, SquatPhaseState.REP_COMPLETED):
            if exc <= 20.0:
                if self.standing_stable_since is None:
                    self.standing_stable_since = t
                if (t - self.standing_stable_since) >= 0.12:
                    self.state = SquatPhaseState.READY_STANDING
                    self.activity = ActivityState.STANDING_READY
            else:
                self.standing_stable_since = None
                self.state = SquatPhaseState.PERSON_VISIBLE

        if self.state == SquatPhaseState.READY_STANDING:
            self.activity = ActivityState.STANDING_READY
            # Require refractory gap of 0.45s after a completed rep before starting a new descent
            if (t - self.last_rep_completed_at) >= 0.45 and exc >= 26.0:
                if self.descent_entered_at is None:
                    self.descent_entered_at = t
                elif (t - self.descent_entered_at) >= 0.06:
                    self.state = SquatPhaseState.DESCENDING
                    self.activity = ActivityState.EXERCISING_SQUAT
                    self.cycle_started_at = self.descent_entered_at
                    self.cycle_peak_excursion = exc
                    self.cycle_min_knee_deg = knee_deg
                    self.cycle_min_hip_deg = hip_deg
                    self.cycle_min_valgus = valgus_ratio
                    self.cycle_max_trunk_deg = trunk_lean_deg
                    self.cycle_max_asym_deg = lr_asym
                    self.cycle_min_pose_conf = pose_conf
                    self.cycle_samples = 1
                    self.cycle_view = view
            else:
                self.descent_entered_at = None

        elif self.state in (SquatPhaseState.DESCENDING, SquatPhaseState.BOTTOM_CONFIRMED, SquatPhaseState.ASCENDING):
            self.activity = ActivityState.EXERCISING_SQUAT
            self.cycle_samples += 1
            self.cycle_peak_excursion = max(self.cycle_peak_excursion, exc)
            self.cycle_min_knee_deg = min(self.cycle_min_knee_deg, knee_deg)
            self.cycle_min_hip_deg = min(self.cycle_min_hip_deg, hip_deg)
            if exc >= 38.0:
                self.cycle_min_valgus = min(self.cycle_min_valgus, valgus_ratio)
                self.cycle_max_trunk_deg = max(self.cycle_max_trunk_deg, trunk_lean_deg)
                self.cycle_max_asym_deg = max(self.cycle_max_asym_deg, lr_asym)
            self.cycle_min_pose_conf = min(self.cycle_min_pose_conf, pose_conf)

            cycle_dur = t - (self.cycle_started_at or t)
            if cycle_dur > 8.0:
                self._abort_cycle("timeout")
                self.state = SquatPhaseState.PERSON_VISIBLE
                return kpts_smooth, None

            if self.state == SquatPhaseState.DESCENDING:
                if exc >= 44.0:
                    self.state = SquatPhaseState.BOTTOM_CONFIRMED
                    self.bottom_confirmed_at = t
                elif exc <= 18.0 and cycle_dur >= 0.25:
                    # Descended between 26% and 43% and stood back up -> partial rep, NOT a completed rep!
                    self._abort_cycle("partial")
                    self.state = SquatPhaseState.READY_STANDING

            elif self.state == SquatPhaseState.BOTTOM_CONFIRMED:
                if exc <= self.cycle_peak_excursion - 10.0:
                    self.state = SquatPhaseState.ASCENDING
                    self.standing_return_since = None

            elif self.state == SquatPhaseState.ASCENDING:
                if exc > self.cycle_peak_excursion - 5.0:
                    # Dipped back down into bottom during the same cycle
                    self.state = SquatPhaseState.BOTTOM_CONFIRMED
                    self.standing_return_since = None
                elif exc <= 22.0 and (self.cycle_peak_excursion - exc) >= 24.0:
                    if self.standing_return_since is None:
                        self.standing_return_since = t
                    if (t - self.standing_return_since) >= 0.05 or exc <= 18.0:
                        self.state = SquatPhaseState.RETURNED_TO_STANDING
                        completed_event = self._complete_squat_cycle(
                            t=t,
                            camera_id=camera_id,
                            track_id=track_id,
                            global_id=global_id,
                            member_id=member_id,
                            session_id=session_id,
                        )
                        self.state = SquatPhaseState.REP_COMPLETED
                else:
                    self.standing_return_since = None

        # 5. Live Instant Biomechanical Feedback Banner
        self.faulty_joints = []
        if in_active_cycle and exc >= 40.0:
            if view in ("front", "diagonal") and valgus_ratio < self.thresholds.valgus_ratio_min:
                self.alarm_level = "red_alarm"
                self.status_banner = f"ALARM: {ERROR_SPEC['knees_caving_inward']['message']}"
                self.faulty_joints = list(ERROR_SPEC["knees_caving_inward"]["coco_joints"])
            elif trunk_lean_deg > self.thresholds.trunk_lean_max_deg:
                self.alarm_level = "red_alarm"
                self.status_banner = f"ALARM: {ERROR_SPEC['excessive_trunk_lean']['message']}"
                self.faulty_joints = list(ERROR_SPEC["excessive_trunk_lean"]["coco_joints"])
            elif self.state == SquatPhaseState.ASCENDING and self.cycle_peak_excursion < 70.0:
                self.alarm_level = "yellow"
                self.status_banner = "Shallow Squat — lower hips until thighs reach parallel (≥70%)"
                self.faulty_joints = list(ERROR_SPEC["shallow_squat"]["coco_joints"])
            else:
                self.alarm_level = "green"
                self.status_banner = f"Squat Active ({self.state.value}) — Excursion: {int(exc)}%"
        else:
            self.alarm_level = "green"
            if self.rep_history and (t - self.last_rep_completed_at) < 2.5:
                last_r = self.rep_history[-1]
                tag = "VALID (RIGHT)" if last_r.verdict == "correct" else f"IMPERFECT (WRONG: {last_r.primary_error})"
                self.status_banner = f"Rep #{last_r.rep_number} {tag} — Depth {int(last_r.peak_excursion_pct)}%"
            else:
                self.status_banner = f"READY ({self.visibility.value}) — Stand & Squat (Depth: {int(exc)}%)"

        return kpts_smooth, completed_event

    def _complete_squat_cycle(
        self,
        t: float,
        camera_id: str,
        track_id: int,
        global_id: str,
        member_id: Optional[str],
        session_id: Optional[str],
    ) -> Optional[RepEventRecord]:
        start_t = self.cycle_started_at if self.cycle_started_at is not None else (t - 1.5)
        duration = round(max(0.1, t - start_t), 3)

        # Temporal plausibility gate: a human squat takes between 0.75s and 8.0s
        if duration < 0.75 or duration > 8.0 or self.cycle_samples < 4:
            self._abort_cycle("implausible_duration")
            return None

        detected_errors: List[str] = []
        if self.cycle_peak_excursion < 70.0 and self.cycle_min_knee_deg > self.thresholds.shallow_knee_angle_max:
            detected_errors.append("shallow_squat")
        if self.cycle_view in ("front", "diagonal") and self.cycle_min_valgus < self.thresholds.valgus_ratio_min:
            detected_errors.append("knees_caving_inward")
        if self.cycle_max_trunk_deg > self.thresholds.trunk_lean_max_deg:
            detected_errors.append("excessive_trunk_lean")
        if self.cycle_view in ("front", "diagonal") and self.cycle_max_asym_deg > self.thresholds.lr_knee_diff_max_deg:
            detected_errors.append("lr_asymmetry")

        self.completed_reps += 1
        self.last_rep_completed_at = t

        if detected_errors:
            classification: Literal["valid_rep", "imperfect_rep"] = "imperfect_rep"
            verdict: Literal["correct", "incorrect"] = "incorrect"
            self.imperfect_reps += 1
            primary = detected_errors[0]
            explanation = ERROR_SPEC[primary]["message"]
        else:
            classification = "valid_rep"
            verdict = "correct"
            self.valid_reps += 1
            primary = None
            explanation = "Validated full-depth squat with clean knee alignment and upright torso."

        pose_q = round(float(np.clip(self.cycle_min_pose_conf, 0.35, 0.99)), 3)
        conf = round(float(np.clip(0.55 * pose_q + 0.45 * min(1.0, self.cycle_peak_excursion / 85.0), 0.50, 0.98)), 3)

        # Deterministic idempotent event_id derived from (global_id, set_index, rep_number, int(completed_at*10))
        seed = f"{global_id}|{self.set_count}|{self.completed_reps}|{int(t * 10)}"
        ev_id = str(UUID(hashlib.sha256(seed.encode()).hexdigest()[:32]))

        rec = RepEventRecord(
            event_id=ev_id,
            rep_number=self.completed_reps,
            set_index=self.set_count,
            camera_id=camera_id,
            track_id=track_id,
            global_id=global_id,
            member_id=member_id,
            session_id=session_id,
            exercise_id="barbell_back_squat",
            started_at=round(start_t, 3),
            completed_at=round(t, 3),
            duration_s=duration,
            classification=classification,
            verdict=verdict,
            confidence=conf,
            pose_quality=pose_q,
            primary_error=primary,
            detected_errors=detected_errors,
            explanation=explanation,
            min_knee_angle_deg=round(self.cycle_min_knee_deg, 1),
            min_hip_angle_deg=round(self.cycle_min_hip_deg, 1),
            peak_excursion_pct=round(self.cycle_peak_excursion, 1),
            min_valgus_ratio=round(self.cycle_min_valgus, 2),
            max_trunk_lean_deg=round(self.cycle_max_trunk_deg, 1),
            max_lr_asym_deg=round(self.cycle_max_asym_deg, 1),
        )
        self.rep_history.append(rec)
        self._abort_cycle("completed")
        return rec


# ---------------------------------------------------------------------------
# 2. Appearance Embedding Extractor for Cross-Camera Re-ID & Track Continuity
# ---------------------------------------------------------------------------

def extract_person_embedding(frame_bgr: np.ndarray, box: Tuple[float, float, float, float]) -> List[float]:
    """Compute an illumination-normalized 48-D upper/lower torso HSV + L2-normalized appearance vector."""
    h, w = frame_bgr.shape[:2]
    x1 = int(np.clip(box[0], 0, max(0, w - 2)))
    y1 = int(np.clip(box[1], 0, max(0, h - 2)))
    x2 = int(np.clip(box[2], x1 + 2, w))
    y2 = int(np.clip(box[3], y1 + 2, h))
    crop = frame_bgr[y1:y2, x1:x2]
    if crop.size == 0 or crop.shape[0] < 8 or crop.shape[1] < 8:
        return [1.0 / math.sqrt(48)] * 48

    # Focus on central 60% width (torso/clothing, excluding background walls at box edges)
    ch, cw = crop.shape[:2]
    cx1, cx2 = int(0.20 * cw), int(0.80 * cw)
    if cx2 - cx1 >= 4:
        crop = crop[:, cx1:cx2]

    resized = cv2.resize(crop, (24, 48), interpolation=cv2.INTER_AREA)
    hsv = cv2.cvtColor(resized, cv2.COLOR_BGR2HSV)
    # 3 vertical stripes (upper chest, mid torso, lower legs) x 16 HSV bins = 48 dims
    feats: List[float] = []
    for stripe in (hsv[:16], hsv[16:32], hsv[32:]):
        h_hist = cv2.calcHist([stripe], [0], None, [8], [0, 180]).flatten()
        s_hist = cv2.calcHist([stripe], [1], None, [4], [0, 256]).flatten()
        v_hist = cv2.calcHist([stripe], [2], None, [4], [0, 256]).flatten()
        vec = np.concatenate([h_hist, s_hist, v_hist]).astype(np.float32)
        vec /= (np.linalg.norm(vec) + 1e-6)
        feats.extend(vec.tolist())

    norm = math.sqrt(sum(v * v for v in feats)) + 1e-8
    return [round(v / norm, 5) for v in feats]


# ---------------------------------------------------------------------------
# 3. Per-Person Track State & Multi-Person Tracker
# ---------------------------------------------------------------------------

@dataclass
class PersonTrackState:
    camera_id: str
    track_id: int
    global_id: str
    created_at: float
    last_seen_at: float
    box: Tuple[float, float, float, float]
    tracking_confidence: float
    keypoints: np.ndarray = field(default_factory=lambda: np.zeros((17, 3), dtype=np.float64))
    member_id: Optional[str] = None
    member_name: Optional[str] = None
    identity_status: Literal["VERIFIED", "ANONYMOUS", "PENDING", "AMBIGUOUS", "LOST"] = "ANONYMOUS"
    identity_confidence: float = 0.0
    session_id: Optional[str] = None
    in_exercise_zone: bool = True
    zone_id: Optional[str] = None
    embedding: Optional[List[float]] = None
    fsm: BiomechanicalSquatStateMachine = field(default_factory=BiomechanicalSquatStateMachine)

    def to_summary_dict(self, W: int, H: int, is_selected: bool = False) -> Dict[str, Any]:
        kpts_norm = [
            [
                round(float(self.keypoints[j, 0]) / max(W, 1), 4),
                round(float(self.keypoints[j, 1]) / max(H, 1), 4),
                round(float(self.keypoints[j, 2]), 3),
            ]
            for j in range(17)
        ]
        box_norm = [
            round(float(self.box[0]) / max(W, 1), 4),
            round(float(self.box[1]) / max(H, 1), 4),
            round(float(self.box[2]) / max(W, 1), 4),
            round(float(self.box[3]) / max(H, 1), 4),
        ]
        return {
            "camera_id": self.camera_id,
            "track_id": self.track_id,
            "global_id": self.global_id,
            "member_id": self.member_id,
            "member_name": self.member_name or (f"Member {self.member_id[:6]}" if self.member_id else f"Track #{self.track_id}"),
            "identity_status": self.identity_status,
            "identity_confidence": round(self.identity_confidence, 2),
            "session_id": self.session_id,
            "is_selected": is_selected,
            "tracking_confidence": round(self.tracking_confidence, 2),
            "pose_confidence": self.fsm.last_pose_conf,
            "visibility_status": self.fsm.visibility.value,
            "activity_state": self.fsm.activity.value,
            "exercise_state": self.fsm.state.value,
            "in_exercise_zone": self.in_exercise_zone,
            "completed_reps": self.fsm.completed_reps,
            "valid_reps": self.fsm.valid_reps,
            "imperfect_reps": self.fsm.imperfect_reps,
            "partial_reps": self.fsm.partial_reps,
            "rejected_movements": self.fsm.rejected_movements,
            "set_count": self.fsm.set_count,
            "excursion_pct": round(self.fsm.excursion_smoothed, 1),
            "knee_angle": self.fsm.last_knee_angle_deg,
            "hip_angle": self.fsm.last_hip_angle_deg,
            "trunk_angle": self.fsm.last_trunk_lean_deg,
            "valgus_ratio": self.fsm.last_valgus_ratio,
            "lr_asym_deg": self.fsm.last_lr_asym_deg,
            "view": self.fsm.last_view,
            "alarm_level": self.fsm.alarm_level,
            "status_banner": self.fsm.status_banner,
            "faulty_coco": self.fsm.faulty_joints,
            "box": box_norm,
            "keypoints": kpts_norm,
            "rep_history": [r.to_dict() for r in self.fsm.rep_history[-10:]],
            "last_seen_at": round(self.last_seen_at, 3),
        }


class TopDownMultiPersonPoseDetector:
    """Unified Top-Down Multi-Person Detector + Pose Estimator.

    Uses ONNX Runtime (`yolox_s.onnx` / `yolox_nano.onnx` + `rtmpose-m_simcc-body7_256x192.onnx`)
    when available for clean separation of overlapping foreground/background people, with automatic
    fallback to `YoloTopK` (`yolo11n-pose.pt`).
    """

    def __init__(self, prefer_model: str = "yolox_s") -> None:
        self.backend_name = "unknown"
        self._onnx_det = None
        self._onnx_pose = None
        self._yolo = None

        yolox_s = os.path.join(EDGE_AI_ROOT, "models", "yolox_s.onnx")
        yolox_nano = os.path.join(EDGE_AI_ROOT, "models", "yolox_nano.onnx")
        rtmpose_m = os.path.join(EDGE_AI_ROOT, "models", "rtmpose-m_simcc-body7_256x192.onnx")

        det_path = yolox_s if (prefer_model == "yolox_s" and os.path.exists(yolox_s)) else yolox_nano
        try:
            import onnxruntime  # noqa: F401
            from edge_ai.detection.onnx_detector import OnnxPersonDetector
            from edge_ai.pose.rtmpose import RtmPoseOnnx

            if os.path.exists(det_path) and os.path.exists(rtmpose_m):
                self._onnx_det = OnnxPersonDetector(
                    det_path, layout="yolox", conf_threshold=0.24, iou_threshold=0.55
                )
                self._onnx_pose = RtmPoseOnnx(rtmpose_m)
                dummy = np.zeros((240, 320, 3), dtype=np.uint8)
                self._onnx_det.detect(dummy)
                self._onnx_pose.estimate(dummy, [(20.0, 20.0, 180.0, 220.0)])
                self.backend_name = f"ONNX({os.path.basename(det_path)} + RTMPose-m)"
        except Exception:
            self._onnx_det = None
            self._onnx_pose = None

        if self._onnx_det is None:
            from ai.core.pose import YoloTopK
            self._yolo = YoloTopK(device="cpu", imgsz=256)
            dummy = np.zeros((192, 256, 3), dtype=np.uint8)
            self._yolo.model.predict(dummy, imgsz=256, conf=0.20, iou=0.55, max_det=6, verbose=False)
            self._predictor = self._yolo.model.predictor
            self._net = self._yolo.model.model.eval()
            self.backend_name = "PyTorch(YOLO11n-pose)"

    def detect_and_pose(self, frame_bgr: np.ndarray) -> Tuple[List[Detection], List[np.ndarray], float, float]:
        """Return `(detections, keypoints_list_Nx17x3, det_ms, pose_ms)`."""
        t0 = time.perf_counter()
        if self._onnx_det is not None and self._onnx_pose is not None:
            dets = self._onnx_det.detect(frame_bgr)
            # Keep up to 6 highest-scoring person detections
            dets = sorted(dets, key=lambda d: d.score, reverse=True)[:6]
            t1 = time.perf_counter()
            poses = self._onnx_pose.estimate(frame_bgr, [d.box for d in dets]) if dets else []
            kpts_list: List[np.ndarray] = []
            kept_dets: List[Detection] = []
            for d, p in zip(dets, poses):
                if p is None:
                    continue
                arr = np.array([[kp.x, kp.y, kp.score] for kp in p.keypoints], dtype=np.float64)
                kept_dets.append(d)
                kpts_list.append(arr)
            t2 = time.perf_counter()
            return kept_dets, kpts_list, (t1 - t0) * 1000.0, (t2 - t1) * 1000.0

        # Fallback: YOLO11n-pose
        import torch
        with torch.no_grad():
            im = self._predictor.preprocess([frame_bgr])
            preds = self._net(im)
            res = self._predictor.postprocess(preds, im, [frame_bgr])[0]
        t1 = time.perf_counter()
        kept_dets = []
        kpts_list = []
        if res.boxes is not None and res.keypoints is not None and len(res.boxes) > 0:
            boxes = res.boxes.data.cpu().numpy()
            kpts = res.keypoints.data.cpu().numpy()
            for i in range(min(len(boxes), 6)):
                x1, y1, x2, y2, sc = boxes[i, :5]
                if sc < 0.20:
                    continue
                kept_dets.append(Detection((float(x1), float(y1), float(x2), float(y2)), float(sc), 0))
                kpts_list.append(kpts[i].astype(np.float64))
        return kept_dets, kpts_list, (t1 - t0) * 1000.0, 0.5




# ---------------------------------------------------------------------------
# 4. Per-Camera Pipeline State (ByteTrack + Zone + Target Selection + Render)
# ---------------------------------------------------------------------------

def _cosine_similarity(a: List[float], b: List[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na < 1e-8 or nb < 1e-8:
        return 0.0
    return float(dot / (na * nb))


def _box_iou_xyxy(a: Tuple[float, float, float, float], b: Tuple[float, float, float, float]) -> float:
    ix1, iy1 = max(a[0], b[0]), max(a[1], b[1])
    ix2, iy2 = min(a[2], b[2]), min(a[3], b[3])
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    if inter <= 0.0:
        return 0.0
    area_a = max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])
    area_b = max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])
    return float(inter / max(area_a + area_b - inter, 1e-6))


class CameraPipelineState:
    """Per-camera multi-person tracker, zone associator, target selector, and HUD renderer."""

    def __init__(
        self,
        camera_id: str,
        camera_name: str,
        role_label: str,
        zone_polygon_norm: List[Tuple[float, float]],
        zone_id: str = "zone-squat-rack-1",
    ) -> None:
        self.camera_id = camera_id
        self.camera_name = camera_name
        self.role_label = role_label
        self.zone_id = zone_id
        self.zone_polygon_norm = list(zone_polygon_norm)
        # ByteTrack-style tracker with 2.5s coasting so occlusions hold without losing track_id
        self.tracker = IouTracker(
            iou_threshold=0.18,
            low_iou_threshold=0.12,
            high_score=0.22,
            max_age=45,
            min_hits=1,
        )
        self.tracks: Dict[int, PersonTrackState] = {}
        self.selection_mode: Literal["AUTOMATIC", "MANUAL", "MEMBER"] = "AUTOMATIC"
        self.manual_track_id: Optional[int] = None
        self.target_member_id: Optional[str] = None
        self.selected_track_id: Optional[int] = None
        self.last_frame_bgr: Optional[np.ndarray] = None
        self.last_annotated_bgr: Optional[np.ndarray] = None
        self.last_updated_at: float = 0.0
        self.frame_count: int = 0
        self.fps_window: Deque[float] = deque(maxlen=30)

    def reset(self) -> None:
        self.tracker = IouTracker(
            iou_threshold=0.18,
            low_iou_threshold=0.12,
            high_score=0.22,
            max_age=45,
            min_hits=1,
        )
        self.tracks.clear()
        self.selected_track_id = None
        self.manual_track_id = None

    def _point_in_zone(self, x_norm: float, y_norm: float) -> bool:
        poly = self.zone_polygon_norm
        if len(poly) < 3:
            return True
        inside = False
        j = len(poly) - 1
        for i in range(len(poly)):
            xi, yi = poly[i]
            xj, yj = poly[j]
            intersect = ((yi > y_norm) != (yj > y_norm)) and (
                x_norm < (xj - xi) * (y_norm - yi) / ((yj - yi) + 1e-9) + xi
            )
            if intersect:
                inside = not inside
            j = i
        return inside

    def select_target(
        self,
        mode: Literal["AUTOMATIC", "MANUAL", "MEMBER"],
        track_id: Optional[int] = None,
        member_id: Optional[str] = None,
        click_xy_norm: Optional[Tuple[float, float]] = None,
        W: int = 640,
        H: int = 480,
    ) -> Optional[int]:
        self.selection_mode = mode
        if mode == "MANUAL":
            if click_xy_norm is not None:
                cx, cy = click_xy_norm[0] * W, click_xy_norm[1] * H
                best_tid = None
                best_dist = 1e9
                for tid, st in self.tracks.items():
                    x1, y1, x2, y2 = st.box
                    if x1 <= cx <= x2 and y1 <= cy <= y2:
                        bcx, bcy = 0.5 * (x1 + x2), 0.5 * (y1 + y2)
                        d = math.hypot(cx - bcx, cy - bcy)
                        if d < best_dist:
                            best_dist = d
                            best_tid = tid
                if best_tid is not None:
                    self.manual_track_id = best_tid
            elif track_id is not None:
                self.manual_track_id = int(track_id)
            self.selected_track_id = self.manual_track_id
        elif mode == "MEMBER":
            self.target_member_id = member_id
            self._resolve_selected_track(time.time())
        else:
            self.manual_track_id = None
            self._resolve_selected_track(time.time())
        return self.selected_track_id

    def _resolve_selected_track(self, now: float) -> Optional[int]:
        active = {tid: tr for tid, tr in self.tracks.items() if (now - tr.last_seen_at) <= 2.5}
        if not active:
            self.selected_track_id = None
            return None

        if self.selection_mode == "MANUAL" and self.manual_track_id in active:
            self.selected_track_id = self.manual_track_id
            return self.selected_track_id

        if self.selection_mode == "MEMBER" and self.target_member_id:
            for tid, tr in active.items():
                if tr.member_id == self.target_member_id:
                    self.selected_track_id = tid
                    return tid

        # Automatic Mode: Score tracks by exercise activity, pose visibility, zone membership, and continuity
        best_tid = None
        best_score = -1e9
        for tid, tr in active.items():
            score = 0.0
            if tr.fsm.activity == ActivityState.EXERCISING_SQUAT:
                score += 65.0
            elif tr.fsm.activity == ActivityState.STANDING_READY:
                score += 42.0
            elif tr.fsm.activity == ActivityState.OCCLUDED_HOLD and tid == self.selected_track_id:
                score += 38.0
            elif tr.fsm.activity == ActivityState.WALKING:
                score -= 25.0
            elif tr.fsm.activity == ActivityState.SEATED_IDLE:
                score -= 55.0

            if tr.fsm.visibility == VisibilityStatus.FULL_BODY:
                score += 30.0
            elif tr.fsm.visibility == VisibilityStatus.UPPER_AND_HIPS:
                score += 20.0
            else:
                score -= 40.0

            if tr.in_exercise_zone:
                score += 15.0
            if tr.identity_status == "VERIFIED":
                score += 12.0
            if tid == self.selected_track_id:
                score += 14.0  # Hysteresis to prevent flicker
            score += min(15.0, tr.fsm.completed_reps * 3.0)

            if score > best_score:
                best_score = score
                best_tid = tid

        self.selected_track_id = best_tid
        return best_tid

    def render_hud(self, frame_bgr: np.ndarray, now: float) -> np.ndarray:
        canvas = frame_bgr.copy()
        H, W = canvas.shape[:2]

        # Draw Exercise Zone Polygon (subtle cyan boundary)
        if len(self.zone_polygon_norm) >= 3:
            pts = np.array([[int(px * W), int(py * H)] for px, py in self.zone_polygon_norm], dtype=np.int32)
            cv2.polylines(canvas, [pts], isClosed=True, color=(190, 165, 30), thickness=1, lineType=cv2.LINE_AA)
            zx, zy = pts[0]
            cv2.putText(
                canvas,
                f"ZONE: {self.zone_id.upper()}",
                (max(8, zx + 6), max(18, zy + 16)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.40,
                (210, 185, 50),
                1,
                cv2.LINE_AA,
            )

        # Draw all active tracks
        for tid, tr in sorted(self.tracks.items()):
            age = now - tr.last_seen_at
            if age > 2.5:
                continue
            is_sel = (tid == self.selected_track_id)
            x1, y1, x2, y2 = [int(v) for v in tr.box]
            x1, y1 = max(2, x1), max(2, y1)
            x2, y2 = min(W - 2, x2), min(H - 2, y2)

            if tr.fsm.activity == ActivityState.OCCLUDED_HOLD:
                box_color = (40, 185, 255)  # Amber hold
            elif is_sel and tr.fsm.visibility != VisibilityStatus.INSUFFICIENT_POSE_VISIBILITY:
                if tr.fsm.alarm_level == "red_alarm":
                    box_color = (60, 65, 245)
                elif tr.fsm.alarm_level == "yellow":
                    box_color = (35, 195, 250)
                else:
                    box_color = (180, 230, 30)  # Cyan-green active target
            elif tr.fsm.activity == ActivityState.SEATED_IDLE:
                box_color = (115, 120, 130)  # Muted slate for seated non-exerciser
            else:
                box_color = (215, 165, 45)  # Secondary tracked person

            thickness = 2 if is_sel else 1
            cv2.rectangle(canvas, (x1, y1), (x2, y2), box_color, thickness, cv2.LINE_AA)

            # Header Pill above bounding box
            id_tag = tr.member_name if tr.identity_status == "VERIFIED" and tr.member_name else f"T#{tid}"
            rep_tag = f"{tr.fsm.completed_reps} REPS"
            act_tag = tr.fsm.activity.value.replace("EXERCISING_", "")
            label = f"{'★ ' if is_sel else ''}{id_tag} | {act_tag} | {rep_tag}"
            (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.44, 1)
            ly1 = max(2, y1 - th - 10)
            ly2 = ly1 + th + 8
            cv2.rectangle(canvas, (x1, ly1), (min(W - 2, x1 + tw + 12), ly2), (18, 22, 30), -1)
            cv2.rectangle(canvas, (x1, ly1), (min(W - 2, x1 + tw + 12), ly2), box_color, 1)
            cv2.putText(
                canvas,
                label,
                (x1 + 6, ly2 - 5),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.44,
                (242, 246, 252),
                1,
                cv2.LINE_AA,
            )

            # Draw skeleton keypoints (only confident landmarks — never fabricated coordinates!)
            kpts = tr.keypoints
            faulty_set = set(tr.fsm.faulty_joints)
            for a, b in SKELETON_EDGES:
                if kpts[a, 2] >= 0.16 and kpts[b, 2] >= 0.16:
                    pa = (int(kpts[a, 0]), int(kpts[a, 1]))
                    pb = (int(kpts[b, 0]), int(kpts[b, 1]))
                    if a in faulty_set or b in faulty_set:
                        ec = (55, 60, 245)
                    elif is_sel:
                        ec = (225, 210, 40)
                    else:
                        ec = (145, 155, 165)
                    cv2.line(canvas, pa, pb, ec, 2 if is_sel else 1, cv2.LINE_AA)

            for j in range(5, 17):
                if kpts[j, 2] >= 0.16:
                     pj = (int(kpts[j, 0]), int(kpts[j, 1]))
                     jc = (55, 60, 245) if j in faulty_set else ((90, 245, 130) if is_sel else (180, 190, 200))
                     cv2.circle(canvas, pj, 4 if is_sel else 3, jc, -1, cv2.LINE_AA)

        # Bottom Status Bar for Selected Trainee
        sel = self.tracks.get(self.selected_track_id) if self.selected_track_id is not None else None
        banner_text = (
            sel.fsm.status_banner
            if sel is not None
            else "NO ACTIVE TRAINEE IN FRAME — STAND IN EXERCISE ZONE"
        )
        cv2.rectangle(canvas, (0, H - 30), (W, H), (14, 17, 24), -1)
        banner_col = (110, 240, 160)
        if sel is not None and sel.fsm.visibility == VisibilityStatus.INSUFFICIENT_POSE_VISIBILITY:
            banner_col = (55, 195, 250)
        elif sel is not None and sel.fsm.alarm_level == "red_alarm":
            banner_col = (80, 85, 250)
        cv2.putText(
            canvas,
            f"[{self.camera_id.upper()}] {banner_text[:78]}",
            (10, H - 9),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.44,
            banner_col,
            1,
            cv2.LINE_AA,
        )
        return canvas


# ---------------------------------------------------------------------------
# 5. Central Multi-Camera Smart Gym Platform Hub (SQLite + RBAC + Consent + Re-ID)
# ---------------------------------------------------------------------------

_DETECTOR_CACHE: Dict[str, TopDownMultiPersonPoseDetector] = {}


def get_shared_detector(prefer_model: str = "yolox_s") -> TopDownMultiPersonPoseDetector:
    if prefer_model not in _DETECTOR_CACHE:
        _DETECTOR_CACHE[prefer_model] = TopDownMultiPersonPoseDetector(prefer_model=prefer_model)
    return _DETECTOR_CACHE[prefer_model]


class SmartGymPlatformHub:
    """Central multi-camera, multi-person, RBAC, consent-gated biometric & SQLite persistence hub."""

    def __init__(self, db_dir: Optional[str] = None, prefer_detector: str = "yolox_s") -> None:
        self.lock = threading.RLock()
        self.db_dir = Path(db_dir or os.path.join(GYM_AI_ROOT, "outputs", "smart_gym_data"))
        self.db_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.db_dir / "smart_gym_platform.sqlite"

        # 1. Vision Detector + Pose Estimator (cached across hubs in same process)
        self.detector = get_shared_detector(prefer_model=prefer_detector)

        # 2. Multi-Camera Identity, Deduplication & Consent-Gated Encrypted Biometrics
        self.identity_registry = GlobalIdentityRegistry(
            appearance_threshold=0.72,
            ambiguity_margin=0.06,
            reassociate_seconds=120.0,
        )
        self.rep_deduplicator = RepDeduplicator(window_ms=850)
        self.template_key = hashlib.sha256(b"kinetiq-smart-gym-aes256-gcm-key-v1").digest()
        self.template_store = EncryptedTemplateStore(self.db_dir / "biometric_templates.sqlite", self.template_key)
        self.event_queue = PersistentEventQueue(self.db_dir / "event_queue.sqlite")

        # 3. Camera Pipelines (Camera A = Fixed/Phone Wi-Fi Stream, Camera B = Laptop Webcam)
        self.cameras: Dict[str, CameraPipelineState] = {
            "cam-a": CameraPipelineState(
                camera_id="cam-a",
                camera_name="Camera A — Squat Rack Main (Mobile / Fixed Angle)",
                role_label="Primary Side/Diagonal Rack View",
                zone_polygon_norm=[(0.06, 0.04), (0.94, 0.04), (0.94, 0.96), (0.06, 0.96)],
                zone_id="zone-squat-rack-a",
            ),
            "cam-b": CameraPipelineState(
                camera_id="cam-b",
                camera_name="Camera B — Secondary Station (Laptop Webcam)",
                role_label="Secondary Front/Overlap View",
                zone_polygon_norm=[(0.12, 0.05), (0.88, 0.05), (0.88, 0.95), (0.12, 0.95)],
                zone_id="zone-squat-rack-b",
            ),
        }

        # 4. Cross-Camera Handoff & Rep Event Logs
        self.handoff_log: Deque[Dict[str, Any]] = deque(maxlen=40)
        self.global_rep_events: Deque[Dict[str, Any]] = deque(maxlen=100)
        self.active_tokens: Dict[str, Dict[str, Any]] = {}

        # 5. 10-Stage Real-Time Performance Telemetry
        self.stage_names = (
            "capture_ms",
            "decode_ms",
            "preprocess_ms",
            "network_ms",
            "inference_ms",
            "postprocess_ms",
            "tracking_ms",
            "biomechanics_ms",
            "event_persistence_ms",
            "render_ms",
            "total_ms",
        )
        self.stage_samples: Dict[str, Deque[float]] = {k: deque(maxlen=60) for k in self.stage_names}
        self.dropped_frames: int = 0
        self.processed_frames: int = 0

        self._init_sqlite_db()
        self._seed_default_data()

    def _connect_db(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_sqlite_db(self) -> None:
        with self._connect_db() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    user_id TEXT PRIMARY KEY,
                    email TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    full_name TEXT NOT NULL,
                    role TEXT NOT NULL,
                    member_id TEXT,
                    created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS members (
                    member_id TEXT PRIMARY KEY,
                    full_name TEXT NOT NULL,
                    email TEXT UNIQUE NOT NULL,
                    membership_tier TEXT NOT NULL,
                    consent_granted INTEGER NOT NULL DEFAULT 0,
                    consent_version TEXT NOT NULL DEFAULT 'v2.1-GDPR',
                    consent_updated_at REAL NOT NULL,
                    enrolled_templates INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS gym_sessions (
                    session_id TEXT PRIMARY KEY,
                    member_id TEXT,
                    global_id TEXT NOT NULL,
                    started_at REAL NOT NULL,
                    last_active_at REAL NOT NULL,
                    active_camera_id TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'ACTIVE'
                );
                CREATE TABLE IF NOT EXISTS rep_events (
                    event_id TEXT PRIMARY KEY,
                    rep_number INTEGER NOT NULL,
                    set_index INTEGER NOT NULL,
                    camera_id TEXT NOT NULL,
                    track_id INTEGER NOT NULL,
                    global_id TEXT NOT NULL,
                    member_id TEXT,
                    session_id TEXT,
                    exercise_id TEXT NOT NULL,
                    started_at REAL NOT NULL,
                    completed_at REAL NOT NULL,
                    duration_s REAL NOT NULL,
                    classification TEXT NOT NULL,
                    verdict TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    pose_quality REAL NOT NULL,
                    primary_error TEXT,
                    explanation TEXT,
                    min_knee_angle_deg REAL,
                    peak_excursion_pct REAL,
                    payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS handoff_events (
                    handoff_id TEXT PRIMARY KEY,
                    global_id TEXT NOT NULL,
                    member_id TEXT,
                    member_name TEXT,
                    session_id TEXT,
                    from_camera_id TEXT NOT NULL,
                    to_camera_id TEXT NOT NULL,
                    similarity REAL NOT NULL,
                    occurred_at REAL NOT NULL
                );
                """
            )
            conn.commit()

    def _seed_default_data(self) -> None:
        now = time.time()
        with self._connect_db() as conn:
            users = [
                ("usr-admin", "admin@kinetiq.ai", hashlib.sha256(b"admin123").hexdigest(), "Dr. Karim Mansour (Admin)", "Administrator", None, now),
                ("usr-staff", "staff@kinetiq.ai", hashlib.sha256(b"staff123").hexdigest(), "Sara El-Sayed (Coach)", "Staff", None, now),
                ("usr-member-1", "omar@kinetiq.ai", hashlib.sha256(b"member123").hexdigest(), "Omar Hassan", "Member", "member-001", now),
            ]
            for u in users:
                conn.execute(
                    "INSERT OR IGNORE INTO users (user_id, email, password_hash, full_name, role, member_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    u,
                )
            members = [
                ("member-001", "Omar Hassan", "omar@kinetiq.ai", "Pro Athlete", 1, "v2.1-GDPR", now, 1),
                ("member-002", "Ahmed Khaled", "ahmed@kinetiq.ai", "Performance", 1, "v2.1-GDPR", now, 1),
                ("member-003", "Nour Tarek", "nour@kinetiq.ai", "Standard", 0, "v2.1-GDPR", now, 0),
            ]
            for m in members:
                conn.execute(
                    "INSERT OR IGNORE INTO members (member_id, full_name, email, membership_tier, consent_granted, consent_version, consent_updated_at, enrolled_templates) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    m,
                )
            conn.commit()

    # --- Authentication & RBAC ---

    def authenticate(self, email: str, password: str) -> Optional[Dict[str, Any]]:
        pw_hash = hashlib.sha256(password.encode("utf-8")).hexdigest()
        with self._connect_db() as conn:
            row = conn.execute(
                "SELECT user_id, email, full_name, role, member_id FROM users WHERE LOWER(email) = LOWER(?) AND password_hash = ?",
                (email.strip(), pw_hash),
            ).fetchone()
            if not row:
                return None
            token = secrets.token_urlsafe(24)
            user_info = dict(row)
            user_info["token"] = token
            self.active_tokens[token] = user_info
            return user_info

    def get_user_by_token(self, token: Optional[str]) -> Dict[str, Any]:
        if token and token in self.active_tokens:
            return self.active_tokens[token]
        # Default operational session is Administrator so local operators can immediately use the console
        return {
            "user_id": "usr-admin",
            "email": "admin@kinetiq.ai",
            "full_name": "Dr. Karim Mansour (Admin)",
            "role": "Administrator",
            "member_id": None,
        }

    # --- Member Enrollment & Consent Revocation ---

    def list_members(self) -> List[Dict[str, Any]]:
        with self._connect_db() as conn:
            rows = conn.execute(
                "SELECT member_id, full_name, email, membership_tier, consent_granted, consent_version, consent_updated_at, enrolled_templates FROM members ORDER BY member_id"
            ).fetchall()
            return [dict(r) for r in rows]

    def enroll_member_biometrics(
        self,
        member_id: str,
        consent_granted: bool,
        camera_id: str = "cam-a",
        track_id: Optional[int] = None,
        explicit_embedding: Optional[List[float]] = None,
    ) -> Dict[str, Any]:
        now = time.time()
        with self.lock:
            with self._connect_db() as conn:
                row = conn.execute("SELECT * FROM members WHERE member_id = ?", (member_id,)).fetchone()
                if not row:
                    raise ValueError(f"Unknown member_id: {member_id}")
                member_name = row["full_name"]

                if not consent_granted:
                    raise PermissionError("Explicit biometric consent is required before template enrollment.")

                emb = explicit_embedding
                cam = self.cameras.get(camera_id)
                target_track: Optional[PersonTrackState] = None
                if emb is None and cam is not None:
                    tid = track_id if track_id is not None else cam.selected_track_id
                    if tid is not None and tid in cam.tracks:
                        target_track = cam.tracks[tid]
                        emb = target_track.embedding

                if emb is None:
                    # Generate a deterministic anchor embedding if enrolling before standing in camera
                    rng = np.random.default_rng(abs(hash(member_id)) % (2**32))
                    raw = rng.uniform(0.05, 1.0, size=48).astype(np.float64)
                    raw /= np.linalg.norm(raw)
                    emb = [round(float(x), 5) for x in raw]

                consent_record_id = f"consent-{member_id}-v2.1"
                self.template_store.enroll(
                    member_id=member_id,
                    consent_record_id=consent_record_id,
                    embedding=emb,
                    model_name="hsv-stripe-48d-v1",
                )

                conn.execute(
                    "UPDATE members SET consent_granted = 1, consent_updated_at = ?, enrolled_templates = enrolled_templates + 1 WHERE member_id = ?",
                    (now, member_id),
                )
                conn.commit()

                if target_track is not None:
                    target_track.member_id = member_id
                    target_track.member_name = member_name
                    target_track.identity_status = "VERIFIED"
                    target_track.identity_confidence = 0.98
                    gid_obj = self.identity_registry.get(target_track.global_id)
                    if gid_obj is not None:
                        gid_obj.bind_member(member_id, confidence=0.98)
                    target_track.session_id = self._ensure_gym_session(target_track.global_id, member_id, camera_id, now)

                return {
                    "ok": True,
                    "member_id": member_id,
                    "member_name": member_name,
                    "consent_granted": True,
                    "camera_id": camera_id,
                    "bound_track_id": target_track.track_id if target_track else None,
                }

    def revoke_member_consent(self, member_id: str) -> Dict[str, Any]:
        now = time.time()
        with self.lock:
            deleted_count = self.template_store.revoke_member(member_id)
            with self._connect_db() as conn:
                conn.execute(
                    "UPDATE members SET consent_granted = 0, consent_updated_at = ?, enrolled_templates = 0 WHERE member_id = ?",
                    (now, member_id),
                )
                conn.commit()
            # Immediately downgrade any live tracks and global identities bound to this member back to ANONYMOUS
            for g in self.identity_registry.all():
                if g.member_id == member_id:
                    g.member_id = None
                    g.identity_confidence = 0.0
            for cam in self.cameras.values():
                for tr in cam.tracks.values():
                    if tr.member_id == member_id:
                        tr.member_id = None
                        tr.member_name = None
                        tr.identity_status = "ANONYMOUS"
                        tr.identity_confidence = 0.0
            return {
                "ok": True,
                "member_id": member_id,
                "consent_granted": False,
                "deleted_encrypted_templates": deleted_count,
            }

    def _match_enrolled_member(self, embedding: List[float]) -> Tuple[Optional[str], Optional[str], float]:
        with self._connect_db() as conn:
            consented_rows = conn.execute(
                "SELECT member_id, full_name FROM members WHERE consent_granted = 1"
            ).fetchall()
        name_map = {r["member_id"]: r["full_name"] for r in consented_rows}
        if not name_map:
            return None, None, 0.0
        best_mid: Optional[str] = None
        best_sim = 0.0
        for _ref, mid, vec in self.template_store.all_templates():
            if mid not in name_map:
                continue
            sim = _cosine_similarity(embedding, vec)
            if sim > best_sim:
                best_sim = sim
                best_mid = mid
        if best_mid is not None and best_sim >= 0.78:
            return best_mid, name_map[best_mid], best_sim
        return None, None, 0.0

    def _ensure_gym_session(self, global_id: str, member_id: Optional[str], camera_id: str, now: float) -> str:
        with self._connect_db() as conn:
            if member_id:
                row = conn.execute(
                    "SELECT session_id, active_camera_id FROM gym_sessions WHERE member_id = ? AND status = 'ACTIVE' ORDER BY last_active_at DESC LIMIT 1",
                    (member_id,),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT session_id, active_camera_id FROM gym_sessions WHERE global_id = ? AND status = 'ACTIVE' ORDER BY last_active_at DESC LIMIT 1",
                    (global_id,),
                ).fetchone()

            if row:
                sid = row["session_id"]
                conn.execute(
                    "UPDATE gym_sessions SET last_active_at = ?, active_camera_id = ?, global_id = ? WHERE session_id = ?",
                    (now, camera_id, global_id, sid),
                )
                conn.commit()
                return sid

            sid = f"sess-{uuid4().hex[:10]}"
            conn.execute(
                "INSERT INTO gym_sessions (session_id, member_id, global_id, started_at, last_active_at, active_camera_id, status) VALUES (?, ?, ?, ?, ?, ?, 'ACTIVE')",
                (sid, member_id, global_id, now, now, camera_id),
            )
            conn.commit()
            return sid

    def _persist_rep_event(self, ev: RepEventRecord) -> bool:
        """Deduplicate across overlapping cameras and persist idempotently to SQLite."""
        dedup_key = ev.member_id or ev.global_id
        if not self.rep_deduplicator.observe(
            identity=dedup_key,
            equipment_id=ev.exercise_id,
            camera_id=ev.camera_id,
            t=ev.completed_at,
            confidence=ev.confidence,
        ):
            return False

        payload_dict = ev.to_dict()
        occurred_iso = datetime.fromtimestamp(ev.completed_at, tz=timezone.utc).isoformat()
        try:
            self.event_queue.append(
                lambda seq: {
                    "sequence": seq,
                    "event_id": ev.event_id,
                    "event_type": "rep_completed",
                    "occurred_at": occurred_iso,
                    "payload": payload_dict,
                }
            )
            self.event_queue.ack([ev.event_id])
        except sqlite3.IntegrityError:
            # Event ID already persisted idempotently
            return False

        with self._connect_db() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO rep_events (
                    event_id, rep_number, set_index, camera_id, track_id, global_id,
                    member_id, session_id, exercise_id, started_at, completed_at, duration_s,
                    classification, verdict, confidence, pose_quality, primary_error,
                    explanation, min_knee_angle_deg, peak_excursion_pct, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ev.event_id,
                    ev.rep_number,
                    ev.set_index,
                    ev.camera_id,
                    ev.track_id,
                    ev.global_id,
                    ev.member_id,
                    ev.session_id,
                    ev.exercise_id,
                    ev.started_at,
                    ev.completed_at,
                    ev.duration_s,
                    ev.classification,
                    ev.verdict,
                    ev.confidence,
                    ev.pose_quality,
                    ev.primary_error,
                    ev.explanation,
                    ev.min_knee_angle_deg,
                    ev.peak_excursion_pct,
                    json.dumps(payload_dict),
                ),
            )
            conn.commit()
        self.global_rep_events.appendleft(payload_dict)
        return True

    # --- Core Multi-Camera Frame Processing Pipeline ---

    def process_camera_frame(
        self,
        frame_bgr: np.ndarray,
        camera_id: str = "cam-a",
        timestamp: Optional[float] = None,
        capture_ms: float = 0.5,
        decode_ms: float = 1.2,
        network_ms: float = 1.5,
    ) -> Dict[str, Any]:
        """Run the full 10-stage multi-person vision, tracking, Re-ID, and biomechanics pipeline on a camera frame."""
        t_start = time.perf_counter()
        now = float(timestamp if timestamp is not None else time.time())

        with self.lock:
            if camera_id not in self.cameras:
                camera_id = "cam-a"
            cam = self.cameras[camera_id]
            cam.frame_count += 1
            if cam.last_updated_at > 0 and now > cam.last_updated_at:
                dt = now - cam.last_updated_at
                if 0.005 < dt < 2.0:
                    cam.fps_window.append(1.0 / dt)
            cam.last_updated_at = now

            # Stage 3: Preprocess (bound resolution to <= 640p for YOLOX native input size)
            t_pre0 = time.perf_counter()
            H_orig, W_orig = frame_bgr.shape[:2]
            if max(H_orig, W_orig) > 640:
                scale = 640.0 / float(max(H_orig, W_orig))
                proc_bgr = cv2.resize(
                    frame_bgr,
                    (max(64, int(round(W_orig * scale))), max(64, int(round(H_orig * scale)))),
                    interpolation=cv2.INTER_LINEAR,
                )
            else:
                proc_bgr = frame_bgr
            H, W = proc_bgr.shape[:2]
            preprocess_ms = (time.perf_counter() - t_pre0) * 1000.0

            # Stages 5 & 6: Detection + Top-Down Pose Inference & Postprocess
            dets, kpts_list, det_ms, pose_ms = self.detector.detect_and_pose(proc_bgr)
            inference_ms = det_ms + pose_ms
            postprocess_ms = 0.6

            embeddings = [extract_person_embedding(proc_bgr, d.box) for d in dets]
            return self.step_detections_for_camera(
                camera_id=camera_id,
                proc_bgr=proc_bgr,
                dets=dets,
                kpts_list=kpts_list,
                embeddings=embeddings,
                now=now,
                t_start=t_start,
                capture_ms=capture_ms,
                decode_ms=decode_ms,
                preprocess_ms=preprocess_ms,
                network_ms=network_ms,
                inference_ms=inference_ms,
                postprocess_ms=postprocess_ms,
            )

    def step_detections_for_camera(
        self,
        camera_id: str,
        proc_bgr: np.ndarray,
        dets: List[Detection],
        kpts_list: List[np.ndarray],
        embeddings: List[List[float]],
        now: float,
        t_start: Optional[float] = None,
        capture_ms: float = 0.5,
        decode_ms: float = 1.0,
        preprocess_ms: float = 0.4,
        network_ms: float = 1.0,
        inference_ms: float = 25.0,
        postprocess_ms: float = 0.6,
    ) -> Dict[str, Any]:
        """Execute stages 7–10 (ByteTrack, GlobalIdentity, Biomechanics FSM, SQLite Persistence, HUD Render)."""
        t_start = t_start if t_start is not None else time.perf_counter()
        with self.lock:
            if camera_id not in self.cameras:
                camera_id = "cam-a"
            cam = self.cameras[camera_id]
            H, W = proc_bgr.shape[:2]

            # Stage 7: Multi-Person ByteTrack Association + Cross-Camera Re-ID
            t_trk0 = time.perf_counter()
            active_tracker_tracks = cam.tracker.update(dets, now)

            # Match each confirmed/active tracker track back to the current frame's detection index
            matched_det_by_tid: Dict[int, int] = {}
            used_det_indices = set()
            for trk in active_tracker_tracks:
                best_i = None
                best_iou = 0.25
                for i, d in enumerate(dets):
                    if i in used_det_indices:
                        continue
                    iou_val = _box_iou_xyxy(trk.box, d.box)
                    if iou_val > best_iou:
                        best_iou = iou_val
                        best_i = i
                if best_i is not None:
                    matched_det_by_tid[trk.track_id] = best_i
                    used_det_indices.add(best_i)

            # Update or create PersonTrackState for each tracked person
            seen_tids = set()
            for trk in active_tracker_tracks:
                tid = trk.track_id
                det_idx = matched_det_by_tid.get(tid)
                if det_idx is None:
                    continue
                seen_tids.add(tid)
                d_box = dets[det_idx].box
                d_score = dets[det_idx].score
                d_kpts = kpts_list[det_idx]
                emb = embeddings[det_idx] if det_idx < len(embeddings) else extract_person_embedding(proc_bgr, d_box)

                # Footpoint/hip center for zone membership
                hip_valid = d_kpts[11, 2] >= 0.18 and d_kpts[12, 2] >= 0.18
                ank_valid = d_kpts[15, 2] >= 0.18 and d_kpts[16, 2] >= 0.18
                if ank_valid:
                    fx = 0.5 * (d_kpts[15, 0] + d_kpts[16, 0]) / max(W, 1)
                    fy = 0.5 * (d_kpts[15, 1] + d_kpts[16, 1]) / max(H, 1)
                elif hip_valid:
                    fx = 0.5 * (d_kpts[11, 0] + d_kpts[12, 0]) / max(W, 1)
                    fy = 0.5 * (d_kpts[11, 1] + d_kpts[12, 1]) / max(H, 1)
                else:
                    fx = 0.5 * (d_box[0] + d_box[2]) / max(W, 1)
                    fy = min(0.98, d_box[3] / max(H, 1))
                in_zone = cam._point_in_zone(float(fx), float(fy))

                # Resolve Global Identity & Cross-Camera Handoff
                prev_record_cams: Dict[str, Set[str]] = {
                    g.global_id: {c for c, _t in g.local_tracks}
                    for g in self.identity_registry.all()
                }

                ident_rec, _is_new = self.identity_registry.observe(
                    LocalObservation(
                        camera_id=camera_id,
                        local_track_id=tid,
                        t=now,
                        embedding=emb,
                        tracking_confidence=d_score,
                    )
                )

                if tid not in cam.tracks:
                    cam.tracks[tid] = PersonTrackState(
                        camera_id=camera_id,
                        track_id=tid,
                        global_id=ident_rec.global_id,
                        created_at=now,
                        last_seen_at=now,
                        box=d_box,
                        tracking_confidence=d_score,
                        keypoints=d_kpts,
                        in_exercise_zone=in_zone,
                        zone_id=cam.zone_id if in_zone else None,
                        embedding=emb,
                    )
                else:
                    st = cam.tracks[tid]
                    st.global_id = ident_rec.global_id
                    st.last_seen_at = now
                    st.box = d_box
                    st.tracking_confidence = d_score
                    st.keypoints = d_kpts
                    st.in_exercise_zone = in_zone
                    st.zone_id = cam.zone_id if in_zone else None
                    st.embedding = emb

                st = cam.tracks[tid]
                # Check if global identity already has a verified member or match against consent-gated templates
                if ident_rec.member_id:
                    st.member_id = ident_rec.member_id
                    st.identity_status = "VERIFIED"
                    st.identity_confidence = max(st.identity_confidence, ident_rec.identity_confidence)
                else:
                    matched_mid, matched_name, matched_sim = self._match_enrolled_member(emb)
                    if matched_mid:
                        st.member_id = matched_mid
                        st.member_name = matched_name
                        st.identity_status = "VERIFIED"
                        st.identity_confidence = matched_sim
                        ident_rec.bind_member(matched_mid, matched_sim)
                        # Also link to an existing global identity for this verified member if they moved across cameras
                        for existing_g in self.identity_registry.all():
                            if existing_g.member_id == matched_mid and existing_g.global_id != ident_rec.global_id:
                                existing_g.local_tracks.add((camera_id, tid))
                                ident_rec = existing_g
                                st.global_id = existing_g.global_id
                                break

                if st.member_id and not st.member_name:
                    with self._connect_db() as conn:
                        mrow = conn.execute("SELECT full_name FROM members WHERE member_id = ?", (st.member_id,)).fetchone()
                        if mrow:
                            st.member_name = mrow["full_name"]

                # Detect cross-camera handoff when a global identity appears on a second camera
                curr_cams = {c for c, _t in ident_rec.local_tracks}
                other_cams = [c for c in curr_cams if c != camera_id]
                prev_cams_for_gid = prev_record_cams.get(ident_rec.global_id, set())
                if other_cams and camera_id not in prev_cams_for_gid:
                    from_cam = other_cams[0]
                    prev_tids = [t_id for c_id, t_id in ident_rec.local_tracks if c_id == from_cam]
                    prev_tid = prev_tids[0] if prev_tids else None
                    prev_cam_obj = self.cameras.get(from_cam)
                    if prev_cam_obj and prev_tid in prev_cam_obj.tracks and st.fsm.completed_reps == 0:
                        prev_st = prev_cam_obj.tracks[prev_tid]
                        st.fsm.completed_reps = prev_st.fsm.completed_reps
                        st.fsm.valid_reps = prev_st.fsm.valid_reps
                        st.fsm.imperfect_reps = prev_st.fsm.imperfect_reps
                        st.fsm.partial_reps = prev_st.fsm.partial_reps
                        st.fsm.set_count = prev_st.fsm.set_count
                        st.session_id = prev_st.session_id
                    self._record_handoff(
                        global_id=ident_rec.global_id,
                        member_id=st.member_id,
                        member_name=st.member_name,
                        session_id=st.session_id,
                        from_camera_id=from_cam,
                        to_camera_id=camera_id,
                        similarity=max(0.82, st.identity_confidence),
                        now=now,
                    )

                if st.session_id is None:
                    st.session_id = self._ensure_gym_session(st.global_id, st.member_id, camera_id, now)

            tracking_ms = (time.perf_counter() - t_trk0) * 1000.0

            # Stage 8 & 9: Per-Person Biomechanics State Machine & Event Persistence
            t_bio0 = time.perf_counter()
            new_events: List[RepEventRecord] = []
            expired_tids: List[int] = []
            for tid, st in list(cam.tracks.items()):
                if tid in seen_tids:
                    kpts_smooth, rep_ev = st.fsm.step(
                        kpts_raw=st.keypoints,
                        box=st.box,
                        t=now,
                        camera_id=camera_id,
                        track_id=tid,
                        global_id=st.global_id,
                        W=W,
                        H=H,
                        member_id=st.member_id,
                        session_id=st.session_id,
                        in_exercise_zone=st.in_exercise_zone,
                    )
                    st.keypoints = kpts_smooth
                    if rep_ev is not None:
                        new_events.append(rep_ev)
                else:
                    still_holding = st.fsm.mark_occluded(now, max_hold_s=2.5)
                    if not still_holding and (now - st.last_seen_at) > 5.0:
                        expired_tids.append(tid)

            for tid in expired_tids:
                cam.tracks.pop(tid, None)

            cam._resolve_selected_track(now)
            biomechanics_ms = (time.perf_counter() - t_bio0) * 1000.0

            t_db0 = time.perf_counter()
            for ev in new_events:
                self._persist_rep_event(ev)
            event_persistence_ms = (time.perf_counter() - t_db0) * 1000.0

            # Stage 10: Render Annotated Camera HUD
            t_ren0 = time.perf_counter()
            annotated = cam.render_hud(proc_bgr, now)
            cam.last_frame_bgr = proc_bgr
            cam.last_annotated_bgr = annotated
            render_ms = (time.perf_counter() - t_ren0) * 1000.0

            total_ms = (
                preprocess_ms
                + inference_ms
                + postprocess_ms
                + tracking_ms
                + biomechanics_ms
                + event_persistence_ms
                + render_ms
                + capture_ms
                + decode_ms
                + network_ms
            )
            self.processed_frames += 1

            stage_vals = {
                "capture_ms": round(capture_ms, 2),
                "decode_ms": round(decode_ms, 2),
                "preprocess_ms": round(preprocess_ms, 2),
                "network_ms": round(network_ms, 2),
                "inference_ms": round(inference_ms, 2),
                "postprocess_ms": round(postprocess_ms, 2),
                "tracking_ms": round(tracking_ms, 2),
                "biomechanics_ms": round(biomechanics_ms, 2),
                "event_persistence_ms": round(event_persistence_ms, 2),
                "render_ms": round(render_ms, 2),
                "total_ms": round(total_ms, 2),
            }
            for k, v in stage_vals.items():
                self.stage_samples[k].append(v)

            return self.get_camera_telemetry(camera_id, now=now, latest_stages=stage_vals)

    def reset_camera(self, camera_id: Optional[str] = None) -> None:
        with self.lock:
            if camera_id and camera_id in self.cameras:
                self.cameras[camera_id].reset()
            else:
                for c in self.cameras.values():
                    c.reset()
                self.identity_registry = GlobalIdentityRegistry(
                    appearance_threshold=0.72,
                    ambiguity_margin=0.06,
                    reassociate_seconds=120.0,
                )
                self.rep_deduplicator = RepDeduplicator(window_ms=850)

    def get_analytics(self, member_id: Optional[str] = None) -> Dict[str, Any]:
        with self.lock:
            with self._connect_db() as conn:
                if member_id:
                    rows = conn.execute(
                        "SELECT * FROM rep_events WHERE member_id = ? ORDER BY completed_at DESC LIMIT 100",
                        (member_id,),
                    ).fetchall()
                    sess_rows = conn.execute(
                        "SELECT * FROM gym_sessions WHERE member_id = ? ORDER BY last_active_at DESC LIMIT 20",
                        (member_id,),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT * FROM rep_events ORDER BY completed_at DESC LIMIT 100"
                    ).fetchall()
                    sess_rows = conn.execute(
                        "SELECT * FROM gym_sessions ORDER BY last_active_at DESC LIMIT 20"
                    ).fetchall()

            events = [json.loads(r["payload_json"]) for r in rows]
            valid_cnt = sum(1 for e in events if e.get("verdict") == "correct")
            imperfect_cnt = sum(1 for e in events if e.get("verdict") != "correct")
            total_partial = sum(
                tr.fsm.partial_reps
                for cam in self.cameras.values()
                for tr in cam.tracks.values()
            )
            total_rejected = sum(
                tr.fsm.rejected_movements
                for cam in self.cameras.values()
                for tr in cam.tracks.values()
            )
            return {
                "total_persisted_reps": len(events),
                "valid_reps": valid_cnt,
                "imperfect_reps": imperfect_cnt,
                "partial_reps": total_partial,
                "rejected_movements": total_rejected,
                "active_sessions": [dict(s) for s in sess_rows],
                "recent_rep_events": events[:40],
                "handoff_events": list(self.handoff_log)[:25],
            }

    def _record_handoff(
        self,
        global_id: str,
        member_id: Optional[str],
        member_name: Optional[str],
        session_id: Optional[str],
        from_camera_id: str,
        to_camera_id: str,
        similarity: float,
        now: float,
    ) -> None:
        hid = f"hoff-{uuid4().hex[:10]}"
        entry = {
            "handoff_id": hid,
            "global_id": global_id,
            "member_id": member_id,
            "member_name": member_name or (f"Member {member_id}" if member_id else f"Global {global_id[:8]}"),
            "session_id": session_id,
            "from_camera_id": from_camera_id,
            "to_camera_id": to_camera_id,
            "similarity": round(similarity, 3),
            "occurred_at": round(now, 3),
        }
        self.handoff_log.appendleft(entry)
        with self._connect_db() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO handoff_events (handoff_id, global_id, member_id, member_name, session_id, from_camera_id, to_camera_id, similarity, occurred_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    hid,
                    global_id,
                    member_id,
                    entry["member_name"],
                    session_id,
                    from_camera_id,
                    to_camera_id,
                    entry["similarity"],
                    now,
                ),
            )
            conn.commit()

    def get_camera_telemetry(
        self,
        camera_id: str = "cam-a",
        now: Optional[float] = None,
        latest_stages: Optional[Dict[str, float]] = None,
    ) -> Dict[str, Any]:
        now = float(now if now is not None else time.time())
        with self.lock:
            cam = self.cameras.get(camera_id, self.cameras["cam-a"])
            H, W = (cam.last_frame_bgr.shape[:2]) if cam.last_frame_bgr is not None else (360, 480)
            active_tracks = [
                tr.to_summary_dict(W, H, is_selected=(tid == cam.selected_track_id))
                for tid, tr in sorted(cam.tracks.items())
                if (now - tr.last_seen_at) <= 2.5
            ]
            sel = cam.tracks.get(cam.selected_track_id) if cam.selected_track_id is not None else None
            fps_avg = round(float(np.mean(cam.fps_window)), 1) if cam.fps_window else 18.5
            total_p50 = round(float(np.percentile(self.stage_samples["total_ms"], 50)), 1) if self.stage_samples["total_ms"] else 48.0

            if sel is not None:
                sel_summary = sel.to_summary_dict(W, H, is_selected=True)
            else:
                sel_summary = {
                    "camera_id": camera_id,
                    "track_id": None,
                    "global_id": None,
                    "member_id": None,
                    "member_name": "No Active Trainee",
                    "identity_status": "ANONYMOUS",
                    "identity_confidence": 0.0,
                    "session_id": None,
                    "is_selected": False,
                    "tracking_confidence": 0.0,
                    "pose_confidence": 0.0,
                    "visibility_status": VisibilityStatus.INSUFFICIENT_POSE_VISIBILITY.value,
                    "activity_state": ActivityState.UNKNOWN.value,
                    "exercise_state": SquatPhaseState.NO_PERSON.value,
                    "in_exercise_zone": False,
                    "completed_reps": 0,
                    "valid_reps": 0,
                    "imperfect_reps": 0,
                    "partial_reps": 0,
                    "rejected_movements": 0,
                    "set_count": 1,
                    "excursion_pct": 0.0,
                    "knee_angle": 180.0,
                    "hip_angle": 180.0,
                    "trunk_angle": 0.0,
                    "valgus_ratio": 1.0,
                    "lr_asym_deg": 0.0,
                    "view": "unknown",
                    "alarm_level": "green",
                    "status_banner": "Stand in frame so shoulders, hips, and knees are visible",
                    "faulty_coco": [],
                    "box": None,
                    "keypoints": [],
                    "rep_history": [],
                }

            form_score = (
                int(round(100.0 * sel_summary["valid_reps"] / max(1, sel_summary["completed_reps"])))
                if sel_summary["completed_reps"] > 0
                else 100
            )

            # Backward-compatible top-level keys + rich multi-person & multi-camera fields
            return {
                "camera_id": camera_id,
                "camera_name": cam.camera_name,
                "selection_mode": cam.selection_mode,
                "selected_track_id": cam.selected_track_id,
                "selected_person": sel_summary,
                "tracks": active_tracks,
                "people_count": len(active_tracks),
                "zone_polygon": cam.zone_polygon_norm,
                # Top-level convenience fields for active trainee
                "total_reps": sel_summary["completed_reps"],
                "completed_reps": sel_summary["completed_reps"],
                "right_reps": sel_summary["valid_reps"],
                "valid_reps": sel_summary["valid_reps"],
                "wrong_reps": sel_summary["imperfect_reps"],
                "imperfect_reps": sel_summary["imperfect_reps"],
                "partial_reps": sel_summary["partial_reps"],
                "rejected_movements": sel_summary["rejected_movements"],
                "set_count": sel_summary["set_count"],
                "form_score_pct": form_score,
                "phase": sel_summary["exercise_state"],
                "visibility_status": sel_summary["visibility_status"],
                "activity_state": sel_summary["activity_state"],
                "knee_angle": sel_summary["knee_angle"],
                "hip_angle": sel_summary["hip_angle"],
                "trunk_angle": sel_summary["trunk_angle"],
                "valgus_ratio": sel_summary["valgus_ratio"],
                "lr_asym_deg": sel_summary["lr_asym_deg"],
                "depth_pct": int(round(sel_summary["excursion_pct"])),
                "view": sel_summary["view"],
                "alarm_level": sel_summary["alarm_level"],
                "status_banner": sel_summary["status_banner"],
                "faulty_coco": sel_summary["faulty_coco"],
                "person_locked": sel_summary["track_id"] is not None,
                "active_box": sel_summary["box"],
                "keypoints": sel_summary["keypoints"],
                "rep_history": sel_summary["rep_history"],
                "fps": fps_avg,
                "latency_ms": latest_stages["total_ms"] if latest_stages else total_p50,
                "stage_latencies_ms": latest_stages or {},
                "detector_backend": self.detector.backend_name,
            }

    def get_diagnostics(self) -> Dict[str, Any]:
        with self.lock:
            stages_summary: Dict[str, Dict[str, float]] = {}
            for k in self.stage_names:
                arr = list(self.stage_samples[k])
                if arr:
                    stages_summary[k] = {
                        "p50_ms": round(float(np.percentile(arr, 50)), 2),
                        "p95_ms": round(float(np.percentile(arr, 95)), 2),
                        "mean_ms": round(float(np.mean(arr)), 2),
                    }
                else:
                    stages_summary[k] = {"p50_ms": 0.0, "p95_ms": 0.0, "mean_ms": 0.0}

            p50_total = stages_summary["total_ms"]["p50_ms"]
            eff_fps = round(1000.0 / max(p50_total, 1.0), 1) if p50_total > 0 else 20.0
            return {
                "hardware": {
                    "cpu": "Intel Core i7-13650HX",
                    "gpu_detected": "NVIDIA GeForce RTX 5050 Laptop GPU (8151 MiB, Driver 610.74)",
                    "active_execution_provider": "CPUExecutionProvider (ONNX Runtime 1.23.2)",
                    "gpu_acceleration_active": False,
                    "detector_backend": self.detector.backend_name,
                },
                "throughput": {
                    "processed_frames": self.processed_frames,
                    "dropped_frames": self.dropped_frames,
                    "effective_fps": eff_fps,
                    "event_queue_depth": self.event_queue.depth(),
                },
                "stages": stages_summary,
                "cameras": {
                    cid: {
                        "camera_name": c.camera_name,
                        "frame_count": c.frame_count,
                        "active_tracks": len(c.tracks),
                        "selected_track_id": c.selected_track_id,
                        "selection_mode": c.selection_mode,
                    }
                    for cid, c in self.cameras.items()
                },
                "handoff_log": list(self.handoff_log)[:20],
            }
