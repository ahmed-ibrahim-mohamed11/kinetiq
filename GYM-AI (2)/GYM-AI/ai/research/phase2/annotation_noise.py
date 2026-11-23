#!/usr/bin/env python3
"""P2.5a (docs/EXPERIMENT_REGISTRY.md): how sensitive are F1/MAE to small START/PULL/END
annotation shifts? Jitters ONLY the ground truth (never the model or its predictions), by a
random integer in [-k,k] frames independently per boundary (clipped to keep start<pull<end
valid), re-scores the SAME frozen predictions against many random jitters, and reports the
resulting metric distribution per k. This isolates "evaluation-protocol sensitivity to
annotation noise" from "model sensitivity" — predictions never change across the sweep.
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, REPO_ROOT)

from ai.evaluation import harness as H  # noqa: E402
from ai.evaluation.protocol import evaluate_dontcare, prf, pull_tol_frames  # noqa: E402

OUT = os.path.join(REPO_ROOT, "ai", "outputs", "phase2")
PIPELINE = "motionbert_lite"
KS = [0, 1, 2, 3, 4, 5]
N_DRAWS = 200


def jitter_gt(gt: list, k: int, rng: np.random.Generator) -> list:
    if k == 0:
        return [dict(r) for r in gt]
    out = []
    for r in gt:
        s, p, e = r["start"], r["pull"], r["end"]
        for _ in range(50):     # resample until still ordered; practically always succeeds fast
            ds, dp, de = rng.integers(-k, k + 1, size=3)
            ns, npu, ne = s + ds, p + dp, e + de
            if ns < npu < ne:
                break
        else:
            ns, npu, ne = s, p, e
        nr = dict(r)
        nr["start"], nr["pull"], nr["end"] = int(ns), int(npu), int(ne)
        out.append(nr)
    return out


def main():
    os.makedirs(OUT, exist_ok=True)
    system = H.fit_system(PIPELINE)

    print("=== mendeley_val (back) ===")
    rows = []
    vids = [v for v in H.mendeley_videos(PIPELINE).values()
           if v["split"] == "val" and v["exercise"] == "back"]
    preds = {}
    for v in vids:
        res = system.predict(v)
        preds[v["name"]] = [dict(start=r["start"], pull=r["pull"], end=r["end"]) for r in res["reps"]]
    rng = np.random.default_rng(0)
    for k in KS:
        f1s, maes = [], []
        n = 1 if k == 0 else N_DRAWS
        for _ in range(n):
            tp = fp = fn = 0
            errs = []   # pooled over MATCHED PAIRS across all videos (canonical, ai/evaluation/harness.py)
            for v in vids:
                gtc = jitter_gt(v["gt"], k, rng)
                gtu = jitter_gt(v["gt_uncertain"], k, rng)
                m = evaluate_dontcare(preds[v["name"]], gtc, gtu, "iou", "v2", pull_tol_frames(v["fps"]))
                tp += m["tp"]; fp += m["fp"]; fn += m["fn"]
                errs += [abs(preds[v["name"]][i]["pull"] - gtc[j]["pull"]) / v["fps"]
                         for i, j in m["matched"]]
            f1s.append(prf(tp, fp, fn)[2])
            if errs:
                maes.append(float(np.mean(errs)))
        rows.append(dict(k_frames=k, f1_mean=np.mean(f1s), f1_std=np.std(f1s),
                         pull_mae_s_mean=np.mean(maes), pull_mae_s_std=np.std(maes)))
        print(f"  k={k} frames: F1 {np.mean(f1s):.4f} +/- {np.std(f1s):.4f}  "
              f"pull MAE {np.mean(maes):.4f} +/- {np.std(maes):.4f} s")
    pd.DataFrame(rows).to_csv(os.path.join(OUT, "annotation_noise_mendeley_val.csv"), index=False)

    print("\n=== internal_sr ===")
    items = H.load_internal_sr()
    preds_sr, fps_sr = {}, {}
    gtc_sr = {}
    for name, it in items.items():
        from ai.core.features import prepare_video
        v = prepare_video(name, it["pose"], it["fps"], conf=it["conf"])
        res = system.predict(v)
        preds_sr[name] = [dict(start=r["start"], pull=r["pull"], end=r["end"]) for r in res["reps"]]
        fps_sr[name] = it["fps"]
        gtc_sr[name] = it["gtc"]
    rows2 = []
    for k in KS:
        f1s, maes = [], []
        n = 1 if k == 0 else N_DRAWS
        for _ in range(n):
            tp = fp = fn = 0
            errs = []
            for name in items:
                gtc = jitter_gt(gtc_sr[name], k, rng)
                m = evaluate_dontcare(preds_sr[name], gtc, [], "iou", "v2", pull_tol_frames(fps_sr[name]))
                tp += m["tp"]; fp += m["fp"]; fn += m["fn"]
                errs += [abs(preds_sr[name][i]["pull"] - gtc[j]["pull"]) / fps_sr[name]
                         for i, j in m["matched"]]
            f1s.append(prf(tp, fp, fn)[2])
            if errs:
                maes.append(float(np.mean(errs)))
        rows2.append(dict(k_frames=k, f1_mean=np.mean(f1s), f1_std=np.std(f1s),
                          pull_mae_s_mean=np.mean(maes), pull_mae_s_std=np.std(maes)))
        print(f"  k={k} frames: F1 {np.mean(f1s):.4f} +/- {np.std(f1s):.4f}  "
              f"pull MAE {np.mean(maes):.4f} +/- {np.std(maes):.4f} s")
    pd.DataFrame(rows2).to_csv(os.path.join(OUT, "annotation_noise_internal_sr.csv"), index=False)


if __name__ == "__main__":
    main()
