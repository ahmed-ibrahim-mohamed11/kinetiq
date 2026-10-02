"""Torso-frame features + signal conditioning. Verbatim from rep_segmentation_v2_hsmm.py."""
from __future__ import annotations

from typing import Dict, List, Optional

import numpy as np
from scipy.ndimage import median_filter
from scipy.signal import butter, sosfiltfilt

from ai.core.config import (Cfg, FACING_IN_CAMERA, L_ELB, L_SHO, PELVIS, PULL,
                            R_ELB, R_SHO, RET, THORAX)


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
