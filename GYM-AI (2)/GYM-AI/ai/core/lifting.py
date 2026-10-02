"""MotionBERT 2D -> 3D lifting (official infer_wild.py recipe).
Moved verbatim from ai/research/extract_poses3d.py (Phase 1); that script now imports from here.
"""
from __future__ import annotations

import os
import sys

import numpy as np
import torch

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
MOTIONBERT_DIR = os.path.join(REPO_ROOT, "motionbert")
if MOTIONBERT_DIR not in sys.path:
    sys.path.insert(0, MOTIONBERT_DIR)

from lib.model.DSTformer import DSTformer  # noqa: E402  (motionbert/lib)
from lib.utils.utils_data import crop_scale, flip_data  # noqa: E402, F401  (crop_scale re-exported)

DEFAULT_CHECKPOINT = os.path.join(
    REPO_ROOT, "ai", "models", "motionbert", "FT_MB_release_MB_ft_h36m", "best_epoch.bin")


def load_motionbert(checkpoint_path: str = DEFAULT_CHECKPOINT, device: str = "cpu") -> torch.nn.Module:
    # Architecture per configs/pose3d/MB_ft_h36m.yaml (the FT_MB_release_MB_ft_h36m checkpoint).
    model = DSTformer(dim_in=3, dim_out=3, dim_feat=512, dim_rep=512, depth=5, num_heads=8,
                      mlp_ratio=2, norm_layer=lambda dim: torch.nn.LayerNorm(dim, eps=1e-6),
                      maxlen=243, num_joints=17)
    ckpt = torch.load(checkpoint_path, map_location="cpu", weights_only=True)
    state = {k[len("module."):] if k.startswith("module.") else k: v for k, v in ckpt["model_pos"].items()}
    model.load_state_dict(state, strict=True)
    return model.to(device).eval()


def lift_3d(model: torch.nn.Module, k2d_h36m: np.ndarray, clip_len: int = 243, device: str = "cpu") -> np.ndarray:
    """(T,17,3) normalised H36M 2D+conf -> (T,17,3) 3D. Official infer_wild.py recipe:
    non-overlapping clips, flip TTA, root-relative (pelvis zeroed every frame)."""
    out = []
    with torch.no_grad():
        for st in range(0, len(k2d_h36m), clip_len):
            clip = torch.from_numpy(k2d_h36m[st:st + clip_len]).float().unsqueeze(0).to(device)
            pred = (model(clip) + flip_data(model(flip_data(clip)))) / 2.0
            pred[:, :, 0, :] = 0  # rootrel
            out.append(pred.squeeze(0).cpu().numpy())
    return np.concatenate(out).astype(np.float32)
