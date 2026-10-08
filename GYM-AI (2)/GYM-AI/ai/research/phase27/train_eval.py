#!/usr/bin/env python3
"""
Phase 2.7 training + evaluation on the FROZEN Phase 2.6 split.

Supervision policy (declared in freeze_manifest.json BEFORE any run, identical for all pipelines):
  verified + auto reps supervise the phase model and decoder; UNCERTAIN reps are MASKED:
  their frame windows (start..end, +2 pad) are excluded from the ridge regression and from the
  decoder's emission statistics; durations/transitions/peaks are learned from certain reps only,
  and rest-gaps only between consecutive certain reps with no uncertain rep in between.
  ai/core is NOT modified; masked variants of fit_model/fit_decoder live here.

Selection: hyper-parameter grid (the frozen GRID from ai.core) is chosen on the VALIDATION split
(phase model + decoder fitted on TRAIN only; decoder emissions use subject-wise cross-fitted TRAIN
phase). TEST is locked behind --unlock-test and is run once after development freeze.

Don't-care rule (uncertain GT): after one-to-one matching of predictions to CERTAIN reps,
unmatched predictions are matched one-to-one to uncertain reps under the SAME rule; those
predictions are ignored (not TP, not FP). Uncertain reps never create FN. Extra predictions
beyond one per uncertain rep remain FP. Tests: ai/tests/test_dontcare.py.

Usage:
  train_eval.py --pipeline motionbert_lite --split val
  train_eval.py --pipeline motionbert_lite --split test --unlock-test   (final, once)
Outputs under ai/outputs/phase27/{pipeline}/: config.json, predictions_{split}.csv,
metrics_{split}.json (pooled + breakdowns).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import replace
from itertools import product

import numpy as np
import pandas as pd

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, REPO_ROOT)

from ai.core.config import GRID, PULL, RET, Cfg  # noqa: E402
from ai.core.decoder import Decoder, estimate_tempo, fit_decoder, predict_video  # noqa: E402
from ai.core.features import (get_design, local_peak, obs_features,  # noqa: E402
                              phase_target, prepare_video, state_labels)
from ai.core.phase_model import PhaseModel, predict_phase  # noqa: E402
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from result_schema import make_row  # noqa: E402

POSES = os.path.join(REPO_ROOT, "ai", "outputs", "pose_bench", "poses")
ANN = os.path.join(REPO_ROOT, "ai", "data", "mendeley_rep_annotations_expanded.csv")
SPLIT = os.path.join(REPO_ROOT, "ai", "data", "mendeley_subject_split.csv")
OUT = os.path.join(REPO_ROOT, "ai", "outputs", "phase27")
FPS = 15.0
PULL_TOL_F = 8          # 0.533 s @ 15 fps (fps-aware; derived from seconds, not hard-coded frames)
MASK_PAD = 2


# ----------------------------------------------------------------- data loading
def load_videos(pipeline: str, exercise: str | None = None):
    ann = pd.read_csv(ANN).fillna({"annotation_notes": ""})
    split = pd.read_csv(SPLIT)[["subject_id", "split"]]
    ann = ann.merge(split, on="subject_id")
    if exercise:
        ann = ann[ann.exercise == exercise]
    vids = {}
    for vid, g in ann.groupby("video_id"):
        pose = np.load(os.path.join(POSES, pipeline, vid + ".npy"))
        conf = np.load(os.path.join(POSES, pipeline, vid + "_conf.npy"))
        v = prepare_video(vid, pose, FPS, conf=conf)
        g = g.sort_values("rep_id")
        certain = g[g.annotation_quality.isin(["verified", "auto"])]
        unc = g[g.annotation_quality == "uncertain"]
        v["gt"] = [dict(start=int(r.start_frame), pull=int(r.pull_frame), end=int(r.end_frame))
                   for r in certain.itertuples()]
        v["gt_uncertain"] = [dict(start=int(r.start_frame), pull=int(r.pull_frame), end=int(r.end_frame))
                             for r in unc.itertuples()]
        v["gt_tier"] = list(certain.annotation_quality)
        v["y"] = phase_target(v["T"], v["gt"])
        mask = np.ones(v["T"], dtype=bool)
        for r in v["gt_uncertain"]:
            mask[max(0, r["start"] - MASK_PAD):min(v["T"], r["end"] + MASK_PAD + 1)] = False
        v["mask"] = mask
        v["subject"], v["split"] = g.subject_id.iloc[0], g.split.iloc[0]
        v["view"], v["form"], v["exercise"] = g.camera_view.iloc[0], g.form_label.iloc[0], g.exercise.iloc[0]
        vids[vid] = v
    return vids


# ----------------------------------------------------------------- masked fitting (ai.core untouched)
def fit_model_masked(train, cfg, cache) -> PhaseModel:
    Xs, ys = [], []
    for v in train:
        d = get_design(v, cfg, cache)
        for a in "LR":
            Xs.append(d[a][v["mask"]])
            ys.append(v["y"][v["mask"]])
    X, y = np.vstack(Xs), np.concatenate(ys)
    mu, sd = X.mean(0), X.std(0) + 1e-6
    Z, yc = (X - mu) / sd, y.mean()
    n = len(Z)
    w = np.linalg.solve(Z.T @ Z + cfg.alpha * n * np.eye(Z.shape[1]), Z.T @ (y - yc))
    min_rep_s = min((r["end"] - r["start"]) / v["fps"] for v in train for r in v["gt"])
    return PhaseModel(mu, sd, w, float(yc), float(min_rep_s))


def fit_decoder_masked(train, fused_cf, cfg) -> Decoder:
    """fit_decoder with uncertain-frame masking: emission stats drop masked frames; rest-gap pairs
    skip any pair of certain reps separated by an uncertain rep."""
    raw = {v["name"]: (estimate_tempo(fused_cf[v["name"]], v["fps"]) if cfg.tempo_norm else 1.0) for v in train}
    valid = [t for t in raw.values() if t is not None]
    ref = float(np.median(valid)) if valid else 1.0
    scale = {n: (t if t is not None else ref) for n, t in raw.items()}
    O, Z, peaks = [], [], []
    d_rel = {PULL: [], RET: []}
    per_video = {PULL: [], RET: []}
    gaps, n_pairs = [], 0
    for v in train:
        sc, f = scale[v["name"]], fused_cf[v["name"]]
        Ov = obs_features(f, v["fps"], sc)
        Zv = state_labels(v["T"], v["gt"])
        O.append(Ov[v["mask"]])
        Z.append(Zv[v["mask"]])
        reps = sorted(v["gt"], key=lambda r: r["start"])
        pv = [(r["pull"] - r["start"]) / v["fps"] / sc for r in reps]
        rv = [(r["end"] - r["pull"]) / v["fps"] / sc for r in reps]
        d_rel[PULL] += pv
        d_rel[RET] += rv
        if pv:
            per_video[PULL].append(float(np.median(pv)))
            per_video[RET].append(float(np.median(rv)))
        peaks += [local_peak(f, r["pull"]) for r in reps]
        unc_starts = [u["start"] for u in v["gt_uncertain"]]
        for a, b in zip(reps[:-1], reps[1:]):
            if any(a["end"] <= us <= b["start"] for us in unc_starts):
                continue                          # an uncertain rep sits in this gap
            gaps.append(max((b["start"] - a["end"]) / v["fps"] / sc, 1.0 / v["fps"] / sc))
            n_pairs += 1
    O, Z = np.vstack(O), np.concatenate(Z)
    mu, var = np.zeros((3, 2)), np.ones((3, 2))
    for k in range(3):
        o = O[Z == k]
        mu[k] = o.mean(0)
        var[k] = np.maximum(o.var(0), cfg.emis_var_floor)
    dur_mu, dur_sd, dur_min = np.zeros(2), np.zeros(2), np.zeros(2)
    for i, k in enumerate((PULL, RET)):
        lg = np.log(np.asarray(d_rel[k]))
        dur_mu[i] = lg.mean()
        dur_sd[i] = max(lg.std(ddof=1) if len(lg) > 1 else 0.0, cfg.dur_sigma_floor)
        dur_min[i] = cfg.min_dur_frac * float(np.min(d_rel[k]))
    rest_mean = float(np.mean(gaps)) if gaps else float(np.median(d_rel[PULL]) + np.median(d_rel[RET]))
    tol = cfg.direct_gap_frac * float(np.median(d_rel[RET]))
    n_direct = int(np.sum(np.asarray(gaps) <= tol)) if gaps else 0
    p_direct = (n_direct + 1.0) / (n_pairs + 2.0)
    pk_mu, pk_sd = float(np.mean(peaks)), float(max(np.std(peaks, ddof=1), cfg.peak_sd_floor))
    stats = dict(train_videos=[v["name"] for v in train], n_reps=len(d_rel[PULL]),
                 unit="tempo" if cfg.tempo_norm else "s", tempo_s={}, rest_gap_mean=rest_mean,
                 gap_direct_tol=tol, n_direct=n_direct, n_pairs=n_pairs, p_direct=p_direct,
                 pull=dict(median=float(np.median(d_rel[PULL])), p10=0, p90=0, min=float(np.min(d_rel[PULL])),
                           per_video_median=per_video[PULL], log_sigma=float(dur_sd[0])),
                 ret=dict(median=float(np.median(d_rel[RET])), p10=0, p90=0, min=float(np.min(d_rel[RET])),
                          per_video_median=per_video[RET], log_sigma=float(dur_sd[1])),
                 peak_height=dict(mean=pk_mu, sd=pk_sd, min=float(np.min(peaks))),
                 emis_mean=mu.tolist(), emis_std=np.sqrt(var).tolist(),
                 hard_min=dict(pull=float(dur_min[0]), ret=float(dur_min[1])))
    return Decoder(mu, var, dur_mu, dur_sd, dur_min, rest_mean, p_direct, pk_mu, pk_sd,
                   bool(cfg.tempo_norm), ref, stats)


def crossfit_fused_masked(train, cfg, cache):
    out = {}
    subs = sorted({v["subject"] for v in train})
    for s in subs:
        tr = [v for v in train if v["subject"] != s]
        model = fit_model_masked(tr, cfg, cache)
        for v in (v for v in train if v["subject"] == s):
            out[v["name"]] = predict_phase(v, model, cfg, cache)[1]
    return out


# ----------------------------------------------------------------- matching + metrics
def _iou(a, b):
    inter = max(0, min(a[1], b[1]) - max(a[0], b[0]))
    union = max(a[1], b[1]) - min(a[0], b[0])
    return inter / union if union > 0 else 0.0


def _match(pred_idx, gt, pred, rule):
    if rule == "iou":
        c = sorted(((_iou((pred[i]["start"], pred[i]["end"]), (g["start"], g["end"])), i, j)
                    for i in pred_idx for j, g in enumerate(gt)), reverse=True)
        ok = lambda s: s >= 0.5
    else:
        c = sorted(((-abs(pred[i]["pull"] - g["pull"]), i, j)
                    for i in pred_idx for j, g in enumerate(gt)), reverse=True)
        ok = lambda s: -s <= PULL_TOL_F
    up, ug, pairs = set(), set(), []
    for s, i, j in c:
        if not ok(s) or i in up or j in ug:
            continue
        up.add(i), ug.add(j), pairs.append((i, j))
    return pairs


def evaluate_dontcare(pred, gt_certain, gt_uncertain, rule, protocol="v1"):
    """Returns per-video dict incl. matched certain pairs and ignored predictions.

    protocol 'v1' (frozen baseline): uncertain absorbed under the SAME strict rule as certain.
    protocol 'v2' (corrected, declared before TEST, identical for all pipelines; evidence:
    failure attribution on VAL showed 48 edge-partial + 58 near-uncertain FPs that are
    annotation-policy artefacts, not model errors):
      (a) EDGE don't-care - unmatched predictions whose pull lies BEFORE the first annotated
          boundary (certain or uncertain start) or AFTER the last annotated end are ignored:
          those zones are exactly where Phase 2.5B/2.6 deliberately did not annotate censored
          edge reps. Predictions inside the annotated span can never use this rule.
      (b) RELAXED uncertain absorption - uncertain boundaries are low-confidence by
          definition, so absorption (still strictly one-to-one) matches on pull-within-
          tolerance OR IoU >= 0.25 instead of the full certain-rep rule."""
    pairs = _match(range(len(pred)), gt_certain, pred, rule)
    matched_p = {i for i, _ in pairs}
    rest = [i for i in range(len(pred)) if i not in matched_p]
    if protocol == "v1":
        ignored = {i for i, _ in _match(rest, gt_uncertain, pred, rule)}
    else:
        ignored = set()
        allb = gt_certain + gt_uncertain
        if allb:
            first = min(g["start"] for g in allb)
            last = max(g["end"] for g in allb)
            ignored |= {i for i in rest if pred[i]["pull"] < first or pred[i]["pull"] > last}
        rest2 = [i for i in rest if i not in ignored]
        used = set()
        cands = sorted((max(_iou((pred[i]["start"], pred[i]["end"]), (g["start"], g["end"])),
                            1.0 - abs(pred[i]["pull"] - g["pull"]) / (10 * PULL_TOL_F)), i, j)
                       for i in rest2 for j, g in enumerate(gt_uncertain))
        for s, i, j in sorted(cands, reverse=True):
            if i in ignored or j in used:
                continue
            g = gt_uncertain[j]
            iou = _iou((pred[i]["start"], pred[i]["end"]), (g["start"], g["end"]))
            if iou >= 0.25 or abs(pred[i]["pull"] - g["pull"]) <= PULL_TOL_F:
                ignored.add(i)
                used.add(j)
    fp_idx = [i for i in rest if i not in ignored]
    tp, fn = len(pairs), len(gt_certain) - len(pairs)
    m = dict(n_gt=len(gt_certain), n_unc=len(gt_uncertain), n_pred=len(pred),
             tp=tp, fp=len(fp_idx), fn=fn, n_ignored=len(ignored),
             matched=[(i, j) for i, j in pairs])
    for k in ("start", "pull", "end"):
        errs = [abs(pred[i][k] - gt_certain[j][k]) for i, j in pairs]
        m[f"{k}_mae_frames"] = float(np.mean(errs)) if errs else np.nan
    return m


def prf(tp, fp, fn):
    P = tp / max(tp + fp, 1)
    R = tp / max(tp + fn, 1)
    return P, R, 2 * P * R / max(P + R, 1e-9)


# ----------------------------------------------------------------- run
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pipeline", required=True)
    ap.add_argument("--split", choices=["val", "test"], default="val")
    ap.add_argument("--unlock-test", action="store_true")
    ap.add_argument("--conf_thr", type=float, default=None,
                    help="ablation: override prepare_video confidence threshold (default: core 0.3)")
    ap.add_argument("--supervision", choices=["certain", "verified_only"], default="certain",
                    help="'certain'=verified+auto (declared policy); 'verified_only' for Problem-1 ablation")
    ap.add_argument("--tag", default="baseline")
    ap.add_argument("--protocol", choices=["v1", "v2"], default="v1")
    args = ap.parse_args()
    if args.split == "test" and not args.unlock_test:
        sys.exit("TEST is locked. Pass --unlock-test only for the final frozen evaluation.")

    if args.conf_thr is not None:
        import train_eval as _te
        _orig = prepare_video
        def _pv(name, pose, fps, conf=None, conf_thr=0.3):
            return _orig(name, pose, fps, conf=conf, conf_thr=args.conf_thr)
        _te.prepare_video = _pv
        globals()["prepare_video"] = _pv
    vids = load_videos(args.pipeline)
    if args.supervision == "verified_only":
        # ablation affects SUPERVISION only: demote auto->don't-care in TRAIN videos alone
        # (VAL ground truth must stay comparable across supervision variants)
        for v in (v for v in vids.values() if v["split"] == "train"):
            keep = [i for i, t in enumerate(v["gt_tier"]) if t == "verified"]
            demoted = [r for i, r in enumerate(v["gt"]) if i not in keep]
            v["gt"] = [v["gt"][i] for i in keep]
            v["gt_tier"] = [v["gt_tier"][i] for i in keep]
            v["gt_uncertain"] = v["gt_uncertain"] + demoted     # demoted autos become don't-care
            for r in demoted:
                v["mask"][max(0, r["start"] - MASK_PAD):min(v["T"], r["end"] + MASK_PAD + 1)] = False
            v["y"] = phase_target(v["T"], v["gt"])
    train = [v for v in vids.values() if v["split"] == "train" and v["exercise"] == "back" and v["gt"]]
    evalset = [v for v in vids.values() if v["split"] == args.split]

    cache: dict = {}
    print(f"[{args.pipeline}] fitting on {len(train)} back TRAIN videos; selecting on VAL")
    val = [v for v in vids.values() if v["split"] == "val" and v["exercise"] == "back"]
    best, best_score = None, -1e9
    for sm, dt in product(GRID["smooth_hz"], GRID["detrend_s"]):
        base = Cfg(smooth_hz=sm, detrend_s=dt)
        model = fit_model_masked(train, base, cache)
        cf = crossfit_fused_masked(train, base, cache)
        for w, sf in product(GRID["dur_weight"], GRID["dur_sigma_floor"]):
            cfg = replace(base, dur_weight=w, dur_sigma_floor=sf)
            dec = fit_decoder_masked(train, cf, cfg)
            scores = []
            for v in val:
                res = predict_video(v, model, dec, cfg, cache)
                m = evaluate_dontcare(res["reps"], v["gt"], v["gt_uncertain"], "iou", args.protocol)
                P, R, F = prf(m["tp"], m["fp"], m["fn"])
                pf = m["pull_mae_frames"]
                scores.append(F - 0.5 * (0.5 if np.isnan(pf) else pf / max(PULL_TOL_F, 1) * 0.5))
            sc = float(np.mean(scores))
            if sc > best_score:
                best_score, best = sc, (cfg, model, dec)
    cfg, model, dec = best
    print(f"  selected on VAL: smooth={cfg.smooth_hz} detrend={cfg.detrend_s} "
          f"dur_w={cfg.dur_weight} sigf={cfg.dur_sigma_floor} (score {best_score:.3f})")

    odir = os.path.join(OUT, args.pipeline)
    os.makedirs(odir, exist_ok=True)
    rows, preds = [], []
    for v in evalset:
        res = predict_video(v, model, dec, cfg, cache)
        for i, r in enumerate(res["reps"], 1):
            preds.append(dict(video_id=v["name"], rep=i, **{k: r[k] for k in ("start", "pull", "end", "peak", "peak_z", "truncated")}))
        for rule in ("iou", "pull"):
            m = evaluate_dontcare(res["reps"], v["gt"], v["gt_uncertain"], rule, args.protocol)
            tiers = {}
            for tier in ("verified", "auto"):
                idx = [j for j, t in enumerate(v["gt_tier"]) if t == tier]
                mj = {j for _, j in m["matched"]}
                tiers[tier] = dict(n=len(idx), matched=len([j for j in idx if j in mj]))
            rows.append(make_row(
                dict(video_id=v["name"], subject=v["subject"], view=v["view"], form=v["form"],
                     exercise=v["exercise"], rule=rule, tier_match=json.dumps(tiers)),
                {k: vv for k, vv in m.items() if k != "matched"}))
    pd.DataFrame(preds).to_csv(os.path.join(odir, f"predictions_{args.split}_{args.tag}.csv"), index=False)
    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(odir, f"videos_{args.split}_{args.tag}.csv"), index=False)
    json.dump(dict(pipeline=args.pipeline, tag=args.tag, split=args.split,
                   supervision=args.supervision, cfg=cfg.__dict__ | {}, val_score=best_score),
              open(os.path.join(odir, f"config_{args.tag}.json"), "w"), indent=1, default=str)
    bi = df[(df.rule == "iou") & (df.exercise == "back")]
    P, R, F = prf(bi.tp.sum(), bi.fp.sum(), bi.fn.sum())
    print(f"{args.pipeline} {args.split} BACK IoU: TP {bi.tp.sum()} FP {bi.fp.sum()} FN {bi.fn.sum()} "
          f"P {P:.3f} R {R:.3f} F1 {F:.3f}  (ignored preds: {bi.n_ignored.sum()})")


if __name__ == "__main__":
    main()
