#!/usr/bin/env python3
"""
Phase 2.5B integrity checks for ai/data/mendeley_rep_annotations.csv (also run as pytest via
ai/tests/test_mendeley_annotations.py). Exits non-zero on any violation.

Checks:
  1. required fields present and non-null
  2. start_frame < pull_frame < end_frame                (per rep)
  3. all frames inside [0, frame_count-1] of the video   (0-BASED indices; manifest join)
  4. rep_id unique and consecutive 1..K per video
  5. reps within a video ordered and non-overlapping (next start >= previous end)
  6. metadata columns match the manifest exactly (subject/exercise/view/form/fps)
  7. every annotated video is in the proposed subset
Prints a summary (reps per video / subject / view / form, durations in frames and seconds).
"""
from __future__ import annotations

import os
import sys

import pandas as pd

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
ANN = os.path.join(REPO_ROOT, "ai", "data", "mendeley_rep_annotations.csv")
MAN = os.path.join(REPO_ROOT, "ai", "data", "mendeley_manifest.csv")
SUBSET = os.path.join(REPO_ROOT, "ai", "data", "mendeley_annotation_subset_PROPOSED.csv")
REQ = ["video_id", "subject_id", "exercise", "camera_view", "form_label",
       "rep_id", "start_frame", "pull_frame", "end_frame", "fps"]


def check(df: pd.DataFrame, man: pd.DataFrame, subset: pd.DataFrame) -> list[str]:
    errs = []
    for c in REQ:
        if c not in df.columns:
            errs.append(f"missing column {c}")
    if errs:
        return errs
    if df[REQ].isna().any().any():
        errs.append("null values in required fields")
    bad = df[~(df.start_frame < df.pull_frame) | ~(df.pull_frame < df.end_frame)]
    for _, r in bad.iterrows():
        errs.append(f"{r.video_id} rep {r.rep_id}: not start<pull<end ({r.start_frame},{r.pull_frame},{r.end_frame})")
    m = man.set_index("video_id")
    unknown = set(df.video_id) - set(m.index)
    if unknown:
        errs.append(f"videos not in manifest: {sorted(unknown)}")
    not_subset = set(df.video_id) - set(subset.video_id)
    if not_subset:
        errs.append(f"videos not in the proposed subset: {sorted(not_subset)}")
    for vid, g in df.groupby("video_id"):
        if vid in unknown:
            continue
        mi = m.loc[vid]
        n = int(mi.frame_count)
        if (g[["start_frame", "pull_frame", "end_frame"]].values >= n).any() or \
           (g[["start_frame", "pull_frame", "end_frame"]].values < 0).any():
            errs.append(f"{vid}: frame outside [0,{n-1}]")
        ids = sorted(g.rep_id.tolist())
        if ids != list(range(1, len(ids) + 1)):
            errs.append(f"{vid}: rep_id not consecutive 1..K: {ids}")
        gs = g.sort_values("rep_id")
        if not gs.start_frame.is_monotonic_increasing:
            errs.append(f"{vid}: reps not in temporal order")
        prev_end = -1
        for _, r in gs.iterrows():
            if r.start_frame < prev_end:
                errs.append(f"{vid}: rep {r.rep_id} overlaps previous (start {r.start_frame} < prev end {prev_end})")
            prev_end = r.end_frame
        for col_a, col_m in (("subject_id", "subject_id"), ("exercise", "exercise"),
                             ("camera_view", "camera_view"), ("form_label", "form_label"), ("fps", "fps")):
            vals = set(g[col_a].unique())
            if vals != {mi[col_m]}:
                errs.append(f"{vid}: {col_a} {vals} != manifest {mi[col_m]}")
    return errs


def summary(df: pd.DataFrame) -> str:
    dur_f = df.end_frame - df.start_frame
    dur_s = dur_f / df.fps
    lines = [
        f"videos annotated: {df.video_id.nunique()}   total reps: {len(df)}",
        f"rep duration: mean {dur_f.mean():.1f} / median {dur_f.median():.0f} frames"
        f"  = {dur_s.mean():.2f} / {dur_s.median():.2f} s @15fps",
        "reps by subject: " + str(df.groupby('subject_id').size().to_dict()),
        "reps by view:    " + str(df.groupby('camera_view').size().to_dict()),
        "reps by form:    " + str(df.groupby('form_label').size().to_dict()),
        "reps by exercise:" + str(df.groupby('exercise').size().to_dict()),
    ]
    return "\n".join(lines)


def main():
    df = pd.read_csv(ANN)
    errs = check(df, pd.read_csv(MAN), pd.read_csv(SUBSET))
    print(summary(df))
    if errs:
        print(f"\n{len(errs)} INTEGRITY ERRORS:")
        for e in errs:
            print(" -", e)
        sys.exit(1)
    print("\nall integrity checks pass")


if __name__ == "__main__":
    main()
