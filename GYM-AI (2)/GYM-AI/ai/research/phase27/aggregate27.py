#!/usr/bin/env python3
"""Phase 2.7: aggregate run outputs into comparison tables.
Reads ai/outputs/phase27/{pipeline}/videos_{split}_{tag}.csv for every (pipeline, tag) found.
Tempo groups: terciles of per-video median pull-gap defined on TRAIN annotations only
(fps-normalised seconds), stored in tempo_thresholds.json the first time and reused."""
from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np
import pandas as pd

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
OUT = os.path.join(REPO_ROOT, "ai", "outputs", "phase27")
ANN = os.path.join(REPO_ROOT, "ai", "data", "mendeley_rep_annotations_expanded.csv")
SPLIT = os.path.join(REPO_ROOT, "ai", "data", "mendeley_subject_split.csv")


def prf(tp, fp, fn):
    P = tp / max(tp + fp, 1)
    R = tp / max(tp + fn, 1)
    return P, R, 2 * P * R / max(P + R, 1e-9)


def pooled(g):
    P, R, F = prf(g.tp.sum(), g.fp.sum(), g.fn.sum())
    out = dict(gt=int(g.n_gt.sum()), pred=int(g.n_pred.sum()), tp=int(g.tp.sum()),
               fp=int(g.fp.sum()), fn=int(g.fn.sum()), ignored=int(g.n_ignored.sum()),
               P=round(P, 3), R=round(R, 3), F1=round(F, 3))
    for k in ("start", "pull", "end"):
        ok = g[f"{k}_mae_frames"].notna() & (g.tp > 0)
        mae = float(np.average(g.loc[ok, f"{k}_mae_frames"], weights=g.tp[ok])) if ok.any() else np.nan
        out[f"{k}_mae_f"] = round(mae, 2)
        out[f"{k}_mae_s"] = round(mae / 15.0, 3)
    return out


def tempo_groups():
    path = os.path.join(OUT, "tempo_thresholds.json")
    ann = pd.read_csv(ANN)
    sp = pd.read_csv(SPLIT)[["subject_id", "split"]]
    ann = ann.merge(sp, on="subject_id")
    per_vid = (ann[ann.annotation_quality.isin(["verified", "auto"])]
               .sort_values(["video_id", "pull_frame"])
               .groupby("video_id")
               .agg(subj=("subject_id", "first"), split=("split", "first"),
                    gap=("pull_frame", lambda x: np.median(np.diff(x)) / 15.0 if len(x) > 2 else np.nan)))
    if os.path.exists(path):
        thr = json.load(open(path))
    else:
        tr = per_vid[per_vid.split == "train"].gap.dropna()
        thr = dict(fast=float(np.percentile(tr, 33.3)), slow=float(np.percentile(tr, 66.7)))
        json.dump(thr, open(path, "w"), indent=1)
    per_vid["tempo"] = np.where(per_vid.gap <= thr["fast"], "fast",
                                np.where(per_vid.gap >= thr["slow"], "slow", "medium"))
    return per_vid["tempo"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="val")
    ap.add_argument("--rule", default="iou")
    args = ap.parse_args()
    tempo = tempo_groups()
    frames = []
    for p in sorted(os.listdir(OUT)):
        pdir = os.path.join(OUT, p)
        if not os.path.isdir(pdir):
            continue
        for f in sorted(os.listdir(pdir)):
            if f.startswith(f"videos_{args.split}_") and f.endswith(".csv"):
                tag = f[len(f"videos_{args.split}_"):-4]
                d = pd.read_csv(os.path.join(pdir, f))
                d["pipeline"], d["tag"] = p, tag
                frames.append(d)
    df = pd.concat(frames, ignore_index=True)
    df["tempo"] = df.video_id.map(tempo)
    df = df[df.rule == args.rule]
    back = df[df.exercise == "back"]

    print(f"=== pooled BACK ({args.split}, {args.rule}) ===")
    rows = []
    for (p, t), g in back.groupby(["pipeline", "tag"]):
        rows.append(dict(pipeline=p, tag=t, **pooled(g)))
    main_tbl = pd.DataFrame(rows).sort_values("F1", ascending=False)
    print(main_tbl.to_string(index=False))
    main_tbl.to_csv(os.path.join(OUT, f"summary_{args.split}_{args.rule}.csv"), index=False)

    for col in ("view", "form", "tempo", "subject"):
        print(f"\n=== F1 by {col} (BACK) ===")
        t = back.groupby(["pipeline", "tag", col]).apply(
            lambda g: round(prf(g.tp.sum(), g.fp.sum(), g.fn.sum())[2], 3)).unstack()
        print(t.to_string())
        t.to_csv(os.path.join(OUT, f"breakdown_{col}_{args.split}.csv"))

    curls = df[df.exercise == "bicep_curl"]
    if len(curls):
        print("\n=== curl transfer (secondary) ===")
        print(pd.DataFrame([dict(pipeline=p, tag=t, **pooled(g))
                            for (p, t), g in curls.groupby(["pipeline", "tag"])]).to_string(index=False))

    print("\n=== annotation-tier recall (BACK) ===")
    for (p, t), g in back.groupby(["pipeline", "tag"]):
        agg = {"verified": [0, 0], "auto": [0, 0]}
        for s in g.tier_match:
            d = json.loads(s)
            for k in agg:
                agg[k][0] += d[k]["matched"]
                agg[k][1] += d[k]["n"]
        msg = "  ".join(f"{k}: {m}/{n} ({m/max(n,1):.3f})" for k, (m, n) in agg.items())
        print(f"{p}/{t}: {msg}")


if __name__ == "__main__":
    main()
