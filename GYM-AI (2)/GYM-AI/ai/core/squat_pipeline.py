"""Squat Assessment System — Task 5 & Task 6: Complete Squat Analysis Pipeline + Visual Skeleton Overlay Renderer.

Implements:
1. Full Mode A & Mode B Squat Analysis Pipeline (`analyze_squat_video`, `analyze_squat_pose`):
   - Runs YOLO11n-pose top-8 -> deterministic subject tracker -> MotionBERT-Lite 3D lifting ->
     One-Euro Filter -> Squat 3D Biomechanical Features -> Squat Rep & Phase Segmentation ->
     Instant Alarm Engine (<=200ms) -> 5 Squat Judging Models -> End-of-Set Report.
2. Visual Skeleton, Joint Angles & Red Faulty Joint Video Overlay (`render_squat_assessment_video`):
   - Draws the athlete's 17-joint skeleton on every frame (FR3).
   - Colors active faulty joints in RED, warning joints in YELLOW, and healthy joints in GREEN (FR7, FR8).
   - Displays live HUD matching the `Squat Assessment System Infographic.png`:
     * Rep Counter: Total / Correct / Incorrect / Uncertain (FR5)
     * Current Phase Badge: `REST` | `DESCENT` | `BOTTOM` | `ASCENT` (FR4)
     * Instant Alarm Banner: Red alert box with corrective message when an error triggers (FR7, FR8)
     * Live 3D Joint Angles: Knee°, Hip°, Ankle°, Trunk° + Camera View (`front`/`diagonal`/`side`) (FR3, FR10)
"""
from __future__ import annotations

import os
import time
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Literal, Optional, Tuple

import numpy as np

from ai.core.lifting import MotionBertLite
from ai.core.pose import YOLO_WEIGHTS, YoloTopK, coco2h36m
from ai.core.squat_alarm import ERROR_SPEC, FrameAlarmEvent, SquatAlarmEngine, SquatThresholds
from ai.core.squat_features import OneEuroFilter, prepare_squat_video
from ai.core.squat_judges import RepJudgment, SquatJudgeSuite
from ai.core.tracking import track_subject

# COCO-17 skeleton connections for OpenCV visual overlay
COCO_SKELETON_EDGES: List[Tuple[int, int]] = [
    (5, 6),    # L_sho - R_sho
    (5, 7), (7, 9),    # L arm
    (6, 8), (8, 10),   # R arm
    (5, 11), (6, 12),  # Torso sides
    (11, 12),          # Pelvis / hips
    (11, 13), (13, 15),  # L leg (hip - knee - ankle)
    (12, 14), (14, 16),  # R leg (hip - knee - ankle)
]


@dataclass
class SquatRepReport:
    index: int
    start_frame: int
    bottom_frame: int
    end_frame: int
    start_s: float
    bottom_s: float
    end_s: float
    duration_s: float
    descent_s: float
    ascent_s: float
    min_knee_angle_deg: float
    min_hip_angle_deg: float
    min_ankle_angle_deg: float
    max_trunk_lean_deg: float
    bottom_depth_rel: float
    min_valgus_ratio: float
    max_forward_knee: float
    verdict: Literal["correct", "incorrect", "uncertain"]
    confidence: float
    primary_error: Optional[str]
    detected_errors: List[str]
    explanation: str
    model_verdicts: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class SquatAnalysisResult:
    schema_version: str
    exercise: str
    status: str
    setup_ok: bool
    camera_view: str
    frontal_ratio: float
    observable_errors: Dict[str, bool]
    n_frames: int
    fps: float
    duration_s: float
    total_reps: int
    correct_reps: int
    incorrect_reps: int
    uncertain_reps: int
    alarm_events_count: int
    alarm_latency_median_ms: float
    alarm_latency_p95_ms: float
    reps: List[SquatRepReport]
    error_counts: Dict[str, int]
    timings_ms: Dict[str, float]
    warnings: List[str]

    def to_dict(self) -> dict:
        return asdict(self)


