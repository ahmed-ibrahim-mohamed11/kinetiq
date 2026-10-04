#!/usr/bin/env python3
"""
Phase 2.6: build ai/data/mendeley_rep_annotations_expanded.csv.

Sources + per-rep annotation_quality:
  'verified'   the 443 Phase 2.5B reps (every video visually verified frame-by-frame)
  'auto'       expansion drafts passing all rep-level QC rules; stratified visual sample of
               12 videos (subject x view x form) inspected, plus all structurally flagged videos
  'uncertain'  expansion drafts failing a deterministic rep-level rule - kept, NOT dropped;
               future evaluation must treat them as DON'T-CARE (match = neither TP nor FP):
                 weak   local peak < 0.45 x video p90 peak (shallow/ambiguous movement;
                        24-rep visual sample showed a mix of real shallow reps and sway)
                 spike  local peak > 1.8 x video p90 (occlusion/passer-by artifacts)
                 gap    pull-to-previous-pull < 0.5 x video median (double-count suspects)
Manual corrections (visually confirmed, reasons inline) are applied before the rules.
Nothing is silently repaired; auto-dropped censored edge reps are logged in drafts26/qc_summary.csv.
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.join(REPO_ROOT, "ai", "research", "pose_benchmark"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from propose_reps import build_signal_from_kpts  # noqa: E402
from tracking import track_subject  # noqa: E402

ANN_25B = os.path.join(REPO_ROOT, "ai", "data", "mendeley_rep_annotations.csv")
EXP = os.path.join(REPO_ROOT, "ai", "data", "mendeley_phase26_expansion_list.csv")
DRAFTS = os.path.join(REPO_ROOT, "ai", "outputs", "mendeley_2d", "drafts26")
CACHE = os.path.join(REPO_ROOT, "ai", "outputs", "mendeley_2d")
OUT = os.path.join(REPO_ROOT, "ai", "data", "mendeley_rep_annotations_expanded.csv")
MAN = os.path.join(REPO_ROOT, "ai", "data", "mendeley_manifest.csv")

# video_id -> list of draft pull_frames to DROP, with the visually confirmed reason
MANUAL_DROPS = {
    "subject_017_back_good_front": [(46, "passer-by occlusion spike, not a rep (visual check)")],
}

COLS = ["video_id", "subject_id", "exercise", "camera_view", "form_label", "rep_id",
        "start_frame", "pull_frame", "end_frame", "fps", "annotation_quality", "annotation_notes"]


def main():
    man = pd.read_csv(MAN).set_index("video_id")
    base = pd.read_csv(ANN_25B)
    base["annotation_quality"] = "verified"
    base["annotation_notes"] = ""

    rows = []
    for _, v in pd.read_csv(EXP).iterrows():
        dpath = os.path.join(DRAFTS, v.video_id + ".csv")
        if not os.path.exists(dpath):
            continue
        d = pd.read_csv(dpath)
        for pf, reason in MANUAL_DROPS.get(v.video_id, []):
            n0 = len(d)
            d = d[d.pull_frame != pf]
            assert len(d) == n0 - 1, (v.video_id, pf)
        if not len(d):
            continue
        z = np.load(os.path.join(CACHE, v.video_id + ".npz"))
        kpts, _ = track_subject(z["kpts_top"], z["boxes"],
                                (float(man.loc[v.video_id, "width"]), float(man.loc[v.video_id, "height"])))
        s, _, _ = build_signal_from_kpts(kpts)
        peaks = np.array([float(np.max(s[max(0, p - 3):p + 4])) for p in d.pull_frame])
        ref = float(np.percentile(peaks, 90))
        gaps = np.diff(d.pull_frame.values)
        med_gap = float(np.median(gaps)) if len(gaps) else np.inf
        d = d.sort_values("start_frame").reset_index(drop=True)
        for i, r in d.iterrows():
            notes = []
            if peaks[i] < 0.45 * ref:
                notes.append("weak")
            if peaks[i] > 1.8 * ref:
                notes.append("spike")
            if i > 0 and (r.pull_frame - d.pull_frame[i - 1]) < 0.5 * med_gap:
                notes.append("gap")
            rows.append(dict(video_id=v.video_id, subject_id=v.subject_id, exercise=v.exercise,
                             camera_view=v.camera_view, form_label=v.form_label, rep_id=i + 1,
                             start_frame=int(r.start_frame), pull_frame=int(r.pull_frame),
                             end_frame=int(r.end_frame), fps=v.fps,
                             annotation_quality="uncertain" if notes else "auto",
                             annotation_notes="|".join(notes)))
    df = pd.concat([base, pd.DataFrame(rows)], ignore_index=True)[COLS]
    df.to_csv(OUT, index=False)
    print(f"{df.video_id.nunique()} videos, {len(df)} reps -> {OUT}")
    print(df.annotation_quality.value_counts().to_string())


if __name__ == "__main__":
    main()
