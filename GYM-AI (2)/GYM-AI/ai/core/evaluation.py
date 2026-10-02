"""Metrics + nested leave-one-group-out validation. Verbatim from rep_segmentation_v2_hsmm.py."""
from __future__ import annotations

from dataclasses import replace
from itertools import product
from typing import Dict

import numpy as np

from ai.core.config import Cfg, STATE_NAMES, TIE_TOL
from ai.core.decoder import fit_decoder, predict_video
from ai.core.phase_model import fit_model, predict_phase


# ----------------------------------------------------------------------------- metrics
def _iou(a, b) -> float:
    inter = max(0, min(a[1], b[1]) - max(a[0], b[0]))
    union = max(a[1], b[1]) - min(a[0], b[0])
    return inter / union if union > 0 else 0.0


def match_reps(pred, gt, iou_thr):
    """One-to-one greedy matching on temporal IoU of [start, end]. IoU>=0.5 is scale-free (independent
    of fps / rep duration) and is the usual convention in temporal action detection."""
    c = sorted(((_iou((p["start"], p["end"]), (g["start"], g["end"])), i, j)
                for i, p in enumerate(pred) for j, g in enumerate(gt)), reverse=True)
    up, ug, pairs = set(), set(), []
    for iou, i, j in c:
        if iou < iou_thr or i in up or j in ug:
            continue
        up.add(i), ug.add(j), pairs.append((i, j))
    return pairs


def evaluate_video(pred, gt, iou_thr=0.5) -> dict:
    pairs = match_reps(pred, gt, iou_thr)
    tp, fp, fn = len(pairs), len(pred) - len(pairs), len(gt) - len(pairs)
    P = tp / (tp + fp) if tp + fp else 0.0
    R = tp / (tp + fn) if tp + fn else 0.0
    F = 2 * P * R / (P + R) if P + R else 0.0
    err = {k: [pred[i][k] - gt[j][k] for i, j in pairs] for k in ("start", "pull", "end")}
    dur = [gt[j]["end"] - gt[j]["start"] for _, j in pairs]
    pull_frac = [abs(e) / d for e, d in zip(err["pull"], dur)]
    return dict(n_gt=len(gt), n_pred=len(pred), tp=tp, fp=fp, fn=fn, P=P, R=R, F1=F, err=err,
                pull_err_frac=float(np.mean(pull_frac)) if pull_frac else np.nan)


def composite(m) -> float:
    """Selection score used ONLY on training groups: detection F1 minus a timing penalty."""
    pf = m["pull_err_frac"]
    return m["F1"] - 0.5 * (0.5 if np.isnan(pf) else pf)


# ----------------------------------------------------------------------------- selection + nested CV
def crossfit_fused(train, groups, cfg, cache) -> Dict[str, np.ndarray]:
    """Out-of-fold fused phase for every TRAINING video (phase model never saw the video it predicts)."""
    out = {}
    names = sorted({groups[v["name"]] for v in train})
    if len(names) < 2:
        print("  [warn] single training group: decoder is fitted on in-sample phase (optimistic emissions)")
        model = fit_model(train, cfg, cache)
        return {v["name"]: predict_phase(v, model, cfg, cache)[1] for v in train}
    for g in names:
        tr = [v for v in train if groups[v["name"]] != g]
        model = fit_model(tr, cfg, cache)
        for v in (v for v in train if groups[v["name"]] == g):
            out[v["name"]] = predict_phase(v, model, cfg, cache)[1]
    return out


def _fit_all(train, cfg, groups, cache):
    """Phase model + cross-fitted phase + decoder, all from `train` only."""
    model = fit_model(train, cfg, cache)
    cf = crossfit_fused(train, groups, cfg, cache)
    dec = fit_decoder(train, cf, cfg)
    return model, dec


def select_config(train, groups, grid, iou_thr, cache) -> Cfg:
    """Inner leave-one-group-out over the TRAINING groups only. For every inner fold the phase model, the
    cross-fitted phase, the emissions, the duration model and the transition model are re-fitted on the inner
    training part, so nothing from the inner validation video (let alone the outer held-out one) leaks."""
    gnames = sorted({groups[v["name"]] for v in train})
    if len(gnames) < 2:
        print("  [warn] <2 training groups: cannot select hyper-parameters, using defaults")
        return Cfg()
    scores: Dict[Cfg, list] = {}
    for sm, dt in product(grid["smooth_hz"], grid["detrend_s"]):
        base = Cfg(smooth_hz=sm, detrend_s=dt)
        for g in gnames:
            tr = [v for v in train if groups[v["name"]] != g]
            va = [v for v in train if groups[v["name"]] == g]
            model = fit_model(tr, base, cache)
            cf = crossfit_fused(tr, groups, base, cache)
            for w, sf, tn in product(grid["dur_weight"], grid["dur_sigma_floor"], grid["tempo_norm"]):
                cfg = replace(base, dur_weight=w, dur_sigma_floor=sf, tempo_norm=tn)
                dec = fit_decoder(tr, cf, cfg)
                for v in va:
                    res = predict_video(v, model, dec, cfg, cache)
                    scores.setdefault(cfg, []).append(composite(evaluate_video(res["reps"], v["gt"], iou_thr)))
    # Pre-specified tie-break (Occam): configurations whose inner-CV score is within TIE_TOL of the best are
    # statistically indistinguishable with so few videos, so take the most neutral one (grid position 0 of each
    # parameter) instead of whichever happened to be enumerated first.
    means = {c: float(np.mean(sc)) for c, sc in scores.items()}
    top = max(means.values())
    tied = [c for c, m in means.items() if m >= top - TIE_TOL]
    pos = lambda c: sum(grid[k].index(getattr(c, k)) for k in ("smooth_hz", "detrend_s", "dur_weight", "dur_sigma_floor"))
    return min(tied, key=pos)