class SquatAssessmentComponents:
    """Reusable detector + 3D lifter + Squat Judge Suite."""

    def __init__(
        self,
        device: str = "cpu",
        imgsz: int = 640,
        thresholds: Optional[SquatThresholds] = None,
        judge_suite: Optional[SquatJudgeSuite] = None,
    ):
        self.device = device
        self.detector = YoloTopK(weights=YOLO_WEIGHTS, device=device, imgsz=imgsz)
        self.lifter = MotionBertLite(device=device)
        self.thresholds = thresholds or SquatThresholds()
        self.judge_suite = judge_suite or SquatJudgeSuite(self.thresholds)


def analyze_squat_pose(
    name: str,
    pose3d: np.ndarray,
    fps: float,
    conf: Optional[np.ndarray] = None,
    pose2d_coco: Optional[np.ndarray] = None,
    thresholds: Optional[SquatThresholds] = None,
    judge_suite: Optional[SquatJudgeSuite] = None,
    model_choice: str = "ensemble",
    timings_ms: Optional[Dict[str, float]] = None,
) -> Tuple[SquatAnalysisResult, dict, List[FrameAlarmEvent]]:
    """Run the complete Squat Assessment pipeline on extracted 3D (and optional 2D) poses."""
    from ai.core.squat_alarm import segment_squat_reps

    thr = thresholds or SquatThresholds()
    suite = judge_suite or SquatJudgeSuite(thr)
    timings = dict(timings_ms or {})

    t0 = time.perf_counter()
    pose2d_h36m = coco2h36m(pose2d_coco) if pose2d_coco is not None else None
    v = prepare_squat_video(
        name=name,
        pose3d=pose3d,
        fps=fps,
        conf=conf,
        pose2d_h36m=pose2d_h36m,
        use_one_euro=True,
    )
    timings["squat_features_ms"] = round((time.perf_counter() - t0) * 1000.0, 2)

    # 1. Fast Per-Frame Instant Alarm Engine (Proposal Figure 5)
    t0 = time.perf_counter()
    alarm_engine = SquatAlarmEngine(fps=fps, thresholds=thr)
    alarm_log = alarm_engine.run_video(v)
    timings["instant_alarm_total_ms"] = round((time.perf_counter() - t0) * 1000.0, 2)

    latencies = [ev.latency_ms for ev in alarm_log]
    lat_med = float(np.median(latencies)) if latencies else 0.0
    lat_p95 = float(np.percentile(latencies, 95)) if latencies else 0.0

    # 2. Accurate End-of-Rep Segmentation & Judging (Proposal Figure 1 bottom path)
    t0 = time.perf_counter()
    raw_reps, phases = segment_squat_reps(v, thresholds=thr)
    v["phases"] = phases

    rep_reports: List[SquatRepReport] = []
    error_counts: Dict[str, int] = {k: 0 for k in ERROR_SPEC}
    n_correct, n_incorrect, n_uncertain = 0, 0, 0

    for r in raw_reps:
        judgment = suite.judge_rep(v, r, model_choice=model_choice)
        # Also collect individual candidate verdicts when suite is fitted
        per_model_verdicts: Dict[str, str] = {}
        for m_name in (
            "06_Rules_Thresholds",
            "07_RF_XGBoost_Ensemble",
            "08_1DCNN_BiGRU_Temporal",
            "09_AngleGraph_GNN",
            "10_Small_STGCN",
        ):
            if suite.is_fitted or m_name == "06_Rules_Thresholds":
                jm = suite.judge_rep(v, r, model_choice=m_name)
                per_model_verdicts[m_name] = jm.verdict

        # Merge any instant alarm that fired inside this rep window [start, end]
        rep_alarms = [
            err
            for ev in alarm_log[r["start"] : r["end"] + 1]
            for err in ev.newly_triggered
        ]
        all_errors = list(dict.fromkeys(judgment.detected_errors + rep_alarms))

        verdict = judgment.verdict
        if verdict == "correct" and all_errors:
            verdict = "incorrect"

        if verdict == "correct":
            n_correct += 1
        elif verdict == "incorrect":
            n_incorrect += 1
            for err in all_errors:
                if err in error_counts:
                    error_counts[err] += 1
        else:
            n_uncertain += 1

        primary_err = all_errors[0] if all_errors else judgment.primary_error
        explanation = (
            ERROR_SPEC[primary_err]["message"]
            if (verdict == "incorrect" and primary_err in ERROR_SPEC)
            else judgment.explanation
        )

        rep_reports.append(
            SquatRepReport(
                index=r["index"],
                start_frame=r["start"],
                bottom_frame=r["bottom"],
                end_frame=r["end"],
                start_s=round(r["start"] / fps, 3),
                bottom_s=round(r["bottom"] / fps, 3),
                end_s=round(r["end"] / fps, 3),
                duration_s=r["duration_s"],
                descent_s=r["descent_s"],
                ascent_s=r["ascent_s"],
                min_knee_angle_deg=r["min_knee_angle_deg"],
                min_hip_angle_deg=r["min_hip_angle_deg"],
                min_ankle_angle_deg=r["min_ankle_angle_deg"],
                max_trunk_lean_deg=r["max_trunk_lean_deg"],
                bottom_depth_rel=r["bottom_depth_rel"],
                min_valgus_ratio=r["min_valgus_ratio"],
                max_forward_knee=r["max_forward_knee"],
                verdict=verdict,
                confidence=judgment.confidence,
                primary_error=primary_err,
                detected_errors=all_errors,
                explanation=explanation,
                model_verdicts=per_model_verdicts,
            )
        )
    timings["rep_judging_ms"] = round((time.perf_counter() - t0) * 1000.0, 2)

    status = "OK" if v["setup_ok"] else "INSUFFICIENT_CONFIDENCE"
    warnings: List[str] = []
    if not v["setup_ok"]:
        warnings.append("Camera setup warning: full lower-body keypoints were not clearly visible in >=70% of frames.")
    unobs = [k for k, ok in v["view_info"].observable_errors.items() if not ok]
    if unobs:
        warnings.append(
            f"Camera view is '{v['view_info'].view}': {', '.join(unobs)} cannot be reliably observed from this angle and are gated as uncertain."
        )

    total_alarms = sum(len(ev.newly_triggered) for ev in alarm_log)
    result = SquatAnalysisResult(
        schema_version="1.0-squat",
        exercise="barbell_squat",
        status=status,
        setup_ok=v["setup_ok"],
        camera_view=v["view_info"].view,
        frontal_ratio=v["view_info"].frontal_ratio,
        observable_errors=v["view_info"].observable_errors,
        n_frames=v["T"],
        fps=fps,
        duration_s=round(v["T"] / fps, 2),
        total_reps=len(rep_reports),
        correct_reps=n_correct,
        incorrect_reps=n_incorrect,
        uncertain_reps=n_uncertain,
        alarm_events_count=total_alarms,
        alarm_latency_median_ms=round(lat_med, 4),
        alarm_latency_p95_ms=round(lat_p95, 4),
        reps=rep_reports,
        error_counts=error_counts,
        timings_ms=timings,
        warnings=warnings,
    )
    return result, v, alarm_log


