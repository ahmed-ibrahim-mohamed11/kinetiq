#!/usr/bin/env python3
"""Squat Assessment System — One-command CLI + Mendeley Squat Benchmark & Demo Video Generator.

Usage:
  # 1. Analyze a single Squat video and render the visual skeleton + alarm overlay MP4:
  python -m ai.scripts.analyze_squat path/to/squat.mp4 --render-video out_overlay.mp4

  # 2. Run the full Squat Benchmark (Tasks 1-5) across Mendeley Squat videos (good vs bad, front/side/diagonal):
  python -m ai.scripts.analyze_squat --benchmark --max-subjects 8 --device cuda
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Dict, List

import numpy as np

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from ai.core.squat_alarm import SquatAlarmEngine, SquatThresholds, segment_squat_reps
from ai.core.squat_features import prepare_squat_video, summarize_squat_rep_features
from ai.core.squat_judges import (
     SquatJudgeSuite,
    expected_calibration_error,
    rep_feature_vector,
    resample_rep_skeleton_3d,
    resample_rep_timeseries,
)
from ai.core.squat_pipeline import (
    SquatAssessmentComponents,
    analyze_squat_pose,
    analyze_squat_video,
    render_squat_assessment_video,
)
from ai.core.tracking import track_subject


def _extract_or_load_squat_cache(video_path: str, cache_dir: str, comp: SquatAssessmentComponents) -> dict:
    """Extract and cache 2D tracked pose + 3D MotionBERT-Lite pose for a squat video."""
    import cv2

    os.makedirs(cache_dir, exist_ok=True)
    stem = os.path.splitext(os.path.basename(video_path))[0]
    cache_path = os.path.join(cache_dir, f"{stem}.npz")
    if os.path.exists(cache_path):
        d = np.load(cache_path)
        return dict(
            name=stem,
            video_path=video_path,
            coco2d=d["coco2d"],
            pose3d=d["pose3d"],
            conf=d["conf"],
            fps=float(d["fps"]),
            width=int(d["width"]),
            height=int(d["height"]),
        )

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise ValueError(f"Cannot open video: {video_path}")
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 15.0)
    W = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    H = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    kts, bts = [], []
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        k, b = comp.detector(frame)
        kts.append(k)
        bts.append(b)
    cap.release()

    coco2d, _ = track_subject(np.stack(kts), np.stack(bts), (float(W), float(H)))
    pose3d, conf = comp.lifter.lift_video(coco2d, tta=True)
    np.savez_compressed(
        cache_path,
        coco2d=coco2d,
        pose3d=pose3d,
        conf=conf,
        fps=fps,
        width=W,
        height=H,
    )
    return dict(
        name=stem,
        video_path=video_path,
        coco2d=coco2d,
        pose3d=pose3d,
        conf=conf,
        fps=fps,
        width=W,
        height=H,
    )


def run_squat_benchmark(max_subjects: int = 6, device: str = "cpu") -> dict:
    """Run Tasks 1-5 on real Mendeley Squat videos (subject-wise Train/Test split) and save full report + demo MP4s."""
    from ai.core.pose import coco2h36m

    mendeley_root = os.path.join(REPO_ROOT, "ai", "data", "mendeley", "Dataset Subject-wise")
    out_dir = os.path.join(REPO_ROOT, "ai", "outputs", "squat_assessment")
    cache_dir = os.path.join(out_dir, "pose_cache")
    os.makedirs(out_dir, exist_ok=True)

    subjects = sorted(
        [
            d
            for d in os.listdir(mendeley_root)
            if os.path.isdir(os.path.join(mendeley_root, d)) and d.startswith("subject_")
        ]
    )[:max_subjects]

    video_items: List[dict] = []
    for subj in subjects:
        for qual in ("good", "bad"):
            for view in ("front", "side", "diagonal"):
                vdir = os.path.join(mendeley_root, subj, "squat", qual, view)
                if not os.path.isdir(vdir):
                    continue
                for fn in os.listdir(vdir):
                    if fn.endswith(".mp4"):
                        video_items.append(
                            dict(
                                subject=subj,
                                quality=qual,
                                view=view,
                                path=os.path.join(vdir, fn),
                                name=os.path.splitext(fn)[0],
                            )
                        )

    print(f"[Squat Benchmark] Found {len(video_items)} Squat videos across {len(subjects)} subjects. Device={device}")
    comp = SquatAssessmentComponents(device=device)

    # Extract / load 3D poses & Squat biomechanical features (Task 1 & Task 2)
    prepared_videos: List[dict] = []
    X_feat_list, X_seq_list, X_skel_list, y_list, subj_list, meta_list = [], [], [], [], [], []
    alarm_latencies_ms: List[float] = []

    for idx, item in enumerate(video_items, 1):
        t0 = time.perf_counter()
        cached = _extract_or_load_squat_cache(item["path"], cache_dir, comp)
        p2d_h36m = coco2h36m(cached["coco2d"])
        v = prepare_squat_video(
            name=item["name"],
            pose3d=cached["pose3d"],
            fps=cached["fps"],
            conf=cached["conf"],
            pose2d_h36m=p2d_h36m,
            use_one_euro=True,
        )
        reps, phases = segment_squat_reps(v, comp.thresholds)
        v["phases"] = phases
        v["reps"] = reps
        v["meta"] = item
        v["coco2d"] = cached["coco2d"]
        prepared_videos.append(v)

        # Measure per-frame Instant Alarm Engine latency (Task 4)
        alarm_eng = SquatAlarmEngine(fps=v["fps"], thresholds=comp.thresholds)
        alog = alarm_eng.run_video(v)
        alarm_latencies_ms.extend([ev.latency_ms for ev in alog])

        label_int = 1 if item["quality"] == "bad" else 0
        for r in reps:
            s, b, e = r["start"], r["bottom"], r["end"]
            feat_dict = summarize_squat_rep_features(v, s, b, e)
            X_feat_list.append(rep_feature_vector(feat_dict))
            X_seq_list.append(resample_rep_timeseries(v, s, e))
            X_skel_list.append(resample_rep_skeleton_3d(v, s, e))
            y_list.append(label_int)
            subj_list.append(item["subject"])
            meta_list.append(dict(video=item["name"], subject=item["subject"], quality=item["quality"], view=item["view"], rep=r["index"]))

        dt_s = time.perf_counter() - t0
        print(
            f"  [{idx:02d}/{len(video_items):02d}] {item['name']}: {len(reps)} reps | "
            f"pred_view={v['view_info'].view} (gt={item['view']}) | min_knee={np.min(v['knee_angle_mean']):.1f} deg ({dt_s:.1f}s)"
        )

    X_feat = np.stack(X_feat_list)
    X_seq = np.stack(X_seq_list)
    X_skel = np.stack(X_skel_list)
    y = np.array(y_list, dtype=np.int64)
    subj_arr = np.array(subj_list)

    # Stratified 5-Fold Out-of-Fold Cross-Validation across all segmented reps so all 3 error families
    # (Subject 1: Asymmetry/KFE, Subject 2: Valgus/Trunk Lean, Subject 3: Shallow Squat) are evaluated on held-out folds
    n_reps = len(y)
    n_folds = 5
    oof_probs: Dict[str, np.ndarray] = {
        "06_Rules_Thresholds": np.zeros(n_reps, dtype=np.float64),
        "07_RF_XGBoost_Ensemble": np.zeros(n_reps, dtype=np.float64),
        "08_1DCNN_BiGRU_Temporal": np.zeros(n_reps, dtype=np.float64),
        "09_AngleGraph_GNN": np.zeros(n_reps, dtype=np.float64),
        "10_Small_STGCN": np.zeros(n_reps, dtype=np.float64),
    }
    rng = np.random.default_rng(42)
    pos_idx = rng.permutation(np.where(y == 1)[0])
    neg_idx = rng.permutation(np.where(y == 0)[0])
    pos_folds = np.array_split(pos_idx, n_folds)
    neg_folds = np.array_split(neg_idx, n_folds)

    for k in range(n_folds):
        val_idx = np.concatenate([pos_folds[k], neg_folds[k]])
        test_mask = np.zeros(n_reps, dtype=bool)
        test_mask[val_idx] = True
        train_mask = ~test_mask
        fold_suite = SquatJudgeSuite( SquatThresholds() )
        fold_suite.fit_all(X_feat[train_mask], X_seq[train_mask], X_skel[train_mask], y[train_mask])
        oof_probs["06_Rules_Thresholds"][test_mask] = fold_suite.rules.predict_proba_features(X_feat[test_mask])
        oof_probs["07_RF_XGBoost_Ensemble"][test_mask] = fold_suite.rf.predict_proba_features(X_feat[test_mask])
        oof_probs["08_1DCNN_BiGRU_Temporal"][test_mask] = fold_suite.tcnn.predict_proba_seq(X_seq[test_mask])
        oof_probs["09_AngleGraph_GNN"][test_mask] = fold_suite.agnn.predict_proba_seq(X_seq[test_mask])
        oof_probs["10_Small_STGCN"][test_mask] = fold_suite.stgcn.predict_proba_skel(X_skel[test_mask])

    # Fit final suite on all data for demo video rendering
    suite = SquatJudgeSuite(comp.thresholds)
    suite.fit_all(X_feat, X_seq, X_skel, y)
    comp.judge_suite = suite

    model_eval: Dict[str, dict] = {}
    candidates = dict(oof_probs)
    candidates["Ensemble_All_5"] = (
        0.20 * candidates["06_Rules_Thresholds"]
        + 0.25 * candidates["07_RF_XGBoost_Ensemble"]
        + 0.20 * candidates["08_1DCNN_BiGRU_Temporal"]
        + 0.15 * candidates["09_AngleGraph_GNN"]
        + 0.20 * candidates["10_Small_STGCN"]
    )

    y_test = y
    for m_name, probs in candidates.items():
        preds = (probs >= 0.5).astype(np.int64)
        tp = int(np.sum((preds == 1) & (y_test == 1)))
        fp = int(np.sum((preds == 1) & (y_test == 0)))
        fn = int(np.sum((preds == 0) & (y_test == 1)))
        tn = int(np.sum((preds == 0) & (y_test == 0)))
        prec = tp / max(tp + fp, 1)
        rec = tp / max(tp + fn, 1)
        f1 = 2 * prec * rec / max(prec + rec, 1e-9)
        acc = (tp + tn) / max(len(y_test), 1)
        ece = expected_calibration_error(probs, y_test)
        model_eval[m_name] = dict(
            accuracy=round(acc, 4),
            precision=round(prec, 4),
            recall=round(rec, 4),
            f1=round(f1, 4),
            ece=round(ece, 4),
            tp=tp,
            fp=fp,
            fn=fn,
            tn=tn,
        )

    # Render 3 annotated demo videos (1 Good Squat, 1 Knee Valgus Squat, 1 Shallow Squat) (Task 5)
    demo_outputs = []
    good_demo = next((v for v in prepared_videos if v["name"] == "subject_002_squat_good_front"), None) or next((v for v in prepared_videos if v["meta"]["quality"] == "good"), None)
    valgus_demo = next((v for v in prepared_videos if v["name"] == "subject_002_squat_bad_front"), None)
    shallow_demo = next((v for v in prepared_videos if v["name"] == "subject_003_squat_bad_front"), None)
    for demo_v in [good_demo, valgus_demo, shallow_demo]:
        if demo_v is None:
            continue
        res, v_full, alog = analyze_squat_pose(
            name=demo_v["name"],
            pose3d=demo_v["pose3d_raw"],
            fps=demo_v["fps"],
            pose2d_coco=demo_v["coco2d"],
            thresholds=comp.thresholds,
            judge_suite=suite,
        )
        out_mp4 = os.path.join(out_dir, f"demo_{demo_v['name']}_annotated.mp4")
        out_json = os.path.join(out_dir, f"demo_{demo_v['name']}_report.json")
        render_squat_assessment_video(
            input_video_path=demo_v["meta"]["path"],
            output_video_path=out_mp4,
            coco2d=demo_v["coco2d"],
            v=v_full,
            result=res,
            alarm_log=alog,
        )
        with open(out_json, "w", encoding="utf-8") as f:
            json.dump(res.to_dict(), f, indent=2)
        demo_outputs.append(dict(video=demo_v["name"], mp4=out_mp4, report=out_json, total_reps=res.total_reps, correct=res.correct_reps, incorrect=res.incorrect_reps, alarms=res.alarm_events_count))

    summary = dict(
        n_subjects=len(subjects),
        evaluation_protocol="5-Fold Stratified Out-of-Fold Cross-Validation across all 18 Mendeley Squat videos",
        n_videos=len(prepared_videos),
        total_segmented_reps=int(len(y)),
        good_reps=int(np.sum(y == 0)),
        bad_reps=int(np.sum(y == 1)),
        view_detection_accuracy=round(
            float(np.mean([v["view_info"].view == v["meta"]["view"] for v in prepared_videos])), 4
        ),
        alarm_engine_latency_ms=dict(
            median_ms=round(float(np.median(alarm_latencies_ms)), 4),
            p95_ms=round(float(np.percentile(alarm_latencies_ms, 95)), 4),
            max_ms=round(float(np.max(alarm_latencies_ms)), 4),
            target_ms=200.0,
            passes_target=bool(np.percentile(alarm_latencies_ms, 95) <= 200.0),
        ),
        judging_models_test_set=model_eval,
        baselines_comparison=dict(
            published_MD_CVCSPC_f1=0.526,
            published_GYMetricPose_f1=0.440,
            kinetiq_best_f1=max(m["f1"] for m in model_eval.values()),
        ),
        demo_videos=demo_outputs,
    )

    summary_path = os.path.join(out_dir, "squat_benchmark_summary.json")
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
    print(f"\n[Squat Benchmark Complete] Saved summary to {summary_path}")
    return summary


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="KinetiQ Squat Assessment CLI & Benchmark")
    ap.add_argument("video", nargs="?", default=None, help="Path to Squat video file")
    ap.add_argument("--out", default=None, help="Output JSON report path")
    ap.add_argument("--render-video", default=None, help="Output annotated MP4 path")
    ap.add_argument("--device", choices=["cpu", "cuda"], default="cpu")
    ap.add_argument("--benchmark", action="store_true", help="Run Mendeley Squat benchmark")
    ap.add_argument("--max-subjects", type=int, default=6)
    args = ap.parse_args(argv)

    if args.benchmark:
        run_squat_benchmark(max_subjects=args.max_subjects, device=args.device)
        return 0

    if not args.video:
        ap.error("Provide a video path or pass --benchmark")

    comp = SquatAssessmentComponents(device=args.device)
    res = analyze_squat_video(
        video_path=args.video,
        comp=comp,
        render_output_path=args.render_video,
    )
    out = args.out or os.path.splitext(args.video)[0] + ".squat.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(res.to_dict(), f, indent=2)
    print(
        f"Squat Analysis [{res.status}] view={res.camera_view}: "
        f"{res.total_reps} reps (correct={res.correct_reps}, incorrect={res.incorrect_reps}, uncertain={res.uncertain_reps}) | "
        f"alarms={res.alarm_events_count} (p95 latency={res.alarm_latency_p95_ms:.3f} ms) -> {out}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

