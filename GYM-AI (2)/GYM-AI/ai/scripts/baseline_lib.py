"""Shared helpers for the AI-core baseline manifest (build + verify)."""
from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
from importlib import metadata

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
MANIFEST_JSON = os.path.join(REPO_ROOT, "docs", "AI_CORE_BASELINE_MANIFEST.json")

TEXT_EXT = {".py", ".csv", ".json", ".yaml", ".yml", ".md", ".txt", ".ini", ".toml"}

# role, path (repo-relative), source, license — checkpoints actually used by the product path
# first, then benchmark-only artifacts (kept for reproducing Phase 2.5C/2.7/2.8 comparisons).
MODELS = [
    ("product", "2d_pose_detector", "yolo11n-pose.pt",
     "Ultralytics auto-download (ultralytics==8.4.155)", "AGPL-3.0 (Ultralytics) - commercial use needs Enterprise licence"),
    ("product", "3d_lifter", "ai/models/motionbert/FT_MB_lite_MB_ft_h36m_global_lite/best_epoch.bin",
     "HF walterzhu/MotionBERT checkpoint/pose3d/FT_MB_lite_MB_ft_h36m_global_lite/best_epoch.bin",
     "code Apache-2.0; weights trained on H36M/AMASS (dataset terms non-commercial)"),
    ("product", "lifter_config", "motionbert/configs/pose3d/MB_ft_h36m_global_lite.yaml",
     "vendored MotionBERT repo", "Apache-2.0"),
    ("product", "rep_segmentation_artifact", "ai/models/rep_segmentation/mblite_rowing_v1.json",
     "ai/scripts/export_model_artifact.py (frozen Mendeley BACK TRAIN fit)", "project-owned"),
    ("benchmark", "3d_lifter", "ai/models/motionbert/FT_MB_release_MB_ft_h36m/best_epoch.bin",
     "HF walterzhu/MotionBERT checkpoint/pose3d/FT_MB_release_MB_ft_h36m/best_epoch.bin",
     "code Apache-2.0; weights trained on H36M/AMASS (non-commercial dataset terms)"),
    ("benchmark", "3d_lifter", "ai/models/poseformerv2/27_243_45.2.bin",
     "Google Drive 14SpqPyq9yiblCzTH5CorymKCUsXapmkg (PoseFormerV2 README)", "code MIT; weights trained on H36M"),
    ("benchmark", "3d_lifter", "ai/models/motionagformer/motionagformer-b-h36m.pth.tr",
     "Google Drive 1Iii5EwsFFm9_9lKBUPfN8bV5LmfkNUMP (MotionAGFormer README)", "code Apache-2.0; weights trained on H36M"),
    ("benchmark", "3d_lifter", "ai/models/videopose3d/pretrained_h36m_detectron_coco.bin",
     "https://dl.fbaipublicfiles.com/video-pose-3d/", "CC BY-NC 4.0 - research only, excluded from product"),
    ("benchmark", "end_to_end_pose", "ai/models/mediapipe/pose_landmarker_full.task",
     "storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_full", "Apache-2.0"),
]

CONFIGS = [
    "ai/outputs/phase27/motionbert_lite/config_final.json",
    "ai/outputs/phase27/freeze_manifest.json",
    "ai/models/form_thresholds_v1_seated_row.json",
    "ai/models/form_thresholds_v1_back.json",
    "ai/core/config.py", "ai/core/decoder.py", "ai/core/features.py", "ai/core/phase_model.py",
    "ai/core/pose.py", "ai/core/form_indicators.py", "ai/core/guards.py",
    "ai/core/lifting.py", "ai/core/tracking.py", "ai/core/live.py",
    "ai/core/io.py", "ai/core/model_artifact.py", "ai/core/schema.py", "ai/core/pipeline.py",
    "ai/schemas/analysis_result.v1.json",
    "ai/research/pose_benchmark/tracking.py", "ai/research/pose_benchmark/lifters.py",
    "ai/research/phase27/train_eval.py", "ai/research/realtime/causal_prototype.py",
]

