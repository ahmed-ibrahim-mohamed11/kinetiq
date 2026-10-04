"""Phase 2.5B: integrity tests for the committed rep-boundary benchmark annotations."""
import os
import sys

import pandas as pd
import pytest

from ai.tests.conftest import REPO_ROOT

sys.path.insert(0, os.path.join(REPO_ROOT, "ai", "research", "mendeley_annotation"))

ANN = os.path.join(REPO_ROOT, "ai", "data", "mendeley_rep_annotations.csv")
MAN = os.path.join(REPO_ROOT, "ai", "data", "mendeley_manifest.csv")
SUBSET = os.path.join(REPO_ROOT, "ai", "data", "mendeley_annotation_subset_PROPOSED.csv")

DUP_EXCLUDED = {"subject_023_back_bad_side", "subject_023_back_good_diagonal",
                "subject_023_bicep_curl_good_side"}


@pytest.fixture(scope="module")
def ann():
    return pd.read_csv(ANN)


def test_all_integrity_checks(ann):
    from validate_annotations import check
    errs = check(ann, pd.read_csv(MAN), pd.read_csv(SUBSET))
    assert errs == [], errs


def test_shape_and_exclusions(ann):
    assert ann.video_id.nunique() == 31          # 34 subset videos minus 3 identical-file duplicates
    assert len(ann) == 443
    assert not (set(ann.video_id) & DUP_EXCLUDED)
    assert set(ann.columns) == {"video_id", "subject_id", "exercise", "camera_view", "form_label",
                                "rep_id", "start_frame", "pull_frame", "end_frame", "fps"}


def test_rep_counts_are_not_fixed(ann):
    """Guards the no-fixed-rep-count rule: real counts vary per video."""
    counts = ann.groupby("video_id").size()
    assert counts.min() >= 8 and counts.max() >= 15 and counts.nunique() > 3


def test_durations_sane_in_frames_and_seconds(ann):
    dur_s = (ann.end_frame - ann.start_frame) / ann.fps
    assert (dur_s > 0.4).all() and (dur_s < 8).all()
    assert (ann.fps == 15.0).all()
