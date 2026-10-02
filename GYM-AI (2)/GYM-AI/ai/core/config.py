"""Constants + configuration for rep segmentation. Verbatim from rep_segmentation_v2_hsmm.py."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np

# ----------------------------------------------------------------------------- H36M-17 indices
PELVIS, R_HIP, L_HIP, SPINE, THORAX, NECK, HEAD = 0, 1, 4, 7, 8, 9, 10
L_SHO, L_ELB, L_WRI, R_SHO, R_ELB, R_WRI = 11, 12, 13, 14, 15, 16

# Athlete faces AWAY from a rear camera -> forward is +Z in H36M camera coordinates.
# If your MotionBERT output uses the opposite depth sign, flip this ONE vector.
FACING_IN_CAMERA = np.array([0.0, 0.0, 1.0])

REST, PULL, RET = 0, 1, 2
STATE_NAMES = ["REST", "PULL", "RET"]
PLATEAU_FRAC = 0.9          # only used for per-arm pull-time (asymmetry feature), not for counting

TIE_TOL = 0.02   # inner-CV composite score difference below which configs are treated as tied


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
