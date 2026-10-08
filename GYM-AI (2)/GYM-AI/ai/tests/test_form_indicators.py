"""Unit tests for Form Analysis v1 (ai/core/form_indicators.py)."""
import numpy as np
import pytest

from ai.core.features import prepare_video
from ai.core.form_indicators import SPEC, evaluate_rep, rep_features

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


def test_confidence_binning_from_window_validity():
    hi = evaluate_rep(_features(_frac_valid=0.95), THR)[0].confidence
    med = evaluate_rep(_features(_frac_valid=0.7), THR)[0].confidence
    lo = evaluate_rep(_features(_frac_valid=0.3), THR)[0].confidence
    assert (hi, med, lo) == ("high", "medium", "low")


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
