"""Phase 2.6: integrity of the expanded dataset + FROZEN subject split."""
import json
import os
import re

import pandas as pd
import pytest

from ai.tests.conftest import REPO_ROOT

EXP = os.path.join(REPO_ROOT, "ai", "data", "mendeley_rep_annotations_expanded.csv")
SPLIT = os.path.join(REPO_ROOT, "ai", "data", "mendeley_subject_split.csv")
DUPS = os.path.join(REPO_ROOT, "ai", "outputs", "mendeley_duplicate_groups.json")
MAN = os.path.join(REPO_ROOT, "ai", "data", "mendeley_manifest.csv")
B25 = os.path.join(REPO_ROOT, "ai", "data", "mendeley_rep_annotations.csv")


@pytest.fixture(scope="module")
def exp():
    return pd.read_csv(EXP).fillna({"annotation_notes": ""})


@pytest.fixture(scope="module")
def split():
    return pd.read_csv(SPLIT)


def test_schema_and_ordering(exp):
    for c in ("video_id", "subject_id", "exercise", "camera_view", "form_label", "rep_id",
              "start_frame", "pull_frame", "end_frame", "fps", "annotation_quality"):
        assert c in exp.columns, c
    assert (exp.start_frame < exp.pull_frame).all()
    assert (exp.pull_frame < exp.end_frame).all()
    assert (exp.fps == 15.0).all()
    assert set(exp.annotation_quality) == {"verified", "auto", "uncertain"}


def test_frames_within_video_and_ids(exp):
    man = pd.read_csv(MAN).set_index("video_id")
    for vid, g in exp.groupby("video_id"):
        n = int(man.loc[vid, "frame_count"])
        assert (g[["start_frame", "pull_frame", "end_frame"]].values < n).all(), vid
        assert (g[["start_frame"]].values >= 0).all(), vid
        ids = sorted(g.rep_id)
        assert ids == list(range(1, len(ids) + 1)), vid
        gs = g.sort_values("rep_id")
        assert (gs.start_frame.values[1:] >= gs.end_frame.values[:-1]).all(), vid


def test_preserves_25b_verified_reps(exp):
    b = pd.read_csv(B25)
    v = exp[exp.annotation_quality == "verified"]
    assert len(v) == len(b) == 443
    key = ["video_id", "rep_id", "start_frame", "pull_frame", "end_frame"]
    assert v[key].sort_values(key).reset_index(drop=True).equals(
        b[key].sort_values(key).reset_index(drop=True))


def test_split_is_complete_and_disjoint(exp, split):
    assert set(split.split) == {"train", "val", "test"}
    assert split.subject_id.is_unique
    assert set(exp.subject_id) == set(split.subject_id)
    m = exp.merge(split, on="subject_id")
    per = m.groupby("split").subject_id.nunique()
    assert per["val"] >= 4 and per["test"] >= 4
    for col in ("camera_view", "form_label", "exercise"):
        cover = m.groupby("split")[col].nunique()
        assert (cover == exp[col].nunique()).all(), f"{col} missing in a split"


def test_duplicate_linked_subjects_same_split(split):
    s = split.set_index("subject_id").split
    groups = json.load(open(DUPS))
    for g in groups:
        subs = {m.group(0) for v in g if (m := re.match(r"subject_\d+", v))}
        subs = [x for x in subs if x in s.index]
        assert len({s[x] for x in subs}) <= 1, f"duplicate-linked subjects split apart: {subs}"


def test_uncertain_notes_present(exp):
    unc = exp[exp.annotation_quality == "uncertain"]
    assert (unc.annotation_notes != "").all()
    assert set("|".join(unc.annotation_notes).split("|")) <= {"weak", "spike", "gap"}
