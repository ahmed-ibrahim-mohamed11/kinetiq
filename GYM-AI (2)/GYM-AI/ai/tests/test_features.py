import numpy as np
import pytest

from ai.core.config import Cfg
from ai.core.features import (_interp_rows, get_design, obs_features,
                              phase_target, prepare_video, state_labels)


@pytest.mark.parametrize("bad_fps", [0.0, -5.0, -1e-9, float("nan"), float("inf")])
def test_prepare_video_rejects_bad_fps_explicitly(bad_fps):
    """Phase 3 P3.7 (clear failure behaviour): fps<=0 used to propagate silently into a
    ZeroDivisionError or an obscure scipy filter error several calls later. Must fail HERE,
    with a message naming fps, not downstream."""
    P = _synthetic_pose(T=30)
    with pytest.raises(ValueError, match="fps"):
        prepare_video("x", P, bad_fps)


def _synthetic_pose(T=120, amp=0.3):
    """Rear-view seated-row-ish skeleton: pelvis at origin, thorax above, elbows oscillating backward."""
    rng = np.arange(T)
    P = np.zeros((T, 17, 3))
    P[:, 8, 1] = 0.5                      # thorax above pelvis -> torso_len 0.5
    P[:, 11, :] = [-0.2, 0.45, 0.0]      # L shoulder
    P[:, 14, :] = [+0.2, 0.45, 0.0]      # R shoulder
    z = amp * np.sin(2 * np.pi * rng / 40.0)
    P[:, 12, :] = np.stack([-0.3 * np.ones(T), 0.2 * np.ones(T), -z], 1)  # L elbow moves toward camera
    P[:, 15, :] = np.stack([+0.3 * np.ones(T), 0.2 * np.ones(T), -z], 1)  # R elbow
    return P


def test_prepare_video_shapes_and_scale_invariance():
    P = _synthetic_pose()
    v1 = prepare_video("a", P, 30.0)
    v2 = prepare_video("b", P * 3.7, 30.0)          # bigger athlete / closer camera
    assert v1["arm"]["L"].shape == (120, 6) and v1["trunk"].shape == (120, 1)
    np.testing.assert_allclose(v1["arm"]["L"], v2["arm"]["L"], atol=1e-9)
    np.testing.assert_allclose(v1["arm"]["R"], v2["arm"]["R"], atol=1e-9)


def test_prepare_video_rejects_wrong_shape():
    with pytest.raises(ValueError):
        prepare_video("bad", np.zeros((10, 16, 3)), 30.0)


def test_prepare_video_conf_mask():
    P = _synthetic_pose()
    conf = np.ones((len(P), 17))
    conf[10:20, 12] = 0.0                            # L elbow unreliable
    v = prepare_video("c", P, 30.0, conf=conf, conf_thr=0.3)
    assert not v["valid"]["L"][10:20].any()
    assert v["valid"]["R"].all()


def test_interp_rows_fills_linearly():
    X = np.array([[0.0], [99.0], [2.0]])
    valid = np.array([True, False, True])
    out = _interp_rows(X, valid)
    assert out[1, 0] == pytest.approx(1.0)


def test_phase_target_peaks_at_pull_and_zero_outside():
    y = phase_target(100, [dict(start=20, pull=40, end=60)])
    assert y[40] == pytest.approx(1.0)
    assert y[20] == pytest.approx(0.0, abs=1e-9)
    assert y[:20].max() == 0.0 and y[61:].max() == 0.0
    assert 0.4 < y[30] < 0.6                         # half-cosine midpoint


def test_state_labels():
    z = state_labels(50, [dict(start=10, pull=20, end=30)])
    assert (z[:10] == 0).all() and (z[10:20] == 1).all() and (z[20:30] == 2).all() and (z[30:] == 0).all()


def test_obs_features_velocity_scaling():
    fused = np.linspace(0, 1, 31)                    # constant slope 1 per 30 frames
    O = obs_features(fused, fps=30.0, scale=2.0)
    assert O.shape == (31, 2)
    assert O[15, 1] == pytest.approx(2.0, rel=1e-6)  # d(phase)/dt * fps * scale = (1/30)*30*2


def test_get_design_cache_hit():
    v = prepare_video("d", _synthetic_pose(), 30.0)
    cache = {}
    a = get_design(v, Cfg(), cache)
    b = get_design(v, Cfg(), cache)
    assert a is b                                    # same dict object returned from cache
