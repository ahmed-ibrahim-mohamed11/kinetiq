#!/usr/bin/env python3
"""P2.2a (docs/EXPERIMENT_REGISTRY.md): reproduce the external paused-tempo IoU degradation
(0.632 vs internal 0.841, EXTERNAL_REAL_WORLD_STRESS_TEST.md) on DEV data only, using TWO
distinct synthetic perturbations:
  - uniform time-warp (slows down the whole rep+rest cycle)
  - targeted pause insertion at the PULL peak and/or at END (a squeeze/extension hold,
    specifically what the real failure taxonomy describes: "the HSMM absorbs the long
    inter-rep extension holds into rep windows")
on internal_sr + Mendeley VAL back, through the frozen decoder, unchanged.
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, REPO_ROOT)

from ai.augmentation.perturb import (make_pause_insertion_transform,  # noqa: E402
                                     make_time_warp_transform)
from ai.core.features import prepare_video  # noqa: E402
from ai.evaluation import harness as H  # noqa: E402
from ai.evaluation.protocol import evaluate_dontcare, prf, pull_tol_frames  # noqa: E402

OUT = os.path.join(REPO_ROOT, "ai", "outputs", "phase2")
PIPELINE = "motionbert_lite"
MEND_FPS = 15.0
SPEEDS = [1.0, 0.9, 0.8, 0.7, 0.6, 0.5, 0.4]
PAUSES_S = [0.0, 0.5, 1.0, 1.5, 2.0, 3.0]


def _mendeley_val_back_raw():
    POSES = os.path.join(REPO_ROOT, "ai", "outputs", "pose_bench", "poses", PIPELINE)
    vids = H.mendeley_videos(PIPELINE)
    out = {}
    for vid, v in vids.items():
        if v["split"] != "val" or v["exercise"] != "back":
            continue
        pose = np.load(os.path.join(POSES, vid + ".npy"))
        conf = np.load(os.path.join(POSES, vid + "_conf.npy"))
        out[vid] = dict(pose=pose, conf=conf, fps=MEND_FPS, gtc=v["gt"], gtu=v["gt_uncertain"])
    return out


def run_suite(system, items, transform, fps_for_tol):
    """NOTE (robustness finding, not yet a Phase 3 item - logged in EXPERIMENT_REGISTRY.md P2.2):
    at extreme synthetic slowdown (speed<=0.4) or a long inserted pause, ai.core.decoder's
    estimate_tempo() can crash with an unhandled IndexError (its autocorrelation peak search
    finds zero peaks in-range). This wraps that per-video so the SWEEP can still characterize
    the (already-bad) region around the crash; a production system must never crash like this
    (Phase 3's abstention requirement) - that fix is separate, tracked, not done here."""
    rows = []
    for name, it in items.items():
        pose, conf, fps, gtc, gtu = it["pose"], it["conf"], it["fps"], it["gtc"], it["gtu"]
        if transform is not None:
            pose, conf, fps, gtc, gtu = transform(name, pose, conf, fps, gtc, gtu)
        v = prepare_video(name, pose, fps, conf=conf)
        try:
            res = system.predict(v)
            pred = [dict(start=r["start"], pull=r["pull"], end=r["end"]) for r in res["reps"]]
        except Exception as e:
            print(f"    [CRASH, treated as 0 predictions] {name}: {type(e).__name__}: {e}")
            pred = []
        tol = pull_tol_frames(fps)
        m = evaluate_dontcare(pred, gtc, gtu, "iou", "v2", tol)
        rows.append(dict(video=name, tp=m["tp"], fp=m["fp"], fn=m["fn"]))
    df = pd.DataFrame(rows)
    return prf(df.tp.sum(), df.fp.sum(), df.fn.sum())[2], int(df.tp.sum()), int(df.fp.sum()), int(df.fn.sum())


def main():
    os.makedirs(OUT, exist_ok=True)
    system = H.fit_system(PIPELINE)
    sr_items = H.load_internal_sr()
    mv_items = _mendeley_val_back_raw()

    print("=== time-warp (uniform slowdown) sweep ===")
    rows = []
    for speed in SPEEDS:
        tf = make_time_warp_transform(speed) if speed != 1.0 else None
        f1_sr, *c_sr = run_suite(system, sr_items, tf, None)
        f1_mv, *c_mv = run_suite(system, mv_items, tf, None)
        rows.append(dict(speed=speed, internal_sr_f1=f1_sr, internal_sr_tfpfn=c_sr,
                         mendeley_val_f1=f1_mv, mendeley_val_tfpfn=c_mv))
        print(f"  speed={speed:.1f}  internal_sr F1={f1_sr:.4f} {c_sr}  mendeley_val F1={f1_mv:.4f} {c_mv}")
    pd.DataFrame(rows).to_csv(os.path.join(OUT, "paused_tempo_timewarp_sweep.csv"), index=False)

    print("\n=== pause insertion at PULL peak ===")
    rows = []
    for p_s in PAUSES_S:
        tf = make_pause_insertion_transform(p_s, at="pull") if p_s > 0 else None
        f1_sr, *c_sr = run_suite(system, sr_items, tf, None)
        f1_mv, *c_mv = run_suite(system, mv_items, tf, None)
        rows.append(dict(pause_s=p_s, at="pull", internal_sr_f1=f1_sr, internal_sr_tfpfn=c_sr,
                         mendeley_val_f1=f1_mv, mendeley_val_tfpfn=c_mv))
        print(f"  pause={p_s:.1f}s@pull  internal_sr F1={f1_sr:.4f} {c_sr}  mendeley_val F1={f1_mv:.4f} {c_mv}")
    pd.DataFrame(rows).to_csv(os.path.join(OUT, "paused_tempo_pause_pull_sweep.csv"), index=False)

    print("\n=== pause insertion at END (extension hold) ===")
    rows = []
    for p_s in PAUSES_S:
        tf = make_pause_insertion_transform(p_s, at="end") if p_s > 0 else None
        f1_sr, *c_sr = run_suite(system, sr_items, tf, None)
        f1_mv, *c_mv = run_suite(system, mv_items, tf, None)
        rows.append(dict(pause_s=p_s, at="end", internal_sr_f1=f1_sr, internal_sr_tfpfn=c_sr,
                         mendeley_val_f1=f1_mv, mendeley_val_tfpfn=c_mv))
        print(f"  pause={p_s:.1f}s@end   internal_sr F1={f1_sr:.4f} {c_sr}  mendeley_val F1={f1_mv:.4f} {c_mv}")
    pd.DataFrame(rows).to_csv(os.path.join(OUT, "paused_tempo_pause_end_sweep.csv"), index=False)

    print("\nReference (real, not synthetic): internal F1 0.841 -> external F1 0.632 (drop 0.209, "
          "EXTERNAL_REAL_WORLD_STRESS_TEST.md)")


if __name__ == "__main__":
    main()
