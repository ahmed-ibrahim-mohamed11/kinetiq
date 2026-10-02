#!/usr/bin/env python3
"""
Form-invariant repetition segmentation for seated row (rear camera).      [v2: explicit-duration decoder]

Input  : MotionBERT 3D skeleton in H36M-17 order, (T, 17, 3) per video  (+ optional per-joint conf (T, 17))
Output : repetitions (start / pull / end), per-arm timing (asymmetry features), honest nested LOVO evaluation

Pipeline
  3D skeleton
    -> torso-frame, body-scaled features, LEFT and RIGHT arm kept separate   (prepare_video)
    -> zero-phase low-pass (+ optional slow-drift removal)                    (get_design)
    -> small ridge regression to a normalised cycle phase in [0, 1]           (PhaseModel)       [unchanged]
    -> robust arm fusion (nan-max: "either arm is pulling")                   (fuse)             [unchanged]
    -> label-free tempo estimate (autocorrelation of phase velocity)           (estimate_tempo)    [NEW]
    -> HSMM / segmental Viterbi over REST / PULL / RET, durations in tempo units (hsmm_decode)    [NEW]
    -> reps = complete PULL+RET pairs; per-arm timing inside each rep          (segments_to_reps) [NEW]

Why a segmental decoder: consecutive reps can have almost no REST between them and a tiny phase valley, so
valley depth / prominence cannot separate "two reps" from "one rep with ripples". Segment DURATIONS
(learned from training videos only) and the direction of motion can.

Leakage rules enforced in code
  * phase model, decoder emissions, duration model, transition model and hyper-parameter selection
    all see TRAINING groups only; the held-out group is decoded once, at the very end of each outer fold.
  * the decoder is fitted on CROSS-FITTED phase predictions (each training video is predicted by a phase
    model that never saw it), so emission variances are not optimistically small.
  * the CSV 'label' column (correct / asymmetry) is dropped on load and never used.
"""
from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass, asdict, replace
from itertools import product
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
from scipy.ndimage import median_filter
from scipy.signal import butter, find_peaks, sosfiltfilt
from scipy.special import logsumexp
from scipy.stats import norm

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# ----------------------------------------------------------------------------- H36M-17 indices
PELVIS, R_HIP, L_HIP, SPINE, THORAX, NECK, HEAD = 0, 1, 4, 7, 8, 9, 10
L_SHO, L_ELB, L_WRI, R_SHO, R_ELB, R_WRI = 11, 12, 13, 14, 15, 16

# Athlete faces AWAY from a rear camera -> forward is +Z in H36M camera coordinates.
# If your MotionBERT output uses the opposite depth sign, flip this ONE vector.
FACING_IN_CAMERA = np.array([0.0, 0.0, 1.0])

REST, PULL, RET = 0, 1, 2
STATE_NAMES = ["REST", "PULL", "RET"]
PLATEAU_FRAC = 0.9          # only used for per-arm pull-time (asymmetry feature), not for counting


# ----------------------------------------------------------------------------- configuration
@dataclass(frozen=True)
class Cfg:
    # --- phase model: selected on training groups only (see GRID) ---
    smooth_hz: float = 2.0
    detrend_s: Optional[float] = None
    alpha: float = 0.03          # ridge strength (scaled by n inside fit); fixed
    conf_thr: float = 0.3
    # --- decoder: selected on training groups only (see GRID) ---
    dur_weight: float = 1.0      # weight of duration/transition log-probs relative to (decorrelated) emissions
    dur_sigma_floor: float = 0.3 # lower bound on log-duration std; guards against over-confident duration priors
    tempo_norm: bool = True      # durations/velocity in units of THIS video's own tempo (label-free estimate)
    # --- structural constants (NOT tuned; each is scaled by quantities learned in the training fold) ---
    emis_var_floor: float = 1e-3
    min_dur_frac: float = 0.5    # hard minimum segment length = 0.5 * shortest TRAINING segment
    direct_gap_frac: float = 0.25  # gap <= 0.25 * median RET duration counts as "no pause" (RET -> PULL)
    cens_q: float = 0.10         # a segment cut by the video edge counts only if >= this quantile of its duration law
    peak_sd_floor: float = 0.05  # lower bound on std of the phase height at a rep's peak


GRID = dict(
    smooth_hz=[2.0, 3.0],        # front-end bandwidth must exceed ~2x the fastest rep rate to support (design constraint,
                                 # not tunable on slow videos: a 1 Hz low-pass erases a 1 s/rep cycle)
    detrend_s=[None, 10.0],
    dur_weight=[1.0, 2.0],
    dur_sigma_floor=[0.3, 0.5],
    tempo_norm=[True],           # NOT tuned: inner CV cannot see tempo extrapolation when training tempos are similar.
)                                # Use --no_tempo_norm to run the absolute-duration ablation.


