#!/usr/bin/env python3
"""
EXTERNAL_WILD_STRESS_TEST: zero-shot evaluation of the FROZEN seated-row product system
(MotionBERT-Lite + Phase 2.7 frozen cfg + Mendeley-back-TRAIN-refit phase model/HSMM)
on the quantitatively usable Instagram units. NOTHING is tuned on these videos.

GT: ai/data/external_stress_test/annotations.csv (manual, frame-level, independent of the
decoder; censored cut-edge reps carry gt_tier=uncertain and are don't-care, exactly like the
Mendeley uncertain tier). Matching tolerance is SECONDS-defined (8/15 s -> 16 f @30fps,
13 f @25fps). Protocols: v2 (primary, comparable to Phase 2.7/2.8) and v1 (strict, reported
for transparency). Per-unit decoding on the audited window slice; predictions reported in
absolute video frames.
"""
from __future__ import annotations

import json
import os
import sys

import numpy as np
import pandas as pd

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.join(REPO_ROOT, "ai", "research", "pose_benchmark"))
sys.path.insert(0, os.path.join(REPO_ROOT, "ai", "research", "phase27"))

from ai.core.config import Cfg  # noqa: E402
from ai.core.decoder import predict_video  # noqa: E402
from ai.core.features import prepare_video  # noqa: E402
from tracking import track_subject  # noqa: E402
import train_eval as te  # noqa: E402
from result_schema import make_row  # noqa: E402

OUT = os.path.join(REPO_ROOT, "ai", "outputs", "external_stress_test")
ANN = os.path.join(REPO_ROOT, "ai", "data", "external_stress_test", "annotations.csv")
PULL_TOL_S = 8 / 15.0

# unit -> (video, lo, hi, fps); windows from the frozen visual audit (manifest.csv)
UNITS = {
    "ext_02_full": ("ext_02", 0, 800, 30.0),
    "ext_03_segA": ("ext_03", 237, 330, 25.0),
    "ext_03_segB": ("ext_03", 361, 470, 25.0),
    "ext_08_segB": ("ext_08", 160, 250, 30.0),
    "ext_08_segC": ("ext_08", 310, 435, 30.0),
}
SEG_KEY = {"ext_02_full": "full", "ext_03_segA": "segA_237_330", "ext_03_segB": "segB_361_470",
           "ext_08_segB": "segB_160_250", "ext_08_segC": "segC_310_435"}


def load_gt():
    ann = pd.read_csv(ANN)
    gt = {}
    for unit, (vid, *_rest) in UNITS.items():
        g = ann[(ann.video_id == vid) & (ann.segment_id == SEG_KEY[unit])]
        mk = lambda df: [dict(start=int(r.start_frame), pull=int(r.pull_frame),
                              end=int(r.end_frame)) for r in df.itertuples()]
        gt[unit] = (mk(g[g.gt_tier == "certain"]), mk(g[g.gt_tier == "uncertain"]))
    return gt


def frozen_system():
    c = json.load(open(os.path.join(REPO_ROOT, "ai", "outputs", "phase27",
                                    "motionbert_lite", "config_final.json")))["cfg"]
    cfg = Cfg(smooth_hz=float(c["smooth_hz"]),
              detrend_s=None if c["detrend_s"] in (None, "None") else float(c["detrend_s"]),
              dur_weight=float(c["dur_weight"]), dur_sigma_floor=float(c["dur_sigma_floor"]))
    vids = te.load_videos("motionbert_lite")
    train = [v for v in vids.values()
             if v["split"] == "train" and v["exercise"] == "back" and v["gt"]]
    cache: dict = {}
    model = te.fit_model_masked(train, cfg, cache)
    dec = te.fit_decoder_masked(train, te.crossfit_fused_masked(train, cfg, cache), cfg)
    return cfg, model, dec


def v2_status(pred, gtc, gtu, rule):
    """Re-derive per-prediction status (TP/IGNORED_EDGE/IGNORED_UNCERTAIN/FP) for reporting;
    metrics themselves come from the frozen te.evaluate_dontcare."""
    pairs = te._match(range(len(pred)), gtc, pred, rule)
    status = {}
    for i, j in pairs:
        status[i] = f"TP(gt{j + 1})"
    rest = [i for i in range(len(pred)) if i not in status]
    allb = gtc + gtu
    first = min(g["start"] for g in allb) if allb else 0
    last = max(g["end"] for g in allb) if allb else 10**9
    edge = [i for i in rest if pred[i]["pull"] < first or pred[i]["pull"] > last]
    for i in edge:
        status[i] = "IGNORED_EDGE"
    rest2 = [i for i in rest if i not in status]
    used = set()
    for i in rest2:
        for j, g in enumerate(gtu):
            if j in used:
                continue
            iou = te._iou((pred[i]["start"], pred[i]["end"]), (g["start"], g["end"]))
            if iou >= 0.25 or abs(pred[i]["pull"] - g["pull"]) <= te.PULL_TOL_F:
                status[i] = f"IGNORED_UNCERTAIN(u{j + 1})"
                used.add(j)
                break
    for i in range(len(pred)):
        status.setdefault(i, "FP")
    return status


