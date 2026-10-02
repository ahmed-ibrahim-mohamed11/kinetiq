import numpy as np
import pytest

from ai.core.config import Cfg
from ai.core.features import phase_target, prepare_video
from ai.core.phase_model import PhaseModel, fit_model, fuse
from ai.tests.test_features import _synthetic_pose


def test_fuse_prefers_valid_arm_and_falls_back():
    phi_L = np.array([0.9, 0.2, 0.4])
    phi_R = np.array([0.1, 0.8, 0.6])
    valid_L = np.array([True, True, False])
    valid_R = np.array([True, False, False])
    f = fuse(phi_L, phi_R, valid_L, valid_R)
    assert f[0] == 0.9          # max over both valid arms
    assert f[1] == 0.2          # R invalid -> L only
    assert f[2] == 0.6          # both invalid -> plain max fallback


def test_fit_model_learns_synthetic_phase():
    P = _synthetic_pose(T=200)
    v = prepare_video("train", P, 30.0)
    v["gt"] = [dict(start=s, pull=s + 10, end=s + 20) for s in (20, 60, 100, 140)]
    v["y"] = phase_target(v["T"], v["gt"])
    model = fit_model([v], Cfg(), cache=None)
    assert model.min_rep_s == pytest.approx(20 / 30.0)
    # prediction correlates strongly with the target on the training video (sanity, not validation)
    from ai.core.phase_model import predict_phase
    _, fused, _ = predict_phase(v, model, Cfg(), None)
    corr = np.corrcoef(fused, v["y"])[0, 1]
    assert corr > 0.6


def test_phase_model_json_roundtrip():
    m = PhaseModel(np.array([1.0, 2.0]), np.array([0.5, 0.5]), np.array([0.1, -0.2]), 0.3, 1.2)
    m2 = PhaseModel.from_json(m.to_json())
    np.testing.assert_allclose(m2.w, m.w)
    assert m2.b == m.b and m2.min_rep_s == m.min_rep_s
