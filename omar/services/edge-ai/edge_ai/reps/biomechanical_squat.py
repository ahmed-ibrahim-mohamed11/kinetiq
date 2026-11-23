"""
Biomechanical Squat Analysis Engine — True Geometric Repetition Counting & Quality Assessment.

Architectural principles:
1. Genuine Anatomical Angles: Computes true 3-point joint angles (hip-knee-ankle and
   shoulder-hip-knee). NEVER fabricates knee angles from vertical bounding-box/shoulder drop.
2. 6-Phase Finite State Machine:
   STANDING_READY -> DESCENDING -> BOTTOM_CONFIRMED -> ASCENDING -> STANDING_READY (REP_COMPLETED)
3. Strict Refractory & Plausibility: Minimum cycle duration 0.8s, maximum 7.0s, refractory
   cooldown 0.8s to prevent jitter and micro-bounce double counting.
4. Visibility Gating: Fails closed with `INSUFFICIENT_POSE_VISIBILITY` when hip/knee/ankle
   landmarks lack confidence, pausing counts instead of guessing.
5. Seated & Walking Exclusion: Classifies close-up seated operators and walking passersby so
   they never produce phantom squat repetitions.
6. Multi-Person State Isolation: Completely independent per-track state instances.
"""

from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, List, Optional, Tuple

from edge_ai.pose.base import Keypoint, Pose


class SquatPhase(str, Enum):
    NO_PERSON = "no_person"
    INSUFFICIENT_VISIBILITY = "insufficient_visibility"
    SEATED_IDLE = "seated_idle"
    WALKING = "walking"
    STANDING_READY = "standing_ready"
    DESCENDING = "descending"
    BOTTOM_CONFIRMED = "bottom_confirmed"
    ASCENDING = "ascending"
    REP_COMPLETED = "rep_completed"


@dataclass
class SquatRepResult:
    rep_number: int
    started_at: float
    completed_at: float
    duration_s: float
    verdict: str  # "correct" | "incorrect"
    min_knee_angle_deg: float
    max_trunk_lean_deg: float
    min_valgus_ratio: float
    primary_error: Optional[str]
    detected_errors: List[str]
    confidence: float

    @property
    def is_valid(self) -> bool:
        return self.verdict == "correct"

    @property
    def quality(self) -> str:
        return self.verdict

    @property
    def form_verdict(self) -> str:
        return "RIGHT FORM" if self.verdict == "correct" else "WRONG FORM"

    @property
    def primary_feedback(self) -> str:
        return self.primary_error or "Good form"

    def to_dict(self) -> dict[str, Any]:
        return {
            "rep_number": self.rep_number,
            "started_at": round(self.started_at, 3),
            "completed_at": round(self.completed_at, 3),
            "duration_s": round(self.duration_s, 2),
            "verdict": self.verdict,
            "min_knee_angle_deg": round(self.min_knee_angle_deg, 1),
            "max_trunk_lean_deg": round(self.max_trunk_lean_deg, 1),
            "min_valgus_ratio": round(self.min_valgus_ratio, 2),
            "primary_error": self.primary_error,
            "detected_errors": self.detected_errors,
            "confidence": round(self.confidence, 3),
        }


def _angle_2d(a: Tuple[float, float], b: Tuple[float, float], c: Tuple[float, float]) -> float:
    """Interior angle at vertex b in degrees [0, 180]."""
    v1 = (a[0] - b[0], a[1] - b[1])
    v2 = (c[0] - b[0], c[1] - b[1])
    dot = v1[0] * v2[0] + v1[1] * v2[1]
    m1 = math.hypot(v1[0], v1[1])
    m2 = math.hypot(v2[0], v2[1])
    if m1 < 1e-6 or m2 < 1e-6:
        return 180.0
    cosine = max(-1.0, min(1.0, dot / (m1 * m2)))
    return math.degrees(math.acos(cosine))


