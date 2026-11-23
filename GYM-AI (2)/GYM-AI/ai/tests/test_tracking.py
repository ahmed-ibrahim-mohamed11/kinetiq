"""ai.core.tracking: production tracker must be byte-identical to the frozen research reference
(ai/research/pose_benchmark/tracking.py), and OnlineTracker (frame-by-frame, live mode) must equal
track_subject on the full sequence — checked on EVERY cached 2D candidate file in the repo."""
import glob
import os
import sys

import numpy as np
import pytest

from ai.core.tracking import OnlineTracker, track_subject
from ai.tests.conftest import REPO_ROOT

sys.path.insert(0, os.path.join(REPO_ROOT, "ai", "research", "pose_benchmark"))
import tracking as research_tracking  # noqa: E402


def _cached_inputs():
    """(name, kpts_top, boxes, wh) for every cached candidate file: 8 seated-row/external
    top-8 caches (with frame size) + the Mendeley top-3 caches (frame size from the manifest)."""
    out = []
    for p in sorted(glob.glob(os.path.join(REPO_ROOT, "ai", "outputs", "phase28", "2d_*.npz")) +
                    glob.glob(os.path.join(REPO_ROOT, "ai", "outputs", "external_stress_test", "2d_*.npz"))):
        z = np.load(p)
        out.append((os.path.basename(p), z["kt"], z["bt"], tuple(float(x) for x in z["wh"])))
    import pandas as pd
    man = pd.read_csv(os.path.join(REPO_ROOT, "ai", "data", "mendeley_manifest.csv")).set_index("video_id")
    for p in sorted(glob.glob(os.path.join(REPO_ROOT, "ai", "outputs", "mendeley_2d", "*.npz"))):
        vid = os.path.basename(p)[:-4]
        if vid not in man.index:
            continue
        z = np.load(p)
        if "kpts_top" not in z.files:
            continue
        out.append((vid, z["kpts_top"], z["boxes"],
                    (float(man.loc[vid, "width"]), float(man.loc[vid, "height"]))))
    return out


INPUTS = _cached_inputs()


def _same(a, b):
    return np.array_equal(np.isnan(a), np.isnan(b)) and np.array_equal(np.nan_to_num(a), np.nan_to_num(b))


def test_inputs_found():
    assert len(INPUTS) >= 20, len(INPUTS)


@pytest.mark.parametrize("idx", range(len(INPUTS)))
def test_production_equals_research_reference(idx):
    name, kt, bt, wh = INPUTS[idx]
    a, la = track_subject(kt, bt, wh)
    b, lb = research_tracking.track_subject(kt, bt, wh)
    assert _same(a, b), name
    assert la == lb, name


@pytest.mark.parametrize("idx", range(len(INPUTS)))
def test_online_tracker_equals_offline(idx):
    name, kt, bt, wh = INPUTS[idx]
    ref, ref_log = track_subject(kt, bt, wh)
    trk = OnlineTracker(wh)
    emitted = []
    for t in range(len(bt)):
        emitted += trk.push(kt[t], bt[t])
    emitted += trk.flush()
    got = np.stack(emitted)
    assert got.shape == ref.shape, name
    assert _same(got, ref), name
    assert trk.log == ref_log, name


def test_online_tracker_no_detections_matches_offline_quirk():
    T = 30
    kt = np.full((T, 8, 17, 3), np.nan, dtype=np.float32)
    bt = np.full((T, 8, 5), np.nan, dtype=np.float32)
    ref, ref_log = track_subject(kt, bt, (1080.0, 1920.0))
    trk = OnlineTracker((1080.0, 1920.0))
    emitted = []
    for t in range(T):
        emitted += trk.push(kt[t], bt[t])
    emitted += trk.flush()
    assert _same(np.stack(emitted), ref)
    assert trk.log == ref_log
