#!/usr/bin/env python3
"""
Phase 2.7: structured failure attribution on VALIDATION (TEST untouched).

Every FP and FN from a run's predictions is classified by deterministic evidence rules,
checked in priority order (first match wins; a failure can also carry secondary tags):

  TRACK_NAN       the rep/prediction window overlaps a tracker-lost (NaN) stretch >= 3 frames
  EDGE_PARTIAL    within 1 median-period of the video start/end (censoring zone)
  NEAR_UNCERTAIN  within 1 median-period of an uncertain GT rep (annotation ambiguity zone)
  FAST_MERGE      FN whose neighbouring GT rep matched the SAME prediction window side
                  (two quick reps decoded as one; gap to neighbour < 0.6 x video median gap)
  PAUSE_SPLIT     FP lying inside a GT rest gap > 2 x video median gap (sway during pauses)
  SHALLOW         FN whose fused-phase local peak is in the bottom decile of the video's peaks
  HIGH_JITTER     video is in the top quartile of wrist jitter for its pipeline (pose quality)
  UNKNOWN

Usage: failure_analysis.py --pipeline motionbert_lite --tag baseline
Writes ai/outputs/phase27/{pipeline}/failures_val_{tag}.csv + prints the attribution table.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np
import pandas as pd

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from train_eval import FPS, PULL_TOL_F, _match, evaluate_dontcare, load_videos  # noqa: E402

OUT = os.path.join(REPO_ROOT, "ai", "outputs", "phase27")
TRACKED = os.path.join(REPO_ROOT, "ai", "outputs", "pose_bench", "tracked2d")


def nan_runs(vid):
    k = np.load(os.path.join(TRACKED, vid + ".npz"))["kpts"]
    bad = ~np.isfinite(k[:, 0, 0])
    runs, s = [], None
    for i, b in enumerate(bad):
        if b and s is None:
            s = i
        if not b and s is not None:
            if i - s >= 3:
                runs.append((s, i))
            s = None
    if s is not None and len(bad) - s >= 3:
        runs.append((s, len(bad)))
    return runs


def overlaps(a0, a1, runs):
    return any(not (a1 < r0 or a0 > r1) for r0, r1 in runs)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pipeline", required=True)
    ap.add_argument("--tag", default="baseline")
    ap.add_argument("--protocol", default="v1")
    args = ap.parse_args()
    odir = os.path.join(OUT, args.pipeline)
    preds = pd.read_csv(os.path.join(odir, f"predictions_val_{args.tag}.csv"))
    vids = load_videos(args.pipeline)
    stab = pd.read_csv(os.path.join(REPO_ROOT, "ai", "outputs", "phase27", "diagnostics.csv"))
    jit_thr = stab[stab.pipeline == args.pipeline].groupby("pipeline").accel_p99.quantile(0.75).iloc[0] \
        if len(stab[stab.pipeline == args.pipeline]) else np.inf
    jit_videos = set(stab[(stab.pipeline == args.pipeline) & (stab.accel_p99 >= jit_thr)].video_id)

    rows = []
    for vid, v in vids.items():
        if v["split"] != "val":
            continue
        p = preds[preds.video_id == vid]
        plist = [dict(start=int(r.start), pull=int(r.pull), end=int(r.end)) for r in p.itertuples()]
        m = evaluate_dontcare(plist, v["gt"], v["gt_uncertain"], "iou", args.protocol)
        mp = {i for i, _ in m["matched"]}
        mg = {j for _, j in m["matched"]}
        unmatched_p = [i for i in range(len(plist)) if i not in mp]
        ign_m = evaluate_dontcare(plist, v["gt"], v["gt_uncertain"], "iou", args.protocol)
        # recompute ignored set exactly as the evaluator does
        fp_count = m["fp"]
        # identify FP indices: unmatched minus ignored; evaluate_dontcare does not return them,
        # so rebuild: ignored = unmatched whose removal reconciles the FP count
        ign = set()
        if args.protocol == "v1":
            ign = {i for i, _ in _match(unmatched_p, v["gt_uncertain"], plist, "iou")}
        else:
            allb = v["gt"] + v["gt_uncertain"]
            if allb:
                first = min(g["start"] for g in allb); last = max(g["end"] for g in allb)
                ign |= {i for i in unmatched_p if plist[i]["pull"] < first or plist[i]["pull"] > last}
            rest2 = [i for i in unmatched_p if i not in ign]
            from train_eval import _iou
            used = set()
            for i in rest2:
                for j, g in enumerate(v["gt_uncertain"]):
                    if j in used:
                        continue
                    if _iou((plist[i]["start"], plist[i]["end"]), (g["start"], g["end"])) >= 0.25                             or abs(plist[i]["pull"] - g["pull"]) <= PULL_TOL_F:
                        ign.add(i); used.add(j); break
        fps_idx = [i for i in unmatched_p if i not in ign]
        fns_idx = [j for j in range(len(v["gt"])) if j not in mg]
        T = v["T"]
        gts = sorted(v["gt"], key=lambda r: r["start"])
        gaps = [b["start"] - a["end"] for a, b in zip(gts[:-1], gts[1:])]
        med_gap = float(np.median(gaps)) if gaps else 30.0
        period = float(np.median([g["end"] - g["start"] for g in gts])) + med_gap if gts else 40.0
        runs = nan_runs(vid)
        unc_zones = [(u["start"] - period, u["end"] + period) for u in v["gt_uncertain"]]

        def classify(kind, w0, w1, j=None):
            if overlaps(w0, w1, runs):
                return "TRACK_NAN"
            if w0 < period or w1 > T - period:
                return "EDGE_PARTIAL"
            if any(z0 <= w0 <= z1 or z0 <= w1 <= z1 for z0, z1 in unc_zones):
                return "NEAR_UNCERTAIN"
            if kind == "FN" and j is not None:
                for nb in (j - 1, j + 1):
                    if 0 <= nb < len(gts) and nb in mg:
                        if abs(gts[min(j, nb)]["end"] - gts[max(j, nb)]["start"]) < 0.6 * med_gap:
                            return "FAST_MERGE"
            if kind == "FP":
                for a, b in zip(gts[:-1], gts[1:]):
                    if a["end"] < w0 and w1 < b["start"] and (b["start"] - a["end"]) > 2 * med_gap:
                        return "PAUSE_SPLIT"
            if vid in jit_videos:
                return "HIGH_JITTER"
            return "UNKNOWN"

        for i in fps_idx:
            r = plist[i]
            rows.append(dict(video_id=vid, kind="FP", start=r["start"], end=r["end"],
                             cat=classify("FP", r["start"], r["end"]), view=v["view"], form=v["form"]))
        for j in fns_idx:
            g = gts_j = v["gt"][j]
            rows.append(dict(video_id=vid, kind="FN", start=g["start"], end=g["end"],
                             cat=classify("FN", g["start"], g["end"], j), view=v["view"], form=v["form"]))
    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(odir, f"failures_val_{args.tag}.csv"), index=False)
    if len(df):
        print(df.groupby(["kind", "cat"]).size().unstack(fill_value=0).to_string())
        print("\nby view:")
        print(df.groupby(["view", "kind"]).size().unstack(fill_value=0).to_string())
    else:
        print("no failures on VAL")


if __name__ == "__main__":
    main()