# ----------------------------------------------------------------------------- features
def _interp_rows(X: np.ndarray, valid: np.ndarray) -> np.ndarray:
    X = X.copy()
    if valid.all() or valid.sum() < 2:
        return X
    idx = np.arange(len(X))
    for k in range(X.shape[1]):
        X[~valid, k] = np.interp(idx[~valid], idx[valid], X[valid, k])
    return X


def _unit(v: np.ndarray) -> np.ndarray:
    return v / (np.linalg.norm(v, axis=1, keepdims=True) + 1e-9)


def prepare_video(name: str, pose: np.ndarray, fps: float,
                  conf: Optional[np.ndarray] = None, conf_thr: float = 0.3) -> dict:
    """Raw (unsmoothed) torso-frame features. Position is relative to the pelvis and divided by the
    video-median torso length, so it is invariant to athlete size, camera distance, resolution and
    camera yaw/tilt. Left and right arms are kept separate."""
    P = np.asarray(pose, dtype=float)
    if P.ndim != 3 or P.shape[1:] != (17, 3):
        raise ValueError(f"{name}: expected (T,17,3) H36M pose, got {P.shape}")
    T = len(P)
    finite = np.isfinite(P).all(axis=(1, 2))
    P = _interp_rows(P.reshape(T, -1), finite).reshape(T, 17, 3)

    pelvis = P[:, PELVIS]
    up_raw = P[:, THORAX] - pelvis
    torso_len = float(np.median(np.linalg.norm(up_raw, axis=1)))
    up = _unit(up_raw)
    lat = P[:, R_SHO] - P[:, L_SHO]
    lat = _unit(lat - (lat * up).sum(1, keepdims=True) * up)
    fwd = np.cross(lat, up)
    sgn = np.sign(np.median(fwd @ FACING_IN_CAMERA)) or 1.0   # one sign per video, not per frame
    fwd = fwd * sgn

    def coords(v):  # [lateral(R positive), vertical(up), BACKWARD]
        d = (v - pelvis) / torso_len
        return np.stack([(d * lat).sum(1), (d * up).sum(1), -(d * fwd).sum(1)], axis=1)

    sho_c = coords(0.5 * (P[:, L_SHO] + P[:, R_SHO]))
    trunk = sho_c[:, 2:3]                                    # trunk lean / shoulder retraction
    arm, valid = {}, {}
    for a, (sho, elb, mirror) in {"L": (L_SHO, L_ELB, -1.0), "R": (R_SHO, R_ELB, 1.0)}.items():
        e, s = coords(P[:, elb]), coords(P[:, sho])
        f = np.hstack([e, e - s])                            # elbow-vs-pelvis (3) + elbow-vs-shoulder (3)
        f[:, [0, 3]] *= mirror                               # "outward" is positive for both arms
        arm[a] = f
        ok = finite.copy()
        if conf is not None:
            ok &= conf[:, [sho, elb, PELVIS, THORAX]].min(axis=1) >= conf_thr
        valid[a] = ok
    return dict(name=name, fps=float(fps), T=T, arm=arm, trunk=trunk, valid=valid, torso_len=torso_len)


def lowpass(X: np.ndarray, fps: float, hz: Optional[float]) -> np.ndarray:
    if hz is None or len(X) < 30:
        return X
    sos = butter(2, min(hz / (fps / 2.0), 0.9), output="sos")
    return sosfiltfilt(sos, X, axis=0)


def get_design(v: dict, cfg: Cfg, cache: Optional[dict]) -> Dict[str, np.ndarray]:
    key = (v["name"], cfg.smooth_hz, cfg.detrend_s)
    if cache is not None and key in cache:
        return cache[key]
    out = {}
    for a in "LR":
        X = np.hstack([v["arm"][a], v["trunk"]])
        X = _interp_rows(X, v["valid"][a])
        X = lowpass(X, v["fps"], cfg.smooth_hz)
        if cfg.detrend_s:                                     # slow drift only (window >> one rep)
            win = max(3, int(cfg.detrend_s * v["fps"]) | 1)
            X = X - median_filter(X, size=(win, 1), mode="nearest")
        out[a] = X
    if cache is not None:
        cache[key] = out
    return out


# ----------------------------------------------------------------------------- phase model
def phase_target(T: int, reps: List[dict]) -> np.ndarray:
    """0 at rest, rises to 1 at annotated PULL, back to 0 at END (half-cosine ramps).
    Built from manual START/PULL/END only; no form label involved."""
    y = np.zeros(T)
    for r in reps:
        s, p, e = r["start"], r["pull"], r["end"]
        if p > s:
            i = np.arange(s, min(p, T - 1) + 1)
            y[i] = np.maximum(y[i], 0.5 - 0.5 * np.cos(np.pi * (i - s) / (p - s)))
        if e > p:
            i = np.arange(p, min(e, T - 1) + 1)
            y[i] = np.maximum(y[i], 0.5 + 0.5 * np.cos(np.pi * (i - p) / (e - p)))
    return y


