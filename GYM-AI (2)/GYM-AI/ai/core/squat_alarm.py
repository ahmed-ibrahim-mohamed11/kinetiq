"""Squat Assessment System — Task 2 & Task 4: Squat Phase Segmentation + Instant Alarm Engine (<=200ms).

Implements:
1. Fast Online Squat Phase Tracker (`SquatPhaseTracker`):
   - Real-time causal tracking of the 4 Squat states from Proposal Figure 4:
     `REST` -> `DESCENT` -> `BOTTOM` -> `ASCENT` -> (`REST`, rep complete).
   - Emits completed reps (`start_frame`, `bottom_frame`, `end_frame`, duration, depth, angles).

2. Instant Alarm Engine (`SquatAlarmEngine`, Proposal Figure 5 & Poster Box 06):
   - Evaluates phase-aware biomechanical rules on EVERY frame with <= 200 ms latency:
     * Whole rep: `heel_lift`, `lr_asymmetry`
     * `DESCENT`: `knees_forward_error` (KFE)
     * `BOTTOM`:  `shallow_squat` (SS), `excessive_trunk_lean`
     * `ASCENT`:  `knees_caving_inward` (KIE)
   - Implements the exact 4-step decision tree from Proposal Figure 5:
     Step 1: Joints visible AND error observable from current camera view?
             -> No  => "uncertain" (gray, no verdict)
     Step 2: Error rule triggered in current phase?
             -> No  => "green" (movement OK)
     Step 3: Persists for >= 4 consecutive frames (debounce)?
             -> No  => "yellow" (possible deviation)
     Step 4: First alarm for this error in this rep (cooldown)?
             -> Yes => "RED ALARM" (faulty joint IDs + text message + sound trigger)
             -> No  => already alarmed in this rep (no repeat sound spam)

3. Offline & HSMM-Assisted Squat Rep Segmenter (`segment_squat_reps`):
   - Segments complete Squat repetitions (`start`, `bottom`, `end`) and per-frame phases
     (`REST`=0, `DESCENT`=1, `BOTTOM`=2, `ASCENT`=3) from `prepare_squat_video` output.
"""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Literal, Optional, Tuple

import numpy as np
from scipy.signal import find_peaks

from ai.core.config import L_HIP, L_SHO, PELVIS, R_HIP, R_SHO, THORAX
from ai.core.squat_features import (
    L_ANK,
    L_KNEE,
    R_ANK,
    R_KNEE,
    OneEuroFilter,
    detect_camera_view,
    summarize_squat_rep_features,
)

PhaseName = Literal["REST", "DESCENT", "BOTTOM", "ASCENT"]
AlarmState = Literal["uncertain", "green", "yellow", "red_alarm"]

PHASE_CODES: Dict[PhaseName, int] = {
    "REST": 0,
    "DESCENT": 1,
    "BOTTOM": 2,
    "ASCENT": 3,
}

# Anatomical joint indices (in H36M-17 and COCO-17) highlighted in RED when each error fires (FR8)
ERROR_SPEC: Dict[str, dict] = {
    "knees_caving_inward": {
        "code": "KIE",
        "name": "Knees Caving Inward",
        "phase": ("ASCENT", "BOTTOM"),
        "h36m_joints": [L_KNEE, R_KNEE],
        "coco_joints": [13, 14],  # L_knee, R_knee
        "message": "Push your knees outward in line with your toes!",
        "core": True,
    },
    "knees_forward_error": {
        "code": "KFE",
        "name": "Excessive Forward Knee Travel",
        "phase": ("DESCENT", "BOTTOM"),
        "h36m_joints": [L_KNEE, R_KNEE],
        "coco_joints": [13, 14],
        "message": "Sit your hips back — knees are traveling too far forward!",
        "core": True,
    },
    "shallow_squat": {
        "code": "SS",
        "name": "Insufficient Squat Depth",
        "phase": ("BOTTOM",),
        "h36m_joints": [L_HIP, R_HIP, L_KNEE, R_KNEE],
        "coco_joints": [11, 12, 13, 14],
        "message": "Squat deeper — lower hips until thighs reach parallel!",
        "core": True,
    },
    "excessive_trunk_lean": {
        "code": "ETL",
        "name": "Excessive Trunk Lean",
        "phase": ("BOTTOM", "ASCENT"),
        "h36m_joints": [THORAX, PELVIS, L_SHO, R_SHO],
        "coco_joints": [5, 6, 11, 12],
        "message": "Keep your chest up and brace your core!",
        "core": False,
    },
    "heel_lift": {
        "code": "HL",
        "name": "Heel Lift",
        "phase": ("DESCENT", "BOTTOM", "ASCENT"),
        "h36m_joints": [L_ANK, R_ANK],
        "coco_joints": [15, 16],
        "message": "Keep your heels planted flat on the floor!",
        "core": False,
    },
    "lr_asymmetry": {
        "code": "ASYM",
        "name": "Left/Right Asymmetry",
        "phase": ("DESCENT", "BOTTOM", "ASCENT"),
        "h36m_joints": [L_HIP, R_HIP, L_KNEE, R_KNEE],
        "coco_joints": [11, 12, 13, 14],
        "message": "Balance weight evenly between your left and right legs!",
        "core": False,
    },
}