def analyze_squat_video(
    video_path: str,
    comp: SquatAssessmentComponents,
    render_output_path: Optional[str] = None,
    model_choice: str = "ensemble",
) -> SquatAnalysisResult:
    """End-to-end Squat Assessment on a raw video file, optionally rendering the visual HUD overlay MP4."""
    import cv2

    if not os.path.exists(video_path):
        raise FileNotFoundError(f"Squat video not found: {video_path}")
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise ValueError(f"Cannot decode video: {video_path}")

    fps = float(cap.get(cv2.CAP_PROP_FPS) or 30.0)
    W = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    H = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    timings: Dict[str, float] = {}
    t0 = time.perf_counter()
    kts, bts = [], []
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        k, b = comp.detector(frame)
        kts.append(k)
        bts.append(b)
    cap.release()
    timings["detect_2d_ms"] = round((time.perf_counter() - t0) * 1000.0, 1)

    if not kts:
        raise ValueError(f"No decodable frames in {video_path}")

    t0 = time.perf_counter()
    coco2d, tlog = track_subject(np.stack(kts), np.stack(bts), (float(W), float(H)))
    timings["track_ms"] = round((time.perf_counter() - t0) * 1000.0, 1)

    t0 = time.perf_counter()
    pose3d, conf = comp.lifter.lift_video(coco2d, tta=True)
    timings["lift_3d_ms"] = round((time.perf_counter() - t0) * 1000.0, 1)

    name = os.path.splitext(os.path.basename(video_path))[0]
    result, v, alarm_log = analyze_squat_pose(
        name=name,
        pose3d=pose3d,
        fps=fps,
        conf=conf,
        pose2d_coco=coco2d,
        thresholds=comp.thresholds,
        judge_suite=comp.judge_suite,
        model_choice=model_choice,
        timings_ms=timings,
    )

    if render_output_path is not None:
        render_squat_assessment_video(
            input_video_path=video_path,
            output_video_path=render_output_path,
            coco2d=coco2d,
            v=v,
            result=result,
            alarm_log=alarm_log,
        )

    return result


