#!/usr/bin/env python3
"""Phase 2.5C: the COMMON downstream rep-segmentation protocol, identical for every pose pipeline.

Protocol (fixed; no per-video tuning; labels/rep-counts never used):
  * back exercise (28 videos, 6 subjects): leave-one-SUBJECT-out with the ai.core nested pipeline
    (ridge phase model + explicit-duration HSMM; hyper-parameters chosen by inner leave-one-subject-out
    on the 5 training subjects only - exactly run_cv from ai.core.evaluation).
  * bicep_curl (3 videos): CROSS-EXERCISE TRANSFER - decoded with the fold model of their held-out
    subject (trained only on OTHER subjects' back videos). Reported separately.
  * fps-aware matching: PULL tolerance = 0.533 s (8 frames @15fps; the seated-row default was
    15 frames @30fps = 0.5 s) and temporal IoU >= 0.5 (scale-free). MAE reported in frames AND seconds.
Linked-subject rule: subject_019 is duplicate-linked to subject_020 (not in this benchmark); within
the benchmark no linked subjects are split apart; all 31 files are MD5-unique.

  run_downstream.py --pipeline motionbert
Writes ai/outputs/pose_bench/downstream/{pipeline}_videos.csv (per-video metrics, both rules).
"""
from __future__ import annotations

import argparse
import os
import sys

import numpy as np
import pandas as pd

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, REPO_ROOT)

from ai.core.config import GRID, Cfg  # noqa: E402
from ai.core.evaluation import _fit_all, _iou, match_reps, select_config  # noqa: E402
from ai.core.decoder import predict_video  # noqa: E402
from ai.core.features import phase_target, prepare_video  # noqa: E402

POSES = os.path.join(REPO_ROOT, "ai", "outputs", "pose_bench", "poses")
ANN = os.path.join(REPO_ROOT, "ai", "data", "mendeley_rep_annotations.csv")
OUT = os.path.join(REPO_ROOT, "ai", "outputs", "pose_bench", "downstream")
FPS = 15.0
PULL_TOL_S = 8 / 15.0          # 0.533 s


def match_pull(pred, gt, tol_frames):
    c = sorted((abs(p["pull"] - g["pull"]), i, j)
               for i, p in enumerate(pred) for j, g in enumerate(gt))
    up, ug, pairs = set(), set(), []
    for d, i, j in c:
        if d > tol_frames or i in up or j in ug:
            continue
        up.add(i), ug.add(j), pairs.append((i, j))
    return pairs


def video_metrics(pred, gt, rule, pairs):
    tp, fp, fn = len(pairs), len(pred) - len(pairs), len(gt) - len(pairs)
    m = dict(rule=rule, n_gt=len(gt), n_pred=len(pred), tp=tp, fp=fp, fn=fn)
    for k in ("start", "pull", "end"):
        errs = [abs(pred[i][k] - gt[j][k]) for i, j in pairs]
        m[f"{k}_mae_frames"] = float(np.mean(errs)) if errs else np.nan
        m[f"{k}_mae_s"] = m[f"{k}_mae_frames"] / FPS if errs else np.nan
    return m


def load_videos(pipeline: str):
    ann = pd.read_csv(ANN)
    vids = {}
    for vid, g in ann.groupby("video_id"):
        pose = np.load(os.path.join(POSES, pipeline, vid + ".npy"))
        conf = np.load(os.path.join(POSES, pipeline, vid + "_conf.npy"))
        v = prepare_video(vid, pose, FPS, conf=conf)
        gt = [dict(start=int(r.start_frame), pull=int(r.pull_frame), end=int(r.end_frame))
              for r in g.sort_values("rep_id").itertuples()]
        v["gt"], v["y"] = gt, phase_target(v["T"], gt)
        v["subject"], v["exercise"] = g.subject_id.iloc[0], g.exercise.iloc[0]
        vids[vid] = v
    return vids


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pipeline", required=True)
    args = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    vids = load_videos(args.pipeline)
    back = [v for v in vids.values() if v["exercise"] == "back"]
    curls = [v for v in vids.values() if v["exercise"] == "bicep_curl"]
    groups = {v["name"]: v["subject"] for v in back}
    tol = int(round(PULL_TOL_S * FPS))

    rows = []
    cache: dict = {}
    for subj in sorted({v["subject"] for v in back}):
        train = [v for v in back if v["subject"] != subj]
        test = [v for v in back if v["subject"] == subj]
        test += [v for v in curls if v["subject"] == subj]      # cross-exercise transfer
        assert all(t["subject"] == subj for t in test)
        cfg = select_config(train, groups, GRID, 0.5, cache)
        model, dec = _fit_all(train, cfg, groups, cache)
        print(f"[fold {subj}] cfg: smooth={cfg.smooth_hz} detrend={cfg.detrend_s} "
              f"dur_w={cfg.dur_weight} sf={cfg.dur_sigma_floor}")
        for v in test:
            res = predict_video(v, model, dec, cfg, cache)
            pred = res["reps"]
            for rule, pairs in (("pull_tol", match_pull(pred, v["gt"], tol)),
                                ("iou", match_reps(pred, v["gt"], 0.5))):
                m = video_metrics(pred, v["gt"], rule, pairs)
                rows.append(dict(pipeline=args.pipeline, video_id=v["name"], subject=subj,
                                 exercise=v["exercise"], **m))
            print(f"  {v['name']}: GT {len(v['gt'])}  pred {len(pred)}")
    df = pd.DataFrame(rows)
    out = os.path.join(OUT, f"{args.pipeline}_videos.csv")
    df.to_csv(out, index=False)
    bi = df[(df.rule == "iou") & (df.exercise == "back")]
    tp, fp, fn = bi.tp.sum(), bi.fp.sum(), bi.fn.sum()
    P, R = tp / max(tp + fp, 1), tp / max(tp + fn, 1)
    print(f"\n{args.pipeline} BACK pooled IoU: TP {tp} FP {fp} FN {fn} "
          f"P {P:.2f} R {R:.2f} F1 {2*P*R/max(P+R,1e-9):.2f} -> {out}")


if __name__ == "__main__":
    main()
