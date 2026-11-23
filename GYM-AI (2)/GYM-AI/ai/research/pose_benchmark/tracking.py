"""Benchmark-only deterministic subject selection/tracking (Phase 2.5C mandatory fix).

The production rule (highest-confidence box) switches to background people on 15/34 Mendeley
videos (Phase 2.5B audit). Every YOLO-based pipeline uses THIS tracker so all pipelines see
identical 2D input; the number of candidates per frame is generic (shape-driven, not hardcoded):
the Mendeley/Phase-2.5C benchmark cache holds top-3 (`ai/outputs/mendeley_2d/*.npz`,
`make_tracked_2d.py`), while the seated-row/external-stress/causal-prototype paths pass top-8
(`yolo_top8()` in `phase28/seated_row_transfer.py`, reused by `causal_prototype.py`):

  init    largest box area, median over the first 10 frames with detections
  assoc   per frame, the candidate maximising IoU with the current track box;
          accepted if IoU >= 0.10, else if its centre lies within 0.30 x track diagonal
  coast   no acceptable candidate -> keep the track box, emit NaN pose (logged as 'lost')
  reacq   lost for >= 15 consecutive frames (1 s @15fps) -> re-initialise from the largest
          current candidate (logged as 'reacquired'); prevents permanent loss
  log     every accepted candidate whose centre jumped > 15% of the image diagonal ('jumps'),
          every frame where the tracked choice differs from candidate 0 (largest box, 'disagree')

Deterministic: no randomness, no learned state. The frozen production baseline is NOT changed.
"""
from __future__ import annotations

import numpy as np


def _iou(a, b) -> float:
    ax1, ay1, ax2, ay2 = a[0] - a[2] / 2, a[1] - a[3] / 2, a[0] + a[2] / 2, a[1] + a[3] / 2
    bx1, by1, bx2, by2 = b[0] - b[2] / 2, b[1] - b[3] / 2, b[0] + b[2] / 2, b[1] + b[3] / 2
    iw = max(0.0, min(ax2, bx2) - max(ax1, bx1))
    ih = max(0.0, min(ay2, by2) - max(ay1, by1))
    inter = iw * ih
    union = a[2] * a[3] + b[2] * b[3] - inter
    return inter / union if union > 0 else 0.0


def track_subject(kpts_top: np.ndarray, boxes: np.ndarray, img_wh: tuple[float, float]):
    """kpts_top (T,N,17,3), boxes (T,N,5)=cx,cy,w,h,conf -> (kpts (T,17,3), log dict).
    N = 3 for the Mendeley benchmark cache, 8 for seated-row/external/causal callers."""
    T = len(boxes)
    diag = float(np.hypot(*img_wh))
    valid_t = [t for t in range(T) if np.isfinite(boxes[t, 0, 0])]
    out = np.full((T, 17, 3), np.nan, dtype=np.float32)
    log = dict(lost=0, jumps=0, disagree=0, reacquired=0, first_frame=None)
    lost_streak = 0
    if not valid_t:
        return out, log
    # init: largest-area candidate, median box over the first up-to-10 detected frames
    init_frames = valid_t[:10]
    init_boxes = []
    for t in init_frames:
        areas = boxes[t, :, 2] * boxes[t, :, 3]
        init_boxes.append(boxes[t, int(np.nanargmax(areas))])
    track = np.nanmedian(np.stack(init_boxes), axis=0)[:4]
    log["first_frame"] = int(init_frames[0])

    for t in range(T):
        cand = boxes[t]
        ok = np.isfinite(cand[:, 0])
        if not ok.any():
            log["lost"] += 1
            lost_streak += 1
            continue
        ious = np.array([_iou(track, c[:4]) if o else -1.0 for c, o in zip(cand, ok)])
        j = int(np.argmax(ious))
        accept = ious[j] >= 0.10
        if not accept:
            dists = np.where(ok, np.hypot(cand[:, 0] - track[0], cand[:, 1] - track[1]), np.inf)
            j = int(np.argmin(dists))
            accept = dists[j] <= 0.30 * np.hypot(track[2], track[3])
        if not accept and lost_streak >= 15:
            areas = np.where(ok, cand[:, 2] * cand[:, 3], -1.0)
            j = int(np.argmax(areas))                 # deterministic re-acquisition: largest box
            accept = True
            log["reacquired"] += 1
        if not accept:
            log["lost"] += 1
            lost_streak += 1
            continue
        lost_streak = 0
        jump = np.hypot(cand[j, 0] - track[0], cand[j, 1] - track[1])
        if jump > 0.15 * diag:
            log["jumps"] += 1
        if j != 0:
            log["disagree"] += 1
        out[t] = kpts_top[t, j]
        track = cand[j, :4]
    return out, log