@dataclass
class PhaseModel:
    mu: np.ndarray
    sd: np.ndarray
    w: np.ndarray
    b: float
    min_rep_s: float          # shortest annotated rep in the TRAINING set (used for blip rejection)

    def predict(self, X: np.ndarray) -> np.ndarray:
        return ((X - self.mu) / self.sd) @ self.w + self.b

    def to_json(self) -> dict:
        return dict(mu=self.mu.tolist(), sd=self.sd.tolist(), w=self.w.tolist(), b=self.b, min_rep_s=self.min_rep_s)

    @staticmethod
    def from_json(d: dict) -> "PhaseModel":
        return PhaseModel(np.array(d["mu"]), np.array(d["sd"]), np.array(d["w"]), d["b"], d["min_rep_s"])


def fit_model(train: List[dict], cfg: Cfg, cache: Optional[dict]) -> PhaseModel:
    Xs, ys = [], []
    for v in train:
        d = get_design(v, cfg, cache)
        for a in "LR":                                        # shared weights across both arms
            Xs.append(d[a])
            ys.append(v["y"])
    X, y = np.vstack(Xs), np.concatenate(ys)
    mu, sd = X.mean(0), X.std(0) + 1e-6
    Z, yc = (X - mu) / sd, y.mean()
    n = len(Z)
    w = np.linalg.solve(Z.T @ Z + cfg.alpha * n * np.eye(Z.shape[1]), Z.T @ (y - yc))
    min_rep_s = min((r["end"] - r["start"]) / v["fps"] for v in train for r in v["gt"])
    return PhaseModel(mu, sd, w, float(yc), float(min_rep_s))


def fuse(phi_L, phi_R, valid_L, valid_R) -> np.ndarray:
    """'Either arm is pulling'. Low-confidence arm frames are ignored; if both are bad, fall back."""
    f = np.fmax(np.where(valid_L, phi_L, np.nan), np.where(valid_R, phi_R, np.nan))
    bad = np.isnan(f)
    f[bad] = np.maximum(phi_L, phi_R)[bad]
    return f


def predict_phase(v: dict, model: PhaseModel, cfg: Cfg, cache: Optional[dict]):
    d = get_design(v, cfg, cache)
    phi = {a: model.predict(d[a]) for a in "LR"}
    return phi, fuse(phi["L"], phi["R"], v["valid"]["L"], v["valid"]["R"]), d


# ----------------------------------------------------------------------------- explicit-duration decoder (HSMM)
def estimate_tempo(fused: np.ndarray, fps: float, lo_s: float = 0.4, hi_s: float = 5.0) -> Optional[float]:
    """Label-free period (s) of one cycle: first strong peak of the autocorrelation of the phase VELOCITY
    (velocity oscillates at the rep rate even when the phase itself barely dips between reps). Uses only the
    signal; no rep count, no annotation. Returns None when no periodicity is found."""
    x = np.gradient(fused) * fps
    x = x - x.mean()
    n = len(x)
    f = np.fft.rfft(x, 2 * n)
    ac = np.fft.irfft(f * np.conj(f))[:n]
    ac = ac / (ac[0] + 1e-12)
    lo, hi = int(lo_s * fps), min(int(hi_s * fps), n - 1)
    if hi <= lo + 2:
        return None
    seg = ac[lo:hi + 1]
    pk, _ = find_peaks(seg)
    if len(pk) == 0:
        return None
    h = seg[pk]
    first = pk[h >= 0.6 * h.max()][0]          # first strong peak: avoids locking on 2x the period
    return float((lo + first) / fps)


def video_scale(fused: np.ndarray, fps: float, cfg: Cfg, ref: Optional[float]) -> float:
    if not cfg.tempo_norm:
        return 1.0
    t = estimate_tempo(fused, fps)
    return float(t if t is not None else (ref if ref is not None else 1.0))


def obs_features(fused: np.ndarray, fps: float, scale: float = 1.0) -> np.ndarray:
    """[fused phase, phase velocity in 1/tempo-unit]. Velocity carries the direction of motion, which separates
    PULL from RET when the valley between two reps is almost flat."""
    return np.stack([fused, np.gradient(fused) * fps * scale], axis=1)


def local_peak(fused: np.ndarray, t: int, w: int = 3) -> float:
    return float(np.max(fused[max(0, t - w):min(len(fused), t + w + 1)]))


def state_labels(T: int, reps: List[dict]) -> np.ndarray:
    z = np.zeros(T, dtype=int)
    for r in sorted(reps, key=lambda r: r["start"]):
        z[r["start"]:r["pull"]] = PULL
        z[r["pull"]:r["end"]] = RET
    return z


