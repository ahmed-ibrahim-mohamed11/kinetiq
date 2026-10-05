#!/usr/bin/env python3
"""
Phase 2.7 Problem 1 (fps robustness) controlled experiment - TRAIN/VAL only.

Question: does the decode path produce temporally equivalent reps when the SAME motion is
presented at a different frame rate? (Future cameras: 30/60 fps vs Mendeley's 15.)

Method: fit the phase model + decoder on TRAIN at 15 fps (declared policy). For every VAL back
video, decode (a) the native 15 fps pose sequence, and (b) the same sequence downsampled 2x
(every 2nd frame, fps=7.5) and (c) upsampled 2x (linear interpolation, fps=30). Rep boundaries
are converted to SECONDS and matched one-to-one (pull within 0.533 s). Reported: rep-count
deltas and median |pull time difference| in seconds. A temporally sound pipeline should keep
counts equal and pull times within ~1 native frame (0.067 s).
"""
from __future__ import annotations

import argparse
import os
import sys
from dataclasses import replace
from itertools import product

import numpy as np

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ai.core.config import GRID, Cfg  # noqa: E402
from ai.core.decoder import predict_video  # noqa: E402
from ai.core.features import phase_target, prepare_video  # noqa: E402
from train_eval import (crossfit_fused_masked, fit_decoder_masked,  # noqa: E402
                        fit_model_masked, load_videos)

POSES = os.path.join(REPO_ROOT, "ai", "outputs", "pose_bench", "poses")


def resample_video(v_src, pipeline, factor, fps_new):
    pose = np.load(os.path.join(POSES, pipeline, v_src["name"] + ".npy"))
    conf = np.load(os.path.join(POSES, pipeline, v_src["name"] + "_conf.npy"))
    if factor == 0.5:
        pose, conf = pose[::2], conf[::2]
    elif factor == 2.0:
        T = len(pose)
        tq = np.arange(0, T - 1 + 1e-9, 0.5)
        flat = pose.reshape(T, -1)
        pose = np.stack([np.interp(tq, np.arange(T), flat[:, c]) for c in range(flat.shape[1])], 1).reshape(len(tq), 17, 3)
        conf = np.stack([np.interp(tq, np.arange(T), conf[:, j]) for j in range(17)], 1)
    v = prepare_video(v_src["name"], pose, fps_new, conf=conf)
    v["mask"] = np.ones(v["T"], dtype=bool)
    return v


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pipeline", default="motionbert_lite")
    args = ap.parse_args()
    vids = load_videos(args.pipeline)
    train = [v for v in vids.values() if v["split"] == "train" and v["exercise"] == "back" and v["gt"]]
    val = [v for v in vids.values() if v["split"] == "val" and v["exercise"] == "back"]
    cache: dict = {}
    cfg = Cfg(smooth_hz=GRID["smooth_hz"][0], detrend_s=None)   # fixed cfg: isolate fps behaviour
    model = fit_model_masked(train, cfg, cache)
    dec = fit_decoder_masked(train, crossfit_fused_masked(train, cfg, cache), cfg)

    rows = []
    for v in val:
        base = predict_video(v, model, dec, cfg, None)["reps"]
        t15 = sorted(r["pull"] / 15.0 for r in base)
        for factor, fps_new, name in ((0.5, 7.5, "7.5fps"), (2.0, 30.0, "30fps")):
            vv = resample_video(v, args.pipeline, factor, fps_new)
            reps = predict_video(vv, model, dec, cfg, None)["reps"]
            tx = sorted(r["pull"] / fps_new for r in reps)
            used = set()
            dts = []
            for t in t15:
                c = [(abs(t - u), k) for k, u in enumerate(tx) if k not in used and abs(t - u) <= 0.533]
                if c:
                    d, k = min(c)
                    used.add(k)
                    dts.append(d)
            rows.append(dict(video=v["name"], cond=name, n15=len(t15), nX=len(tx),
                             matched=len(dts), med_dt=float(np.median(dts)) if dts else np.nan))
    import pandas as pd
    df = pd.DataFrame(rows)
    out = os.path.join(REPO_ROOT, "ai", "outputs", "phase27", f"fps_consistency_{args.pipeline}.csv")
    df.to_csv(out, index=False)
    for cond, g in df.groupby("cond"):
        print(f"{cond}: count delta mean {np.mean(g.nX - g.n15):+.2f} "
              f"(exact-count videos {(g.nX == g.n15).sum()}/{len(g)}), "
              f"median |pull dt| {np.nanmedian(g.med_dt):.3f}s, "
              f"match rate {g.matched.sum()}/{g.n15.sum()}")
    print("->", out)


if __name__ == "__main__":
    main()
