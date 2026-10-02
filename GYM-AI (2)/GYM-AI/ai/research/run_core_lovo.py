#!/usr/bin/env python3
"""
Run the ai.core rep-segmentation pipeline with nested leave-one-group-out validation.
Mirrors ai/research/rep_segmentation_v2_hsmm.py main() (which stays frozen), minus plots.
From repo root:  .venv\Scripts\python.exe ai\research\run_core_lovo.py --out_dir ai\outputs\rep_eval_core
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np
import pandas as pd

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from ai.core.config import GRID  # noqa: E402
from ai.core.evaluation import _fit_all, run_cv, select_config  # noqa: E402
from ai.core.features import phase_target, prepare_video  # noqa: E402
from ai.core.io import load_annotations  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--poses_dir", default=os.path.join(REPO_ROOT, "ai", "data", "poses3d"))
    ap.add_argument("--annotations", default=os.path.join(REPO_ROOT, "ai", "outputs", "manual_rep_annotations.csv"))
    ap.add_argument("--conf_dir", default=None)
    ap.add_argument("--fps", type=float, default=30.0)
    ap.add_argument("--groups_csv", default=None, help="optional video,athlete -> leave-one-PERSON-out")
    ap.add_argument("--iou", type=float, default=0.5)
    ap.add_argument("--out_dir", default=os.path.join(REPO_ROOT, "ai", "outputs", "rep_eval_core"))
    ap.add_argument("--save_model", default=None)
    args = ap.parse_args()
    os.makedirs(args.out_dir, exist_ok=True)

    ann = load_annotations(args.annotations)
    vids = []
    for name, gt in ann.items():
        pose = np.load(os.path.join(args.poses_dir, name + ".npy"))
        conf = None
        if args.conf_dir and os.path.exists(os.path.join(args.conf_dir, name + ".npy")):
            conf = np.load(os.path.join(args.conf_dir, name + ".npy"))
        v = prepare_video(name, pose, args.fps, conf)
        v["gt"], v["y"] = gt, phase_target(v["T"], gt)
        vids.append(v)
    vids.sort(key=lambda v: v["name"])

    if args.groups_csv:
        g = pd.read_csv(args.groups_csv)
        groups = dict(zip(g["video"].str.replace(r"\.[A-Za-z0-9]+$", "", regex=True), g["athlete"]))
        print("Validation: leave-one-PERSON-out")
    else:
        groups = {v["name"]: v["name"] for v in vids}
        print("Validation: leave-one-VIDEO-out (one athlete: does NOT measure new-athlete generalisation)")

    results = run_cv(vids, groups, GRID, args.iou)

    rows = []
    for v in vids:
        for i, r in enumerate(results[v["name"]]["reps"], 1):
            rows.append(dict(video=v["name"], rep=i, **r))
    out_csv = os.path.join(args.out_dir, "predicted_reps_with_arm_timing.csv")
    pd.DataFrame(rows).to_csv(out_csv, index=False)
    print(f"\npredicted reps written to {out_csv}")

    if args.save_model:
        from dataclasses import asdict
        cache: dict = {}
        cfg = select_config(vids, groups, GRID, args.iou, cache)
        model, dec = _fit_all(vids, cfg, groups, cache)
        json.dump(dict(cfg=asdict(cfg), model=model.to_json(), decoder=dec.to_json()),
                  open(args.save_model, "w"), indent=1)
        print(f"Final model (fit on ALL videos, config chosen by inner CV) saved to {args.save_model}")
        print("Do not report scores from this model on these same videos; use the nested CV results.")


if __name__ == "__main__":
    main()
