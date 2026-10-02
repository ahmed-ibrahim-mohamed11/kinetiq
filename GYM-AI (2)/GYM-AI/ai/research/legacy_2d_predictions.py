#!/usr/bin/env python3
"""
Package the legacy 2D pipeline's existing per-video outputs into one predictions CSV for
eval_rep_segmentation.py.

Source : ai/outputs/dataset_build/<video>/reps.csv   (written by ai/src/build_dataset.py)
Target : ai/outputs/baselines/legacy_2d_predictions.csv

This does NOT re-run YOLO; it only concatenates what build_dataset.py already produced, so the
legacy numbers are reproducible without the 1-2 min pose extraction.

Legacy pipeline = YOLO11n-pose 2D keypoints -> |elbow_L.x - elbow_R.x| / shoulder_width ->
Savitzky-Golay -> 30th/70th-percentile state machine. The thresholds are computed on each video
itself (label-free but transductive); nothing is learned, so there is no nested validation to
preserve. The final rep of a video may be flagged incomplete_return=True (end = last frame); it is
kept because it is a prediction the pipeline made.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

AI_DIR = Path(__file__).resolve().parent.parent
BUILD_DIR = AI_DIR / "outputs" / "dataset_build"
OUT_CSV = AI_DIR / "outputs" / "baselines" / "legacy_2d_predictions.csv"


def main() -> int:
    files = sorted(BUILD_DIR.glob("*/reps.csv"))
    if not files:
        print(f"ERROR: no {BUILD_DIR}/<video>/reps.csv found; run ai/src/build_dataset.py first", file=sys.stderr)
        return 1
    frames = []
    for f in files:
        df = pd.read_csv(f)
        df.insert(0, "video", f.parent.name)
        frames.append(df[["video", "rep", "start_frame", "pull_frame", "end_frame", "incomplete_return"]])
    out = pd.concat(frames, ignore_index=True).sort_values(["video", "rep"]).reset_index(drop=True)
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(OUT_CSV, index=False)
    print(f"{len(out)} predicted reps from {len(files)} videos -> {OUT_CSV}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
