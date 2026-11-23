"""Phase 3 (P3.5 abstention, P3.6 exercise guard) — explicit, never-silent failure handling.

ADDITIVE ONLY: `predict_video` (ai.core.decoder) and every existing caller of it are UNTOUCHED —
this module wraps it in `safe_predict_video`, a new function, so nothing that already depends on
`predict_video`'s exact behaviour (100+ test/research call sites) can regress. No black-box
classifier anywhere here, consistent with the form-indicator design: every check is a named,
interpretable threshold on an already-computed, already-tested signal (keypoint validity,
tracker log counters, or the decoder's own `peak_z` plausibility score — ai/core/decoder.py's
`segments_to_reps`), never a new learned model.

Abstention statuses (never a silent/garbage result):
  OK                      — normal decode, trust the returned reps
  NO_PERSON               — no finite/confident keypoints anywhere in the video
  VIDEO_TOO_SHORT         — fewer frames than the smallest plausible rep (PULL+RET minimum,
                            ai.core.decoder.Decoder.dur_min, converted via the SAME formula
                            ai.core.decoder._min_len_frames already uses) — checked BOTH with a
                            frame-count floor (pre-decode, cheap) and the precise trained minimum
                            (post-phase-model, exact)
  INSUFFICIENT_CONFIDENCE — mean keypoint validity across the WHOLE video below threshold
  LOST_TRACK              — tracker log (if supplied) shows track lost more than `max_lost_frac`
                            of frames
  AMBIGUOUS_SUBJECT       — tracker log shows the tracked choice disagreed with the largest-box
                            candidate more than `max_disagree_frac` of frames (sustained
                            ambiguity among similarly-prominent people, not one-off jumps)
  UNSUPPORTED_EXERCISE    — reps WERE decoded, but their `peak_z` (how far the decoded peak
                            height sits from the trained exercise's learned peak-height law —
                            already computed by `segments_to_reps`, not a new metric) is beyond
                            `peak_z_thr` for enough of them that this doesn't look like the
                            exercise the decoder was trained on. Reps are still RETURNED (never
                            hidden) but the status tells the caller not to trust them as
                            confident detections — this is "a guard", not a reason to go dark.

Thresholds are deliberately conservative relative to every real case already on record (worst
documented Mendeley lost-frame case 42%, DECISION_LOG/a41d759; worst external tracked-frame NaN
16%, EXTERNAL_REAL_WORLD_STRESS_TEST.md) — see test_guards.py for the explicit false-abstention
check against every real dev/external video this project has.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from ai.core.decoder import (Decoder, _min_len_frames, build_tables, emission_loglik,
                             hsmm_decode, peak_loglik, segments_to_reps, video_scale)
from ai.core.phase_model import PhaseModel, predict_phase

MIN_FRAMES_ABSOLUTE = 10          # cheap pre-decode sanity floor (far below any plausible rep)


@dataclass
class GuardResult:
    status: str                  # "OK" or one of the named abstention statuses above
    reason: str                  # human-readable, one sentence, no injury/diagnosis language
    diagnostics: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status == "OK"


def check_pre_decode(v: dict, tracker_log: Optional[dict] = None,
                     min_frac_valid: float = 0.5, max_lost_frac: float = 0.5,
                     max_disagree_frac: float = 0.3) -> GuardResult:
    T = v["T"]
    if T < MIN_FRAMES_ABSOLUTE:
        return GuardResult("VIDEO_TOO_SHORT", "Video is too short to analyze.", dict(T=T))
    frac_valid = float(np.mean([v["valid"]["L"], v["valid"]["R"]]))
    if frac_valid <= 0.0:
        return GuardResult("NO_PERSON", "No person was reliably detected in this video.",
                           dict(frac_valid=frac_valid))
    if frac_valid < min_frac_valid:
        return GuardResult("INSUFFICIENT_CONFIDENCE",
                           "Too few frames have a confidently-tracked person for a reliable result.",
                           dict(frac_valid=round(frac_valid, 4)))
    if tracker_log is not None and T:
        lost_frac = tracker_log.get("lost", 0) / T
        if lost_frac > max_lost_frac:
            return GuardResult("LOST_TRACK", "Tracking was lost for too much of this video.",
                               dict(lost_frac=round(lost_frac, 4)))
        disagree_frac = tracker_log.get("disagree", 0) / T
        if disagree_frac > max_disagree_frac:
            return GuardResult("AMBIGUOUS_SUBJECT",
                               "Could not reliably tell which person in frame is the subject.",
                               dict(disagree_frac=round(disagree_frac, 4)))
    return GuardResult("OK", "")


def check_video_length_precise(T: int, dec: Decoder, cfg, fps: float, scale: float) -> GuardResult:
    """Precise version of VIDEO_TOO_SHORT using the DECODER'S OWN trained minimum PULL+RET
    duration (ai.core.decoder._min_len_frames, the same formula build_tables/segments_to_reps
    already use) instead of the cheap absolute floor — run once the phase model/scale exist."""
    min_total = _min_len_frames(dec, 0, cfg, fps, scale) + _min_len_frames(dec, 1, cfg, fps, scale)
    if T < min_total:
        return GuardResult("VIDEO_TOO_SHORT",
                           "Video is shorter than the shortest complete repetition this model "
                           "has ever seen for this exercise.",
                           dict(T=T, min_plausible_frames=round(min_total, 1)))
    return GuardResult("OK", "")


def check_exercise_plausibility(reps: list, peak_z_thr: float = 4.0,
                                frac_implausible_thr: float = 0.5) -> GuardResult:
    """UNSUPPORTED_EXERCISE: most decoded reps' peak height is implausible under the TRAINED
    exercise's own learned peak-height law (`rep["peak_z"]`, already computed by
    `segments_to_reps` — not a new metric). A rep list that is empty is NOT flagged here (zero
    reps decoded is a legitimate real outcome, e.g. someone standing still — that is a counting
    result, not an exercise-mismatch signal)."""
    if not reps:
        return GuardResult("OK", "")
    implausible = [r for r in reps if abs(r.get("peak_z", 0.0)) > peak_z_thr]
    frac = len(implausible) / len(reps)
    if frac > frac_implausible_thr:
        return GuardResult("UNSUPPORTED_EXERCISE",
                           "These movements do not look like the exercise this model was "
                           "trained on; detected repetitions are LOW CONFIDENCE.",
                           dict(frac_implausible=round(frac, 3), n_reps=len(reps)))
    return GuardResult("OK", "")


def safe_predict_video(v: dict, model: PhaseModel, dec: Decoder, cfg, cache=None,
                       tracker_log: Optional[dict] = None, peak_z_thr: float = 4.0) -> dict:
    """Wraps ai.core.decoder.predict_video with explicit abstention. Returns a dict with the
    SAME keys predict_video returns (reps, segments, phi, fused, design, tempo_s) PLUS `status`
    (one of the named statuses) and `guard` (the GuardResult that decided it). On any non-OK
    PRE-decode status, `reps` is [] and the model is never even run — explicit, not silent."""
    g = check_pre_decode(v, tracker_log)
    if not g.ok:
        return dict(status=g.status, guard=g, reps=[], segments=[], phi=None, fused=None,
                   design=None, tempo_s=None)

    phi, fused, design = predict_phase(v, model, cfg, cache)
    scale = video_scale(fused, v["fps"], cfg, dec.ref_tempo_s) if dec.tempo_norm else 1.0
    g2 = check_video_length_precise(v["T"], dec, cfg, v["fps"], scale)
    if not g2.ok:
        return dict(status=g2.status, guard=g2, reps=[], segments=[], phi=phi, fused=fused,
                   design=design, tempo_s=scale if dec.tempo_norm else None)

    E = emission_loglik(dec, fused, v["fps"], cfg, scale)
    tabs = build_tables(dec, v["fps"], v["T"], scale)
    segs = hsmm_decode(E, tabs, dec.p_direct, cfg.dur_weight, peak_loglik(dec, fused))
    reps = segments_to_reps(segs, dec, cfg, v["fps"], scale, fused, phi)

    g3 = check_exercise_plausibility(reps, peak_z_thr)
    status = g3.status
    result = dict(status=status, guard=g3, reps=reps, segments=segs, phi=phi, fused=fused,
                 design=design, tempo_s=scale if dec.tempo_norm else None)
    return result
