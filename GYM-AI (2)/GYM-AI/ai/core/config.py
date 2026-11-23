"""Constants + configuration for rep segmentation. Verbatim from rep_segmentation_v2_hsmm.py."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np

# ----------------------------------------------------------------------------- H36M-17 indices
PELVIS, R_HIP, L_HIP, SPINE, THORAX, NECK, HEAD = 0, 1, 4, 7, 8, 9, 10
L_SHO, L_ELB, L_WRI, R_SHO, R_ELB, R_WRI = 11, 12, 13, 14, 15, 16
# NOTE (verified 2026-10-09, ai/tests/test_augmentation.py::test_wrist_joint_is_not_used_by_the_decoder):
# L_WRI/R_WRI are defined but NEVER read by ai.core.features/phase_model/decoder/form_indicators —
# rep segmentation and form indicators use only PELVIS, THORAX, L/R SHOULDER, L/R ELBOW. The system
# is therefore trivially robust to "the wrist disappears" (Phase 3 failure-injection question): wrist
# confidence/coordinates have zero effect on any output. If this ever changes, update this note.

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

    def __post_init__(self):
        # Phase 3 (P3.7, clear failure behaviour): an invalid Cfg used to fail later with a
        # confusing downstream error (or silently produce nonsense) instead of here, at
        # construction, naming the offending field. Bounds are deliberately generous -- every
        # value any experiment in this project has ever actually used is well inside them.
        checks = [("smooth_hz", self.smooth_hz is None or self.smooth_hz > 0),
                 ("detrend_s", self.detrend_s is None or self.detrend_s > 0),
                 ("alpha", self.alpha >= 0), ("conf_thr", 0.0 <= self.conf_thr <= 1.0),
                 ("dur_weight", self.dur_weight >= 0), ("dur_sigma_floor", self.dur_sigma_floor > 0),
                 ("emis_var_floor", self.emis_var_floor > 0),
                 ("min_dur_frac", 0.0 < self.min_dur_frac <= 1.0),
                 ("direct_gap_frac", self.direct_gap_frac >= 0),
                 ("cens_q", 0.0 <= self.cens_q < 1.0), ("peak_sd_floor", self.peak_sd_floor > 0)]
        bad = [(name, getattr(self, name)) for name, ok in checks if not ok]
        if bad:
            raise ValueError(f"Cfg: invalid field(s) {bad}")


GRID = dict(
    smooth_hz=[2.0, 3.0],        # front-end bandwidth must exceed ~2x the fastest rep rate to support (design constraint,
                                 # not tunable on slow videos: a 1 Hz low-pass erases a 1 s/rep cycle)
    detrend_s=[None, 10.0],
    dur_weight=[1.0, 2.0],
    dur_sigma_floor=[0.3, 0.5],
    tempo_norm=[True],           # NOT tuned: inner CV cannot see tempo extrapolation when training tempos are similar.
)                                # Use --no_tempo_norm to run the absolute-duration ablation.
