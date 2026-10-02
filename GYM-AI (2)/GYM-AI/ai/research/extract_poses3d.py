#!/usr/bin/env python3
"""
extract_poses3d.py — rebuild the missing MotionBERT stage (Path B, Phase 0 / blocker B1).

video (.mp4)
  -> YOLO11n-Pose            COCO-17 2D keypoints + per-joint confidence (highest-confidence person per frame)
  -> coco2h36m()             COCO-17 -> H36M-17 (midpoints for pelvis/thorax/spine/head; conf averaged)
  -> crop_scale()            normalise to [-1,1], official MotionBERT in-the-wild default (whole video,
                             scale_range=[1,1], motionbert/lib/utils/utils_data.py)
  -> DSTformer               checkpoint FT_MB_release_MB_ft_h36m (configs/pose3d/MB_ft_h36m.yaml:
                             maxlen 243, dim_feat 512, depth 5, heads 8, mlp_ratio 2, dim_rep 512),
                             243-frame clips, flip test-time augmentation, root-relative pelvis
  -> {video}.npy  (T,17,3)   estimated monocular 3D, H36M-17 order, frame-aligned with the video
  -> conf {video}.npy (T,17) 2D keypoint confidences mapped to H36M order (for --conf_dir of v2)

Depth convention: MotionBERT outputs camera-frame coordinates with +Z away from the camera. The athlete
faces away from the rear camera, so the facing direction is +Z — matching FACING_IN_CAMERA in
rep_segmentation_v2_hsmm.py.

Frames where YOLO finds no person get linearly interpolated 2D keypoints and confidence 0
(so crop_scale ignores them and downstream confidence masking can drop them).

Determinism: no augmentation at inference; crop_scale's scale_range=[1,1] makes the uniform draw constant.

Usage (from repo root):
  .venv\Scripts\python.exe ai\research\extract_poses3d.py
  # or per flag: --videos ai/data/seated_row --out_dir ai/data/poses3d --conf_out_dir ai/data/poses3d_conf
"""
from __future__ import annotations

import argparse
import os
import sys
import time

import numpy as np

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
MOTIONBERT_DIR = os.path.join(REPO_ROOT, "motionbert")
sys.path.insert(0, MOTIONBERT_DIR)

import torch  # noqa: E402

from lib.model.DSTformer import DSTformer  # noqa: E402  (motionbert/lib)
from lib.utils.utils_data import crop_scale, flip_data  # noqa: E402

# COCO-17: 0 nose 1 Leye 2 Reye 3 Lear 4 Rear 5 Lsho 6 Rsho 7 Lelb 8 Relb
#          9 Lwri 10 Rwri 11 Lhip 12 Rhip 13 Lknee 14 Rknee 15 Lank 16 Rank
# H36M-17: 0 pelvis 1 Rhip 2 Rknee 3 Rank 4 Lhip 5 Lknee 6 Lank 7 spine 8 thorax
#          9 nose 10 head 11 Lsho 12 Lelb 13 Lwri 14 Rsho 15 Relb 16 Rwri


def coco2h36m(x: np.ndarray) -> np.ndarray:
    """(T,17,3) COCO [x,y,conf] -> (T,17,3) H36M [x,y,conf]; midpoint joints average coords AND conf."""
    y = np.zeros_like(x)
    y[:, 0] = (x[:, 11] + x[:, 12]) * 0.5          # pelvis = mid-hip
    y[:, 1] = x[:, 12]                              # R hip
    y[:, 2] = x[:, 14]                              # R knee
    y[:, 3] = x[:, 16]                              # R ankle
    y[:, 4] = x[:, 11]                              # L hip
    y[:, 5] = x[:, 13]                              # L knee
    y[:, 6] = x[:, 15]                              # L ankle
    y[:, 8] = (x[:, 5] + x[:, 6]) * 0.5             # thorax = mid-shoulder
    y[:, 7] = (y[:, 0] + y[:, 8]) * 0.5             # spine = mid pelvis-thorax
    y[:, 9] = x[:, 0]                               # nose
    y[:, 10] = (x[:, 1] + x[:, 2]) * 0.5            # head = mid-eye
    y[:, 11] = x[:, 5]                              # L shoulder
    y[:, 12] = x[:, 7]                              # L elbow
    y[:, 13] = x[:, 9]                              # L wrist
    y[:, 14] = x[:, 6]                              # R shoulder
    y[:, 15] = x[:, 8]                              # R elbow
    y[:, 16] = x[:, 10]                             # R wrist
    return y


