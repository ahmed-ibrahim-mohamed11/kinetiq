#!/usr/bin/env python3
"""Phase 2.5C: produce the COMMON tracked 2D keypoints for every annotated benchmark video.
Input : ai/outputs/mendeley_2d/{video}.npz   (top-3 YOLO candidates per frame, Phase 2.5B cache)
Output: ai/outputs/pose_bench/tracked2d/{video}.npz   kpts (T,17,3) COCO x,y,conf (NaN when lost)
        ai/outputs/pose_bench/tracking_log.csv        per-video identity log (lost/jumps/disagree)
All YOLO-based lifting pipelines consume exactly these files (fairness)."""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tracking import track_subject  # noqa: E402

CACHE = os.path.join(REPO_ROOT, "ai", "outputs", "mendeley_2d")
OUT = os.path.join(REPO_ROOT, "ai", "outputs", "pose_bench", "tracked2d")
import argparse as _ap
_a = _ap.ArgumentParser(); _a.add_argument("--annotations", default=os.path.join(REPO_ROOT, "ai", "data", "mendeley_rep_annotations.csv"))
ANN = _a.parse_args().annotations
MAN = os.path.join(REPO_ROOT, "ai", "data", "mendeley_manifest.csv")


def main():
    os.makedirs(OUT, exist_ok=True)
    vids = sorted(pd.read_csv(ANN).video_id.unique())
    man = pd.read_csv(MAN).set_index("video_id")
    rows = []
    for v in vids:
        d = np.load(os.path.join(CACHE, v + ".npz"))
        kpts, log = track_subject(d["kpts_top"], d["boxes"],
                                  (float(man.loc[v, "width"]), float(man.loc[v, "height"])))
        np.savez_compressed(os.path.join(OUT, v + ".npz"), kpts=kpts)
        T = len(kpts)
        rows.append(dict(video_id=v, frames=T, lost=log["lost"], jumps=log["jumps"],
                         reacquired=log["reacquired"], disagree_largest=log["disagree"],
                         nan_frac=float(np.isnan(kpts[:, 0, 0]).mean())))
        print(f"{v}: lost {log['lost']}  jumps {log['jumps']}  reacq {log['reacquired']}  disagree {log['disagree']}")
    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(REPO_ROOT, "ai", "outputs", "pose_bench", "tracking_log.csv"), index=False)
    print(f"\ntotal: jumps {df.jumps.sum()}  lost {df.lost.sum()} / {df.frames.sum()} frames")


if __name__ == "__main__":
    main()
