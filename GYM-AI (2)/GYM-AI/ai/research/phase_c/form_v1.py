#!/usr/bin/env python3
"""
Form Analysis v1 - calibration + evaluation (final version; experiment trail in git history).

1) BACK thresholds: p95/p05 over TRAIN-split GOOD-form certain reps (GT windows, MB-Lite poses)
   -> ai/models/form_thresholds_v1_back.json. VAL separability (rank-AUC + attention rates).
2) SEATED-ROW thresholds: PROVISIONAL, from the 3 development 'correct' videos (15 GT reps)
   -> ai/models/form_thresholds_v1_seated_row.json (demo-grade; proper calibration waits for the
   fresh dataset). Qualitative sanity on DECODED zero-shot MB-Lite windows for all 6 videos.
Controlled decisions already applied in ai/core/form_indicators.py: signal-xcorr lag replaces
phase-peak lag (0/15 -> 3/15 asymmetry hits, equal false rate); lr_onset_lag removed (degenerate);
thresholds exercise-scoped (cross-exercise transfer experimentally invalid).
"""
from __future__ import annotations

import json
import os
import sys

import numpy as np
import pandas as pd

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.join(REPO_ROOT, "ai", "research", "phase27"))

from ai.core.config import Cfg  # noqa: E402
from ai.core.decoder import predict_video  # noqa: E402
from ai.core.features import prepare_video  # noqa: E402
from ai.core.form_indicators import SPEC, evaluate_rep, rep_features, thresholds_path  # noqa: E402
from ai.core.phase_model import predict_phase  # noqa: E402
import train_eval as te  # noqa: E402

OUT = os.path.join(REPO_ROOT, "ai", "outputs", "phase_c")
FPS_MENDELEY, FPS_SR = 15.0, 30.0


def rank_auc(pos, neg):
    pos, neg = np.asarray(pos, float), np.asarray(neg, float)
    if not len(pos) or not len(neg):
        return np.nan
    ranks = pd.Series(np.concatenate([pos, neg])).rank().values
    return float((ranks[:len(pos)].sum() - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg)))


def collect_mendeley(vids, split):
    rows = []
    for v in vids.values():
        if v["split"] != split or v["exercise"] != "back" or not v["gt"]:
            continue
        for i, g in enumerate(v["gt"]):
            f = rep_features(v, None, g["start"], g["end"], FPS_MENDELEY)
            rows.append(dict(video_id=v["name"], subject=v["subject"], form=v["form"],
                             rep=i + 1, **f))
    return pd.DataFrame(rows)


def fit_thresholds(df_good, tag, provenance):
    thr = {}
    for name, spec in SPEC.items():
        q = 95 if spec["direction"] > 0 else 5
        thr[name] = round(float(np.percentile(df_good[name], q)), 4)
    json.dump(dict(version="v1", exercise=tag, method=provenance,
                   n_reps=int(len(df_good)), created="2026-10-08", thresholds=thr),
              open(thresholds_path(tag), "w"), indent=1)
    return thr


def main():
    os.makedirs(OUT, exist_ok=True)
    vids = te.load_videos("motionbert_lite")

    tr = collect_mendeley(vids, "train")
    thr_back = fit_thresholds(tr[tr.form == "good"], "back",
                              "p95/p05 over Mendeley TRAIN-split GOOD certain reps (GT windows)")
    print("back thresholds:", thr_back)

    va = collect_mendeley(vids, "val")
    rows = []
    for name, spec in SPEC.items():
        auc = rank_auc(va[va.form == "bad"][name], va[va.form == "good"][name])
        if spec["direction"] < 0 and not np.isnan(auc):
            auc = 1 - auc
        thr = thr_back[name]
        att = (lambda s: float(((s / thr) > 1).mean())) if spec["direction"] > 0 else \
              (lambda s: float(((thr / s.clip(lower=1e-9)) > 1).mean()))
        rows.append(dict(indicator=name, auc_pooled=round(auc, 3),
                         attention_rate_bad=round(att(va[va.form == "bad"][name]), 3),
                         attention_rate_good=round(att(va[va.form == "good"][name]), 3)))
    sep = pd.DataFrame(rows)
    sep.to_csv(os.path.join(OUT, "val_separability.csv"), index=False)
    print(sep.to_string(index=False))

    # seated row: provisional per-exercise calibration + decoded-window sanity
    gt = pd.read_csv(os.path.join(REPO_ROOT, "ai", "outputs", "manual_rep_annotations.csv"))
    gt["video"] = gt.video.str.replace(".mp4", "", regex=False)
    srv = {}
    for name in ("correct_01", "correct_02", "correct_03",
                 "asymmetry_01", "asymmetry_02", "asymmetry_03"):
        pose = np.load(os.path.join(REPO_ROOT, "ai", "outputs", "phase28",
                                    f"pose_motionbert_lite_{name}.npy"))
        conf = np.load(os.path.join(REPO_ROOT, "ai", "outputs", "phase28",
                                    f"pose_motionbert_lite_{name}_conf.npy"))
        srv[name] = prepare_video(name, pose, FPS_SR, conf=conf)
    cal = []
    for name in ("correct_01", "correct_02", "correct_03"):
        for r in gt[gt.video == name].itertuples():
            cal.append(rep_features(srv[name], None, int(r.start_frame),
                                    int(r.end_frame), FPS_SR))
    thr_sr = fit_thresholds(pd.DataFrame(cal), "seated_row",
                            "PROVISIONAL demo calibration: 15 GT reps from the 3 development "
                            "'correct' videos; replace with fresh-dataset calibration")
    print("seated_row provisional thresholds:", thr_sr)

    cfgj = json.load(open(os.path.join(REPO_ROOT, "ai", "outputs", "phase27",
                                       "motionbert_lite", "config_final.json")))["cfg"]
    cfg = Cfg(smooth_hz=float(cfgj["smooth_hz"]),
              detrend_s=None if cfgj["detrend_s"] in (None, "None") else float(cfgj["detrend_s"]),
              dur_weight=float(cfgj["dur_weight"]), dur_sigma_floor=float(cfgj["dur_sigma_floor"]))
    train = [v for v in vids.values() if v["split"] == "train" and v["exercise"] == "back" and v["gt"]]
    cache: dict = {}
    model = te.fit_model_masked(train, cfg, cache)
    dec = te.fit_decoder_masked(train, te.crossfit_fused_masked(train, cfg, cache), cfg)
    sr_rows = []
    for name, v in srv.items():
        res = predict_video(v, model, dec, cfg, None)
        for i, r in enumerate(res["reps"], 1):
            f = rep_features(v, None, r["start"], r["end"], FPS_SR)
            for ind in evaluate_rep(f, thr_sr):
                sr_rows.append(dict(video=name, rep=i, **ind.to_dict()))
    sr = pd.DataFrame(sr_rows)
    sr.to_csv(os.path.join(OUT, "seated_row_indicators.csv"), index=False)
    att = sr[sr.severity == "attention"].groupby(
        [sr.video.str.split("_0").str[0], "indicator"]).size().unstack(fill_value=0)
    n = sr.groupby(sr.video.str.split("_0").str[0]).rep.count() / len(SPEC)
    print("\nseated-row attention counts (decoded windows; reps per group:",
          n.astype(int).to_dict(), ")")
    print(att.to_string())


if __name__ == "__main__":
    main()
