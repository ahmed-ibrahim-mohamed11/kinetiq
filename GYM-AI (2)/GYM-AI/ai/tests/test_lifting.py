"""ai.core.lifting.MotionBertLite: production MB-Lite lifter must reproduce the frozen research
references exactly on CPU (offline recipe: pose_benchmark/lifters.py::MotionBertLiteLifter.lift;
causal-window recipe: realtime/causal_prototype.py::CausalLifter.lift_window), and must reproduce
the cached Phase 2.8 poses the whole product evaluation was built on. GPU checks (skipped on a
CPU-only machine) bound the fp32/fp16 numeric drift the Phase 4 live configs introduce."""
import os
import sys

import numpy as np
import pytest
import torch

from ai.core.lifting import MBLITE_CHECKPOINT, MotionBertLite
from ai.core.tracking import track_subject
from ai.tests.conftest import REPO_ROOT, needs_mblite, needs_sr_caches

sys.path.insert(0, os.path.join(REPO_ROOT, "ai", "research", "pose_benchmark"))
sys.path.insert(0, os.path.join(REPO_ROOT, "ai", "research", "realtime"))
sys.path.insert(0, os.path.join(REPO_ROOT, "ai", "research", "phase27"))

NAME = "asymmetry_02"   # shortest internal video (250 frames) — keeps the CPU test fast


@pytest.fixture(scope="module")
def tracked():
    z = np.load(os.path.join(REPO_ROOT, "ai", "outputs", "phase28", f"2d_{NAME}.npz"))
    wh = tuple(float(x) for x in z["wh"])
    coco, _ = track_subject(z["kt"], z["bt"], wh)
    return coco, wh


@pytest.fixture(scope="module")
def cpu_lifter():
    return MotionBertLite(device="cpu")


def test_missing_checkpoint_raises_explicitly(tmp_path):
    with pytest.raises(FileNotFoundError, match="MotionBERT-Lite checkpoint not found"):
        MotionBertLite(checkpoint=str(tmp_path / "nope.bin"))


@needs_mblite   # the constructor checks the checkpoint before the device arguments
def test_fp16_on_cpu_rejected():
    with pytest.raises(ValueError, match="cuda"):
        MotionBertLite(device="cpu", half=True)


@needs_mblite
def test_offline_recipe_equals_research_reference(tracked, cpu_lifter):
    from lifters import MotionBertLiteLifter
    coco, wh = tracked
    ref_pose, ref_conf = MotionBertLiteLifter().lift(coco, wh)
    pose, conf = cpu_lifter.lift_video(coco, tta=True)
    assert np.array_equal(conf, ref_conf)
    assert np.allclose(pose, ref_pose, atol=1e-6, rtol=0), float(np.abs(pose - ref_pose).max())


@needs_mblite
@needs_sr_caches
def test_offline_recipe_reproduces_cached_phase28_poses(tracked, cpu_lifter):
    """The cached poses every Phase 2.8+ number rests on were lifted with the CPU-only torch
    build on the original machine; this machine now runs the cu130 build (Phase 4)."""
    coco, _ = tracked
    cached = np.load(os.path.join(REPO_ROOT, "ai", "outputs", "phase28", f"pose_motionbert_lite_{NAME}.npy"))
    pose, _ = cpu_lifter.lift_video(coco, tta=True)
    assert np.allclose(pose, cached, atol=1e-5, rtol=0), float(np.abs(pose - cached).max())


@needs_mblite
@pytest.mark.parametrize("tta", [True, False])
def test_causal_window_recipe_equals_research_reference(tracked, cpu_lifter, tta):
    from causal_prototype import CausalLifter
    coco, _ = tracked
    win = coco[:243]
    ref_pose, ref_conf = CausalLifter(tta).lift_window(win, [])
    pose, conf = cpu_lifter.lift_window(win, tta=tta)
    assert np.array_equal(conf, ref_conf)
    assert np.allclose(pose, ref_pose, atol=1e-6, rtol=0), float(np.abs(pose - ref_pose).max())


@needs_mblite
@pytest.mark.skipif(not torch.cuda.is_available(), reason="no CUDA device")
@pytest.mark.parametrize("half,tol", [(False, 1e-3), (True, 5e-2)])
def test_gpu_drift_is_bounded(tracked, cpu_lifter, half, tol):
    """fp32 cuda vs cpu differs only by kernel rounding; fp16 is a deliberate precision trade —
    both bounded here (pose units: crop_scale-normalised, roughly torso-length scale)."""
    coco, _ = tracked
    ref, _ = cpu_lifter.lift_window(coco[:243], tta=True)
    gpu = MotionBertLite(device="cuda", half=half)
    got, _ = gpu.lift_window(coco[:243], tta=True)
    assert np.abs(got - ref).max() < tol, float(np.abs(got - ref).max())


def test_checkpoint_path_is_the_registered_one():
    assert MBLITE_CHECKPOINT.replace("\\", "/").endswith(
        "ai/models/motionbert/FT_MB_lite_MB_ft_h36m_global_lite/best_epoch.bin")
