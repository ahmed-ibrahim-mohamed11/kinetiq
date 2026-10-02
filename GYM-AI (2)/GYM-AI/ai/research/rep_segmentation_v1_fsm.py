#!/usr/bin/env python3
"""
Form-invariant repetition segmentation for seated row (rear camera).

Input  : MotionBERT 3D skeleton in H36M-17 order, (T, 17, 3) per video  (+ optional per-joint conf (T, 17))
Output : repetitions (start / pull / end), per-arm timing (asymmetry features), honest LOVO evaluation

Pipeline
  3D skeleton
    -> torso-frame, body-scaled features, LEFT and RIGHT arm kept separate   (extract: prepare_video)
    -> zero-phase low-pass (+ optional slow-drift removal)                    (design)
    -> small ridge regression to a normalised cycle phase in [0, 1]           (PhaseModel, 7 weights)
         (shared by both arms, so the same model sees left and right)
    -> robust arm fusion (nan-max: "either arm is pulling")                   (fuse)
    -> finite-state machine with return-to-rest relative to each rep's peak   (segment_reps)
    -> boundaries / pull centre / per-arm lag                                 (refine_reps)

The rep detector never sees the correct/asymmetry label (the CSV 'label' column is dropped on load).
Hyper-parameters are selected ONLY inside the training groups (inner leave-one-group-out);
the held-out video/athlete is never used for fitting or selection.
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
from scipy.signal import butter, sosfiltfilt

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# ----------------------------------------------------------------------------- H36M-17 indices
PELVIS, R_HIP, L_HIP, SPINE, THORAX, NECK, HEAD = 0, 1, 4, 7, 8, 9, 10
L_SHO, L_ELB, L_WRI, R_SHO, R_ELB, R_WRI = 11, 12, 13, 14, 15, 16

# Athlete faces AWAY from a rear camera -> forward is +Z in H36M camera coordinates.
# If your MotionBERT output uses the opposite depth sign, flip this ONE vector.
FACING_IN_CAMERA = np.array([0.0, 0.0, 1.0])


# ----------------------------------------------------------------------------- configuration
@dataclass(frozen=True)
class Cfg:
    # --- selected on training groups only (see GRID) ---
    smooth_hz: float = 2.0
    detrend_s: Optional[float] = None
    alpha: float = 0.03          # ridge strength (scaled by n inside fit)
    min_peak: float = 0.65       # a rep must reach this normalised phase
    edge_frac: float = 0.15      # START/END = where phase falls to edge_frac * peak
    pull_frac: float = 0.90      # PULL = centre of plateau where phase >= pull_frac * peak
    # --- structural constants of the normalised phase (NOT tuned) ---
    on_thr: float = 0.5          # "half way through the cycle"
    release_frac: float = 0.5    # return-to-rest relative to THIS rep's own peak
    trunc_return_frac: float = 0.75
    conf_thr: float = 0.3


GRID = dict(
    smooth_hz=[1.0, 2.0],
    detrend_s=[None, 10.0],
    alpha=[0.3, 0.03, 0.001],
    min_peak=[0.5, 0.65, 0.8],
    edge_frac=[0.1, 0.25],
    pull_frac=[0.8, 0.95],
)


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


# ----------------------------------------------------------------------------- state machine
def segment_reps(phi: np.ndarray, fps: float, cfg: Cfg, min_active_s: float) -> List[dict]:
    """REST -> ACTIVE (pull/peak/return) -> REST.
    A rep closes only when phase falls below release_frac * ITS OWN peak, and a new rep can start only
    after the phase has returned below on_thr. A dip inside one pull (asynchronous arms, jitter) that
    stays above half the peak therefore cannot create a second repetition, and a weak rep is still
    counted if it clears min_peak."""
    T = len(phi)
    cand, state, rearmed = [], "REST", True
    a0 = pi = 0
    pk = 0.0
    for t in range(T):
        v = phi[t]
        if state == "REST":
            if not rearmed:
                rearmed = v < cfg.on_thr
                continue
            if v >= cfg.on_thr:
                state, a0, pk, pi = "ACTIVE", t, v, t
        else:
            if v > pk:
                pk, pi = v, t
            if v <= cfg.release_frac * pk:
                if pk >= cfg.min_peak and (t - a0) / fps >= min_active_s:
                    cand.append(dict(pi=pi, pk=pk, trunc=False))
                state, rearmed = "REST", v < cfg.on_thr
    if state == "ACTIVE" and pk >= cfg.min_peak and phi[-1] <= cfg.trunc_return_frac * pk \
            and (T - a0) / fps >= min_active_s:
        cand.append(dict(pi=pi, pk=pk, trunc=True))        # video ended while the rep was returning
    return cand


def _plateau_center(sig: np.ndarray, pi: int, lo: int, hi: int, frac: float) -> int:
    thr, l, r = frac * sig[pi], pi, pi
    while l > lo and sig[l - 1] >= thr:
        l -= 1
    while r < hi and sig[r + 1] >= thr:
        r += 1
    return int(round((l + r) / 2))


def refine_reps(cand, phi_arm, fused, fps, cfg) -> List[dict]:
    T, reps = len(fused), []
    for i, c in enumerate(cand):
        pi, pk = c["pi"], c["pk"]
        lo = cand[i - 1]["pi"] + int(np.argmin(fused[cand[i - 1]["pi"]:pi + 1])) if i > 0 else 0
        hi = pi + int(np.argmin(fused[pi:cand[i + 1]["pi"] + 1])) if i + 1 < len(cand) else T - 1
        thr = cfg.edge_frac * pk
        s = pi
        while s > lo and fused[s] > thr:
            s -= 1
        e = pi
        while e < hi and fused[e] > thr:
            e += 1
        pull = _plateau_center(fused, pi, s, e, cfg.pull_frac)
        rep = dict(start=int(s), pull=int(pull), end=int(e), peak=float(pk), truncated=bool(c["trunc"]))
        st = {}
        for a in "LR":                                      # per-arm timing INSIDE the fused window
            seg = phi_arm[a][s:e + 1]
            api = s + int(np.argmax(seg))
            apk = float(phi_arm[a][api])
            ap = _plateau_center(phi_arm[a], api, s, e, cfg.pull_frac)
            k = api
            while k > s and phi_arm[a][k - 1] >= 0.5 * apk:
                k -= 1
            st[a] = (apk, ap, k)
        rep.update(peak_L=st["L"][0], peak_R=st["R"][0], pull_L=st["L"][1], pull_R=st["R"][1],
                   lag_pull_s=(st["R"][1] - st["L"][1]) / fps,        # >0: right arm peaks later
                   lag_onset_s=(st["R"][2] - st["L"][2]) / fps,
                   amp_ratio=min(st["L"][0], st["R"][0]) / (max(st["L"][0], st["R"][0]) + 1e-9))
        reps.append(rep)
    return reps


def predict_video(v, model, cfg, cache=None) -> dict:
    phi, fused, design = predict_phase(v, model, cfg, cache)
    cand = segment_reps(fused, v["fps"], cfg, 0.25 * model.min_rep_s)
    return dict(reps=refine_reps(cand, phi, fused, v["fps"], cfg), phi=phi, fused=fused, design=design)


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


# ----------------------------------------------------------------------------- selection + CV
def select_config(train, groups, grid, iou_thr, cache) -> Cfg:
    """Inner leave-one-group-out over the TRAINING groups only."""
    gnames = sorted({groups[v["name"]] for v in train})
    if len(gnames) < 2:
        print("  [warn] <2 training groups: cannot select hyper-parameters, using defaults")
        return Cfg()
    scores: Dict[Cfg, list] = {}
    for sm, dt, al in product(grid["smooth_hz"], grid["detrend_s"], grid["alpha"]):
        base = Cfg(smooth_hz=sm, detrend_s=dt, alpha=al)
        for g in gnames:
            tr = [v for v in train if groups[v["name"]] != g]
            va = [v for v in train if groups[v["name"]] == g]
            model = fit_model(tr, base, cache)
            for v in va:
                phi, fused, _ = predict_phase(v, model, base, cache)
                for mp, ef, pf in product(grid["min_peak"], grid["edge_frac"], grid["pull_frac"]):
                    cfg = replace(base, min_peak=mp, edge_frac=ef, pull_frac=pf)
                    cand = segment_reps(fused, v["fps"], cfg, 0.25 * model.min_rep_s)
                    reps = refine_reps(cand, phi, fused, v["fps"], cfg)
                    scores.setdefault(cfg, []).append(composite(evaluate_video(reps, v["gt"], iou_thr)))
    return max(scores.items(), key=lambda kv: np.mean(kv[1]))[0]


def run_cv(vids, groups, grid, iou_thr):
    cache: dict = {}
    results = {}
    for g in sorted({groups[v["name"]] for v in vids}):
        test = [v for v in vids if groups[v["name"]] == g]
        train = [v for v in vids if groups[v["name"]] != g]
        cfg = select_config(train, groups, grid, iou_thr, cache)
        model = fit_model(train, cfg, cache)
        print(f"[fold: hold out {g}] chosen on train only -> "
              f"smooth={cfg.smooth_hz}Hz detrend={cfg.detrend_s} alpha={cfg.alpha} "
              f"min_peak={cfg.min_peak} edge={cfg.edge_frac} pull_frac={cfg.pull_frac}")
        for v in test:
            res = predict_video(v, model, cfg, cache)
            res["metrics"], res["cfg"] = evaluate_video(res["reps"], v["gt"], iou_thr), cfg
            results[v["name"]] = res
    return results


# ----------------------------------------------------------------------------- reporting
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
    fig, ax = plt.subplots(2, 1, figsize=(14, 7), sharex=True)
    for a, c in (("L", "tab:blue"), ("R", "tab:orange")):
        ax[0].plot(t, d[a][:, 2], c=c, label=f"{a} elbow back (vs pelvis)")
        ax[0].plot(t, d[a][:, 5], c=c, ls="--", alpha=0.6, label=f"{a} elbow back (vs shoulder)")
    ax[0].plot(t, d["L"][:, 6], c="gray", label="trunk / shoulder-centre back")
    ax[0].set_ylabel("torso-lengths (back = +)")
    for a, c in (("L", "tab:blue"), ("R", "tab:orange")):
        ax[1].plot(t, phi[a], c=c, alpha=0.6, label=f"phase {a}")
    ax[1].plot(t, fused, c="k", lw=2, label="fused phase")
    ax[1].axhline(cfg.on_thr, c="gray", ls=":")
    ax[1].axhline(cfg.min_peak, c="gray", ls="-.")
    ax[1].set_ylabel("normalised phase")
    ax[1].set_xlabel("frame")
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
    ax[1].legend(ncol=6, fontsize=7, loc="upper right")
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
    return Cfg(**d["cfg"]), PhaseModel.from_json(d["model"])


def segment_video(pose3d: np.ndarray, fps: float, model_path: str, conf: Optional[np.ndarray] = None) -> List[dict]:
    """Drop-in call for the live pipeline: MotionBERT (T,17,3) -> list of reps with per-arm timing."""
    cfg, model = load_model(model_path)
    v = prepare_video("new", pose3d, fps, conf, cfg.conf_thr)
    return predict_video(v, model, cfg, None)["reps"]


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
    args = ap.parse_args()
    os.makedirs(args.out_dir, exist_ok=True)

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
        model = fit_model(vids, cfg, cache)
        json.dump(dict(cfg=asdict(cfg), model=model.to_json()), open(args.save_model, "w"), indent=1)
        print(f"Final model (fit on ALL videos, config chosen by inner CV) saved to {args.save_model}")
        print("Do not report scores from this model on these same videos; use the LOVO tables above.")


if __name__ == "__main__":
    main()
