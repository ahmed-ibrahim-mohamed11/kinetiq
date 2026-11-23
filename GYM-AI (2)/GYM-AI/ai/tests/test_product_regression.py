"""P5.6: product regression from TRACKED inputs only, so it runs on a fresh clone after
`python -m ai.scripts.download_checkpoints` (no raw video, no git-ignored cache):

  tracked 2D detector caches (ai/outputs/{phase28,external_stress_test}/2d_*.npz)
    -> ai.core.tracking.track_subject -> ai.core.lifting.MotionBertLite (CPU, flip-TTA)
    -> committed artifact -> ai.core.pipeline.analyze_pose -> AnalysisResult v1.0

Asserts known rep counts, metric RANGES (not exact floats), schema validity, status and
determinism. Exact frozen values are reproduced by verify_baseline.py and test_pipeline.py.
"""
import os

import numpy as np
import pandas as pd
import pytest

from ai.core.lifting import MBLITE_CHECKPOINT
from ai.tests.conftest import REPO_ROOT

pytestmark = [pytest.mark.slow,
              pytest.mark.skipif(not os.path.exists(MBLITE_CHECKPOINT),
                                 reason="MB-Lite checkpoint not downloaded (python -m ai.scripts.download_checkpoints)")]

OUT = os.path.join(REPO_ROOT, "ai", "outputs")
SR_COUNTS = {"correct_01": 11, "correct_02": 5, "correct_03": 5,
             "asymmetry_01": 15, "asymmetry_02": 6, "asymmetry_03": 5}
EXT_COUNTS = {"ext_02_full": 6, "ext_03_segA": 1, "ext_03_segB": 1, "ext_08_segB": 1, "ext_08_segC": 2}
# frozen: internal IoU 0.8406 / pull 0.8529; external IoU 0.6316 / pull 0.8421
RANGES = {("internal", "iou"): (0.80, 0.88), ("internal", "pull"): (0.82, 0.89),
          ("external", "iou"): (0.55, 0.72), ("external", "pull"): (0.78, 0.90)}


@pytest.fixture(scope="module")
def lifter():
    from ai.core.lifting import MotionBertLite
    return MotionBertLite(device="cpu")


@pytest.fixture(scope="module")
def comp():
    from ai.core.form_indicators import load_thresholds
    from ai.core.model_artifact import load_artifact
    from ai.core.pipeline import Components
    s = load_artifact()
    return Components(detector=None, lifter=None, system=s, thresholds=load_thresholds("seated_row"),
                      versions=dict(artifact_version=s.version, artifact_fingerprint=s.fingerprint,
                                    detector="tracked 2D cache", lifter="MotionBERT-Lite"),
                      device="cpu")


def _lift(npz, lifter):
    from ai.core.tracking import track_subject
    z = np.load(npz)
    coco, tlog = track_subject(z["kt"], z["bt"], tuple(float(x) for x in z["wh"]))
    pose, conf = lifter.lift_video(coco, tta=True)
    return pose, conf, tlog, tuple(int(x) for x in z["wh"])


def _track(pose, conf, tlog, fps, wh):
    from ai.core.pipeline import PoseTrack
    return PoseTrack(pose=pose, conf=conf, tracker_log=tlog, container_fps=fps, width=wh[0],
                     height=wh[1], source_sha256=None, timings_ms={})


def _score(pred, gtc, gtu, fps, tot):
    from ai.evaluation.protocol import evaluate_dontcare, pull_tol_frames
    for rule in ("iou", "pull"):
        m = evaluate_dontcare(pred, gtc, gtu, rule, "v2", pull_tol_frames(fps))
        tot[rule] = [a + b for a, b in zip(tot[rule], (m["tp"], m["fp"], m["fn"]))]


def _check_ranges(suite, tot):
    from ai.evaluation.protocol import prf
    for rule, (lo, hi) in ((r, RANGES[(suite, r)]) for r in ("iou", "pull")):
        f1 = prf(*tot[rule])[2]
        assert lo <= f1 <= hi, (suite, rule, f1, tot[rule])


def _reps(res):
    return [dict(start=r.start_frame, pull=r.pull_frame, end=r.end_frame) for r in res.reps]


def test_internal_known_counts_and_ranges(lifter, comp):
    from ai.core.pipeline import analyze_pose
    from ai.core.schema import AnalysisResult
    from ai.evaluation import harness as H
    gt = pd.read_csv(H.SR_GT)
    gt["video"] = gt.video.str.replace(".mp4", "", regex=False)
    tot = {"iou": [0, 0, 0], "pull": [0, 0, 0]}
    for n, want in SR_COUNTS.items():
        pose, conf, tlog, wh = _lift(os.path.join(OUT, "phase28", f"2d_{n}.npz"), lifter)
        res = analyze_pose(_track(pose, conf, tlog, 30.0, wh), comp, name=n)
        AnalysisResult.model_validate_json(res.model_dump_json())
        assert res.status == "OK", (n, res.status)
        assert res.summary.n_reps == want, (n, res.summary.n_reps)
        gtc = [dict(start=int(r.start_frame), pull=int(r.pull_frame), end=int(r.end_frame))
               for r in gt[gt.video == n].itertuples()]
        _score(_reps(res), gtc, [], 30.0, tot)
    _check_ranges("internal", tot)


def test_external_known_counts_and_ranges(lifter, comp):
    from ai.core.pipeline import analyze_pose
    from ai.evaluation import harness as H
    ann = pd.read_csv(H.EXT_GT)
    lifted = {}
    tot = {"iou": [0, 0, 0], "pull": [0, 0, 0]}
    for unit, (vid, lo, hi, fps, seg) in H.EXT_UNITS.items():
        if vid not in lifted:
            lifted[vid] = _lift(os.path.join(OUT, "external_stress_test", f"2d_{vid}.npz"), lifter)
        pose, conf, _, wh = lifted[vid]
        # a window of a longer video: the full-video tracker log does not describe the window,
        # so (like the harness) no tracker-based guard is applied
        res = analyze_pose(_track(pose[lo:hi + 1], conf[lo:hi + 1], {}, fps, wh), comp, name=unit)
        assert res.status == "OK", (unit, res.status)
        assert res.summary.n_reps == EXT_COUNTS[unit], (unit, res.summary.n_reps)
        g = ann[(ann.video_id == vid) & (ann.segment_id == seg)]
        mk = lambda df: [dict(start=int(r.start_frame) - lo, pull=int(r.pull_frame) - lo,
                              end=int(r.end_frame) - lo) for r in df.itertuples()]
        _score(_reps(res), mk(g[g.gt_tier == "certain"]), mk(g[g.gt_tier == "uncertain"]), fps, tot)
    _check_ranges("external", tot)


def test_lifting_from_tracked_2d_is_deterministic(lifter):
    a = _lift(os.path.join(OUT, "phase28", "2d_asymmetry_02.npz"), lifter)
    b = _lift(os.path.join(OUT, "phase28", "2d_asymmetry_02.npz"), lifter)
    np.testing.assert_array_equal(a[0], b[0])
    np.testing.assert_array_equal(a[1], b[1])
    assert a[2] == b[2]