@dataclass
class Decoder:
    mu: np.ndarray           # (3, 2) emission means  [phase, velocity]
    var: np.ndarray          # (3, 2) emission variances
    dur_mu: np.ndarray       # (2,) mean of log-duration for PULL, RET   (tempo units if tempo_norm else seconds)
    dur_sigma: np.ndarray    # (2,)
    dur_min: np.ndarray      # (2,) hard minimum duration (same units)
    rest_mean: float         # mean REST gap between reps (same units; geometric duration law)
    p_direct: float          # P(RET -> PULL without REST)
    pk_mu: float             # learned law of the phase height at a complete rep's peak
    pk_sd: float
    tempo_norm: bool
    ref_tempo_s: float       # median tempo of training videos (fallback only)
    stats: dict

    def to_json(self) -> dict:
        d = {k: (v.tolist() if isinstance(v, np.ndarray) else v) for k, v in self.__dict__.items()}
        return d

    @staticmethod
    def from_json(d: dict) -> "Decoder":
        d = dict(d)
        for k in ("mu", "var", "dur_mu", "dur_sigma", "dur_min"):
            d[k] = np.array(d[k])
        return Decoder(**d)


def fit_decoder(train: List[dict], fused_cf: Dict[str, np.ndarray], cfg: Cfg) -> Decoder:
    """Everything here is estimated from TRAINING videos only: emissions / peak-height law from cross-fitted
    phase predictions + GT-derived frame states; durations / transitions from the training START/PULL/END
    annotations; tempo from the (cross-fitted) phase signal, never from the annotations."""
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
        O.append(obs_features(f, v["fps"], sc))
        Z.append(state_labels(v["T"], v["gt"]))
        reps = sorted(v["gt"], key=lambda r: r["start"])
        pv = [(r["pull"] - r["start"]) / v["fps"] / sc for r in reps]
        rv = [(r["end"] - r["pull"]) / v["fps"] / sc for r in reps]
        d_rel[PULL] += pv
        d_rel[RET] += rv
        per_video[PULL].append(float(np.median(pv)))
        per_video[RET].append(float(np.median(rv)))
        peaks += [local_peak(f, r["pull"]) for r in reps]
        for a, b in zip(reps[:-1], reps[1:]):
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
    p_direct = (n_direct + 1.0) / (n_pairs + 2.0)             # Laplace-smoothed: never exactly 0 or 1
    pk_mu, pk_sd = float(np.mean(peaks)), float(max(np.std(peaks, ddof=1), cfg.peak_sd_floor))

    q = lambda x, p: float(np.percentile(x, p))
    unit = "tempo" if cfg.tempo_norm else "s"
    stats = dict(
        train_videos=[v["name"] for v in train], n_reps=len(d_rel[PULL]), unit=unit,
        tempo_s={n: round(float(t), 2) if t is not None else None for n, t in raw.items()} if cfg.tempo_norm else {},
        pull=dict(median=q(d_rel[PULL], 50), p10=q(d_rel[PULL], 10), p90=q(d_rel[PULL], 90), min=float(np.min(d_rel[PULL])),
                  per_video_median=per_video[PULL], log_sigma=float(dur_sd[0])),
        ret=dict(median=q(d_rel[RET], 50), p10=q(d_rel[RET], 10), p90=q(d_rel[RET], 90), min=float(np.min(d_rel[RET])),
                 per_video_median=per_video[RET], log_sigma=float(dur_sd[1])),
        rest_gap_mean=rest_mean, gap_direct_tol=tol, n_direct=n_direct, n_pairs=n_pairs, p_direct=p_direct,
        peak_height=dict(mean=pk_mu, sd=pk_sd, min=float(np.min(peaks))),
        emis_mean=mu.tolist(), emis_std=np.sqrt(var).tolist(),
        hard_min=dict(pull=float(dur_min[0]), ret=float(dur_min[1])))
    return Decoder(mu, var, dur_mu, dur_sd, dur_min, rest_mean, p_direct, pk_mu, pk_sd, bool(cfg.tempo_norm), ref, stats)


def emission_loglik(dec: Decoder, fused: np.ndarray, fps: float, cfg: Cfg, scale: float) -> np.ndarray:
    O = obs_features(fused, fps, scale)
    ll = np.zeros((len(O), 3))
    for k in range(3):
        ll[:, k] = -0.5 * (((O - dec.mu[k]) ** 2) / dec.var[k] + np.log(2 * np.pi * dec.var[k])).sum(1)
    # A signal low-passed at f_c carries ~2*f_c independent samples per second, not fps. Without this factor the
    # emissions over-count evidence ~10x and drown the duration model. Not tuned: it follows from the filter.
    return ll * min(1.0, 2.0 * (cfg.smooth_hz or fps / 2.0) / fps)


