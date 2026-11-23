"""Unit tests for Form Analysis v1 (ai/core/form_indicators.py)."""
import numpy as np
import pytest

from ai.core.features import prepare_video
from ai.core.form_indicators import (SPEC, evaluate_rep, heuristic_confidence_score,
                                     rep_features)

THR = {"lr_lag": 0.20, "lr_amplitude_ratio": 0.80, "trunk_motion": 0.15, "rom_proxy": 0.30}


def _features(**over):
    base = dict(lr_lag=0.05, lr_amplitude_ratio=0.95,
                trunk_motion=0.05, rom_proxy=0.60, _frac_valid=1.0)
    base.update(over)
    return base


def test_all_ok_for_clean_rep():
    inds = {i.indicator: i for i in evaluate_rep(_features(), THR)}
    assert all(i.severity == "ok" for i in inds.values())
    assert all(i.normalized <= 1.0 for i in inds.values())


@pytest.mark.parametrize("name,bad_value", [
    ("lr_lag", 0.5), ("trunk_motion", 0.4),                               # high-is-attention
    ("lr_amplitude_ratio", 0.5), ("rom_proxy", 0.1),                      # low-is-attention
])
def test_each_indicator_fires_in_its_direction(name, bad_value):
    inds = {i.indicator: i for i in evaluate_rep(_features(**{name: bad_value}), THR)}
    assert inds[name].severity == "attention"
    assert inds[name].normalized > 1.0
    others = [i for k, i in inds.items() if k != name]
    assert all(i.severity == "ok" for i in others)


RANK = {"low": 0, "medium": 1, "high": 2}


def test_confidence_binning_from_window_validity():
    """Lower validity must never give a STRICTLY HIGHER confidence bucket (monotonicity of the
    composite heuristic in its validity input), and the extremes must still land in the expected
    buckets — not pinned to the exact old frac_valid-only thresholds, which Phase 2.4's richer
    heuristic deliberately superseded (docs/EXPERIMENT_REGISTRY.md P2.4)."""
    hi = evaluate_rep(_features(_frac_valid=0.95), THR)[0].confidence
    med = evaluate_rep(_features(_frac_valid=0.7), THR)[0].confidence
    lo = evaluate_rep(_features(_frac_valid=0.3), THR)[0].confidence
    assert RANK[hi] >= RANK[med] >= RANK[lo]
    assert hi == "high"
    assert lo == "low"


def test_confidence_score_is_never_a_probability_label_but_a_float_in_0_1():
    inds = evaluate_rep(_features(), THR)
    for i in inds:
        assert 0.0 <= i.confidence_score <= 1.0
        assert i.confidence in ("high", "medium", "low")


def test_heuristic_confidence_score_monotonic_in_each_input():
    base = heuristic_confidence_score(0.5, 0.5, 0.1)
    assert heuristic_confidence_score(0.9, 0.5, 0.1) >= base          # more validity -> >= confidence
    assert heuristic_confidence_score(0.5, 0.9, 0.1) >= base          # more pose conf -> >= confidence
    assert heuristic_confidence_score(0.5, 0.5, 0.4) >= base          # further from threshold -> >= confidence
    assert heuristic_confidence_score(0.1, 0.1, 0.0) <= base


def test_heuristic_confidence_score_falls_back_to_frac_valid_when_pose_conf_missing():
    # conf=None -> behaves as if mean_pose_conf == frac_valid (old, validity-only behaviour)
    assert heuristic_confidence_score(0.8, None, 0.2) == pytest.approx(
        heuristic_confidence_score(0.8, 0.8, 0.2))


def test_rep_features_mean_pose_conf_none_without_conf_and_computed_with_it():
    v = _synthetic_video()
    f_no_conf = rep_features(v, None, 0, 89, 30.0)
    assert f_no_conf["_mean_pose_conf"] is None
    conf = np.full((90, 17), 0.42)
    f_with_conf = rep_features(v, None, 0, 89, 30.0, conf=conf)
    assert f_with_conf["_mean_pose_conf"] == pytest.approx(0.42)
    # passing conf changes ONLY the confidence-related field, never the indicator values
    for k in SPEC:
        assert f_no_conf[k] == pytest.approx(f_with_conf[k])


