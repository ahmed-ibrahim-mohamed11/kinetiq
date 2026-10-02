"""Explicit-duration HSMM decoder + per-arm timing. Verbatim from rep_segmentation_v2_hsmm.py."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

import numpy as np
from scipy.signal import find_peaks
from scipy.special import logsumexp
from scipy.stats import norm

from ai.core.config import Cfg, PLATEAU_FRAC, PULL, REST, RET
from ai.core.features import local_peak, obs_features, state_labels
from ai.core.phase_model import PhaseModel, predict_phase


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
        # Survival consistent with the truncated duration law: beyond dmax (= +4 sigma of the fitted
        # lognormal) a PULL/RET segment is impossible, exactly as the pmf already says. The original
        # research script used a constant -30.0 floor here; that floor let ONE absurdly long censored
        # segment (e.g. a 231-frame RET on asymmetry_01, 4.6x the longest training return) undercut the
        # per-segment duration cost of decoding the real repetitions. Phase 2 fix, 2026-10-02:
        # selected from the score decomposition of the TRAINING-fold model (diagnose_asymmetry01.py),
        # not tuned on any held-out video; validated by a full nested-LOVO rerun.
        surv = np.full(T + 1, -np.inf)
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


def predict_video(v, model: PhaseModel, dec: Decoder, cfg: Cfg, cache=None) -> dict:
    phi, fused, design = predict_phase(v, model, cfg, cache)
    scale = video_scale(fused, v["fps"], cfg, dec.ref_tempo_s) if dec.tempo_norm else 1.0
    E = emission_loglik(dec, fused, v["fps"], cfg, scale)
    tabs = build_tables(dec, v["fps"], v["T"], scale)
    segs = hsmm_decode(E, tabs, dec.p_direct, cfg.dur_weight, peak_loglik(dec, fused))
    return dict(reps=segments_to_reps(segs, dec, cfg, v["fps"], scale, fused, phi), segments=segs,
                phi=phi, fused=fused, design=design, tempo_s=scale if dec.tempo_norm else None)