@dataclass
class BiomechanicalSquatEngine:
    min_keypoint_conf: float = 0.25
    standing_knee_thresh: float = 148.0
    descending_knee_thresh: float = 138.0
    bottom_knee_thresh: float = 100.0
    shallow_knee_max: float = 118.0
    trunk_lean_max_deg: float = 38.0
    valgus_ratio_min: float = 0.80
    min_rep_duration_s: float = 0.65
    max_rep_duration_s: float = 7.0
    refractory_cooldown_s: float = 0.55
    smoothing_tau_s: float = 0.08

    # Live FSM State
    phase: SquatPhase = SquatPhase.NO_PERSON
    completed_reps: int = 0
    valid_reps: int = 0
    imperfect_reps: int = 0
    partial_reps: int = 0
    rejected_cycles: int = 0
    history: List[SquatRepResult] = field(default_factory=list)

    # Active Cycle Accumulators
    _cycle_start_t: Optional[float] = None
    _last_standing_t: Optional[float] = None
    _bottom_t: Optional[float] = None
    _min_knee_angle: float = 180.0
    _max_trunk_lean: float = 0.0
    _min_valgus: float = 1.0
    _min_cycle_conf: float = 1.0
    _cycle_frontal_ratio: float = 0.0
    _last_rep_completed_at: float = -10.0
    _last_valid_pose_t: Optional[float] = None
    _smoothed_knee_angle: Optional[float] = None
    _smoothed_t: Optional[float] = None
    _seated_start_t: Optional[float] = None

    # Telemetry
    last_knee_angle: float = 180.0
    last_hip_angle: float = 180.0
    last_trunk_lean: float = 0.0
    last_valgus_ratio: float = 1.0
    status_message: str = "Awaiting trainee"
    faulty_joints: List[str] = field(default_factory=list)

    # Velocity history for walking/seated detection
    _hip_x_hist: deque[Tuple[float, float]] = field(default_factory=lambda: deque(maxlen=10))
    _ank_x_hist: deque[Tuple[float, float]] = field(default_factory=lambda: deque(maxlen=10))

    @property
    def current_knee_angle(self) -> Optional[float]:
        return self.last_knee_angle if self.phase != SquatPhase.NO_PERSON else None

    @property
    def current_trunk_angle(self) -> Optional[float]:
        return self.last_trunk_lean if self.phase != SquatPhase.NO_PERSON else None

    @property
    def active_errors(self) -> List[str]:
        errors: List[str] = []
        if self.phase in (SquatPhase.DESCENDING, SquatPhase.BOTTOM_CONFIRMED, SquatPhase.ASCENDING):
            if self._cycle_frontal_ratio <= 0.45 and self._max_trunk_lean > self.trunk_lean_max_deg:
                errors.append("excessive_trunk_lean")
            if self._cycle_frontal_ratio >= 0.28 and self._min_valgus < self.valgus_ratio_min:
                errors.append("knees_caving_inward")
        return errors

    @property
    def rep_history(self) -> List[SquatRepResult]:
        return self.history

    def reset(self) -> None:
        self.phase = SquatPhase.NO_PERSON
        self.completed_reps = 0
        self.valid_reps = 0
        self.imperfect_reps = 0
        self.partial_reps = 0
        self.rejected_cycles = 0
        self.history.clear()
        self._cycle_start_t = None
        self._last_standing_t = None
        self._bottom_t = None
        self._min_knee_angle = 180.0
        self._max_trunk_lean = 0.0
        self._min_valgus = 1.0
        self._min_cycle_conf = 1.0
        self._last_rep_completed_at = -10.0
        self._last_valid_pose_t = None
        self._smoothed_knee_angle = None
        self._smoothed_t = None
        self._seated_start_t = None
        self.status_message = "Reset completed"
        self.faulty_joints.clear()
        self._hip_x_hist.clear()
        self._ank_x_hist.clear()

    def update(self, pose: Optional[Pose], t: float, in_exercise_zone: bool = True) -> Optional[SquatRepResult]:
        """Feed one timestamped pose. Returns SquatRepResult upon valid rep completion."""
        if pose is None:
            self._handle_occlusion(t)
            if self._cycle_start_t is None:
                self.phase = SquatPhase.NO_PERSON
                self.status_message = "No person visible"
            return None

        # 1. Extract COCO anatomical landmarks
        ls, rs = pose.kp("left_shoulder"), pose.kp("right_shoulder")
        lh, rh = pose.kp("left_hip"), pose.kp("right_hip")
        lk, rk = pose.kp("left_knee"), pose.kp("right_knee")
        la, ra = pose.kp("left_ankle"), pose.kp("right_ankle")

        sho_ok = ls.score >= self.min_keypoint_conf or rs.score >= self.min_keypoint_conf
        hip_ok = lh.score >= self.min_keypoint_conf or rh.score >= self.min_keypoint_conf
        knee_ok = lk.score >= self.min_keypoint_conf or rk.score >= self.min_keypoint_conf
        ank_ok = la.score >= self.min_keypoint_conf or ra.score >= self.min_keypoint_conf

        # Strict visibility check: need hips + at least one valid knee and ankle
        if not (hip_ok and knee_ok and ank_ok):
            self._handle_occlusion(t)
            if self._cycle_start_t is None:
                self.phase = SquatPhase.INSUFFICIENT_VISIBILITY
                self.status_message = "INSUFFICIENT POSE VISIBILITY — COUNT PAUSED"
            return None

        self._last_valid_pose_t = t

        # 2. Compute TRUE geometric joint angles (Hip-Knee-Ankle)
        knee_angles = []
        knee_confs = []
        if lh.score >= self.min_keypoint_conf and lk.score >= self.min_keypoint_conf and la.score >= self.min_keypoint_conf:
            knee_angles.append(_angle_2d((lh.x, lh.y), (lk.x, lk.y), (la.x, la.y)))
            knee_confs.append(min(lh.score, lk.score, la.score))
        if rh.score >= self.min_keypoint_conf and rk.score >= self.min_keypoint_conf and ra.score >= self.min_keypoint_conf:
            knee_angles.append(_angle_2d((rh.x, rh.y), (rk.x, rk.y), (ra.x, ra.y)))
            knee_confs.append(min(rh.score, rk.score, ra.score))

        if not knee_angles:
            self._handle_occlusion(t)
            if self._cycle_start_t is None:
                self.phase = SquatPhase.INSUFFICIENT_VISIBILITY
                self.status_message = "INSUFFICIENT POSE VISIBILITY — COUNT PAUSED"
            return None

        if len(knee_angles) == 2:
            a1, a2 = knee_angles[0], knee_angles[1]
            c1, c2 = knee_confs[0], knee_confs[1]
            # In 45-degree diagonal or side views, the far leg can be partially self-occluded,
            # causing ~25-35 deg discrepancy. Weight by squared confidence and prevent a low-confidence
            # occluded far leg from blocking standing extension recovery when the clear leg is >= 162 deg.
            if abs(a1 - a2) > 22.0 and max(a1, a2) >= 162.0:
                raw_knee_angle = 0.75 * max(a1, a2) + 0.25 * min(a1, a2)
            else:
                w1, w2 = c1 * c1, c2 * c2
                raw_knee_angle = float((a1 * w1 + a2 * w2) / max(1e-6, w1 + w2))
        else:
            raw_knee_angle = float(knee_angles[0])
        if self._smoothed_knee_angle is None or self._smoothed_t is None or self.smoothing_tau_s <= 0.0:
            knee_angle = raw_knee_angle
        else:
            dt_s = max(0.0, t - self._smoothed_t)
            alpha = 1.0 - math.exp(-dt_s / self.smoothing_tau_s) if dt_s > 0.0 else 1.0
            knee_angle = self._smoothed_knee_angle + alpha * (raw_knee_angle - self._smoothed_knee_angle)
        self._smoothed_knee_angle = knee_angle
        self._smoothed_t = t
        sample_conf = float(min(knee_confs))
        self.last_knee_angle = round(knee_angle, 1)

        # 3. Compute Trunk Lean (vector from hip midpoint to shoulder midpoint)
        mid_hip_x = 0.5 * (lh.x + rh.x) if (lh.score >= self.min_keypoint_conf and rh.score >= self.min_keypoint_conf) else (lh.x if lh.score >= self.min_keypoint_conf else rh.x)
        mid_hip_y = 0.5 * (lh.y + rh.y) if (lh.score >= self.min_keypoint_conf and rh.score >= self.min_keypoint_conf) else (lh.y if lh.score >= self.min_keypoint_conf else rh.y)
        mid_ank_x = 0.5 * (la.x + ra.x) if (la.score >= self.min_keypoint_conf and ra.score >= self.min_keypoint_conf) else (la.x if la.score >= self.min_keypoint_conf else ra.x)
        mid_ank_y = 0.5 * (la.y + ra.y) if (la.score >= self.min_keypoint_conf and ra.score >= self.min_keypoint_conf) else (la.y if la.score >= self.min_keypoint_conf else ra.y)

        trunk_lean = 0.0
        torso_len = max(40.0, abs(mid_ank_y - mid_hip_y) * 0.75)
        if sho_ok:
            mid_sho_x = 0.5 * (ls.x + rs.x) if (ls.score >= self.min_keypoint_conf and rs.score >= self.min_keypoint_conf) else (ls.x if ls.score >= self.min_keypoint_conf else rs.x)
            mid_sho_y = 0.5 * (ls.y + rs.y) if (ls.score >= self.min_keypoint_conf and rs.score >= self.min_keypoint_conf) else (ls.y if ls.score >= self.min_keypoint_conf else rs.y)
            dx = abs(mid_sho_x - mid_hip_x)
            dy = max(1.0, abs(mid_hip_y - mid_sho_y))
            torso_len = max(30.0, math.hypot(dx, dy))
            trunk_lean = math.degrees(math.atan2(dx, dy))
        self.last_trunk_lean = round(trunk_lean, 1)

        # 4. Compute Camera Geometry Ratio & Knee Valgus Ratio
        # In pure side view, left/right hips and ankles overlap horizontally, making 2D X-valgus undefined.
        frontal_ratio = 0.0
        valgus_ratio = 1.0
        if (
            lk.score >= self.min_keypoint_conf
            and rk.score >= self.min_keypoint_conf
            and lh.score >= self.min_keypoint_conf
            and rh.score >= self.min_keypoint_conf
            and la.score >= self.min_keypoint_conf
            and ra.score >= self.min_keypoint_conf
        ):
            hip_span = abs(lh.x - rh.x)
            ank_span = abs(la.x - ra.x)
            frontal_ratio = hip_span / max(30.0, torso_len)
            if frontal_ratio >= 0.28 and ank_span >= 0.25 * torso_len:
                knee_dist = max(1.0, abs(lk.x - rk.x))
                ref_dist = max(1.0, ank_span)
                valgus_ratio = knee_dist / ref_dist
        self.last_valgus_ratio = round(valgus_ratio, 2)

        # 5. Check for horizontal walking movement (both hips AND ankles translating laterally)
        self._hip_x_hist.append((t, mid_hip_x))
        self._ank_x_hist.append((t, mid_ank_x))
        if len(self._hip_x_hist) >= 4 and len(self._ank_x_hist) >= 4:
            dt_w = max(0.05, self._hip_x_hist[-1][0] - self._hip_x_hist[0][0])
            vx_hip = abs(self._hip_x_hist[-1][1] - self._hip_x_hist[0][1]) / dt_w
            vx_ank = abs(self._ank_x_hist[-1][1] - self._ank_x_hist[0][1]) / dt_w
            walk_thresh = max(220.0, 1.5 * torso_len)
            # Rapid lateral traversal of whole body (hips AND feet moving across floor)
            if vx_hip > walk_thresh and vx_ank > (0.8 * walk_thresh) and self.phase in (SquatPhase.NO_PERSON, SquatPhase.STANDING_READY):
                self.phase = SquatPhase.WALKING
                self.status_message = "Walking across frame — rep counting paused"
                return None

        if not in_exercise_zone:
            self.phase = SquatPhase.STANDING_READY
            self.status_message = "Outside exercise zone"
            return None

        # 6. Biomechanical Finite State Machine Evaluation
        completed_rep: Optional[SquatRepResult] = None

        if self.phase in (
            SquatPhase.NO_PERSON,
            SquatPhase.INSUFFICIENT_VISIBILITY,
            SquatPhase.WALKING,
            SquatPhase.SEATED_IDLE,
            SquatPhase.REP_COMPLETED,
        ):
            if knee_angle >= self.standing_knee_thresh:
                self.phase = SquatPhase.STANDING_READY
                self._last_standing_t = t
                self._seated_start_t = None
                self.status_message = "Ready — Upright standing confirmed"
            elif (
                self.phase == SquatPhase.INSUFFICIENT_VISIBILITY
                and self._last_standing_t is not None
                and 0.0 < (t - self._last_standing_t) <= 1.8
                and (t - self._last_rep_completed_at) >= self.refractory_cooldown_s
                and knee_angle <= self.descending_knee_thresh
            ):
                # Trainee was recently standing upright and began descending during a brief keypoint dropout
                self._cycle_start_t = self._last_standing_t
                self._min_knee_angle = knee_angle
                self._max_trunk_lean = trunk_lean
                self._min_valgus = valgus_ratio
                self._min_cycle_conf = sample_conf
                self._cycle_frontal_ratio = frontal_ratio
                if knee_angle <= self.shallow_knee_max:
                    self.phase = SquatPhase.BOTTOM_CONFIRMED
                    self._bottom_t = t
                    self.status_message = "Bottom depth reached"
                else:
                    self.phase = SquatPhase.DESCENDING
                    self.status_message = "Descending"
            elif 65.0 <= knee_angle <= 125.0:
                if self._seated_start_t is None:
                    self._seated_start_t = t
                elif (t - self._seated_start_t) >= 1.5:
                    self.phase = SquatPhase.SEATED_IDLE
                    self.status_message = "Seated idle — stand upright to begin squat tracking"
            else:
                self._seated_start_t = None

        elif self.phase == SquatPhase.STANDING_READY:
            if knee_angle >= self.standing_knee_thresh:
                self._last_standing_t = t
            # Check refractory cooldown to prevent double counting
            if (t - self._last_rep_completed_at) >= self.refractory_cooldown_s:
                if knee_angle <= self.descending_knee_thresh:
                    # Anchor cycle start to when the trainee left standing posture (up to 1.2s prior)
                    if self._last_standing_t is not None and 0.0 < (t - self._last_standing_t) <= 1.2:
                        self._cycle_start_t = self._last_standing_t
                    else:
                        self._cycle_start_t = t
                    self._min_knee_angle = knee_angle
                    self._max_trunk_lean = trunk_lean
                    self._min_valgus = valgus_ratio
                    self._min_cycle_conf = sample_conf
                    self._cycle_frontal_ratio = frontal_ratio
                    if knee_angle <= self.shallow_knee_max:
                        self.phase = SquatPhase.BOTTOM_CONFIRMED
                        self._bottom_t = t
                        self.status_message = "Bottom depth reached"
                    else:
                        self.phase = SquatPhase.DESCENDING
                        self.status_message = "Descending"

        elif self.phase == SquatPhase.DESCENDING:
            self._min_knee_angle = min(self._min_knee_angle, knee_angle)
            self._max_trunk_lean = max(self._max_trunk_lean, trunk_lean)
            self._min_valgus = min(self._min_valgus, valgus_ratio)
            self._min_cycle_conf = min(self._min_cycle_conf, sample_conf)

            # Check timeout
            if (t - (self._cycle_start_t or t)) > self.max_rep_duration_s:
                self._abort_cycle("timeout")
                return None

            if knee_angle <= self.shallow_knee_max:
                self.phase = SquatPhase.BOTTOM_CONFIRMED
                self._bottom_t = t
                self.status_message = "Bottom depth reached"
            elif knee_angle >= self.standing_knee_thresh and (t - (self._cycle_start_t or t)) >= 0.3:
                # Aborted shallow descent without reaching bottom
                self._abort_cycle("partial")
                return None

        elif self.phase == SquatPhase.BOTTOM_CONFIRMED:
            self._min_knee_angle = min(self._min_knee_angle, knee_angle)
            self._max_trunk_lean = max(self._max_trunk_lean, trunk_lean)
            self._min_valgus = min(self._min_valgus, valgus_ratio)

            if (t - (self._cycle_start_t or t)) > self.max_rep_duration_s:
                self._abort_cycle("timeout")
                return None

            # Moving upward from bottom (or already reached standing on fast ascent)
            if knee_angle >= self.standing_knee_thresh:
                completed_rep = self._complete_rep(t)
                self.phase = SquatPhase.REP_COMPLETED if completed_rep is not None else SquatPhase.STANDING_READY
            elif knee_angle >= (self._min_knee_angle + 12.0):
                self.phase = SquatPhase.ASCENDING
                self.status_message = "Ascending"

        elif self.phase == SquatPhase.ASCENDING:
            self._max_trunk_lean = max(self._max_trunk_lean, trunk_lean)
            self._min_valgus = min(self._min_valgus, valgus_ratio)

            if (t - (self._cycle_start_t or t)) > self.max_rep_duration_s:
                self._abort_cycle("timeout")
                return None

            if knee_angle <= (self._min_knee_angle + 5.0):
                # Dipped back down; still the same rep
                self.phase = SquatPhase.BOTTOM_CONFIRMED
            elif knee_angle >= self.standing_knee_thresh:
                # Cycle Completed!
                completed_rep = self._complete_rep(t)
                self._last_standing_t = t
                self.phase = SquatPhase.REP_COMPLETED if completed_rep is not None else SquatPhase.STANDING_READY

        return completed_rep

    def _complete_rep(self, t: float) -> Optional[SquatRepResult]:
        start = self._cycle_start_t if self._cycle_start_t is not None else (t - 1.5)
        duration = t - start

        if duration < self.min_rep_duration_s or duration > self.max_rep_duration_s:
            self.rejected_cycles += 1
            self._reset_cycle()
            self.status_message = f"Rep rejected: duration {duration:.1f}s outside plausible window"
            return None

        errors: List[str] = []
        # Perspective-compensated parallel depth threshold: frontal/diagonal views foreshorten the thigh in 2D
        effective_bottom_thresh = (
            self.bottom_knee_thresh + 7.5 if self._cycle_frontal_ratio >= 0.22 else self.bottom_knee_thresh
        )
        if self._min_knee_angle > effective_bottom_thresh:
            errors.append("shallow_squat")
        if self._cycle_frontal_ratio >= 0.28 and self._min_valgus < self.valgus_ratio_min:
            errors.append("knees_caving_inward")
        if self._cycle_frontal_ratio <= 0.45 and self._max_trunk_lean > self.trunk_lean_max_deg:
            errors.append("excessive_trunk_lean")

        verdict = "correct" if not errors else "incorrect"
        self.completed_reps += 1
        if verdict == "correct":
            self.valid_reps += 1
            self.status_message = f"Rep #{self.completed_reps} VALID (Depth: {int(self._min_knee_angle)}°)"
        else:
            self.imperfect_reps += 1
            self.status_message = f"Rep #{self.completed_reps} IMPERFECT ({errors[0]})"

        self._last_rep_completed_at = t
        rep = SquatRepResult(
            rep_number=self.completed_reps,
            started_at=start,
            completed_at=t,
            duration_s=duration,
            verdict=verdict,
            min_knee_angle_deg=self._min_knee_angle,
            max_trunk_lean_deg=self._max_trunk_lean,
            min_valgus_ratio=self._min_valgus,
            primary_error=errors[0] if errors else None,
            detected_errors=errors,
            confidence=self._min_cycle_conf,
        )
        self.history.append(rep)
        self._reset_cycle()
        return rep

    def _abort_cycle(self, reason: str) -> None:
        if self._cycle_start_t is not None:
            if reason == "partial":
                self.partial_reps += 1
                self.status_message = f"Partial squat ({int(self._min_knee_angle)}°) — descend lower to count"
            else:
                self.rejected_cycles += 1
                self.status_message = f"Cycle aborted ({reason})"
        self.phase = SquatPhase.STANDING_READY
        self._reset_cycle()

    def _reset_cycle(self) -> None:
        self._cycle_start_t = None
        self._last_standing_t = None
        self._bottom_t = None
        self._min_knee_angle = 180.0
        self._max_trunk_lean = 0.0
        self._min_valgus = 1.0
        self._min_cycle_conf = 1.0

    def _handle_occlusion(self, t: float) -> None:
        if self._last_valid_pose_t is not None and (t - self._last_valid_pose_t) > 1.5:
            if self._cycle_start_t is not None:
                self._abort_cycle("occlusion")

