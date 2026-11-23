import csv
import os
import sys

import pytest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

# Git-ignored inputs. On a fresh clone, tests that need them SKIP with the command that provides
# them (docs/REPRODUCIBILITY.md); they must never fail or error for that reason.
_OUT = os.path.join(REPO_ROOT, "ai", "outputs")
MBLITE_CKPT = os.path.join(REPO_ROOT, "ai", "models", "motionbert", "FT_MB_lite_MB_ft_h36m_global_lite",
                           "best_epoch.bin")
SR_VIDEOS = ["correct_01", "correct_02", "correct_03", "asymmetry_01", "asymmetry_02", "asymmetry_03"]
_REBUILD = "python -m ai.scripts.rebuild_pose_caches"


def _pairs(stem_paths):
    return [p + s for p in stem_paths for s in (".npy", "_conf.npy")]


def _mendeley_stems():
    with open(os.path.join(REPO_ROOT, "ai", "data", "mendeley_rep_annotations_expanded.csv"),
              newline="", encoding="utf-8") as f:
        vids = sorted({r["video_id"] for r in csv.DictReader(f)})
    return [os.path.join(_OUT, "pose_bench", "poses", "motionbert_lite", v) for v in vids]


def _all_exist(paths):
    return all(os.path.exists(p) for p in paths)


needs_mblite = pytest.mark.skipif(
    not os.path.exists(MBLITE_CKPT),
    reason="MotionBERT-Lite checkpoint not downloaded (python -m ai.scripts.download_checkpoints)")
needs_sr_caches = pytest.mark.skipif(
    not _all_exist(_pairs([os.path.join(_OUT, "phase28", f"pose_motionbert_lite_{n}") for n in SR_VIDEOS])),
    reason=f"seated-row 3D pose caches not built ({_REBUILD})")
needs_ext_caches = pytest.mark.skipif(
    not _all_exist(_pairs([os.path.join(_OUT, "external_stress_test", f"pose_{v}")
                           for v in ("ext_02", "ext_03", "ext_08")])),
    reason=f"external 3D pose caches not built ({_REBUILD})")
needs_mendeley_caches = pytest.mark.skipif(
    not _all_exist(_pairs(_mendeley_stems())),
    reason=f"Mendeley 3D pose caches not built ({_REBUILD})")
