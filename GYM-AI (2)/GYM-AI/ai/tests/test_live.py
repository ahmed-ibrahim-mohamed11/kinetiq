"""ai.core.live: the production Mode B streamer must reproduce the validated causal prototype
(ai/research/realtime/causal_prototype.py::stream_video, REALTIME_FEASIBILITY.md) EXACTLY —
same committed rep events, video by video — when fed the same cached 2D on CPU. That is what
makes the prototype's measured accuracy (V1 0.740 / V2 0.806 / V3 0.812 GT F1) evidence for the
production module. Plus config validation and detect_every interpolation behaviour."""
import os
import sys

import numpy as np
import pytest

from ai.core.form_indicators import load_thresholds
from ai.core.lifting import MotionBertLite
from ai.core.live import LiveConfig, LiveStreamer
from ai.tests.conftest import REPO_ROOT, needs_mblite

sys.path.insert(0, os.path.join(REPO_ROOT, "ai", "research", "realtime"))
P28 = os.path.join(REPO_ROOT, "ai", "outputs", "phase28")
VIDEOS = ["correct_01", "correct_02", "correct_03", "asymmetry_01", "asymmetry_02", "asymmetry_03"]


class CachedDetector:
    """Replays the cached top-8 YOLO output; the 'frame' passed in is the native frame index."""
    def __init__(self, kt, bt):
        self.kt, self.bt = kt, bt

    def __call__(self, frame_index):
        return self.kt[frame_index], self.bt[frame_index]


@pytest.fixture(scope="module")
def system():
    """The committed artifact: decode byte-identical to causal_prototype.frozen_system()'s refit
    (P5.1), without needing the git-ignored Mendeley caches. Both sides of every comparison below
    receive this same system."""
    from ai.core.model_artifact import load_artifact
    s = load_artifact()
    return s.cfg, s.model, s.dec


@pytest.fixture(scope="module")
def lifters():
    return MotionBertLite(device="cpu")


def run_streamer(name, cfg, system_tuple, lifter):
    z = np.load(os.path.join(P28, f"2d_{name}.npz"))
    kt, bt, wh = z["kt"], z["bt"], tuple(float(x) for x in z["wh"])

    class Sys:
        pass
    s = Sys()
    s.cfg, s.model, s.dec = system_tuple
    st = LiveStreamer(cfg, CachedDetector(kt, bt), lifter, s, wh, 30.0,
                      thresholds=load_thresholds("seated_row"), name=name)
    for i in range(len(kt)):
        st.push_frame(i)
    st.finish()
    return [dict(start=e.start, pull=e.pull, end=e.end) for e in st.events], st


@pytest.mark.slow
@needs_mblite
@pytest.mark.parametrize("variant", ["V1_low_latency", "V2_quality", "V3_budget_15fps"])
def test_streamer_reproduces_causal_prototype_exactly(variant, system, lifters):
    import causal_prototype as cp
    cfg_p, model, dec = system
    thr = load_thresholds("seated_row")
    names = VIDEOS if variant == "V2_quality" else VIDEOS[:2]   # full check on the shipped preset
    for name in names:
        ref, _tm, _lat, _T = cp.stream_video(name, cp.VARIANTS[variant], cfg_p, model, dec, thr)
        got, st = run_streamer(name, LiveConfig.preset(variant), system, lifters)
        assert got == ref, (variant, name, got, ref)
        assert all(s == "OK" for s in st.chunk_status), st.chunk_status


def test_config_validation():
    with pytest.raises(ValueError, match="finalize"):
        LiveConfig(finalize="sideways")
    with pytest.raises(ValueError, match="stride"):
        LiveConfig(stride=300, window=243)
    with pytest.raises(ValueError, match="detect_every"):
        LiveConfig(detect_every=0)


def test_presets_match_the_measured_variants():
    import causal_prototype as cp
    for name, v in cp.VARIANTS.items():
        c = LiveConfig.preset(name)
        assert c.commit_lag_f == v["commit_lag_f"]
        assert c.finalize == v["finalize"]
        assert c.tta == v["tta"]
        assert 30.0 / c.proc_fps_div == v["proc_fps"]


@needs_mblite
def test_detect_every_interpolates_xy_and_confidence(system, lifters):
    """Skipped frames get linearly interpolated keypoints AND confidence (not conf 0, which is
    reserved for tracking-LOST frames)."""
    z = np.load(os.path.join(P28, "2d_asymmetry_03.npz"))
    kt, bt, wh = z["kt"], z["bt"], tuple(float(x) for x in z["wh"])

    class Sys:
        pass
    s = Sys()
    s.cfg, s.model, s.dec = system
    st = LiveStreamer(LiveConfig(detect_every=3), CachedDetector(kt, bt), lifters, s, wh, 30.0)
    for i in range(40):
        st.push_frame(i)
    st._assign(st.tracker.flush())
    win = st._window_coco(0, 36)
    a, b = st.coco[30], st.coco[33]
    for j, w in ((31, 1 / 3), (32, 2 / 3)):
        assert not st.detected[j]
        assert np.allclose(win[j], (1 - w) * a + w * b)
    assert (win[31:33, :, 2] > 0).all()