def build_tables(dec: Decoder, fps: float, T: int, scale: float) -> dict:
    """Per-state log duration pmf (exact, arrays indexed by d in frames) and log survival (for segments cut by
    the video edge). Durations are converted to the model's unit (seconds, or this video's tempo)."""
    tabs = {}
    idx = np.arange(T + 1)
    unit_per_frame = 1.0 / fps / scale
    q = float(np.clip(unit_per_frame / max(dec.rest_mean, unit_per_frame), 1e-3, 0.9))
    lp = np.full(T + 1, -np.inf)
    lp[1:] = np.log(q) + (idx[1:] - 1) * np.log1p(-q)
    ls = np.full(T + 1, -np.inf)
    ls[1:] = (idx[1:] - 1) * np.log1p(-q)
    ls[0] = 0.0
    tabs[REST] = dict(dmin=1, lpmf=lp, lsurv=ls)
    for k, i in ((PULL, 0), (RET, 1)):
        mu, sg = dec.dur_mu[i], dec.dur_sigma[i]
        dmin = max(2, int(np.floor(dec.dur_min[i] / unit_per_frame)))
        dmax = int(min(T, max(dmin + 2, np.ceil(np.exp(mu + 4 * sg) / unit_per_frame))))
        d = np.arange(1, dmax + 1)
        u = d * unit_per_frame
        l = -0.5 * ((np.log(u) - mu) / sg) ** 2 - np.log(u)
        l[d < dmin] = -np.inf
        l -= logsumexp(l)
        pmf = np.full(T + 1, -np.inf)
        pmf[1:dmax + 1] = l
        surv = np.full(T + 1, -30.0)
        surv[1:dmax + 1] = np.logaddexp.accumulate(l[::-1])[::-1]
        surv[0] = 0.0
        tabs[k] = dict(dmin=dmin, lpmf=pmf, lsurv=surv, dmax=dmax)
    return tabs


def peak_loglik(dec: Decoder, fused: np.ndarray) -> np.ndarray:
    """Log-likelihood of a PULL->RET switch at boundary t, from the learned peak-height law. A weak partial
    movement is therefore expensive as a COMPLETE repetition; it is not rejected by a threshold."""
    T = len(fused)
    h = np.array([local_peak(fused, min(t, T - 1)) for t in range(T + 1)])
    return -0.5 * ((h - dec.pk_mu) / dec.pk_sd) ** 2 - np.log(dec.pk_sd * np.sqrt(2 * np.pi))


def hsmm_decode(E: np.ndarray, tabs: dict, p_direct: float, lam: float, pk_ll: np.ndarray, return_score: bool = False):
    """Segmental Viterbi. Grammar:  REST -> PULL -> RET -> {REST | PULL}.   RET -> PULL is the 'no pause' path.
    Segment tuple: (state, start, end_exclusive, left_cut, right_cut). The first segment is left-censored and the
    last is right-censored (survival instead of pmf), so partial movements at the video edges are decoded but are
    not forced to look like complete repetitions."""
    T, K = E.shape
    NEG = -1e9
    C = np.vstack([np.zeros((1, K)), np.cumsum(E, axis=0)])
    A = np.full((K, K), NEG)
    A[REST, PULL] = 0.0
    A[PULL, RET] = 0.0
    A[RET, REST] = lam * np.log1p(-p_direct)
    A[RET, PULL] = lam * np.log(p_direct)
    logpi = -np.log(K)

    delta = np.full((K, T + 1), NEG)
    bd = np.zeros((K, T + 1), dtype=int)
    bj = np.full((K, T + 1), -1, dtype=int)
    bp = np.full((K, T + 1), NEG)           # best score of "some segment ended at s, next one is state k"
    bpj = np.zeros((K, T + 1), dtype=int)

    for t in range(1, T + 1):
        for k in range(K):
            tab = tabs[k]
            best = logpi + lam * tab["lsurv"][t] + C[t, k] - C[0, k]     # first segment, left-censored
            bdv, bjv = t, -1
            lo, hi = max(tab["dmin"], 1), min(tab.get("dmax", T), t - 1)
            if hi >= lo:
                ds = np.arange(lo, hi + 1)
                s = t - ds
                sc = bp[k, s] + lam * tab["lpmf"][ds] + C[t, k] - C[s, k]
                i = int(np.argmax(sc))
                if sc[i] > best:
                    best, bdv, bjv = float(sc[i]), int(ds[i]), int(bpj[k, s[i]])
            delta[k, t], bd[k, t], bj[k, t] = best, bdv, bjv
        for k in range(K):
            cand = delta[:, t] + A[:, k]
            if k == RET:
                cand[PULL] += lam * pk_ll[t]          # a COMPLETE pull must end at a plausible peak height
            j = int(np.argmax(cand))
            bp[k, t], bpj[k, t] = cand[j], j

    best_final, arg = -np.inf, None                                       # last segment, right-censored
    for k in range(K):
        tab = tabs[k]
        sc0 = logpi + lam * tab["lsurv"][T] + C[T, k] - C[0, k]
        if sc0 > best_final:
            best_final, arg = sc0, (k, T, -1)
        ds = np.arange(1, T)
        s = T - ds
        sc = bp[k, s] + lam * tab["lsurv"][ds] + C[T, k] - C[s, k]
        i = int(np.argmax(sc))
        if sc[i] > best_final:
            best_final, arg = float(sc[i]), (k, int(ds[i]), int(bpj[k, s[i]]))

    k, d, jprev = arg
    segs = [(k, T - d, T, T - d == 0, True)]
    t, j = T - d, jprev
    while t > 0:
        dd = bd[j, t]
        segs.append((j, t - dd, t, t - dd == 0, False))
        t, j = t - dd, bj[j, t]
    return (segs[::-1], best_final) if return_score else segs[::-1]


