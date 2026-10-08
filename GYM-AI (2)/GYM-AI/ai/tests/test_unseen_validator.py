"""Fixture tests for the unseen-athlete dataset validator: a synthetic minimal dataset must PASS,
and each required failure mode must flip the report to FAIL."""
import os
import sys

import pandas as pd
import pytest

from ai.tests.conftest import REPO_ROOT

sys.path.insert(0, os.path.join(REPO_ROOT, "ai", "research", "dataset_validation"))
from validate_unseen_dataset import MANIFEST_COLS, REP_COLS, validate  # noqa: E402


def build_fixture(root, n_subjects=10):
    rows, reps = [], []
    for s in range(1, n_subjects + 1):
        sid = f"ua_{s:03d}"
        for v in (1, 2, 3):
            for form, dev, tempo in (("good", "none", "normal"), ("deviated", "reduced_rom", "slow")):
                vid = f"{sid}_view{v:02d}_{form}"
                d = os.path.join(root, sid, f"view_{v:02d}")
                os.makedirs(d, exist_ok=True)
                open(os.path.join(d, vid + ".mp4"), "wb").write(vid.encode())  # unique bytes
                rows.append(dict(video_id=vid, subject_id=sid, session_id=f"{sid}_s1",
                                 view_id=f"view_{v:02d}", exercise="seated_row", form_type=form,
                                 deviation_type=dev, tempo_class=tempo, camera_fps=30.0,
                                 width=1080, height=1920, lighting_condition="bright",
                                 clothing_category="tshirt", camera_distance_m=2.5,
                                 camera_height_m=1.2, camera_angle_deg=0,
                                 recording_timestamp="2026-10-08T10:00:00",
                                 annotation_version="v1"))
                reps.append(dict(rep_id=f"{vid}_r1", video_id=vid, subject_id=sid,
                                 exercise="seated_row", form_label=form, deviation_type=dev,
                                 start_frame=10, pull_frame=25, end_frame=40,
                                 start_time_s=10 / 30, pull_time_s=25 / 30, end_time_s=40 / 30,
                                 annotation_confidence="high", notes=""))
    os.makedirs(os.path.join(root, "annotations"), exist_ok=True)
    pd.DataFrame(rows)[MANIFEST_COLS].to_csv(os.path.join(root, "manifest.csv"), index=False)
    pd.DataFrame(reps)[REP_COLS].to_csv(os.path.join(root, "annotations", "reps.csv"), index=False)


def outcome(root):
    r = validate(str(root), decode_videos=False)
    return all(ok for _, ok, _ in r.items), {n: ok for n, ok, _ in r.items}


def test_valid_fixture_passes(tmp_path):
    build_fixture(tmp_path)
    ok, checks = outcome(tmp_path)
    assert ok, checks


def test_too_few_subjects_fails(tmp_path):
    build_fixture(tmp_path, n_subjects=4)
    ok, checks = outcome(tmp_path)
    assert not ok and checks["Subjects >= 10"] is False


def test_dev_subject_leakage_fails(tmp_path):
    build_fixture(tmp_path)
    m = pd.read_csv(tmp_path / "manifest.csv")
    m.loc[m.index[:6], "subject_id"] = "subject_001"       # a Mendeley development subject
    m.to_csv(tmp_path / "manifest.csv", index=False)
    ok, checks = outcome(tmp_path)
    assert not ok
    assert checks["Dev-subject leakage"] is False or checks["Subject namespace (ua_)"] is False


def test_missing_deviated_form_fails(tmp_path):
    build_fixture(tmp_path)
    m = pd.read_csv(tmp_path / "manifest.csv")
    m.loc[m.subject_id == "ua_001", "form_type"] = "good"
    m.to_csv(tmp_path / "manifest.csv", index=False)
    ok, checks = outcome(tmp_path)
    assert not ok and checks["Deviated-form coverage"] is False


def test_duplicate_video_bytes_fail(tmp_path):
    build_fixture(tmp_path)
    src = tmp_path / "ua_001" / "view_01" / "ua_001_view01_good.mp4"
    dst = tmp_path / "ua_002" / "view_01" / "ua_002_view01_good.mp4"
    dst.write_bytes(src.read_bytes())
    ok, checks = outcome(tmp_path)
    assert not ok and checks["Duplicate videos"] is False


def test_bad_rep_ordering_fails(tmp_path):
    build_fixture(tmp_path)
    a = pd.read_csv(tmp_path / "annotations" / "reps.csv")
    a.loc[0, "pull_frame"] = 5                             # pull before start
    a.to_csv(tmp_path / "annotations" / "reps.csv", index=False)
    ok, checks = outcome(tmp_path)
    assert not ok and checks["Rep ordering start<pull<end"] is False


def test_unannotated_video_fails(tmp_path):
    build_fixture(tmp_path)
    a = pd.read_csv(tmp_path / "annotations" / "reps.csv")
    a = a.iloc[1:]
    a.to_csv(tmp_path / "annotations" / "reps.csv", index=False)
    ok, checks = outcome(tmp_path)
    assert not ok and checks["All videos annotated"] is False