@dataclass
class SquatThresholds:
    """Biomechanical thresholds for squat phase detection and error rules.

    Calibrated from multi-subject good-form squat distributions (Proposal Gap 11).
    """
    # Phase thresholds on `squat_signal` (0 = standing, ~0.6-1.0 = bottom)
    descent_start_signal: float = 0.16
    bottom_enter_signal: float = 0.42
    rest_return_signal: float = 0.14
    min_rep_s: float = 0.80
    max_rep_s: float = 8.00

    # Error thresholds
    valgus_ratio_min: float = 0.82          # KIE: knee_width / ankle_width < 0.82 triggers
    forward_knee_max: float = 0.52          # KFE: knee forward of ankle > 0.52 * tibia_len triggers
    shallow_knee_angle_max: float = 115.0   # SS: min knee angle > 115° (or depth_rel > 0.18) at bottom triggers
    shallow_depth_rel_max: float = 0.18     # SS: hip_above_knee / femur_len > 0.18 at bottom triggers
    trunk_lean_max_deg: float = 43.0        # ETL: trunk angle from vertical > 43° triggers
    heel_lift_max_ratio: float = 0.12       # HL: upward ankle rise > 0.12 * tibia_len triggers
    lr_knee_diff_max_deg: float = 14.0      # ASYM: |L - R knee angle| > 14° triggers

    # Debounce frames (Proposal Figure 5: persists for >= 4 frames)
    debounce_frames: int = 4

    def to_dict(self) -> dict:
        return asdict(self)


# -----------------------------------------------------------------------------
# Offline / Batch Squat Rep & Phase Segmenter (for Mode A & Dataset Training)
# -----------------------------------------------------------------------------
def segment_squat_reps(
    v: dict,
    thresholds: Optional[SquatThresholds] = None,
) -> Tuple[List[dict], np.ndarray]:
    """Segment a Squat video into repetitions (`start`, `bottom`, `end`) and per-frame phases.

    Uses the smoothed `squat_signal` (hip descent + knee flexion) with prominence-adaptive
    peak detection and velocity boundary refinement, plus HSMM-compatible phase assignment:
      0 = REST, 1 = DESCENT, 2 = BOTTOM, 3 = ASCENT.
    """
    thr = thresholds or SquatThresholds()
    sig = np.asarray(v["squat_signal"], dtype=np.float64)
    fps = float(v["fps"])
    T = len(sig)
    phases = np.zeros(T, dtype=int)  # default REST (0)

    if T < max(10, int(thr.min_rep_s * fps)):
        return [], phases

    min_dist = max(4, int(0.85 * thr.min_rep_s * fps))
    # Find bottom apexes (peaks of squat_signal)
    peaks, _ = find_peaks(
        sig,
        height=max(0.25, thr.bottom_enter_signal * 0.75),
        distance=min_dist,
        prominence=0.18,
    )

    reps: List[dict] = []
    vel = np.gradient(sig) * fps

    for idx, pk in enumerate(peaks):
        pk_val = float(sig[pk])
        # Search left for start of descent
        left_bound = reps[-1]["end"] if reps else 0
        s = int(pk)
        base_thr = max(thr.rest_return_signal, 0.18 * pk_val)
        while s > left_bound and (sig[s - 1] > base_thr or vel[s - 1] > 0.08):
            if sig[s - 1] > sig[s] + 0.03 and (pk - s) >= int(0.25 * fps):
                break
            s -= 1

        # Search right for end of ascent
        right_bound = int(peaks[idx + 1]) - 1 if (idx + 1 < len(peaks)) else (T - 1)
        e = int(pk)
        while e < right_bound and (sig[e + 1] > base_thr or vel[e + 1] < -0.08):
            if sig[e + 1] > sig[e] + 0.03 and (e - pk) >= int(0.25 * fps):
                break
            e += 1

        dur_s = (e - s) / fps
        if not (thr.min_rep_s * 0.65 <= dur_s <= thr.max_rep_s):
            continue
        if not (s < pk < e):
            continue

        # Assign phases inside [s, e]:
        # BOTTOM is the region around pk where signal >= 0.82 * pk_val
        bot_thr = 0.82 * pk_val
        b_lo = int(pk)
        while b_lo > s and sig[b_lo - 1] >= bot_thr:
            b_lo -= 1
        b_hi = int(pk)
        while b_hi < e and sig[b_hi + 1] >= bot_thr:
            b_hi += 1

        phases[s:b_lo] = PHASE_CODES["DESCENT"]
        phases[b_lo : b_hi + 1] = PHASE_CODES["BOTTOM"]
        phases[b_hi + 1 : e + 1] = PHASE_CODES["ASCENT"]

        rep_metrics = summarize_squat_rep_features(v, s, int(pk), e)
        rep = {
            "index": len(reps) + 1,
            "start": int(s),
            "bottom": int(pk),
            "pull": int(pk),  # alias for compatibility with existing GYM-AI evaluators
            "end": int(e),
            "bottom_lo": int(b_lo),
            "bottom_hi": int(b_hi),
            "peak": round(pk_val, 4),
            "truncated": bool(s == 0 or e >= T - 1),
            **rep_metrics,
        }
        reps.append(rep)

    return reps, phases