def _min_len_frames(dec: Decoder, i: int, cfg: Cfg, fps: float, scale: float) -> float:
    return float(np.exp(dec.dur_mu[i] + dec.dur_sigma[i] * norm.ppf(cfg.cens_q)) * scale * fps)


def arm_timing(phi_arm: Dict[str, np.ndarray], s: int, e: int, fps: float) -> dict:
    """Per-arm pull time / onset inside the decoded rep window: the asymmetry features. Left/right timing
    differences live here and cannot create extra repetitions because the window comes from the decoder."""
    st = {}
    for a in "LR":
        seg = phi_arm[a][s:e + 1]
        api = s + int(np.argmax(seg))
        apk = float(phi_arm[a][api])
        thr = PLATEAU_FRAC * apk
        l = r = api
        while l > s and phi_arm[a][l - 1] >= thr:
            l -= 1
        while r < e and phi_arm[a][r + 1] >= thr:
            r += 1
        k = api
        while k > s and phi_arm[a][k - 1] >= 0.5 * apk:
            k -= 1
        st[a] = (apk, int(round((l + r) / 2)), k)
    return dict(peak_L=st["L"][0], peak_R=st["R"][0], pull_L=st["L"][1], pull_R=st["R"][1],
                lag_pull_s=(st["R"][1] - st["L"][1]) / fps,           # >0: right arm peaks later
                lag_onset_s=(st["R"][2] - st["L"][2]) / fps,
                amp_ratio=min(st["L"][0], st["R"][0]) / (max(st["L"][0], st["R"][0]) + 1e-9))


def segments_to_reps(segs, dec: Decoder, cfg: Cfg, fps: float, scale: float, fused, phi_arm) -> List[dict]:
    """A repetition = a PULL segment immediately followed by a RET segment. Segments cut by the video edge are
    accepted only if their observed length is plausible for a real segment (>= cens_q quantile of the duration
    law learned on the training folds): this rejects a 1-frame PULL at frame 11, yet still accepts a final
    return that is cut by the end of the video."""
    reps = []
    for i, (k, a, b, lc, rc) in enumerate(segs):
        if k != PULL or i + 1 >= len(segs) or segs[i + 1][0] != RET:
            continue
        k2, a2, b2, lc2, rc2 = segs[i + 1]
        if lc and (b - a) < _min_len_frames(dec, 0, cfg, fps, scale):
            continue
        if rc2 and (b2 - a2) < _min_len_frames(dec, 1, cfg, fps, scale):
            continue
        s, p, e = int(a), int(b), int(min(b2, len(fused) - 1))
        pk = local_peak(fused, p)
        rep = dict(start=s, pull=p, end=e, peak=float(np.max(fused[s:e + 1])), peak_z=(pk - dec.pk_mu) / dec.pk_sd,
                   truncated=bool(lc or rc2))
        rep.update(arm_timing(phi_arm, s, e, fps))
        reps.append(rep)
    return reps


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


def predict_video(v, model: PhaseModel, dec: Decoder, cfg: Cfg, cache=None) -> dict:
    phi, fused, design = predict_phase(v, model, cfg, cache)
    scale = video_scale(fused, v["fps"], cfg, dec.ref_tempo_s) if dec.tempo_norm else 1.0
    E = emission_loglik(dec, fused, v["fps"], cfg, scale)
    tabs = build_tables(dec, v["fps"], v["T"], scale)
    segs = hsmm_decode(E, tabs, dec.p_direct, cfg.dur_weight, peak_loglik(dec, fused))
    return dict(reps=segments_to_reps(segs, dec, cfg, v["fps"], scale, fused, phi), segments=segs,
                phi=phi, fused=fused, design=design, tempo_s=scale if dec.tempo_norm else None)


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
def _fit_all(train, cfg, groups, cache):
    """Phase model + cross-fitted phase + decoder, all from `train` only."""
    model = fit_model(train, cfg, cache)
    cf = crossfit_fused(train, groups, cfg, cache)
    dec = fit_decoder(train, cf, cfg)
    return model, dec


TIE_TOL = 0.02   # inner-CV composite score difference below which configs are treated as tied


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