def test_messages_do_not_claim_injury_or_verdicts():
    inds = evaluate_rep(_features(lr_lag=1.0, rom_proxy=0.01), THR)
    text = " ".join(i.message.lower() for i in inds)
    for banned in ("injury", "incorrect", "wrong", "dangerous", "diagnos"):
        assert banned not in text


def _synthetic_video(T=90):
    P = np.zeros((T, 17, 3))
    P[:, 8, 1] = 0.5
    P[:, 11, :] = [-0.2, 0.45, 0.0]
    P[:, 14, :] = [+0.2, 0.45, 0.0]
    z = 0.3 * np.sin(2 * np.pi * np.arange(T) / 45.0)
    P[:, 12, 2] = -z                       # left elbow full motion
    P[:, 15, 2] = -0.5 * z                 # right elbow half amplitude
    return prepare_video("syn", P, 30.0)


def test_rep_features_capture_amplitude_asymmetry_and_lag():
    v = _synthetic_video()
    f = rep_features(v, None, 0, 89, 30.0)
    assert f["lr_amplitude_ratio"] == pytest.approx(0.5, abs=0.1)   # R amp = half of L
    assert f["rom_proxy"] > 0.1
    assert f["lr_lag"] == pytest.approx(0.0, abs=0.05)              # in-phase arms -> no lag
    assert 0 <= f["_frac_valid"] <= 1
    assert set(SPEC) <= set(f)


def _old_xcorr_lag_s(L, R, fps, max_lag_s=0.5):
    """Frozen copy of the pre-Phase-4 implementation (crashes when a window is shorter than
    max_lag) — kept only to prove the fix changes nothing where the old code worked."""
    L, R = L - L.mean(), R - R.mean()
    max_lag = int(max_lag_s * fps)
    best, best_k = -2.0, 0
    for k in range(-max_lag, max_lag + 1):
        a = L[max(0, -k):len(L) - max(0, k)]
        b = R[max(0, k):len(R) - max(0, -k)]
        if len(a) <= 8 or a.std() < 1e-9 or b.std() < 1e-9:
            continue
        c = float(np.corrcoef(a, b)[0, 1])
        if c > best:
            best, best_k = c, k
    return abs(best_k) / fps


def test_xcorr_lag_short_window_no_longer_crashes():
    """Phase 4 bug: a 12-frame rep window at 30 fps (shorter than max_lag = 15 frames) made the
    old slicing wrap around and np.corrcoef crash on mismatched lengths."""
    from ai.core.form_indicators import _xcorr_lag_s
    rng = np.random.default_rng(0)
    L, R = rng.normal(size=12), rng.normal(size=12)
    with pytest.raises(ValueError):
        _old_xcorr_lag_s(L, R, 30.0)          # the bug, reproduced
    lag = _xcorr_lag_s(L, R, 30.0)            # fixed
    assert 0.0 <= lag <= 0.5


def test_xcorr_lag_fix_is_identical_wherever_old_code_worked():
    from ai.core.form_indicators import _xcorr_lag_s
    rng = np.random.default_rng(1)
    checked = 0
    for _ in range(300):
        n = int(rng.integers(9, 80))
        fps = float(rng.choice([15.0, 25.0, 30.0]))
        L = np.cumsum(rng.normal(size=n))
        R = np.roll(L, int(rng.integers(-5, 6))) + rng.normal(scale=0.1, size=n)
        try:
            old = _old_xcorr_lag_s(L, R, fps)
        except ValueError:
            continue
        assert _xcorr_lag_s(L, R, fps) == old
        checked += 1
    assert checked > 200


def test_rep_features_detect_timing_lag():
    T = 90
    P = np.zeros((T, 17, 3))
    P[:, 8, 1] = 0.5
    P[:, 11, :] = [-0.2, 0.45, 0.0]
    P[:, 14, :] = [+0.2, 0.45, 0.0]
    t = np.arange(T)
    P[:, 12, 2] = -0.3 * np.sin(2 * np.pi * t / 45.0)
    P[:, 15, 2] = -0.3 * np.sin(2 * np.pi * (t - 9) / 45.0)        # right lags 9f = 0.3 s
    v = prepare_video("lag", P, 30.0)
    f = rep_features(v, None, 0, 89, 30.0)
    assert f["lr_lag"] == pytest.approx(0.3, abs=0.07)
