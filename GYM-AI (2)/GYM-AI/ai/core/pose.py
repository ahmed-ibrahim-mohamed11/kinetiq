"""2D pose extraction (YOLO11n-pose) + COCO-17 -> H36M-17 conversion.
Moved verbatim from ai/research/extract_poses3d.py (Phase 1); that script now imports from here.

COCO-17: 0 nose 1 Leye 2 Reye 3 Lear 4 Rear 5 Lsho 6 Rsho 7 Lelb 8 Relb
         9 Lwri 10 Rwri 11 Lhip 12 Rhip 13 Lknee 14 Rknee 15 Lank 16 Rank
H36M-17: 0 pelvis 1 Rhip 2 Rknee 3 Rank 4 Lhip 5 Lknee 6 Lank 7 spine 8 thorax
         9 nose 10 head 11 Lsho 12 Lelb 13 Lwri 14 Rsho 15 Relb 16 Rwri
"""
from __future__ import annotations

import numpy as np


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