def render_squat_assessment_video(
    input_video_path: str,
    output_video_path: str,
    coco2d: np.ndarray,
    v: dict,
    result: SquatAnalysisResult,
    alarm_log: List[FrameAlarmEvent],
) -> str:
    """Render the KinetiQ Squat Assessment HUD overlay onto an MP4 video (FR3, FR5, FR7, FR8, FR10)."""
    import cv2

    cap = cv2.VideoCapture(input_video_path)
    if not cap.isOpened():
        raise ValueError(f"Cannot open video for rendering: {input_video_path}")

    fps = float(cap.get(cv2.CAP_PROP_FPS) or v["fps"])
    W = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    H = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    os.makedirs(os.path.dirname(os.path.abspath(output_video_path)), exist_ok=True)
    writer = cv2.VideoWriter(
        output_video_path,
        cv2.VideoWriter_fourcc(*"mp4v"),
        fps,
        (W, H),
    )

    # Smooth 2D keypoints with One-Euro filter so visual skeleton is rock-solid
    oef2d = OneEuroFilter(min_cutoff=1.5, beta=0.05, d_cutoff=1.0)
    coco_xy = oef2d.filter_sequence(np.nan_to_num(coco2d[:, :, :2], nan=0.0), fps)
    coco_conf = np.nan_to_num(coco2d[:, :, 2], nan=0.0)

    # Track cumulative rep verdicts by frame
    completed_by_frame: List[SquatRepReport] = []
    rep_idx_ptr = 0
    reps_sorted = sorted(result.reps, key=lambda r: r.end_frame)

    frame_i = 0
    try:
        while True:
            ok, frame = cap.read()
            if not ok or frame_i >= len(coco_xy):
                break

            while rep_idx_ptr < len(reps_sorted) and frame_i >= reps_sorted[rep_idx_ptr].end_frame:
                completed_by_frame.append(reps_sorted[rep_idx_ptr])
                rep_idx_ptr += 1

            ev = alarm_log[min(frame_i, len(alarm_log) - 1)]
            pts = coco_xy[frame_i]
            confs = coco_conf[frame_i]
            faulty_set = set(ev.faulty_coco_joints)

            # Determine theme color from alarm state (Green / Yellow / Red / Gray)
            if ev.state == "red_alarm":
                bone_color = (60, 60, 235)      # BGR Red
                badge_color = (30, 30, 210)
            elif ev.state == "yellow":
                bone_color = (0, 200, 255)      # BGR Yellow/Amber
                badge_color = (0, 165, 230)
            elif ev.state == "uncertain":
                bone_color = (160, 160, 160)    # BGR Gray
                badge_color = (110, 110, 110)
            else:
                bone_color = (90, 220, 80)      # BGR Green
                badge_color = (45, 165, 45)

            # 1. Draw Skeleton Bones
            for i, j in COCO_SKELETON_EDGES:
                if confs[i] >= 0.25 and confs[j] >= 0.25:
                    p1 = (int(pts[i, 0]), int(pts[i, 1]))
                    p2 = (int(pts[j, 0]), int(pts[j, 1]))
                    edge_col = (0, 0, 255) if (i in faulty_set or j in faulty_set) else bone_color
                    cv2.line(frame, p1, p2, edge_col, 3, cv2.LINE_AA)

            # 2. Draw Skeleton Joints (Highlight faulty joints in bright RED with outer ring!)
            for j in range(5, 17):
                if confs[j] >= 0.25:
                    pt = (int(pts[j, 0]), int(pts[j, 1]))
                    if j in faulty_set:
                        cv2.circle(frame, pt, 10, (0, 0, 255), -1, cv2.LINE_AA)
                        cv2.circle(frame, pt, 15, (255, 255, 255), 2, cv2.LINE_AA)
                    else:
                        cv2.circle(frame, pt, 5, (255, 255, 255), -1, cv2.LINE_AA)
                        cv2.circle(frame, pt, 7, bone_color, 2, cv2.LINE_AA)

            # 3. Draw Top HUD Panel (Rep Count, Correct/Wrong, Phase, 3D Angles)
            overlay = frame.copy()
            hud_h = max(95, int(H * 0.14))
            cv2.rectangle(overlay, (0, 0), (W, hud_h), (22, 22, 28), -1)
            cv2.addWeighted(overlay, 0.82, frame, 0.18, 0, frame)

            n_tot = len(completed_by_frame)
            n_ok = sum(1 for r in completed_by_frame if r.verdict == "correct")
            n_bad = sum(1 for r in completed_by_frame if r.verdict == "incorrect")

            cv2.putText(
                frame,
                f"KinetiQ Squat Coach | View: {result.camera_view.upper()}",
                (16, 28),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.62,
                (220, 220, 235),
                2,
                cv2.LINE_AA,
            )
            cv2.putText(
                frame,
                f"REPS: {n_tot}  (Correct: {n_ok} | Faulty: {n_bad})",
                (16, 58),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.68,
                (255, 255, 255),
                2,
                cv2.LINE_AA,
            )

            # 3D Angles readout
            k_deg = float(v["knee_angle_mean"][frame_i])
            h_deg = float(v["hip_angle_mean"][frame_i])
            a_deg = float(v["ankle_angle_mean"][frame_i])
            t_deg = float(v["trunk_lean_deg"][frame_i])
            cv2.putText(
                frame,
                f"3D Angles -> Knee: {k_deg:.0f} deg | Hip: {h_deg:.0f} deg | Ankle: {a_deg:.0f} deg | Trunk: {t_deg:.0f} deg",
                (16, 84),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.50,
                (180, 235, 255),
                1,
                cv2.LINE_AA,
            )

            # Phase Badge on top-right
            badge_w = 185
            cv2.rectangle(frame, (W - badge_w - 16, 14), (W - 16, 72), badge_color, -1)
            cv2.putText(
                frame,
                f"PHASE: {ev.phase}",
                (W - badge_w - 4, 48),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.58,
                (255, 255, 255),
                2,
                cv2.LINE_AA,
            )

            # 4. Bottom Instant Alarm / Feedback Banner
            banner_h = 54
            cv2.rectangle(frame, (0, H - banner_h), (W, H), badge_color, -1)
            prefix = "ALARM! " if ev.state == "red_alarm" else ""
            cv2.putText(
                frame,
                f"{prefix}{ev.primary_message}",
                (18, H - 18),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.62,
                (255, 255, 255),
                2,
                cv2.LINE_AA,
            )

            writer.write(frame)
            frame_i += 1
    finally:
        cap.release()
        writer.release()

    return output_video_path

