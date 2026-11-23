"""2D pose extraction (YOLO11n-pose) + COCO-17 -> H36M-17 conversion.
Moved verbatim from ai/research/extract_poses3d.py (Phase 1); that script now imports from here.

COCO-17: 0 nose 1 Leye 2 Reye 3 Lear 4 Rear 5 Lsho 6 Rsho 7 Lelb 8 Relb
         9 Lwri 10 Rwri 11 Lhip 12 Rhip 13 Lknee 14 Rknee 15 Lank 16 Rank
H36M-17: 0 pelvis 1 Rhip 2 Rknee 3 Rank 4 Lhip 5 Lknee 6 Lank 7 spine 8 thorax
         9 nose 10 head 11 Lsho 12 Lelb 13 Lwri 14 Rsho 15 Relb 16 Rwri
"""
from __future__ import annotations

import os

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
    """RESEARCH/LEGACY ONLY (ai/research/extract_poses3d.py) — NOT the product detector; the product
    path is `YoloTopK` + `ai.core.tracking.track_subject` (RISK_REGISTER R-01).

    Run YOLO11n-pose over every frame; return (T,17,3) COCO [x_px, y_px, conf].

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


# ----------------------------------------------------------------------------- product detector
# `yolo_keypoints` above is the LEGACY single-highest-confidence-box rule (RISK_REGISTER R-01:
# it switches to background people on 15/34 Mendeley videos). The validated product path feeds
# the deterministic tracker (ai.core.tracking) with the top-K candidates per frame sorted by box
# area — per-frame form of ai/research/phase28/seated_row_transfer.py::yolo_top8 (which ran
# ultralytics in video-stream mode); ai/tests/test_pose.py checks the two agree on real frames.
YOLO_WEIGHTS = os.path.join(os.path.dirname(__file__), "..", "..", "yolo11n-pose.pt")


def disable_ultralytics_analytics() -> None:
    """Ultralytics posts anonymous usage events (Google Analytics) after every predict call while its
    global `sync` setting is on, which is the default. Nothing may leave the machine from the product
    path (docs/PRIVACY.md), so this switches it off for the current process only; the user's global
    Ultralytics settings are not modified. Inference is unaffected."""
    from ultralytics.utils import events
    events.events.enabled = False


class YoloTopK:
    """Per-frame YOLO11n-pose -> (kpts (K,17,3) [x_px,y_px,conf], boxes (K,5) [cx,cy,w,h,conf]),
    candidates sorted by box area descending, NaN-padded to K. device: 'cpu' | 'cuda'."""

    def __init__(self, weights: str = YOLO_WEIGHTS, device: str = "cpu", imgsz: int = 640, k: int = 8,
                 half: bool = False):
        if not os.path.exists(weights):
            raise FileNotFoundError(f"YOLO weights not found: {weights} (auto-download source: "
                                    f"ultralytics, see ai/models/README.md)")
        if half and device == "cpu":
            raise ValueError("fp16 detection is only supported on cuda")
        # Importing ultralytics runs an online check (DNS lookups of one.one.one.one / dns.google).
        # Offline mode skips it; the weights are local (checked above), so nothing else needs the
        # network. Only effective before ultralytics is first imported in this process, and an
        # explicit YOLO_OFFLINE set by the user wins.
        os.environ.setdefault("YOLO_OFFLINE", "1")
        from ultralytics import YOLO
        disable_ultralytics_analytics()
        self.model = YOLO(weights)
        self.device = 0 if device == "cuda" else "cpu"
        self.imgsz, self.k, self.half = imgsz, k, half

    def __call__(self, frame_bgr: np.ndarray):
        k = np.full((self.k, 17, 3), np.nan, dtype=np.float32)
        b = np.full((self.k, 5), np.nan, dtype=np.float32)
        extra = dict(half=True) if self.half else {}   # ultralytics 8.4 warns on any explicit `half`
        r = self.model.predict(frame_bgr, device=self.device, imgsz=self.imgsz, verbose=False,
                               **extra)[0]
        if r.boxes is not None and len(r.boxes) > 0 and r.keypoints is not None:
            conf = r.boxes.conf.cpu().numpy()
            xywh = r.boxes.xywh.cpu().numpy()
            order = np.argsort(-(xywh[:, 2] * xywh[:, 3]))[:self.k]
            for s, i in enumerate(order):
                k[s] = r.keypoints.data[int(i)].cpu().numpy()
                b[s] = [*xywh[int(i)], conf[int(i)]]
        return k, b