def main():
    gt = load_gt()
    cfg, model, dec = frozen_system()
    rows, pred_rows, diag_rows = [], [], []
    for unit, (vid, lo, hi, fps) in UNITS.items():
        pose = np.load(os.path.join(OUT, f"pose_{vid}.npy"))[lo:hi + 1]
        conf = np.load(os.path.join(OUT, f"pose_{vid}_conf.npy"))[lo:hi + 1]
        v = prepare_video(unit, pose, fps, conf=conf)
        res = predict_video(v, model, dec, cfg, None)
        pred = [dict(start=r["start"] + lo, pull=r["pull"] + lo, end=r["end"] + lo)
                for r in res["reps"]]
        gtc, gtu = gt[unit]
        te.PULL_TOL_F = int(round(PULL_TOL_S * fps))
        for protocol in ("v2", "v1"):
            for rule in ("iou", "pull"):
                m = te.evaluate_dontcare(pred, gtc, gtu, rule, protocol=protocol)
                metrics = {k: vv for k, vv in m.items() if k != "matched"}
                for k in ("start", "pull", "end"):
                    metrics[f"{k}_mae_s"] = round(metrics[f"{k}_mae_frames"] / fps, 3) \
                        if not np.isnan(metrics[f"{k}_mae_frames"]) else np.nan
                rows.append(make_row(dict(unit=unit, video=vid, fps=fps, protocol=protocol,
                                          rule=rule, pull_tol_f=te.PULL_TOL_F,
                                          tempo_s=res["tempo_s"]), metrics))
        st_iou = v2_status(pred, gtc, gtu, "iou")
        st_pull = v2_status(pred, gtc, gtu, "pull")
        for i, p in enumerate(pred):
            pred_rows.append(dict(unit=unit, video=vid, pred_idx=i + 1, start=p["start"],
                                  pull=p["pull"], end=p["end"],
                                  status_v2_iou=st_iou[i], status_v2_pull=st_pull[i]))
        z = np.load(os.path.join(OUT, f"2d_{vid}.npz"))
        kpts, tlog = track_subject(z["kt"], z["bt"], tuple(z["wh"]))
        w = kpts[lo:hi + 1]
        nan_frames = int(np.isnan(w[..., 0]).all(axis=1).sum())
        diag_rows.append(dict(unit=unit, video=vid, frames=len(w),
                              track_nan_frames=nan_frames,
                              track_nan_frac=round(nan_frames / len(w), 4),
                              video_lost=tlog["lost"], video_jumps=tlog["jumps"],
                              kpt_conf_mean=round(float(np.nanmean(w[..., 2])), 3),
                              lift_conf_mean=round(float(np.nanmean(conf)), 3),
                              lift_conf_low_frac=round(float((np.nanmean(conf, axis=1) < 0.5).mean()), 4),
                              tempo_s=res["tempo_s"], n_pred=len(pred)))
        print(f"{unit}: GT {len(gtc)}+{len(gtu)}u pred {len(pred)} "
              f"(window {lo}-{hi} @{fps:g}fps, tol {te.PULL_TOL_F}f)")
    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(OUT, "metrics.csv"), index=False)
    pd.DataFrame(pred_rows).to_csv(os.path.join(OUT, "predictions.csv"), index=False)
    pd.DataFrame(diag_rows).to_csv(os.path.join(OUT, "diagnostics.csv"), index=False)

    summary = {}
    for protocol in ("v2", "v1"):
        for rule in ("iou", "pull"):
            g = df[(df.protocol == protocol) & (df.rule == rule)]
            tp, fp, fn = int(g.tp.sum()), int(g.fp.sum()), int(g.fn.sum())
            P, R, F = te.prf(tp, fp, fn)
            w_mae = {}
            for k in ("start", "pull", "end"):
                num = (g[f"{k}_mae_s"] * g.tp).sum()
                w_mae[k] = round(float(num / max(tp, 1)), 3)
            summary[f"motionbert_lite_{protocol}_{rule}"] = dict(
                tp=tp, fp=fp, fn=fn, precision=round(P, 3), recall=round(R, 3),
                f1=round(F, 3), start_mae_s=w_mae["start"], pull_mae_s=w_mae["pull"],
                end_mae_s=w_mae["end"])
            print(f"POOLED {protocol}/{rule}: TP {tp} FP {fp} FN {fn} "
                  f"P {P:.3f} R {R:.3f} F1 {F:.3f} "
                  f"MAE s/p/e {w_mae['start']}/{w_mae['pull']}/{w_mae['end']} s")
    summary["n_certain_gt"] = int(sum(len(gt[u][0]) for u in UNITS))
    summary["n_uncertain_gt"] = int(sum(len(gt[u][1]) for u in UNITS))
    summary["units"] = {u: dict(video=UNITS[u][0], window=[UNITS[u][1], UNITS[u][2]],
                                fps=UNITS[u][3]) for u in UNITS}
    json.dump(summary, open(os.path.join(OUT, "summary.json"), "w"), indent=1)


if __name__ == "__main__":
    main()