# -----------------------------------------------------------------------------
# Fast Per-Frame Instant Alarm Engine (Proposal Figure 5 — Target <= 200 ms)
# -----------------------------------------------------------------------------
@dataclass
class FrameAlarmEvent:
    frame_index: int
    timestamp_s: float
    phase: PhaseName
    state: AlarmState                   # "uncertain" | "green" | "yellow" | "red_alarm"
    active_errors: List[str]            # errors currently persisted >= debounce_frames
    newly_triggered: List[str]          # errors firing their FIRST red alarm in this rep (play sound!)
    yellow_warnings: List[str]          # errors triggered 1..debounce_frames-1 frames
    uncertain_errors: List[str]         # errors unobservable from current view or low conf
    faulty_h36m_joints: List[int]       # joints to highlight in RED on screen
    faulty_coco_joints: List[int]
    primary_message: str
    latency_ms: float


class SquatAlarmEngine:
    """Real-time causal per-frame Phase + Instant Alarm Engine (Proposal Section 3.6 & Figure 5).

    Enforces:
    1. Visibility & View Gating -> returns 'uncertain' instead of guessing (FR9).
    2. Phase-Aware Error Checking -> each error is checked only in the phase where it occurs.
    3. Debounce + Hysteresis -> must persist >= `debounce_frames` (default 4) before firing RED ALARM.
    4. Cooldown -> at most ONE red alarm sound/event per error type per repetition.
    """

    def __init__(self, fps: float, thresholds: Optional[SquatThresholds] = None):
        if fps <= 0:
            raise ValueError(f"fps must be positive, got {fps!r}")
        self.fps = float(fps)
        self.thr = thresholds or SquatThresholds()
        self.reset()

    def reset(self) -> None:
        self.phase: PhaseName = "REST"
        self.rep_count: int = 0
        self.correct_reps: int = 0
        self.incorrect_reps: int = 0
        self._rep_start_frame: Optional[int] = None
        self._rep_bottom_frame: Optional[int] = None
        self._rep_max_signal: float = 0.0
        self._rep_min_knee_deg: float = 180.0
        self._rep_min_depth_rel: float = 1.0
        self._prev_signal: float = 0.0
        self._descent_frames: int = 0
        self._ascent_frames: int = 0
        # Per-error consecutive-frame persistence counters (debounce)
        self._streak: Dict[str, int] = {k: 0 for k in ERROR_SPEC}
        # Per-rep cooldown set (errors already alarmed in the current rep)
        self._alarmed_this_rep: set[str] = set()
        self.completed_reps: List[dict] = []
        self.alarm_log: List[FrameAlarmEvent] = []

    def _update_phase(self, frame_idx: int, sig: float, knee_deg: float, depth_rel: float) -> Optional[dict]:
        """Causal per-frame state machine: REST -> DESCENT -> BOTTOM -> ASCENT -> REST."""
        vel = (sig - self._prev_signal) * self.fps
        self._prev_signal = sig
        completed_rep = None

        if self.phase == "REST":
            if sig >= self.thr.descent_start_signal and vel > 0.05:
                self._descent_frames += 1
                if self._descent_frames >= 2:
                    self.phase = "DESCENT"
                    self._rep_start_frame = max(0, frame_idx - 1)
                    self._rep_bottom_frame = frame_idx
                    self._rep_max_signal = sig
                    self._rep_min_knee_deg = knee_deg
                    self._rep_min_depth_rel = depth_rel
                    self._alarmed_this_rep.clear()
                    for k in self._streak:
                        self._streak[k] = 0
            else:
                self._descent_frames = 0

        elif self.phase == "DESCENT":
            if sig > self._rep_max_signal:
                self._rep_max_signal = sig
                self._rep_bottom_frame = frame_idx
            self._rep_min_knee_deg = min(self._rep_min_knee_deg, knee_deg)
            self._rep_min_depth_rel = min(self._rep_min_depth_rel, depth_rel)

            if sig >= self.thr.bottom_enter_signal and abs(vel) < 0.35:
                self.phase = "BOTTOM"
            elif vel < -0.15 and sig >= 0.30:
                # Reversed directly into ascent
                self.phase = "BOTTOM"
            elif sig < self.thr.rest_return_signal:
                # Aborted partial movement
                self.phase = "REST"
                self._descent_frames = 0

        elif self.phase == "BOTTOM":
            if sig > self._rep_max_signal:
                self._rep_max_signal = sig
                self._rep_bottom_frame = frame_idx
            self._rep_min_knee_deg = min(self._rep_min_knee_deg, knee_deg)
            self._rep_min_depth_rel = min(self._rep_min_depth_rel, depth_rel)

            if vel < -0.10 and sig < self._rep_max_signal - 0.04:
                self._ascent_frames += 1
                if self._ascent_frames >= 2:
                    self.phase = "ASCENT"
            else:
                self._ascent_frames = 0

        elif self.phase == "ASCENT":
            if sig <= self.thr.rest_return_signal or (sig < 0.22 and abs(vel) < 0.08):
                s = self._rep_start_frame if self._rep_start_frame is not None else 0
                b = self._rep_bottom_frame if self._rep_bottom_frame is not None else frame_idx
                dur_s = (frame_idx - s) / self.fps
                if self.thr.min_rep_s * 0.65 <= dur_s <= self.thr.max_rep_s:
                    self.rep_count += 1
                    errors_in_rep = sorted(self._alarmed_this_rep)
                    is_correct = len(errors_in_rep) == 0
                    if is_correct:
                        self.correct_reps += 1
                    else:
                        self.incorrect_reps += 1
                    completed_rep = {
                        "index": self.rep_count,
                        "start_frame": s,
                        "bottom_frame": b,
                        "end_frame": frame_idx,
                        "duration_s": round(dur_s, 3),
                        "min_knee_angle_deg": round(self._rep_min_knee_deg, 2),
                        "bottom_depth_rel": round(self._rep_min_depth_rel, 4),
                        "verdict": "correct" if is_correct else "incorrect",
                        "errors": errors_in_rep,
                    }
                    self.completed_reps.append(completed_rep)
                self.phase = "REST"
                self._descent_frames = 0
                self._ascent_frames = 0

        return completed_rep

    def step_frame(
        self,
        frame_idx: int,
        sig: float,
        knee_deg: float,
        depth_rel: float,
        valgus_ratio: float,
        forward_knee: float,
        trunk_lean_deg: float,
        heel_lift: float,
        lr_knee_diff_deg: float,
        joints_visible: bool,
        observable_errors: Dict[str, bool],
    ) -> FrameAlarmEvent:
        """Run Figure 5 Instant Alarm logic for a single frame in < 0.1 ms."""
        t0 = time.perf_counter()
        self._update_phase(frame_idx, sig, knee_deg, depth_rel)

        # If joints are occluded or person not visible -> "uncertain" (Figure 5 left branch)
        if not joints_visible:
            latency_ms = (time.perf_counter() - t0) * 1000.0
            ev = FrameAlarmEvent(
                frame_index=frame_idx,
                timestamp_s=round(frame_idx / self.fps, 3),
                phase=self.phase,
                state="uncertain",
                active_errors=[],
                newly_triggered=[],
                yellow_warnings=[],
                uncertain_errors=list(ERROR_SPEC.keys()),
                faulty_h36m_joints=[],
                faulty_coco_joints=[],
                primary_message="Uncertain — ensure full body (hips, knees, ankles) is clearly visible.",
                latency_ms=round(latency_ms, 4),
            )
            self.alarm_log.append(ev)
            return ev

        # Evaluate raw rule triggers for errors active in `self.phase`
        raw_triggers: Dict[str, bool] = {
            "knees_caving_inward": (
                self.phase in ERROR_SPEC["knees_caving_inward"]["phase"]
                and valgus_ratio < self.thr.valgus_ratio_min
            ),
            "knees_forward_error": (
                self.phase in ERROR_SPEC["knees_forward_error"]["phase"]
                and forward_knee > self.thr.forward_knee_max
            ),
            "shallow_squat": (
                self.phase == "BOTTOM"
                and sig >= self.thr.bottom_enter_signal * 0.85
                and (
                    knee_deg > self.thr.shallow_knee_angle_max
                    and depth_rel > self.thr.shallow_depth_rel_max
                )
            ),
            "excessive_trunk_lean": (
                self.phase in ERROR_SPEC["excessive_trunk_lean"]["phase"]
                and trunk_lean_deg > self.thr.trunk_lean_max_deg
            ),
            "heel_lift": (
                self.phase in ERROR_SPEC["heel_lift"]["phase"]
                and heel_lift > self.thr.heel_lift_max_ratio
            ),
            "lr_asymmetry": (
                self.phase in ERROR_SPEC["lr_asymmetry"]["phase"]
                and lr_knee_diff_deg > self.thr.lr_knee_diff_max_deg
            ),
        }

        active_errors: List[str] = []
        newly_triggered: List[str] = []
        yellow_warnings: List[str] = []
        uncertain_errors: List[str] = []
        faulty_h36m: List[int] = []
        faulty_coco: List[int] = []

        for err_name, spec in ERROR_SPEC.items():
            if not observable_errors.get(err_name, True):
                uncertain_errors.append(err_name)
                self._streak[err_name] = 0
                continue

            if raw_triggers.get(err_name, False):
                self._streak[err_name] += 1
            else:
                # Hysteresis decay: drop by 1 instead of hard reset on a single borderline frame
                self._streak[err_name] = max(0, self._streak[err_name] - 1)

            streak = self._streak[err_name]
            if streak >= self.thr.debounce_frames:
                active_errors.append(err_name)
                for j in spec["h36m_joints"]:
                    if j not in faulty_h36m:
                        faulty_h36m.append(j)
                for j in spec["coco_joints"]:
                    if j not in faulty_coco:
                        faulty_coco.append(j)
                if err_name not in self._alarmed_this_rep:
                    self._alarmed_this_rep.add(err_name)
                    newly_triggered.append(err_name)
            elif streak > 0:
                yellow_warnings.append(err_name)

        if active_errors:
            state: AlarmState = "red_alarm"
            primary_err = newly_triggered[0] if newly_triggered else active_errors[0]
            msg = ERROR_SPEC[primary_err]["message"]
        elif yellow_warnings:
            state = "yellow"
            msg = f"Watch form: {ERROR_SPEC[yellow_warnings[0]]['name']}"
        else:
            state = "green"
            msg = "Movement OK" if self.phase != "REST" else "Ready"

        latency_ms = (time.perf_counter() - t0) * 1000.0
        ev = FrameAlarmEvent(
            frame_index=frame_idx,
            timestamp_s=round(frame_idx / self.fps, 3),
            phase=self.phase,
            state=state,
            active_errors=active_errors,
            newly_triggered=newly_triggered,
            yellow_warnings=yellow_warnings,
            uncertain_errors=uncertain_errors,
            faulty_h36m_joints=faulty_h36m,
            faulty_coco_joints=faulty_coco,
            primary_message=msg,
            latency_ms=round(latency_ms, 4),
        )
        self.alarm_log.append(ev)
        return ev

    def run_video(self, v: dict) -> List[FrameAlarmEvent]:
        """Run the causal per-frame alarm engine across a prepared squat video dict `v`."""
        self.reset()
        T = int(v["T"])
        obs = v["view_info"].observable_errors
        valid_mask = v["valid"]["L"] & v["valid"]["R"]
        for t in range(T):
            self.step_frame(
                frame_idx=t,
                sig=float(v["squat_signal"][t]),
                knee_deg=float(v["knee_angle_mean"][t]),
                depth_rel=float(v["squat_depth_rel"][t]),
                valgus_ratio=float(v["knee_valgus_ratio"][t]),
                forward_knee=float(v["knee_forward_travel"][t]),
                trunk_lean_deg=float(v["trunk_lean_deg"][t]),
                heel_lift=float(v["heel_lift_proxy"][t]),
                lr_knee_diff_deg=float(v["lr_knee_diff_deg"][t]),
                joints_visible=bool(valid_mask[t]),
                observable_errors=obs,
            )
        return self.alarm_log