def print_tables(vids, results):
    print("\nDETECTION (held-out predictions, match = temporal IoU >= 0.5 on [start, end])")
    print(f"{'Video':<14}{'GT reps':>8}{'Pred reps':>10}{'TP':>4}{'FP':>4}{'FN':>4}"
          f"{'Precision':>11}{'Recall':>8}{'F1':>7}")
    tot = np.zeros(3, int)
    for v in vids:
        m = results[v["name"]]["metrics"]
        tot += [m["tp"], m["fp"], m["fn"]]
        print(f"{v['name']:<14}{m['n_gt']:>8}{m['n_pred']:>10}{m['tp']:>4}{m['fp']:>4}{m['fn']:>4}"
              f"{m['P']:>11.2f}{m['R']:>8.2f}{m['F1']:>7.2f}")
    tp, fp, fn = tot
    P, R = tp / max(tp + fp, 1), tp / max(tp + fn, 1)
    print(f"{'ALL (pooled)':<14}{tp + fn:>8}{tp + fp:>10}{tp:>4}{fp:>4}{fn:>4}{P:>11.2f}{R:>8.2f}"
          f"{(2 * P * R / (P + R) if P + R else 0):>7.2f}")

    print("\nTIMING ERROR on matched reps (frames, absolute)")
    print(f"{'Video':<14}{'Start MAE':>10}{'Pull MAE':>10}{'End MAE':>9}{'Pull err / rep dur':>20}{'matched':>9}")
    allerr = {k: [] for k in ("start", "pull", "end")}
    for v in vids:
        m = results[v["name"]]["metrics"]
        f = lambda k: np.mean(np.abs(m["err"][k])) if m["err"][k] else np.nan
        for k in allerr:
            allerr[k] += list(np.abs(m["err"][k]))
        print(f"{v['name']:<14}{f('start'):>10.1f}{f('pull'):>10.1f}{f('end'):>9.1f}"
              f"{m['pull_err_frac']:>19.1%}{m['tp']:>9}")
    g = lambda k: np.mean(allerr[k]) if allerr[k] else np.nan
    print(f"{'ALL (pooled)':<14}{g('start'):>10.1f}{g('pull'):>10.1f}{g('end'):>9.1f}")


def plot_video(path, v, res, gt):
    d, fused, phi, cfg = res["design"], res["fused"], res["phi"], res["cfg"]
    t = np.arange(v["T"])
    fig, ax = plt.subplots(3, 1, figsize=(14, 9), sharex=True, gridspec_kw=dict(height_ratios=[2, 2, 1]))
    for a, c in (("L", "tab:blue"), ("R", "tab:orange")):
        ax[0].plot(t, d[a][:, 2], c=c, label=f"{a} elbow back (vs pelvis)")
        ax[0].plot(t, d[a][:, 5], c=c, ls="--", alpha=0.6, label=f"{a} elbow back (vs shoulder)")
    ax[0].plot(t, d["L"][:, 6], c="gray", label="trunk / shoulder-centre back")
    ax[0].set_ylabel("torso-lengths (back = +)")
    for a, c in (("L", "tab:blue"), ("R", "tab:orange")):
        ax[1].plot(t, phi[a], c=c, alpha=0.6, label=f"phase {a}")
    ax[1].plot(t, fused, c="k", lw=2, label="fused phase")
    ax[1].set_ylabel("normalised phase")
    ax[2].plot(t, np.gradient(fused) * v["fps"], c="k")
    ax[2].axhline(0, c="gray", lw=0.5)
    ax[2].set_ylabel("phase velocity (1/s)")
    ax[2].set_xlabel("frame")
    shade = {PULL: "#ffb3b3", RET: "#b3d1ff"}
    for k, a, b, lc, rc in res["segments"]:               # decoded state sequence
        if k in shade:
            ax[1].axvspan(a, b, color=shade[k], alpha=0.35, lw=0,
                          label=f"decoded {STATE_NAMES[k]}" if a == min(s[1] for s in res["segments"] if s[0] == k) else None)
    styles = {"start": ":", "pull": "-", "end": "--"}
    for A in ax:
        for k, r in enumerate(gt):
            for key, ls in styles.items():
                A.axvline(r[key], c="green", ls=ls, alpha=0.7, label=f"GT {key}" if k == 0 else None)
        for k, r in enumerate(res["reps"]):
            for key, ls in styles.items():
                A.axvline(r[key], c="red", ls=ls, alpha=0.7, label=f"pred {key}" if k == 0 else None)
    m = res["metrics"]
    ax[0].set_title(f"{v['name']}  GT={m['n_gt']} pred={m['n_pred']} TP={m['tp']} FP={m['fp']} FN={m['fn']}  (held-out)")
    ax[0].legend(ncol=5, fontsize=7, loc="upper right")
    ax[1].legend(ncol=7, fontsize=7, loc="upper right")
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)


