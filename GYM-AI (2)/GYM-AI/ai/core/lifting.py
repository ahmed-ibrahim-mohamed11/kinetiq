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

from ai.core.pose import coco2h36m  # noqa: E402

# RESEARCH/BENCHMARK ONLY: the full MotionBERT checkpoint used by ai/research/extract_poses3d.py and
# pose_benchmark/lifters.py. The product lifter is `MotionBertLite` below (RISK_REGISTER R-01).
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


# ----------------------------------------------------------------------------- MotionBERT-Lite
# Production MotionBERT-Lite (the seated-row product-primary lifter since DECISION_LOG
# 2026-10-05b; RISK_REGISTER R-01 noted ai/core only had the full MotionBERT loader above).
# Numerics are VERBATIM from the frozen research references -- offline recipe from
# ai/research/pose_benchmark/lifters.py::MotionBertLiteLifter.lift, causal-window recipe from
# ai/research/realtime/causal_prototype.py::CausalLifter.lift_window -- and ai/tests/test_lifting.py
# proves equality on CPU. New here: device (cpu/cuda), optional flip-TTA, optional fp16 (cuda).
MBLITE_CHECKPOINT = os.path.join(
    REPO_ROOT, "ai", "models", "motionbert", "FT_MB_lite_MB_ft_h36m_global_lite", "best_epoch.bin")
MBLITE_CONFIG = os.path.join(MOTIONBERT_DIR, "configs", "pose3d", "MB_ft_h36m_global_lite.yaml")
H36M_LEFT = [4, 5, 6, 11, 12, 13]
H36M_RIGHT = [1, 2, 3, 14, 15, 16]
CLIP_LEN = 243


def flip_h36m(x: torch.Tensor) -> torch.Tensor:
    out = x.clone()
    out[..., 0] *= -1
    out[..., H36M_LEFT + H36M_RIGHT, :] = out[..., H36M_RIGHT + H36M_LEFT, :]
    return out


def interp_nan_2d(k: np.ndarray) -> np.ndarray:
    """(T,17,3) with NaN rows (tracking lost) -> linear interp on x,y; conf 0 on filled frames."""
    k = k.copy()
    valid = np.isfinite(k[:, 0, 0])
    if valid.all() or valid.sum() < 2:
        return np.nan_to_num(k)
    idx = np.arange(len(k))
    for j in range(17):
        for c in range(2):
            k[~valid, j, c] = np.interp(idx[~valid], idx[valid], k[valid, j, c])
    k[~valid, :, 2] = 0.0
    return k


class MotionBertLite:
    """MotionBERT-Lite 'global' checkpoint, official in-the-wild recipe (coco2h36m -> crop_scale
    [1,1] -> DSTformer -> optional flip-TTA -> pelvis zeroed; the global head is not root-relative)."""

    def __init__(self, checkpoint: str = MBLITE_CHECKPOINT, device: str = "cpu", half: bool = False):
        import yaml
        if not os.path.exists(checkpoint):
            raise FileNotFoundError(f"MotionBERT-Lite checkpoint not found: {checkpoint} "
                                    f"(re-download source: ai/models/README.md)")
        if half and device == "cpu":
            raise ValueError("fp16 lifting is only supported on cuda")
        c = yaml.safe_load(open(MBLITE_CONFIG))
        model = DSTformer(dim_in=3, dim_out=3, dim_feat=c["dim_feat"], dim_rep=c["dim_rep"],
                          depth=c["depth"], num_heads=c["num_heads"], mlp_ratio=c["mlp_ratio"],
                          norm_layer=lambda d: torch.nn.LayerNorm(d, eps=1e-6),
                          maxlen=c["maxlen"], num_joints=c["num_joints"])
        ckpt = torch.load(checkpoint, map_location="cpu", weights_only=True)
        state = {k[len("module."):] if k.startswith("module.") else k: v
                 for k, v in ckpt["model_pos"].items()}
        model.load_state_dict(state, strict=True)
        self.model = model.to(device).eval()
        if half:
            self.model = self.model.half()
        self.device, self.half = device, half
        self.no_conf = bool(c.get("no_conf", False))

    def _forward(self, norm: np.ndarray, tta: bool) -> np.ndarray:
        clip = torch.from_numpy(norm).float().unsqueeze(0).to(self.device)
        if self.half:
            clip = clip.half()
        with torch.no_grad():
            if tta:
                pred = (self.model(clip) + flip_h36m(self.model(flip_h36m(clip)))) / 2.0
            else:
                pred = self.model(clip)
        return pred.squeeze(0).float().cpu().numpy()

    def _normalise(self, coco: np.ndarray):
        h36m = coco2h36m(interp_nan_2d(coco))
        conf = h36m[:, :, 2].copy()
        norm = crop_scale(h36m, scale_range=[1, 1]).astype(np.float32)
        if self.no_conf:
            norm = norm[..., :2]
        return norm, conf

    def lift_video(self, coco: np.ndarray, tta: bool = True):
        """OFFLINE (Mode A) recipe: crop_scale over the WHOLE video, non-overlapping 243-frame
        clips. Returns (pose3d (T,17,3) pelvis-zeroed, conf (T,17) from the 2D detector)."""
        norm, conf = self._normalise(coco)
        out = [self._forward(norm[st:st + CLIP_LEN], tta) for st in range(0, len(norm), CLIP_LEN)]
        p = np.concatenate(out).astype(np.float32)
        return p - p[:, :1], conf

    def lift_window(self, coco_win: np.ndarray, tta: bool = True):
        """CAUSAL (Mode B) recipe: crop_scale over THIS window only (no future frames)."""
        norm, conf = self._normalise(coco_win)
        p = self._forward(norm, tta).astype(np.float32)
        return p - p[:, :1], conf


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
