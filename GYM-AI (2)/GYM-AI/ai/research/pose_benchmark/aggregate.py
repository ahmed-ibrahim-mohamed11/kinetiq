#!/usr/bin/env python3
"""Phase 2.5C: aggregate downstream + stability + deployment results into the final tables.

Outputs (ai/outputs/pose_bench/):
  summary.csv            one row per pipeline (the headline comparison table)
  breakdown_view.csv     pooled IoU-F1 per pipeline x camera_view            (back only)
  breakdown_form.csv     pooled IoU-F1 per pipeline x form_label             (back only)
  breakdown_subject.csv  pooled IoU-F1 per pipeline x subject                (back only)
  breakdown_exercise.csv back vs bicep_curl (curl = cross-exercise transfer)
  f1_vs_fps.png          quality/speed scatter
All pooled numbers aggregate TP/FP/FN over videos (micro), not averages of per-video F1.
Duplicate note: all 31 benchmark files are MD5-unique (Phase 2.5B dedup), so no clip is counted twice.
"""
from __future__ import annotations

import json
import os
import sys

import numpy as np
import pandas as pd

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
BENCH = os.path.join(REPO_ROOT, "ai", "outputs", "pose_bench")
ANN = os.path.join(REPO_ROOT, "ai", "data", "mendeley_rep_annotations.csv")

import matplotlib  # noqa: E402
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402


def prf(tp, fp, fn):
    P = tp / max(tp + fp, 1)
    R = tp / max(tp + fn, 1)
    return P, R, 2 * P * R / max(P + R, 1e-9)


def pooled(df):
    tp, fp, fn = df.tp.sum(), df.fp.sum(), df.fn.sum()
    P, R, F = prf(tp, fp, fn)
    out = dict(n_gt=int(df.n_gt.sum()), n_pred=int(df.n_pred.sum()),
               tp=int(tp), fp=int(fp), fn=int(fn),
               precision=round(P, 3), recall=round(R, 3), f1=round(F, 3))
    for k in ("start", "pull", "end"):
        w = df.tp  # weight MAE by matched reps
        ok = df[f"{k}_mae_frames"].notna() & (w > 0)
        out[f"{k}_mae_frames"] = round(float(np.average(df.loc[ok, f"{k}_mae_frames"], weights=w[ok])), 2) if ok.any() else np.nan
        out[f"{k}_mae_s"] = round(out[f"{k}_mae_frames"] / 15.0, 3) if ok.any() else np.nan
    return out


def main():
    ann = pd.read_csv(ANN)
    meta = ann.groupby("video_id").first()[["camera_view", "form_label"]]
    pipelines = sorted(d for d in os.listdir(os.path.join(BENCH, "poses"))
                       if os.path.isdir(os.path.join(BENCH, "poses", d)))
    stab = pd.read_csv(os.path.join(BENCH, "stability.csv"))
    rows, bview, bform, bsub, bexe = [], [], [], [], []
    for p in pipelines:
        vp = os.path.join(BENCH, "downstream", f"{p}_videos.csv")
        if not os.path.exists(vp):
            print(f"[warn] no downstream results for {p}")
            continue
        df = pd.read_csv(vp).merge(meta, left_on="video_id", right_index=True)
        back_iou = df[(df.rule == "iou") & (df.exercise == "back")]
        back_pt = df[(df.rule == "pull_tol") & (df.exercise == "back")]
        curl_iou = df[(df.rule == "iou") & (df.exercise == "bicep_curl")]
        dep = json.load(open(os.path.join(BENCH, "poses", p, "deployment.json")))
        st = stab[stab.pipeline == p]
        r = dict(pipeline=p)
        r.update({f"iou_{k}": v for k, v in pooled(back_iou).items()})
        pt = pooled(back_pt)
        r["pulltol_f1"] = pt["f1"]
        r["pulltol_precision"] = pt["precision"]
        r["pulltol_recall"] = pt["recall"]
        r.update(curl_f1=pooled(curl_iou)["f1"] if len(curl_iou) else np.nan,
                 jitter_elbow=round(float(st.jitter_elbow.median()), 4),
                 jitter_wrist=round(float(st.jitter_wrist.median()), 4),
                 jitter_shoulder=round(float(st.jitter_shoulder.median()), 4),
                 depth_jitter_elbow=round(float(st.depth_jitter_elbow.median()), 4),
                 lr_corr_elbow=round(float(st.lr_corr_elbow.median()), 3),
                 lowconf_frac=round(float(st.lowconf_frac.median()), 3),
                 lift_fps=dep["fps"], ms_per_frame=dep["ms_per_frame"],
                 model_mb=dep["model_size_mb"], peak_rss_mb=dep["peak_rss_delta_mb"],
                 load_s=dep["load_seconds"], vram_mb=dep.get("vram_mb", 0.0))
        rows.append(r)
        for col, acc in (("camera_view", bview), ("form_label", bform), ("subject", bsub)):
            for val, g in back_iou.groupby(col):
                acc.append(dict(pipeline=p, **{col: val}, **{k: v for k, v in pooled(g).items()
                                                             if k in ("tp", "fp", "fn", "f1", "pull_mae_frames")}))
        for val, g in df[df.rule == "iou"].groupby("exercise"):
            bexe.append(dict(pipeline=p, exercise=val, **{k: v for k, v in pooled(g).items()
                                                          if k in ("tp", "fp", "fn", "f1", "pull_mae_frames")}))
    summary = pd.DataFrame(rows)
    summary.to_csv(os.path.join(BENCH, "summary.csv"), index=False)
    for name, acc in (("breakdown_view", bview), ("breakdown_form", bform),
                      ("breakdown_subject", bsub), ("breakdown_exercise", bexe)):
        pd.DataFrame(acc).to_csv(os.path.join(BENCH, f"{name}.csv"), index=False)

    fig, ax = plt.subplots(figsize=(8, 5.5))
    for _, r in summary.iterrows():
        ax.scatter(r.lift_fps, r.iou_f1, s=np.sqrt(r.model_mb) * 14, alpha=0.75)
        ax.annotate(r.pipeline, (r.lift_fps, r.iou_f1), fontsize=9,
                    xytext=(5, 4), textcoords="offset points")
    ax.set_xscale("log")
    ax.set_xlabel("3D lifting throughput on this CPU (frames/s, log)")
    ax.set_ylabel("rep-segmentation F1 (back, LOSO, IoU>=0.5)")
    ax.set_title("Phase 2.5C: quality vs speed (marker size = model file size)")
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(os.path.join(BENCH, "f1_vs_fps.png"), dpi=110)

    cols = ["pipeline", "iou_f1", "iou_precision", "iou_recall", "pulltol_f1", "curl_f1",
            "iou_start_mae_frames", "iou_pull_mae_frames", "iou_end_mae_frames",
            "jitter_wrist", "lift_fps", "model_mb"]
    print(summary[cols].sort_values("iou_f1", ascending=False).to_string(index=False))


if __name__ == "__main__":
    main()
