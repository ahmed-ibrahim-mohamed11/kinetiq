"""Phase 6 data tools: metronome labelling and threshold tuning (no camera needed)."""

from __future__ import annotations

import math

from edge_ai.tools.default_profiles import DEFAULT_PROFILES
from edge_ai.tools.record_reps import _label_samples
from edge_ai.tools.tune_reps import tune


def _curl_clip(reps: int = 6, period: float = 3.0, lo: float = 75.0, hi: float = 165.0, hz: float = 10.0):
    """Synthetic biceps curls: elbow starts straight (hi), bends to lo mid-period, straight again on each beep."""
    out = []
    n = int((reps * period + 1.0) * hz)
    beeps = {round(period * (i + 1), 1) for i in range(reps)}
    for k in range(n):
        t = k / hz
        phase = (t % period) / period
        v = lo + (hi - lo) * (0.5 + 0.5 * math.cos(2 * math.pi * phase))
        out.append((t, v, 0.9, 1 if round(t, 1) in beeps else 0))
    return out


def test_label_samples_marks_nearest_sample_and_skips_beeps_after_the_end() -> None:
    times = [0.0, 0.1, 0.2, 0.3, 0.4]
    assert _label_samples(times, [0.12, 0.29, 5.0]) == [0, 1, 0, 1, 0]


def test_tune_recovers_a_band_inside_a_shallow_range_of_motion() -> None:
    # This person only bends to 75 deg: the default bottom threshold (60) never triggers.
    clips = [_curl_clip(), _curl_clip(reps=8)]
    out = tune(dict(DEFAULT_PROFILES["biceps-curl"]), clips, tol=0.75)
    assert out["baseline"]["mean_f1"] == 0.0
    assert out["tuned"]["mean_f1"] == 1.0
    assert 75 <= out["tuned"]["bottom_threshold"] < out["tuned"]["top_threshold"] <= 165