DATA = [
    ("mendeley_annotations", "ai/data/mendeley_rep_annotations_expanded.csv"),
    ("mendeley_subject_split", "ai/data/mendeley_subject_split.csv"),
    ("mendeley_duplicate_groups", "ai/outputs/mendeley_duplicate_groups.json"),
    ("internal_seated_row_gt", "ai/outputs/manual_rep_annotations.csv"),
    ("external_gt", "ai/data/external_stress_test/annotations.csv"),
]

PACKAGES = ["torch", "torchvision", "ultralytics", "numpy", "scipy", "pandas", "opencv-python",
            "timm", "einops", "PyYAML", "pydantic", "pytest", "psutil"]


def sha256_file(path: str, normalize_eol: bool | None = None) -> str:
    """Text files are hashed with CRLF->LF normalisation so the hash is identical on Windows
    (autocrlf working tree) and Linux/Docker clones. Binaries are hashed raw."""
    ext = os.path.splitext(path)[1].lower()
    norm = (ext in TEXT_EXT) if normalize_eol is None else normalize_eol
    h = hashlib.sha256()
    with open(path, "rb") as f:
        if norm:
            h.update(f.read().replace(b"\r\n", b"\n"))
        else:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
    return h.hexdigest()


def find_git() -> str | None:
    g = shutil.which("git")
    if g:
        return g
    cand = os.path.expandvars(r"%LOCALAPPDATA%\Programs\MinGit\cmd\git.exe")
    return cand if os.path.exists(cand) else None


def git_info() -> dict:
    g = find_git()
    if not g:
        return dict(commit=None, dirty=None, note="git not found")
    raw = lambda *a: subprocess.run([g, *a], cwd=REPO_ROOT, capture_output=True, text=True).stdout
    run = lambda *a: raw(*a).strip()
    # porcelain lines are "XY path": never strip them, or the first line loses its leading space
    # and its path its first character ("EADME.md")
    status = raw("status", "--porcelain").rstrip("\n")
    return dict(commit=run("rev-parse", "HEAD"), branch=run("rev-parse", "--abbrev-ref", "HEAD"),
                dirty=bool(status), dirty_files=[l[3:] for l in status.splitlines()][:50])


def environment() -> dict:
    pk = {}
    for p in PACKAGES:
        try:
            pk[p] = metadata.version(p)
        except metadata.PackageNotFoundError:
            pk[p] = None
    env = dict(python=sys.version.split()[0], platform=platform.platform(), machine=platform.machine(),
               processor=platform.processor(), packages=pk)
    try:
        import psutil
        env["cpu_logical"] = psutil.cpu_count()
        env["cpu_physical"] = psutil.cpu_count(logical=False)
        env["ram_gb"] = round(psutil.virtual_memory().total / 2**30, 1)
    except Exception:  # pragma: no cover
        pass
    try:
        import torch
        env["torch_cuda_available"] = bool(torch.cuda.is_available())
        env["torch_cuda_build"] = torch.version.cuda
        if torch.cuda.is_available():
            env["gpu"] = torch.cuda.get_device_name(0)
    except Exception:  # pragma: no cover
        pass
    smi = shutil.which("nvidia-smi")
    if smi:
        out = subprocess.run([smi, "--query-gpu=name,driver_version,memory.total", "--format=csv,noheader"],
                             capture_output=True, text=True).stdout.strip()
        env["nvidia_smi"] = out
    return env


def _round(x, nd=6):
    if isinstance(x, float):
        return round(x, nd)
    if isinstance(x, dict):
        return {k: _round(v, nd) for k, v in sorted(x.items())}
    if isinstance(x, (list, tuple)):
        return [_round(v, nd) for v in x]
    return x


def system_fingerprint(system) -> str:
    """Hash of the fitted phase model + decoder, rounded to 6 decimals (robust to BLAS ULP noise).
    Single definition lives in ai.core.model_artifact (Phase 5) — delegated so the manifest's
    fingerprint and the persisted artifact's can never diverge."""
    from ai.core.model_artifact import fingerprint
    return fingerprint(system.cfg, system.model, system.dec)
