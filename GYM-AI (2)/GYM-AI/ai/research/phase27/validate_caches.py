#!/usr/bin/env python3
"""Phase 2.7: pose-cache integrity for the three frozen pipelines over the expanded dataset.
Checks per pipeline: all 165 videos present, (T,17,3) shape with T == manifest frame_count,
finite values, pelvis zeroed, conf shape (T,17) in [0,1]. Writes cache_integrity.json."""
from __future__ import annotations

import json
import os
import sys

import numpy as np
import pandas as pd

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
POSES = os.path.join(REPO_ROOT, "ai", "outputs", "pose_bench", "poses")
ANN = os.path.join(REPO_ROOT, "ai", "data", "mendeley_rep_annotations_expanded.csv")
MAN = os.path.join(REPO_ROOT, "ai", "data", "mendeley_manifest.csv")
PIPELINES = ["motionbert_lite", "motionbert", "poseformerv2"]


def main():
    ann = pd.read_csv(ANN)
    man = pd.read_csv(MAN).set_index("video_id")
    vids = sorted(ann.video_id.unique())
    subj = ann.groupby("video_id").subject_id.first()
    report, ok_all = {}, True
    for p in PIPELINES:
        errs, checked = [], 0
        for v in vids:
            f = os.path.join(POSES, p, v + ".npy")
            cf = os.path.join(POSES, p, v + "_conf.npy")
            if not (os.path.exists(f) and os.path.exists(cf)):
                errs.append(f"{v}: missing")
                continue
            a = np.load(f)
            c = np.load(cf)
            T = int(man.loc[v, "frame_count"])
            if a.shape != (T, 17, 3):
                errs.append(f"{v}: shape {a.shape} != ({T},17,3)")
            elif not np.isfinite(a).all():
                errs.append(f"{v}: non-finite values")
            elif np.abs(a[:, 0]).max() > 1e-4:
                errs.append(f"{v}: pelvis not zeroed")
            if c.shape != (T, 17) or np.nanmin(c) < -1e-6 or np.nanmax(c) > 1.0 + 1e-6:
                errs.append(f"{v}: bad conf")
            checked += 1
        report[p] = dict(videos=len(vids), present=checked,
                         subjects=int(subj.loc[vids].nunique()), errors=errs[:20], n_errors=len(errs))
        ok = checked == len(vids) and not errs
        ok_all &= ok
        print(f"{p}: {checked}/{len(vids)} present, {len(errs)} errors {'OK' if ok else 'FAIL'}")
    out = os.path.join(REPO_ROOT, "ai", "outputs", "phase27", "cache_integrity.json")
    json.dump(report, open(out, "w"), indent=1)
    sys.exit(0 if ok_all else 1)


if __name__ == "__main__":
    main()
