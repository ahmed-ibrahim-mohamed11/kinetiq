"""Form Analysis v1 — interpretable per-rep coach-attention indicators (no classifier).

Confidence (Phase 2.4, 2026-10-09): a HEURISTIC SCORE combining keypoint validity, raw pose
confidence (when available) and how far the indicator sits from its decision threshold —
NEVER a calibrated probability. True calibration (score -> P(correct)) needs per-rep deviation
ground truth that does not exist yet (UNSEEN_ATHLETE_DATASET_SPEC.md §5, pre-registered,
BLOCKED on the fresh Domain-C dataset); fabricating a proxy label to fit one would hide that gap
instead of stating it, so this file does not do that. `confidence` stays the existing 3-bucket
label every consumer already reads; `confidence_score` (new) exposes the underlying [0,1]
heuristic for anyone who wants finer granularity, documented as non-probabilistic everywhere
it's surfaced.

Five indicators, each computed INSIDE a decoded (or ground-truth) rep window on the torso-frame
features from ai.core.features.prepare_video:

  lr_lag             |left/right time offset| from the cross-correlation of the raw per-arm
                     elbow-backward signals inside the window (s). Chosen over phase-model peak
                     lag by a controlled comparison (asymmetry hits 0/15 -> 3/15 at equal 1/15
                     false rate; phase-model per-arm peaks are too smoothed).  high = attention
  lr_amplitude_ratio min/max of per-arm elbow-backward amplitude (p95-p5)            LOW  = attention
                     [NOT VALIDATED yet - kept, labelled provisional]
  trunk_motion       p95-p5 range of the trunk backward signal in the window
                     (torso-length units; proxy for trunk swing/retraction cheat)    high = attention
  rom_proxy          mean L/R range of elbow-backward travel in the window
                     (torso-length units; proxy for range of motion)                 LOW  = attention

Thresholds are EXERCISE-SCOPED (controlled experiment: Mendeley-back thresholds do not transfer
to seated row - good-form distributions differ by 3-8x). Files ai/models/form_thresholds_v1_<exercise>.json,
p95 for high-is-attention / p05 for low-is-attention over GOOD-form calibration reps:
  back: Mendeley TRAIN-good (never VAL/TEST).  seated_row: PROVISIONAL (3 development correct
  videos, 15 reps) - demo-grade until the fresh dataset provides proper calibration data.
lr_onset_lag was REMOVED in v1: the onset measurement degenerates (p95=0.0 on seated row).
Severity is binary {ok, attention} in v1 - no "error type" claims, no correct/incorrect verdicts,
no injury language. Confidence reflects keypoint validity (+ pose confidence, + threshold
margin when available) inside the window — see the heuristic-vs-probability note above.

Limitations (state in every consumer): 3D is estimated monocular (proxy-stable, not metrically
validated); thresholds are provisional single-dataset percentiles; Mendeley 'bad' labels are
video-level and unspecific, so separability evidence is directional only.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass

import numpy as np

def thresholds_path(exercise: str) -> str:
    return os.path.join(os.path.dirname(__file__), "..", "models",
                        f"form_thresholds_v1_{exercise}.json")

SPEC = {   # direction: +1 = high value raises attention, -1 = low value raises attention
    "lr_lag":             dict(direction=+1, unit="s",
                               message="Left and right sides move noticeably out of sync."),
    "lr_amplitude_ratio": dict(direction=-1, unit="ratio",
                               message="Noticeable asymmetry between left and right pull amplitude."),
    "trunk_motion":       dict(direction=+1, unit="torso-lengths",
                               message="Large trunk movement during the repetition."),
    "rom_proxy":          dict(direction=-1, unit="torso-lengths",
                               message="Reduced range of motion compared to typical form."),
}


@dataclass
class Indicator:
    indicator: str
    value: float
    normalized: float          # value/threshold (direction-aware; >1.0 => attention)
    threshold: float
    severity: str              # "ok" | "attention"
    message: str
    confidence: str            # "high" | "medium" | "low" (bucketed from confidence_score)
    confidence_score: float = 1.0  # [0,1] HEURISTIC, NOT a calibrated probability (module docstring)

    def to_dict(self):
        return asdict(self)


def _xcorr_lag_s(L: np.ndarray, R: np.ndarray, fps: float, max_lag_s: float = 0.5) -> float:
    L, R = L - L.mean(), R - R.mean()
    max_lag = int(max_lag_s * fps)
    best, best_k = -2.0, 0
    for k in range(-max_lag, max_lag + 1):
        # Overlap length for lag k. Phase 4 bug fix (found by the live benchmark): the old slices
        # L[max(0,-k):len(L)-max(0,k)] / R[max(0,k):len(R)-max(0,-k)] wrap around via a NEGATIVE
        # index once k > len(L) (a rep window shorter than max_lag frames, e.g. 12 frames at 30 fps),
        # giving a non-empty `a` and an empty `b` -> np.corrcoef crashed. Explicit-length slices
        # are identical to the old ones for every lag the old code could evaluate (|k| < len-8),
        # so results are unchanged wherever it did not crash.
        n = len(L) - abs(k)
        if n <= 8:
            continue
        a = L[max(0, -k):max(0, -k) + n]
        b = R[max(0, k):max(0, k) + n]
        if a.std() < 1e-9 or b.std() < 1e-9:
            continue
        c = float(np.corrcoef(a, b)[0, 1])
        if c > best:
            best, best_k = c, k
    return abs(best_k) / fps


_CONF_JOINTS = [11, 12, 14, 15, 0, 8]  # L_SHO, L_ELB, R_SHO, R_ELB, PELVIS, THORAX (ai.core.config)


def rep_features(v: dict, phi: dict, start: int, end: int, fps: float,
                 lag_pull_s: float = 0.0, lag_onset_s: float = 0.0,
                 amp_ratio: float | None = None, conf: "np.ndarray | None" = None) -> dict:
    """Window features from the torso-frame signals. lag is measured directly on the smoothed
    per-arm elbow-backward signals (cross-correlation); the decoder's phase-peak lag/ratio args
    are accepted for backward compatibility but only amp_ratio is used as a fallback.

    `conf` (optional, (T,17) raw per-joint confidence from the lifter, NOT the thresholded
    `v["valid"]` mask): when given, its mean over the same joints `coords()` actually reads
    (`_CONF_JOINTS`) feeds the confidence heuristic in `evaluate_rep` as a second signal beyond
    keypoint validity. Omit it and the heuristic falls back to validity alone (unchanged
    behaviour) — fully backward compatible with every existing caller."""
    from ai.core.features import lowpass
    s, e = int(start), int(min(end, v["T"] - 1))
    trunk = v["trunk"][s:e + 1, 0]
    sig, roms = {}, []
    for a in "LR":
        raw = lowpass(v["arm"][a][:, 2:3], fps, 3.0)[:, 0]
        sig[a] = raw[s:e + 1]
        roms.append(float(np.percentile(sig[a], 95) - np.percentile(sig[a], 5)))
    amp = min(roms) / max(max(roms), 1e-9)
    frac_valid = float(np.minimum(v["valid"]["L"][s:e + 1], v["valid"]["R"][s:e + 1]).mean())
    mean_pose_conf = float(np.mean(conf[s:e + 1][:, _CONF_JOINTS])) if conf is not None else None
    return dict(lr_lag=_xcorr_lag_s(sig["L"], sig["R"], fps),
                lr_amplitude_ratio=float(amp),
                trunk_motion=float(np.percentile(trunk, 95) - np.percentile(trunk, 5)),
                rom_proxy=float(np.mean(roms)), _frac_valid=frac_valid,
                _mean_pose_conf=mean_pose_conf)


def load_thresholds(exercise: str = "back", path: str | None = None) -> dict:
    return json.load(open(path or thresholds_path(exercise)))["thresholds"]


MARGIN_SATURATE = 0.5    # |normalized-1.0| past which "distance from threshold" maxes out
CONF_WEIGHTS = (0.5, 0.3, 0.2)  # (validity, pose-confidence-or-fallback, threshold-margin)


def heuristic_confidence_score(frac_valid: float, mean_pose_conf: float | None,
                               margin: float) -> float:
    """[0,1] composite confidence HEURISTIC (not a probability — see module docstring):
    monotonically non-decreasing in every input, by construction (weighted average of three
    already-monotonic [0,1] terms). `mean_pose_conf` falls back to `frac_valid` when the caller
    didn't supply raw confidence (keeps old behaviour when only validity is known)."""
    m = mean_pose_conf if mean_pose_conf is not None else frac_valid
    margin_score = min(1.0, margin / MARGIN_SATURATE)
    wv, wm, wg = CONF_WEIGHTS
    return wv * frac_valid + wm * m + wg * margin_score


def evaluate_rep(features: dict, thresholds: dict) -> list[Indicator]:
    frac_valid = features.get("_frac_valid", 1.0)
    mean_pose_conf = features.get("_mean_pose_conf")
    out = []
    for name, spec in SPEC.items():
        val = float(features[name])
        thr = float(thresholds[name])
        if spec["direction"] > 0:
            normalized = val / max(thr, 1e-9)
        else:
            normalized = (thr / max(val, 1e-9))
        severity = "attention" if normalized > 1.0 else "ok"
        score = heuristic_confidence_score(frac_valid, mean_pose_conf, abs(normalized - 1.0))
        conf_label = "high" if score >= 0.75 else ("medium" if score >= 0.5 else "low")
        out.append(Indicator(name, round(val, 4), round(float(normalized), 3), thr, severity,
                             spec["message"] if severity == "attention" else "Within typical range.",
                             conf_label, round(score, 3)))
    return out