def yolo_keypoints(video_path: str, weights: str, device: str) -> np.ndarray:
    """Run YOLO11n-pose over every frame; return (T,17,3) COCO [x_px, y_px, conf].

    One person per frame: the detection with the highest box confidence. Frames with no detection
    get conf 0 and linearly interpolated coordinates (never dropped — frame alignment with the
    manual annotations must hold)."""
    from ultralytics import YOLO

    model = YOLO(weights)
    kpts, missing = [], 0
    for r in model.predict(source=video_path, stream=True, device=device, verbose=False):
        if r.keypoints is not None and r.keypoints.data.shape[0] > 0 and r.boxes is not None and len(r.boxes) > 0:
            best = int(r.boxes.conf.argmax())
            k = r.keypoints.data[best].cpu().numpy()  # (17,3) x,y,conf
        else:
            k = np.full((17, 3), np.nan, dtype=np.float32)
            missing += 1
        kpts.append(k)
    K = np.stack(kpts).astype(np.float32)
    if missing:
        valid = np.isfinite(K[:, 0, 0])
        idx = np.arange(len(K))
        for j in range(17):
            for c in range(2):
                K[~valid, j, c] = np.interp(idx[~valid], idx[valid], K[valid, j, c])
        K[~valid, :, 2] = 0.0
        print(f"    {missing}/{len(K)} frames had no detection -> interpolated, conf 0")
    return K


def load_motionbert(checkpoint_path: str, device: str) -> torch.nn.Module:
    # Architecture per configs/pose3d/MB_ft_h36m.yaml (the FT_MB_release_MB_ft_h36m checkpoint).
    model = DSTformer(dim_in=3, dim_out=3, dim_feat=512, dim_rep=512, depth=5, num_heads=8,
                      mlp_ratio=2, norm_layer=lambda dim: torch.nn.LayerNorm(dim, eps=1e-6),
                      maxlen=243, num_joints=17)
    ckpt = torch.load(checkpoint_path, map_location="cpu", weights_only=True)
    state = {k[len("module."):] if k.startswith("module.") else k: v for k, v in ckpt["model_pos"].items()}
    model.load_state_dict(state, strict=True)
    return model.to(device).eval()


def lift_3d(model: torch.nn.Module, k2d_h36m: np.ndarray, clip_len: int, device: str) -> np.ndarray:
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


def main() -> None:
    ap = argparse.ArgumentParser(description="YOLO11n-pose -> COCO->H36M -> MotionBERT -> (T,17,3) .npy")
    ap.add_argument("--videos", default=os.path.join(REPO_ROOT, "ai", "data", "seated_row"),
                    help="directory searched recursively for *.mp4")
    ap.add_argument("--out_dir", default=os.path.join(REPO_ROOT, "ai", "data", "poses3d"))
    ap.add_argument("--conf_out_dir", default=os.path.join(REPO_ROOT, "ai", "data", "poses3d_conf"),
                    help="2D keypoint confidences in H36M order, (T,17), for v2's --conf_dir")
    ap.add_argument("--yolo_weights", default=os.path.join(REPO_ROOT, "yolo11n-pose.pt"))
    ap.add_argument("--checkpoint", default=os.path.join(
        REPO_ROOT, "ai", "models", "motionbert", "FT_MB_release_MB_ft_h36m", "best_epoch.bin"))
    ap.add_argument("--clip_len", type=int, default=243)
    ap.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    ap.add_argument("--overwrite", action="store_true")
    args = ap.parse_args()

    videos = sorted(
        os.path.join(r, f) for r, _, fs in os.walk(args.videos) for f in fs if f.lower().endswith(".mp4"))
    if not videos:
        sys.exit(f"no .mp4 under {args.videos}")
    os.makedirs(args.out_dir, exist_ok=True)
    os.makedirs(args.conf_out_dir, exist_ok=True)

    print(f"device={args.device}  videos={len(videos)}  checkpoint={os.path.relpath(args.checkpoint, REPO_ROOT)}")
    model = load_motionbert(args.checkpoint, args.device)

    for vp in videos:
        name = os.path.splitext(os.path.basename(vp))[0]
        out_path = os.path.join(args.out_dir, name + ".npy")
        if os.path.exists(out_path) and not args.overwrite:
            print(f"  {name}: exists, skipping (--overwrite to redo)")
            continue
        t0 = time.time()
        print(f"  {name}: YOLO 2D ...", flush=True)
        coco = yolo_keypoints(vp, args.yolo_weights, args.device)
        h36m_2d = coco2h36m(coco)
        np.save(os.path.join(args.conf_out_dir, name + ".npy"), h36m_2d[:, :, 2].astype(np.float32))
        norm2d = crop_scale(h36m_2d, scale_range=[1, 1]).astype(np.float32)
        print(f"  {name}: MotionBERT 3D ({len(norm2d)} frames) ...", flush=True)
        pose3d = lift_3d(model, norm2d, args.clip_len, args.device)
        assert pose3d.shape == (len(coco), 17, 3), pose3d.shape
        np.save(out_path, pose3d)
        print(f"  {name}: saved {os.path.relpath(out_path, REPO_ROOT)}  ({time.time() - t0:.0f}s)")

    print("done.")


if __name__ == "__main__":
    main()