def describe_decoder(g, cfg, dec, train):
    s = dec.stats
    u = s["unit"]
    f = lambda d: f"median {d['median']:.2f} p10-p90 {d['p10']:.2f}-{d['p90']:.2f} min {d['min']:.2f} [{u}]"
    pv = lambda d: "[" + ", ".join(f"{x:.2f}" for x in d["per_video_median"]) + "]"
    print(f"[outer fold: HOLD OUT {g}]")
    print(f"  fitted on {len(train)} videos / {s['n_reps']} reps: {', '.join(s['train_videos'])}")
    print(f"  chosen on training only: smooth={cfg.smooth_hz}Hz detrend={cfg.detrend_s} dur_weight={cfg.dur_weight} "
          f"dur_sigma_floor={cfg.dur_sigma_floor} tempo_norm={cfg.tempo_norm}")
    if s["tempo_s"]:
        print(f"  label-free tempo estimate per training video (s/cycle): {s['tempo_s']}")
    print(f"  PULL duration : {f(s['pull'])}  log-sigma {s['pull']['log_sigma']:.2f}  per-video medians {pv(s['pull'])}")
    print(f"  RET  duration : {f(s['ret'])}  log-sigma {s['ret']['log_sigma']:.2f}  per-video medians {pv(s['ret'])}")
    print(f"  REST gap mean {s['rest_gap_mean']:.2f} [{u}] | P(RET->PULL, no pause) = {s['p_direct']:.2f} "
          f"({s['n_direct']}/{s['n_pairs']} train pairs with gap <= {s['gap_direct_tol']:.2f}, Laplace-smoothed)")
    print(f"  phase height at a rep's peak: mean {s['peak_height']['mean']:.2f} sd {s['peak_height']['sd']:.2f} "
          f"(min over training reps {s['peak_height']['min']:.2f})")
    print(f"  hard minimum segment: PULL {s['hard_min']['pull']:.2f}  RET {s['hard_min']['ret']:.2f} [{u}]")
    print("  emission mean (phase, velocity): " + "  ".join(
        f"{STATE_NAMES[k]}=({s['emis_mean'][k][0]:.2f},{s['emis_mean'][k][1]:+.2f})" for k in range(3)))


def tempo_check(v, res, dec):
    """POST-HOC diagnostic on the held-out video (never used for fitting): is its decoded tempo inside the range
    covered by the training videos? If not, the duration prior is extrapolating and recall may suffer."""
    reps = res["reps"]
    sc = res["tempo_s"] if res["tempo_s"] else 1.0
    if res["tempo_s"]:
        tr = list(dec.stats["tempo_s"].values())
        tr = [x for x in tr if x]
        print(f"  held-out label-free tempo {res['tempo_s']:.2f}s/cycle vs training [{min(tr):.2f}, {max(tr):.2f}] -> "
              f"{'inside' if min(tr) <= res['tempo_s'] <= max(tr) else 'OUTSIDE (handled by tempo normalisation)'}")
    if not reps:
        print("  held-out: no repetition decoded")
        return
    pull = float(np.median([(r["pull"] - r["start"]) / v["fps"] / sc for r in reps]))
    ret = float(np.median([(r["end"] - r["pull"]) / v["fps"] / sc for r in reps]))
    for name, val, pvv in (("PULL", pull, dec.stats["pull"]["per_video_median"]), ("RET", ret, dec.stats["ret"]["per_video_median"])):
        lo, hi = min(pvv), max(pvv)
        print(f"  held-out decoded {name} median {val:.2f} vs training per-video range [{lo:.2f}, {hi:.2f}] -> "
              f"{'inside' if lo <= val <= hi else 'OUTSIDE'}  [{dec.stats['unit']}]")


def run_cv(vids, groups, grid, iou_thr):
    cache: dict = {}
    results = {}
    for g in sorted({groups[v["name"]] for v in vids}):
        test = [v for v in vids if groups[v["name"]] == g]
        train = [v for v in vids if groups[v["name"]] != g]
        assert not ({v["name"] for v in test} & {v["name"] for v in train}), "LEAK: test video in training set"
        cfg = select_config(train, groups, grid, iou_thr, cache)
        model, dec = _fit_all(train, cfg, groups, cache)
        describe_decoder(g, cfg, dec, train)
        for v in test:                                   # held-out video is touched only here
            res = predict_video(v, model, dec, cfg, cache)
            res["metrics"], res["cfg"], res["decoder"] = evaluate_video(res["reps"], v["gt"], iou_thr), cfg, dec
            results[v["name"]] = res
            tempo_check(v, res, dec)
    return results