# ----------------------------------------------------------------------------- IO
def load_annotations(path: str) -> Dict[str, List[dict]]:
    df = pd.read_csv(path)[["video", "rep", "start_frame", "pull_frame", "end_frame"]]  # 'label' dropped on purpose
    df["video"] = df["video"].astype(str).str.replace(r"\.[A-Za-z0-9]+$", "", regex=True)
    out = {}
    for name, g in df.sort_values(["video", "rep"]).groupby("video"):
        reps = [dict(start=int(r.start_frame), pull=int(r.pull_frame), end=int(r.end_frame)) for r in g.itertuples()]
        for r in reps:
            if not r["start"] < r["pull"] < r["end"]:
                raise ValueError(f"{name}: annotation not ordered start<pull<end: {r}")
        out[name] = reps
    return out


def get_fps(name, args) -> float:
    if args.videos_dir:
        try:
            import cv2
            for ext in (".mp4", ".mov", ".avi"):
                p = os.path.join(args.videos_dir, name + ext)
                if os.path.exists(p):
                    cap = cv2.VideoCapture(p)
                    fps = cap.get(cv2.CAP_PROP_FPS)
                    cap.release()
                    if fps and fps > 0:
                        return float(fps)
        except ImportError:
            pass
    return float(args.fps)


def load_model(path: str):
    d = json.load(open(path))
    return Cfg(**d["cfg"]), PhaseModel.from_json(d["model"]), Decoder.from_json(d["decoder"])


def segment_video(pose3d: np.ndarray, fps: float, model_path: str, conf: Optional[np.ndarray] = None) -> List[dict]:
    """Drop-in call for the live pipeline: MotionBERT (T,17,3) -> list of reps with per-arm timing."""
    cfg, model, dec = load_model(model_path)
    v = prepare_video("new", pose3d, fps, conf, cfg.conf_thr)
    return predict_video(v, model, dec, cfg, None)["reps"]


# ----------------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--poses_dir", required=True, help="{video}.npy = MotionBERT (T,17,3) H36M, frame-aligned with annotations")
    ap.add_argument("--annotations", required=True)
    ap.add_argument("--conf_dir", default=None, help="optional {video}.npy = (T,17) joint confidences in H36M order")
    ap.add_argument("--videos_dir", default=None, help="optional, only used to read fps")
    ap.add_argument("--fps", type=float, default=30.0)
    ap.add_argument("--groups_csv", default=None, help="optional video,athlete -> leave-one-PERSON-out")
    ap.add_argument("--iou", type=float, default=0.5)
    ap.add_argument("--out_dir", default="rep_eval")
    ap.add_argument("--save_model", default=None)
    ap.add_argument("--no_tempo_norm", action="store_true", help="ablation: absolute (seconds) duration model")
    args = ap.parse_args()
    os.makedirs(args.out_dir, exist_ok=True)
    if args.no_tempo_norm:
        GRID["tempo_norm"] = [False]

    ann = load_annotations(args.annotations)
    vids = []
    for name, gt in ann.items():
        pose = np.load(os.path.join(args.poses_dir, name + ".npy"))
        conf = None
        if args.conf_dir and os.path.exists(os.path.join(args.conf_dir, name + ".npy")):
            conf = np.load(os.path.join(args.conf_dir, name + ".npy"))
        v = prepare_video(name, pose, get_fps(name, args), conf)
        v["gt"], v["y"] = gt, phase_target(v["T"], gt)
        vids.append(v)
    vids.sort(key=lambda v: v["name"])

    if args.groups_csv:
        g = pd.read_csv(args.groups_csv)
        groups = dict(zip(g["video"].str.replace(r"\.[A-Za-z0-9]+$", "", regex=True), g["athlete"]))
        print("Validation: leave-one-PERSON-out")
    else:
        groups = {v["name"]: v["name"] for v in vids}
        print("Validation: leave-one-VIDEO-out (all videos are treated as one athlete: this does NOT measure new-athlete generalisation)")

    results = run_cv(vids, groups, GRID, args.iou)
    print_tables(vids, results)

    rows = []
    for v in vids:
        res = results[v["name"]]
        plot_video(os.path.join(args.out_dir, f"{v['name']}_diagnostic.png"), v, res, v["gt"])
        for i, r in enumerate(res["reps"], 1):
            rows.append(dict(video=v["name"], rep=i, **r))
    pd.DataFrame(rows).to_csv(os.path.join(args.out_dir, "predicted_reps_with_arm_timing.csv"), index=False)
    print(f"\nPlots + predicted reps (incl. left/right lag features) written to {args.out_dir}/")

    if args.save_model:
        cache: dict = {}
        cfg = select_config(vids, groups, GRID, args.iou, cache)
        model, dec = _fit_all(vids, cfg, groups, cache)
        json.dump(dict(cfg=asdict(cfg), model=model.to_json(), decoder=dec.to_json()), open(args.save_model, "w"), indent=1)
        print(f"Final model (fit on ALL videos, config chosen by inner CV) saved to {args.save_model}")
        print("Do not report scores from this model on these same videos; use the nested CV tables above.")


if __name__ == "__main__":
    main()