#!/usr/bin/env python3
"""
Phase 2.6: draft rep boundaries + automated QC for the expansion videos.

Per video (needs the 8-candidate 2D cache):
  benchmark tracker (ai/research/pose_benchmark/tracking.py)
  -> wrist-height signal + peak proposal (same aid as Phase 2.5B)
  -> automated QC indicators and flags:
       period_cv       std/mean of pull-to-pull spacing (rhythm regularity)
       weak_peaks      count of pulls with peak below 45% of the video's p90 peak height
       nan_frac        tracker-lost fraction
       censored_first  first rep's start within 3 frames of the video start
       censored_last   last rep's end within 3 frames of the video end
  -> per-rep auto-policy identical to Phase 2.5B: censored edge reps are DROPPED and logged
     (never silently repaired); everything else is kept as a draft.
  annotation_quality written per rep:
       'auto'       QC clean, pending/covered-by-sample visual QC
       'flagged'    QC indicator fired -> mandatory visual verification before finalize
Outputs: ai/outputs/mendeley_2d/drafts26/{video}.csv (+ verification .png),
         ai/outputs/mendeley_2d/drafts26/qc_summary.csv
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

from propose_reps import build_signal_from_kpts, propose, verification_image  # noqa: E402
from tracking import track_subject  # noqa: E402

CACHE = os.path.join(REPO_ROOT, "ai", "outputs", "mendeley_2d")
DRAFTS = os.path.join(CACHE, "drafts26")
EXP = os.path.join(REPO_ROOT, "ai", "data", "mendeley_phase26_expansion_list.csv")
DATA_ROOT = os.path.join(REPO_ROOT, "ai", "data", "mendeley")


def main():
    os.makedirs(DRAFTS, exist_ok=True)
    sub = pd.read_csv(EXP)
    qc_rows = []
    for _, row in sub.iterrows():
        npz = os.path.join(CACHE, row.video_id + ".npz")
        if not os.path.exists(npz):
            continue
        out_csv = os.path.join(DRAFTS, row.video_id + ".csv")
        d = np.load(npz)
        kpts, tlog = track_subject(d["kpts_top"], d["boxes"], (float(row.width), float(row.height)))
        s, arms, var = build_signal_from_kpts(kpts)
        reps = propose(s)
        T = len(s)
        flags = []
        nan_frac = float(np.isnan(kpts[:, 0, 0]).mean())
        if nan_frac > 0.10:
            flags.append(f"nan{nan_frac:.2f}")
        drops = []
        if reps:
            if reps[0][0] <= 3:
                drops.append((0, "censored_first"))
            if reps[-1][2] >= T - 4:
                drops.append((len(reps) - 1, "censored_last"))
            spacing = np.diff([r[1] for r in reps])
            if len(spacing) >= 3:
                cv = float(np.std(spacing) / max(np.mean(spacing), 1e-6))
                if cv > 0.45:
                    flags.append(f"period_cv{cv:.2f}")
            else:
                cv = np.nan
                flags.append("few_reps")
            peaks = np.array([float(np.max(s[max(0, p - 3):p + 4])) for _, p, _ in reps])
            ref = np.percentile(peaks, 90)
            weak = int((peaks < 0.45 * ref).sum()) if np.isfinite(ref) else 0
            if weak:
                flags.append(f"weak_peaks{weak}")
        else:
            cv = np.nan
            flags.append("no_reps")
        kept = [r for i, r in enumerate(reps) if i not in {j for j, _ in drops}]
        pd.DataFrame(kept, columns=["start_frame", "pull_frame", "end_frame"]).to_csv(out_csv, index=False)
        verification_image(os.path.join(DATA_ROOT, row.video_path), s, arms, kept,
                           os.path.join(DRAFTS, row.video_id + ".png"),
                           f"{row.video_id} [{'|'.join(flags) or 'clean'}]")
        qc_rows.append(dict(video_id=row.video_id, subject_id=row.subject_id, exercise=row.exercise,
                            camera_view=row.camera_view, form_label=row.form_label, n_draft=len(kept),
                            n_auto_dropped=len(drops),
                            drop_reasons=";".join(r for _, r in drops),
                            period_cv=round(cv, 3) if np.isfinite(cv) else np.nan,
                            nan_frac=round(nan_frac, 3),
                            flags="|".join(flags), quality="flagged" if flags else "auto"))
        print(f"{row.video_id}: {len(kept)} drafts  [{'|'.join(flags) or 'clean'}]")
    qdf = pd.DataFrame(qc_rows)
    qdf.to_csv(os.path.join(DRAFTS, "qc_summary.csv"), index=False)
    print(f"\n{len(qdf)} videos drafted; flagged: {(qdf.quality=='flagged').sum()}, "
          f"clean: {(qdf.quality=='auto').sum()}")


if __name__ == "__main__":
    main()
