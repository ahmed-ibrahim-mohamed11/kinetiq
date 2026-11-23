"""P5.1: the committed rep-segmentation artifact IS the frozen product system.
- fingerprint equals the locked baseline manifest's fitted_system_sha256
- loaded artifact gives reps byte-identical to the refit system on every dev suite
  (mendeley_val needs the git-ignored Mendeley caches -> skipped cleanly on a fresh clone)
- tampering / missing file / wrong schema fail loudly, never silently
"""
import json
import os

import pytest

from ai.core.features import prepare_video
from ai.core.model_artifact import DEFAULT_ARTIFACT, load_artifact
from ai.tests.conftest import REPO_ROOT

MANIFEST = os.path.join(REPO_ROOT, "docs", "AI_CORE_BASELINE_MANIFEST.json")
MENDELEY_CACHE = os.path.join(REPO_ROOT, "ai", "outputs", "pose_bench", "poses", "motionbert_lite")


def test_artifact_is_committed_and_matches_manifest_fingerprint():
    assert os.path.exists(DEFAULT_ARTIFACT)
    sys_ = load_artifact()
    man = json.load(open(MANIFEST, encoding="utf-8"))
    assert sys_.fingerprint == man["product_system"]["fitted_system_sha256"]
    assert sys_.version == "1.0.0"
    assert sys_.cfg.__dict__ == man["product_system"]["cfg"]


def test_tampered_artifact_is_rejected(tmp_path):
    d = json.load(open(DEFAULT_ARTIFACT, encoding="utf-8"))
    d["model"]["w"][0] += 0.01                      # a silent edit to one weight
    p = tmp_path / "tampered.json"
    p.write_text(json.dumps(d))
    with pytest.raises(ValueError, match="fingerprint mismatch"):
        load_artifact(str(p))


def test_missing_artifact_and_wrong_schema_fail_explicitly(tmp_path):
    with pytest.raises(FileNotFoundError, match="artifact not found"):
        load_artifact(str(tmp_path / "nope.json"))
    d = json.load(open(DEFAULT_ARTIFACT, encoding="utf-8"))
    d["schema"] = "something/else"
    p = tmp_path / "wrong.json"
    p.write_text(json.dumps(d))
    with pytest.raises(ValueError, match="unsupported artifact schema"):
        load_artifact(str(p))


@pytest.fixture(scope="module")
def systems():
    from ai.evaluation import harness as H
    if not os.path.isdir(MENDELEY_CACHE):
        pytest.skip("Mendeley pose caches not present (git-ignored) - refit comparison needs them")
    return load_artifact(), H.fit_system()


def _reps(system, v):
    return [{k: r[k] for k in ("start", "pull", "end")} for r in system.predict(v)["reps"]]


def test_artifact_equals_refit_on_internal_sr_and_external(systems):
    from ai.evaluation import harness as H
    art, refit = systems
    for loader in (H.load_internal_sr, H.load_external):
        for name, it in loader().items():
            v = prepare_video(name, it["pose"], it["fps"], conf=it["conf"])
            assert _reps(art, v) == _reps(refit, v), name


def test_artifact_equals_refit_on_mendeley_val(systems):
    from ai.evaluation import harness as H
    art, refit = systems
    for v in H.mendeley_videos().values():
        if v["split"] == "val" and v["exercise"] == "back":
            assert _reps(art, v) == _reps(refit, v), v["name"]


@pytest.mark.skipif(not os.path.exists(os.path.join(REPO_ROOT, "ai", "outputs", "phase28",
                                                    "pose_motionbert_lite_asymmetry_03.npy")),
                    reason="phase28 pose caches not present (git-ignored; rebuild_pose_caches.py)")
def test_io_segment_video_entry_point_now_works_with_the_artifact():
    """R-01: ai.core.io.segment_video/load_model were dead code (no artifact existed to load).
    The artifact uses the {cfg, model, decoder} layout load_model always expected."""
    from ai.core.io import segment_video
    from ai.evaluation import harness as H
    it = H.load_internal_sr()["asymmetry_03"]
    reps = segment_video(it["pose"], it["fps"], DEFAULT_ARTIFACT, conf=it["conf"])
    v = prepare_video("asymmetry_03", it["pose"], it["fps"], conf=it["conf"])
    assert [{k: r[k] for k in ("start", "pull", "end")} for r in reps] == _reps(load_artifact(), v)
