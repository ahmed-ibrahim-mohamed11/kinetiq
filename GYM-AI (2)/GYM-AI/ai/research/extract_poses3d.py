#!/usr/bin/env python3
"""
extract_poses3d.py — rebuild the MotionBERT 3D pose inputs (Path B, Phase 0 / blocker B1).

video (.mp4)
  -> YOLO11n-Pose            COCO-17 2D keypoints + per-joint confidence (highest-confidence person per frame)
  -> coco2h36m()             COCO-17 -> H36M-17 (midpoints for pelvis/thorax/spine/head; conf averaged)
  -> crop_scale()            normalise to [-1,1], official MotionBERT in-the-wild default (whole video,
                             scale_range=[1,1], motionbert/lib/utils/utils_data.py)
  -> DSTformer               checkpoint FT_MB_release_MB_ft_h36m (configs/pose3d/MB_ft_h36m.yaml),
                             243-frame clips, flip test-time augmentation, root-relative pelvis
  -> {video}.npy  (T,17,3)   estimated monocular 3D, H36M-17 order, frame-aligned with the video
  -> conf {video}.npy (T,17) 2D keypoint confidences mapped to H36M order (for --conf_dir of v2)

Depth convention: MotionBERT outputs camera-frame coordinates with +Z away from the camera. The athlete
faces away from the rear camera, so the facing direction is +Z — matching FACING_IN_CAMERA in ai/core/config.py.

Determinism: no augmentation at inference; crop_scale's scale_range=[1,1] makes the uniform draw constant.

Phase 1 note: the implementation lives in ai/core/pose.py (YOLO + coco2h36m) and ai/core/lifting.py
(MotionBERT); this file is the CLI. Behaviour is unchanged from the Phase 0 version (same functions, moved).

Usage (from repo root):
  .venv\Scripts\python.exe ai\research\extract_poses3d.py            # or scripts\extract_poses3d.ps1
"""
from __future__ import annotations

import argparse
import os
import sys
import time

import numpy as np

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import torch  # noqa: E402

from ai.core.lifting import DEFAULT_CHECKPOINT, crop_scale, lift_3d, load_motionbert  # noqa: E402
from ai.core.pose import coco2h36m, yolo_keypoints  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description="YOLO11n-pose -> COCO->H36M -> MotionBERT -> (T,17,3) .npy")
    ap.add_argument("--videos", default=os.path.join(REPO_ROOT, "ai", "data", "seated_row"),
                    help="directory searched recursively for *.mp4")
    ap.add_argument("--out_dir", default=os.path.join(REPO_ROOT, "ai", "data", "poses3d"))
    ap.add_argument("--conf_out_dir", default=os.path.join(REPO_ROOT, "ai", "data", "poses3d_conf"),
                    help="2D keypoint confidences in H36M order, (T,17), for v2's --conf_dir")
    ap.add_argument("--yolo_weights", default=os.path.join(REPO_ROOT, "yolo11n-pose.pt"))
    ap.add_argument("--checkpoint", default=DEFAULT_CHECKPOINT)
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
