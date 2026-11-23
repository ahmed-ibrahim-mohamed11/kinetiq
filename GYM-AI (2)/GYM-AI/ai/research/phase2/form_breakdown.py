#!/usr/bin/env python3
"""P2.3 (docs/EXPERIMENT_REGISTRY.md): sensitivity/specificity/false-alert rate per indicator,
broken down by subject/view/tempo (never pooled-only), to check whether the existing pooled
VALIDATED/PARTIAL verdicts (FORM_ANALYSIS_V1.md) hold up per-slice, or hide a slice that fails.

Thresholds are read from the ALREADY-FROZEN files (`ai/models/form_thresholds_v1_{back,
seated_row}.json`, fit on TRAIN/dev only, Phase C) — this experiment does NOT refit thresholds,
it only measures behaviour at the existing operating point, per-slice, honestly including weak
slices (owner rule: "report every slice, including weak ones - no cherry-picking").

Definitions (at the EXISTING threshold, direction-aware per ai.core.form_indicators.SPEC):
  sensitivity        = attention rate on BAD-labelled reps (Mendeley) / on DEVIATED seated-row sets
  false-alert rate   = attention rate on GOOD-labelled reps (Mendeley) / on GOOD seated-row sets
  specificity        = 1 - false-alert rate
Tempo class (fast/medium/slow) uses the SAME boundaries as the existing tempo_thresholds.json
(1.73 s / 2.27 s per-rep duration), applied per-rep here (that file's values are per-video).
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

from ai.core.form_indicators import SPEC, evaluate_rep, load_thresholds, rep_features  # noqa: E402
import train_eval as te  # noqa: E402

OUT = os.path.join(REPO_ROOT, "ai", "outputs", "phase2")
FPS_MENDELEY, FPS_SR = 15.0, 30.0
TEMPO_FAST, TEMPO_SLOW = 1.7333333333333334, 2.2666666666666666   # ai/outputs/phase27/tempo_thresholds.json


def tempo_class(dur_s: float) -> str:
    if dur_s <= TEMPO_FAST:
        return "fast"
    if dur_s >= TEMPO_SLOW:
        return "slow"
    return "medium"


def rate(series, thr, direction) -> float:
    s = series.clip(lower=1e-9)
    return float(((s / thr) > 1).mean()) if direction > 0 else float(((thr / s) > 1).mean())


def mendeley_rows():
    vids = te.load_videos("motionbert_lite")
    rows = []
    for v in vids.values():
        if v["split"] != "val" or v["exercise"] != "back" or not v["gt"]:
            continue
        for i, g in enumerate(v["gt"]):
            f = rep_features(v, None, g["start"], g["end"], FPS_MENDELEY)
            rows.append(dict(video_id=v["name"], subject=v["subject"], view=v["view"],
                             form=v["form"], tempo=tempo_class((g["end"] - g["start"]) / FPS_MENDELEY),
                             **f))
    return pd.DataFrame(rows)


def breakdown(df: pd.DataFrame, thr: dict, group_cols: list, min_n: int = 5) -> pd.DataFrame:
    rows = []
    groups = [((), df)] if not group_cols else list(df.groupby(group_cols))
    for name, spec in SPEC.items():
        for key, g in groups:
            key = key if isinstance(key, tuple) else (key,)
            bad = g[g.form == "bad"][name]
            good = g[g.form == "good"][name]
            row = dict(zip(group_cols, key), indicator=name,
                       n_bad=len(bad), n_good=len(good),
                       sensitivity=round(rate(bad, thr[name], spec["direction"]), 3) if len(bad) >= min_n else None,
                       false_alert_rate=round(rate(good, thr[name], spec["direction"]), 3) if len(good) >= min_n else None)
            rows.append(row)
    return pd.DataFrame(rows)


def seated_row_rows():
    gt = pd.read_csv(os.path.join(REPO_ROOT, "ai", "outputs", "manual_rep_annotations.csv"))
    gt["video"] = gt.video.str.replace(".mp4", "", regex=False)
    thr_sr = load_thresholds("seated_row")
    rows = []
    for name in ("correct_01", "correct_02", "correct_03",
                "asymmetry_01", "asymmetry_02", "asymmetry_03"):
        pose = np.load(os.path.join(REPO_ROOT, "ai", "outputs", "phase28",
                                    f"pose_motionbert_lite_{name}.npy"))
        conf = np.load(os.path.join(REPO_ROOT, "ai", "outputs", "phase28",
                                    f"pose_motionbert_lite_{name}_conf.npy"))
        from ai.core.features import prepare_video
        v = prepare_video(name, pose, FPS_SR, conf=conf)
        form = "good" if name.startswith("correct") else "deviated"
        for r in gt[gt.video == name].itertuples():
            f = rep_features(v, None, int(r.start_frame), int(r.end_frame), FPS_SR)
            dur_s = (int(r.end_frame) - int(r.start_frame)) / FPS_SR
            for ind in evaluate_rep(f, thr_sr):
                rows.append(dict(video=name, form=form, tempo=tempo_class(dur_s),
                                 indicator=ind.indicator, attention=int(ind.severity == "attention")))
    return pd.DataFrame(rows)


def main():
    os.makedirs(OUT, exist_ok=True)
    thr_back = json.load(open(os.path.join(REPO_ROOT, "ai", "models", "form_thresholds_v1_back.json")))["thresholds"]
    mend = mendeley_rows()
    mend.to_csv(os.path.join(OUT, "form_mendeley_val_reps.csv"), index=False)

    print("=== Mendeley VAL: pooled (sanity vs existing FORM_ANALYSIS_V1.md) ===")
    pooled = breakdown(mend, thr_back, [])
    print(pooled.drop(columns=[]).to_string(index=False))

    for dim in ("subject", "view", "tempo"):
        print(f"\n=== Mendeley VAL: by {dim} ===")
        b = breakdown(mend, thr_back, [dim])
        b.to_csv(os.path.join(OUT, f"form_breakdown_{dim}.csv"), index=False)
        print(b.to_string(index=False))

    print("\n=== Seated row: by form (good vs deviated) and tempo ===")
    sr = seated_row_rows()
    sr.to_csv(os.path.join(OUT, "form_seated_row_reps.csv"), index=False)
    g = sr.groupby(["indicator", "form"]).attention.agg(["mean", "count"]).reset_index()
    g.to_csv(os.path.join(OUT, "form_seated_row_by_form.csv"), index=False)
    print(g.to_string(index=False))
    g2 = sr.groupby(["indicator", "tempo", "form"]).attention.agg(["mean", "count"]).reset_index()
    g2.to_csv(os.path.join(OUT, "form_seated_row_by_tempo.csv"), index=False)
    print(g2.to_string(index=False))


if __name__ == "__main__":
    main()
