#!/usr/bin/env python3
"""
Phase 2 diagnosis: WHY does the v2 HSMM decode asymmetry_01 as one giant truncated rep?

Reproduces the exact outer fold (hold out asymmetry_01; config selected on the 5 training videos
by the same inner CV), then scores TWO segmentations of the held-out video under the SAME decoder:
  (a) the decoded Viterbi path,
  (b) the ground-truth segmentation (REST/PULL/RET from the manual annotations),
and decomposes each total score into: emission, duration, transition, peak-height terms,
per segment. The term that makes (a) beat (b) is the culprit.

READ-ONLY: this script changes nothing and must never be used to tune on asymmetry_01.
Any fix it motivates is selected on training folds only and re-run with full nested LOVO.
"""
from __future__ import annotations

import os
import sys

import numpy as np

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from ai.core.config import GRID, PULL, REST, RET, STATE_NAMES  # noqa: E402
from ai.core.decoder import (build_tables, emission_loglik, hsmm_decode,  # noqa: E402
                             peak_loglik, video_scale)
from ai.core.evaluation import _fit_all, select_config  # noqa: E402
from ai.core.features import phase_target, prepare_video, state_labels  # noqa: E402
from ai.core.io import load_annotations  # noqa: E402
from ai.core.phase_model import predict_phase  # noqa: E402

HOLD = "asymmetry_01"


def gt_segments(T: int, reps) -> list:
    """GT reps -> full (state, a, b, left_cut, right_cut) segmentation with REST fill."""
    z = state_labels(T, reps)
    segs, s = [], 0
    for t in range(1, T + 1):
        if t == T or z[t] != z[s]:
            segs.append((int(z[s]), s, t, s == 0, t == T))
            s = t
    return segs


def score_path(segs, E, tabs, p_direct, lam, pk_ll):
    """Total path score with the same arithmetic as hsmm_decode, split per term and per segment."""
    C = np.vstack([np.zeros((1, E.shape[1])), np.cumsum(E, axis=0)])
    logpi = -np.log(E.shape[1])
    A = {(REST, PULL): 0.0, (PULL, RET): 0.0,
         (RET, REST): lam * np.log1p(-p_direct), (RET, PULL): lam * np.log(p_direct)}
    rows, tot = [], dict(emis=0.0, dur=0.0, trans=0.0, peak=0.0, init=logpi)
    for i, (k, a, b, lc, rc) in enumerate(segs):
        emis = float(C[b, k] - C[a, k])
        d = b - a
        tab = tabs[k]
        if lc or rc:                       # censored segment -> survival
            dur = lam * float(tab["lsurv"][min(d, len(tab["lsurv"]) - 1)])
            dur_kind = "surv"
        else:
            dur = lam * float(tab["lpmf"][d] if d < len(tab["lpmf"]) else -np.inf)
            dur_kind = "pmf"
        trans = peak = 0.0
        if i + 1 < len(segs):
            k2 = segs[i + 1][0]
            trans = A.get((k, k2), -1e9)
            if k == PULL and k2 == RET:
                peak = lam * float(pk_ll[b])
        rows.append(dict(i=i, state=STATE_NAMES[k], a=a, b=b, frames=d, cens=(lc or rc),
                         emis=emis, dur=dur, dur_kind=dur_kind, trans=trans, peak=peak))
        tot["emis"] += emis
        tot["dur"] += dur
        tot["trans"] += trans
        tot["peak"] += peak
    tot["TOTAL"] = sum(tot.values())
    return rows, tot


def main():
    ann = load_annotations(os.path.join(REPO_ROOT, "ai", "outputs", "manual_rep_annotations.csv"))
    vids = []
    for name, gt in ann.items():
        pose = np.load(os.path.join(REPO_ROOT, "ai", "data", "poses3d", name + ".npy"))
        v = prepare_video(name, pose, 30.0, conf=None)
        v["gt"], v["y"] = gt, phase_target(v["T"], gt)
        vids.append(v)
    vids.sort(key=lambda v: v["name"])
    groups = {v["name"]: v["name"] for v in vids}

    test = [v for v in vids if v["name"] == HOLD][0]
    train = [v for v in vids if v["name"] != HOLD]
    cache: dict = {}
    cfg = select_config(train, groups, GRID, 0.5, cache)
    model, dec = _fit_all(train, cfg, groups, cache)
    print(f"cfg (chosen on training only): {cfg}")

    phi, fused, _ = predict_phase(test, model, cfg, cache)
    scale = video_scale(fused, test["fps"], cfg, dec.ref_tempo_s)
    print(f"held-out tempo scale: {scale:.3f} s/cycle (training median ref {dec.ref_tempo_s:.3f})")
    E = emission_loglik(dec, fused, test["fps"], cfg, scale)
    tabs = build_tables(dec, test["fps"], test["T"], scale)
    pk_ll = peak_loglik(dec, fused)

    segs_dec, viterbi_score = hsmm_decode(E, tabs, dec.p_direct, cfg.dur_weight, pk_ll, return_score=True)

    print(f"\nfused-phase stats on {HOLD}: min {fused.min():.2f} max {fused.max():.2f}")
    print(f"peak-height law from training: mean {dec.pk_mu:.2f} sd {dec.pk_sd:.2f}")
    print("GT pulls and the local fused-phase peak there:")
    for r in test["gt"]:
        from ai.core.features import local_peak
        h = local_peak(fused, r["pull"])
        print(f"  pull@{r['pull']:>3}: local peak {h:.2f}  peak_ll {pk_ll[r['pull']]:.2f}  "
              f"z={(h - dec.pk_mu) / dec.pk_sd:+.1f}")
    print(f"PULL duration table: dmin {tabs[PULL]['dmin']} dmax {tabs[PULL]['dmax']} frames")
    print(f"RET  duration table: dmin {tabs[RET]['dmin']} dmax {tabs[RET]['dmax']} frames "
          f"(survival floor beyond dmax = -30)")

    for label, segs in (("DECODED (Viterbi)", segs_dec), ("GROUND TRUTH", gt_segments(test["T"], test["gt"]))):
        rows, tot = score_path(segs, E, tabs, dec.p_direct, cfg.dur_weight, pk_ll)
        print(f"\n=== {label} ===")
        print(f"{'seg':>3} {'state':<5} {'a':>4} {'b':>4} {'len':>4} {'cens':>5} "
              f"{'emission':>10} {'duration':>10} {'kind':>5} {'trans':>8} {'peak':>8}")
        for r in rows:
            print(f"{r['i']:>3} {r['state']:<5} {r['a']:>4} {r['b']:>4} {r['frames']:>4} {str(r['cens']):>5} "
                  f"{r['emis']:>10.2f} {r['dur']:>10.2f} {r['dur_kind']:>5} {r['trans']:>8.2f} {r['peak']:>8.2f}")
        print("totals: " + "  ".join(f"{k}={v:.2f}" for k, v in tot.items()))
    print(f"\n(hsmm_decode internal best score: {viterbi_score:.2f} - should match DECODED total)")


if __name__ == "__main__":
    main()
