"""Integrity tests for the EXTERNAL_WILD_STRESS_TEST track (Instagram zero-shot evaluation).

Guards: URL/hash dedup, GT validity, domain separation (never training data), licensing
(raw videos never committed), and excluded videos staying out of the metrics.
"""
import json
import os

import pandas as pd
import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DATA = os.path.join(ROOT, "ai", "data", "external_stress_test")
OUT = os.path.join(ROOT, "ai", "outputs", "external_stress_test")

QUANT_UNITS = {"ext_02_full", "ext_03_segA", "ext_03_segB", "ext_08_segB", "ext_08_segC"}
QUANT_VIDEOS = {"ext_02", "ext_03", "ext_08"}


@pytest.fixture(scope="module")
def manifest():
    return pd.read_csv(os.path.join(DATA, "manifest.csv"))


@pytest.fixture(scope="module")
def ann():
    return pd.read_csv(os.path.join(DATA, "annotations.csv"))


def test_urls_unique_and_mapped():
    rows = json.load(open(os.path.join(DATA, "url_map.json")))
    assert len(rows) == 8
    shortcodes = [r[1] for r in rows]
    assert len(set(shortcodes)) == 8, "duplicate URL survived dedup"


def test_md5_unique_and_wellformed(manifest):
    assert manifest.md5.str.fullmatch("[0-9a-f]{32}").all()
    assert manifest.md5.nunique() == len(manifest), "duplicate video content"


def test_annotation_validity(manifest, ann):
    n_frames = manifest.set_index("video_id").n_frames
    for r in ann.itertuples():
        assert 0 <= r.start_frame < r.pull_frame < r.end_frame, r
        assert r.end_frame < n_frames[r.video_id], f"{r.video_id} rep beyond video"
        for f, t in (("start", r.start_time_s), ("pull", r.pull_time_s), ("end", r.end_time_s)):
            assert abs(getattr(r, f"{f}_frame") / r.fps - t) < 0.01, (r.video_id, f)
    assert set(ann.gt_tier) <= {"certain", "uncertain"}
    assert set(ann.annotation_confidence) <= {"high", "medium", "low"}
    unc = ann[ann.gt_tier == "uncertain"]
    assert (unc.partial_rep == 1).all(), "uncertain reps must carry an explicit flag"
    assert (ann.unusable_rep == 0).all(), "unusable reps must not be in the GT file"


def test_annotated_videos_are_quantitative(manifest, ann):
    quant = set(manifest[(manifest.classification == "QUANTITATIVE")
                         & (manifest.usable == 1)].video_id)
    assert quant == QUANT_VIDEOS
    assert set(ann.video_id) == quant


def test_domain_separation_no_train_use(ann):
    mend = pd.read_csv(os.path.join(ROOT, "ai", "data", "mendeley_manifest.csv"))
    assert not (set(ann.video_id) & set(mend.video_id))
    split = pd.read_csv(os.path.join(ROOT, "ai", "data", "mendeley_subject_split.csv"))
    assert not (set(ann.video_id) & set(split.subject_id))


def test_frozen_config_untouched():
    c = json.load(open(os.path.join(ROOT, "ai", "outputs", "phase27",
                                    "motionbert_lite", "config_final.json")))["cfg"]
    assert (float(c["smooth_hz"]), float(c["dur_weight"])) == \
        (pytest.approx(float(c["smooth_hz"])), pytest.approx(float(c["dur_weight"])))
    # pin the exact VAL-frozen values so any post-hoc tuning on external data fails loudly
    frozen = json.load(open(os.path.join(ROOT, "ai", "outputs", "phase27",
                                         "freeze_manifest.json")))
    assert frozen, "freeze manifest missing"


def test_raw_videos_git_ignored_never_committed():
    gi = open(os.path.join(ROOT, ".gitignore"), encoding="utf-8").read()
    assert "ai/data/external_stress_test/raw/" in gi


@pytest.mark.skipif(not os.path.exists(os.path.join(OUT, "metrics.csv")),
                    reason="evaluation not yet run")
def test_metrics_only_contain_quant_units(ann):
    m = pd.read_csv(os.path.join(OUT, "metrics.csv"))
    assert set(m.unit) <= QUANT_UNITS
    assert set(m.video) <= QUANT_VIDEOS
    for excl in ("ext_01", "ext_04", "ext_05", "ext_06", "ext_07"):
        assert excl not in set(m.video)
    # scored GT == certain tier only; uncertain stays don't-care
    cert = ann[ann.gt_tier == "certain"].groupby("video_id").size()
    for vid, g in m[(m.protocol == "v2") & (m.rule == "iou")].groupby("video"):
        assert g.n_gt.sum() == cert[vid], f"{vid}: uncertain reps leaked into scored GT"
