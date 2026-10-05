"""Phase 2.5C: integrity of the committed benchmark result tables."""
import os

import pandas as pd
import pytest

from ai.tests.conftest import REPO_ROOT

BENCH = os.path.join(REPO_ROOT, "ai", "outputs", "pose_bench")
PIPELINES = {"motionbert", "motionbert_lite", "motionagformer", "poseformerv2",
             "videopose3d", "mediapipe"}


@pytest.fixture(scope="module")
def summary():
    return pd.read_csv(os.path.join(BENCH, "summary.csv"))


def test_all_six_pipelines_present(summary):
    assert set(summary.pipeline) == PIPELINES


def test_back_rep_totals_consistent(summary):
    # every pipeline was scored against the same 410 back ground-truth reps
    assert (summary.iou_n_gt == 410).all()
    assert ((summary.iou_tp + summary.iou_fn) == 410).all()


def test_metric_ranges(summary):
    for c in ("iou_f1", "iou_precision", "iou_recall", "pulltol_f1"):
        assert summary[c].between(0, 1).all(), c
    assert (summary.iou_pull_mae_frames < 8).all()          # matched reps must beat the tolerance
    assert (summary.lift_fps > 0).all()


def test_tracking_log_covers_benchmark():
    tl = pd.read_csv(os.path.join(BENCH, "tracking_log.csv"))
    ann = pd.read_csv(os.path.join(REPO_ROOT, "ai", "data", "mendeley_rep_annotations.csv"))
    assert set(ann.video_id) <= set(tl.video_id)
    # Original 2.5B benchmark videos: tracker never loses one outright.
    bench = tl[tl.video_id.isin(set(ann.video_id))]
    assert (bench.nan_frac < 0.25).all()
    # Expanded (Phase 2.6/2.7) set: one known heavy-occlusion TRAIN video is tolerated and
    # documented (subject_007_back_bad_side, 42% lost; see REP_SEGMENTATION_PHASE_2_7.md).
    rest = tl[~tl.video_id.isin(set(ann.video_id))]
    bad = set(rest[rest.nan_frac >= 0.25].video_id)
    assert bad <= {"subject_007_back_bad_side"}, bad


def test_downstream_files_exist():
    for p in PIPELINES:
        f = os.path.join(BENCH, "downstream", f"{p}_videos.csv")
        assert os.path.exists(f), f
        df = pd.read_csv(f)
        assert df[df.rule == "iou"].video_id.nunique() == 31
