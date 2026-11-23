"""P5.2: Mode A single-call pipeline, from RAW video on CPU.

- The expensive stage (decode -> YoloTopK -> track_subject -> MB-Lite) reproduces the Phase 2.8
  pose cache bit-for-bit.
- At the frozen evaluation's nominal 30.0 fps the reps equal the research path on every dev video.
- At the container's own frame rate (the production default; 29.81-29.90 fps on these phone
  videos) the pooled result is identical (IoU F1 0.8406, pull F1 0.8529); a few boundaries move by
  one frame (measured), far below the ~3-4 frame annotation precision.
- Results are schema-valid and deterministic, and bad input fails explicitly.
"""
import json
import os
import subprocess
import sys

import numpy as np
import pytest

from ai.tests.conftest import REPO_ROOT

VID_DIR = os.path.join(REPO_ROOT, "ai", "data", "seated_row")
CACHE_DIR = os.path.join(REPO_ROOT, "ai", "outputs", "phase28")
NAMES = ["correct_01", "correct_02", "correct_03", "asymmetry_01", "asymmetry_02", "asymmetry_03"]


def _path(n):
    return os.path.join(VID_DIR, "correct" if n.startswith("correct") else "asymmetry", n + ".mp4")


have_videos = all(os.path.exists(_path(n)) for n in NAMES)
have_caches = all(os.path.exists(os.path.join(CACHE_DIR, f"pose_motionbert_lite_{n}.npy")) for n in NAMES)
needs_videos = pytest.mark.skipif(not have_videos, reason="raw dev videos not present (git-ignored)")
needs_caches = pytest.mark.skipif(not have_caches, reason="Phase 2.8 pose caches not present (git-ignored)")


@pytest.fixture(scope="module")
def comp():
    from ai.core.pipeline import load_components
    return load_components(device="cpu")


@pytest.mark.slow
@needs_videos
@needs_caches
def test_mode_a_reproduces_frozen_phase28_from_raw_video(comp):
    from ai.core.pipeline import analyze_pose, extract_pose
    from ai.evaluation import harness as H
    from ai.evaluation.protocol import evaluate_dontcare, prf, pull_tol_frames
    items = H.load_internal_sr()
    ref = H.run_pose_suite(H.fit_system(), items)["predictions"]
    tot = {mode: {rule: [0, 0, 0] for rule in ("iou", "pull")} for mode in ("nominal", "container")}
    for n in NAMES:
        track = extract_pose(_path(n), comp)
        np.testing.assert_array_equal(track.pose, items[n]["pose"])
        np.testing.assert_array_equal(track.conf, items[n]["conf"])
        for mode, fps in (("nominal", 30.0), ("container", None)):
            res = analyze_pose(track, comp, name=n, fps=fps)
            assert res.status == "OK", (n, mode, res.status)
            got = [dict(start=r.start_frame, pull=r.pull_frame, end=r.end_frame) for r in res.reps]
            if mode == "nominal":
                assert got == ref[n], n
            else:
                assert len(got) == len(ref[n]), n
                shift = max((abs(a[k] - b[k]) for a, b in zip(got, ref[n]) for k in a), default=0)
                assert shift <= 2, (n, shift)
            for rule in ("iou", "pull"):
                m = evaluate_dontcare(got, items[n]["gtc"], [], rule, "v2", pull_tol_frames(res.video.fps))
                tot[mode][rule] = [a + b for a, b in zip(tot[mode][rule], (m["tp"], m["fp"], m["fn"]))]
    for mode in tot:
        assert round(prf(*tot[mode]["iou"])[2], 4) == 0.8406, mode
        assert round(prf(*tot[mode]["pull"])[2], 4) == 0.8529, mode


@needs_videos
def test_mode_a_is_deterministic_schema_valid_and_reports_fps_override(comp):
    from ai.core.pipeline import analyze_pose, extract_pose
    from ai.core.schema import AnalysisResult
    t1, t2 = extract_pose(_path("asymmetry_02"), comp), extract_pose(_path("asymmetry_02"), comp)
    np.testing.assert_array_equal(t1.pose, t2.pose)
    np.testing.assert_array_equal(t1.conf, t2.conf)
    a, b = analyze_pose(t1, comp), analyze_pose(t2, comp)
    da, db = a.model_dump(), b.model_dump()
    da.pop("timings_ms"), db.pop("timings_ms")
    assert da == db
    AnalysisResult.model_validate_json(a.model_dump_json())
    assert a.video.fps == pytest.approx(t1.container_fps)
    assert a.video.source_sha256 and len(a.video.source_sha256) == 64
    assert "asymmetry" not in a.model_dump_json()
    assert any("PROVISIONAL" in w for w in a.warnings)
    assert not any("overridden" in w for w in a.warnings)

    c = analyze_pose(t1, comp, fps=30.0)
    assert c.video.fps == 30.0
    assert any("overridden" in w for w in c.warnings)
    for bad in (0.0, -30.0, float("nan"), float("inf")):
        with pytest.raises(ValueError, match="frame rate"):
            analyze_pose(t1, comp, fps=bad)


def _cli(*args):
    return subprocess.run([sys.executable, "-m", "ai.scripts.analyze_set", *args], cwd=REPO_ROOT,
                          capture_output=True, text=True)


def test_cli_missing_video_exits_2_with_reason(tmp_path):
    p = _cli(str(tmp_path / "nope.mp4"))
    assert p.returncode == 2
    assert "video not found" in p.stderr


def test_cli_corrupt_video_exits_2_with_reason(tmp_path):
    bad = tmp_path / "corrupt.mp4"
    bad.write_bytes(os.urandom(4096))
    p = _cli(str(bad))
    assert p.returncode == 2, p.stderr
    assert "ERROR" in p.stderr


@needs_videos
def test_cli_writes_valid_result(tmp_path):
    out = tmp_path / "r.json"
    p = _cli(_path("asymmetry_02"), "--out", str(out))
    assert p.returncode == 0, p.stderr
    from ai.core.schema import AnalysisResult
    r = AnalysisResult.model_validate(json.load(open(out, encoding="utf-8")))
    assert r.status == "OK" and r.summary.n_reps >= 1
