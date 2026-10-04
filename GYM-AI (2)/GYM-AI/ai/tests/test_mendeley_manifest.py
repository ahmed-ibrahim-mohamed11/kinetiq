"""Phase 2.5A: integrity checks on the committed Mendeley inventory CSVs.
These test the manifest files tracked in git, not the (git-ignored) videos."""
import os

import pandas as pd
import pytest

from ai.tests.conftest import REPO_ROOT

MANIFEST = os.path.join(REPO_ROOT, "ai", "data", "mendeley_manifest.csv")
SUBSET = os.path.join(REPO_ROOT, "ai", "data", "mendeley_annotation_subset_PROPOSED.csv")

EXERCISES = {"abs", "back", "bicep_curl", "push_up", "shoulder", "squat", "tricep"}
VIEWS = {"front", "side", "diagonal"}


@pytest.fixture(scope="module")
def man():
    return pd.read_csv(MANIFEST)


@pytest.fixture(scope="module")
def sub():
    return pd.read_csv(SUBSET)


def test_manifest_shape_and_columns(man):
    assert len(man) == 1042
    for c in ("video_id", "subject_id", "exercise", "camera_view", "form_label",
              "video_path", "fps", "frame_count", "duration_sec", "width", "height", "parse_ok"):
        assert c in man.columns, c


def test_manifest_values(man):
    assert man.subject_id.nunique() == 26
    # one known filename typo: 'bicep' (subject_003_bicep_good_side); every other exercise is canonical
    assert set(man.exercise.unique()) == EXERCISES | {"bicep"}
    assert (man.exercise == "bicep").sum() == 1
    assert set(man.camera_view.unique()) == VIEWS
    assert set(man.form_label.unique()) == {"good", "bad"}
    assert man.parse_ok.all()
    assert (man.fps == 15.0).all()                       # verified property of this dataset
    assert man.duration_sec.notna().all()
    assert man.video_id.is_unique


def test_known_incomplete_subjects(man):
    cnt = man.groupby("subject_id").size()
    assert cnt["subject_025"] == 4
    assert cnt["subject_026"] == 30
    assert (cnt.drop(["subject_025", "subject_026"]) == 42).all()


def test_subset_is_valid_selection(man, sub):
    assert len(sub) == 34
    assert sub.video_id.is_unique
    assert set(sub.video_id).issubset(set(man.video_id))
    assert set(sub.exercise.unique()) == {"back", "bicep_curl"}
    assert sub.subject_id.nunique() >= 6
    assert set(sub.form_label.unique()) == {"good", "bad"}
    assert set(sub[sub.exercise == "back"].camera_view.unique()) == VIEWS
    assert (sub.duration_sec >= 8).all()                 # short/truncated recordings excluded
    assert "subject_013_back_good_side" not in set(sub.video_id)
