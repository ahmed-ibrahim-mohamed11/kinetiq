#!/usr/bin/env python3
"""
Phase 2.5B: build ai/data/mendeley_rep_annotations.csv from the machine drafts plus the
per-video VISUAL VERIFICATION decisions recorded below (every video was reviewed frame-by-frame
via contact sheets / verification images; see the Phase 2.5B report for the evidence trail).

Conventions
  frames    0-BASED indices into the video (unlike the legacy seated-row CSV, which is 1-based)
  rep       complete repetitions only: a rep whose START or END lies outside the video, or whose
            bottom position is not visibly reached on camera, is NOT annotated (censored)
  form_label is metadata copied from the manifest; it never influenced boundary placement
  EXCLUDED  duplicate files (identical MD5) are annotated once, under the kept video_id

Corrections format: video_id -> dict(
    drop   = list of 0-based DRAFT rep indices to remove (with the reason in a comment),
    pull   = {draft_rep_index: corrected_pull_frame},
    start  = {draft_rep_index: corrected_start_frame},
)
"""
from __future__ import annotations

import os
import sys

import pandas as pd

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, REPO_ROOT)

SUBSET = os.path.join(REPO_ROOT, "ai", "data", "mendeley_annotation_subset_PROPOSED.csv")
DRAFTS = os.path.join(REPO_ROOT, "ai", "outputs", "mendeley_2d", "drafts")
OUT = os.path.join(REPO_ROOT, "ai", "data", "mendeley_rep_annotations.csv")

# Videos excluded entirely (identical-file duplicates; one copy is kept and annotated).
EXCLUDED = {
    "subject_023_back_bad_side":       "MD5-identical to subject_023_back_bad_diagonal (kept)",
    "subject_023_back_good_diagonal":  "MD5-identical to subject_023_back_bad_diagonal (kept); good/bad label contradiction",
    "subject_023_bicep_curl_good_side": "MD5-identical to subject_023_bicep_curl_bad_side (kept); good/bad label contradiction",
}

CORRECTIONS: dict = {
    # setup motion (crouching at the rack / lifting the dumbbell into position), not a rep
    "subject_001_back_bad_diagonal": dict(drop=[0]),
    "subject_001_back_good_diagonal": dict(drop=[0]),      # P28: subject still standing upright
    "subject_001_back_good_front": dict(drop=[0]),         # P36 peaks at -1.3 vs ~0.5: settling after pickup
    # first rep censored: video starts mid-rep / bottom position never visible
    "subject_005_back_good_side": dict(drop=[0]),          # video opens at pull apex (P3)
    "subject_019_back_bad_side": dict(drop=[0]),
    "subject_019_back_good_diagonal": dict(drop=[0]),
    "subject_019_back_good_side": dict(drop=[0]),
    "subject_023_back_good_side": dict(drop=[0]),
    # last rep censored: return not completed before the video ends
    "subject_009_back_good_front": dict(drop=[12]),        # P453
    # walk-in / settling phase entangled with first movements (frames 0-60 blurry)
    "subject_023_back_bad_diagonal": dict(drop=[0, 1]),    # P15, P52
    # pause sway miscounted as a rep (frame zoom 168-212: dumbbell never rises) + trailing partial
    "subject_005_bicep_curl_bad_side": dict(drop=[5, 13]), # P179, P465
    # pull apex corrected against raw frames (dumbbell at shoulder 204-208, draft lagged);
    # the draft's start valley (210) was equally late - bottom position visible at ~194
    "subject_005_bicep_curl_good_side": dict(pull={5: 206}, start={5: 194}),
}


def main():
    sub = pd.read_csv(SUBSET)
    rows = []
    for _, v in sub.iterrows():
        if v.video_id in EXCLUDED:
            print(f"EXCLUDED {v.video_id}: {EXCLUDED[v.video_id]}")
            continue
        d = pd.read_csv(os.path.join(DRAFTS, v.video_id + ".csv"))
        corr = CORRECTIONS.get(v.video_id, {})
        for i, pf in corr.get("pull", {}).items():
            d.loc[i, "pull_frame"] = pf
        for i, sf in corr.get("start", {}).items():
            d.loc[i, "start_frame"] = sf
        if corr.get("drop"):
            d = d.drop(index=corr["drop"])
        d = d.sort_values("start_frame").reset_index(drop=True)
        for i, r in d.iterrows():
            rows.append(dict(video_id=v.video_id, subject_id=v.subject_id, exercise=v.exercise,
                             camera_view=v.camera_view, form_label=v.form_label, rep_id=i + 1,
                             start_frame=int(r.start_frame), pull_frame=int(r.pull_frame),
                             end_frame=int(r.end_frame), fps=v.fps))
    df = pd.DataFrame(rows)
    df.to_csv(OUT, index=False)
    print(f"{df.video_id.nunique()} videos, {len(df)} reps -> {OUT}")


if __name__ == "__main__":
    main()
