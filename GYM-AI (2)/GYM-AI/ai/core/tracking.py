"""Deterministic single-subject tracker — production copy (Phase 4/5, RISK_REGISTER R-01).

`track_subject` is VERBATIM from the frozen research reference
`ai/research/pose_benchmark/tracking.py` (Phase 2.5C; hash-tracked in
ai/outputs/phase27/freeze_manifest.json). ai/tests/test_tracking.py proves byte-identical
output on every cached 2D candidate file in the repo, so production code no longer needs a
sys.path hack into ai/research. `OnlineTracker` is the frame-by-frame (live, Mode B) form of
the same algorithm — exactly equal to `track_subject` on the full sequence (also tested).

Rules (unchanged): init = median box of the largest candidate over the first 10 frames with
detections; per frame pick the candidate with max IoU to the track box (accept >= 0.10), else the
nearest centre within 0.30 x track diagonal; coast (NaN pose, 'lost') when nothing is
acceptable; after >= 15 consecutive lost frames re-acquire the largest current candidate.
N candidates per frame is shape-driven (3 in the Mendeley benchmark cache, 8 elsewhere).
"""
from __future__ import annotations

from typing import List, Optional, Tuple

import numpy as np

INIT_FRAMES = 10
IOU_ACCEPT = 0.10
DIST_ACCEPT = 0.30
REACQ_STREAK = 15
JUMP_FRAC = 0.15


def _iou(a, b) -> float:
    ax1, ay1, ax2, ay2 = a[0] - a[2] / 2, a[1] - a[3] / 2, a[0] + a[2] / 2, a[1] + a[3] / 2
    bx1, by1, bx2, by2 = b[0] - b[2] / 2, b[1] - b[3] / 2, b[0] + b[2] / 2, b[1] + b[3] / 2
    iw = max(0.0, min(ax2, bx2) - max(ax1, bx1))
    ih = max(0.0, min(ay2, by2) - max(ay1, by1))
    inter = iw * ih
    union = a[2] * a[3] + b[2] * b[3] - inter
    return inter / union if union > 0 else 0.0


def track_subject(kpts_top: np.ndarray, boxes: np.ndarray, img_wh: Tuple[float, float]):
    """kpts_top (T,N,17,3), boxes (T,N,5)=cx,cy,w,h,conf -> (kpts (T,17,3), log dict)."""
    T = len(boxes)
    diag = float(np.hypot(*img_wh))
    valid_t = [t for t in range(T) if np.isfinite(boxes[t, 0, 0])]
    out = np.full((T, 17, 3), np.nan, dtype=np.float32)
    log = dict(lost=0, jumps=0, disagree=0, reacquired=0, first_frame=None)
    lost_streak = 0
    if not valid_t:
        return out, log
    init_frames = valid_t[:INIT_FRAMES]
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
        accept = ious[j] >= IOU_ACCEPT
        if not accept:
            dists = np.where(ok, np.hypot(cand[:, 0] - track[0], cand[:, 1] - track[1]), np.inf)
            j = int(np.argmin(dists))
            accept = dists[j] <= DIST_ACCEPT * np.hypot(track[2], track[3])
        if not accept and lost_streak >= REACQ_STREAK:
            areas = np.where(ok, cand[:, 2] * cand[:, 3], -1.0)
            j = int(np.argmax(areas))
            accept = True
            log["reacquired"] += 1
        if not accept:
            log["lost"] += 1
            lost_streak += 1
            continue
        lost_streak = 0
        jump = np.hypot(cand[j, 0] - track[0], cand[j, 1] - track[1])
        if jump > JUMP_FRAC * diag:
            log["jumps"] += 1
        if j != 0:
            log["disagree"] += 1
        out[t] = kpts_top[t, j]
        track = cand[j, :4]
    return out, log


class OnlineTracker:
    """Frame-by-frame form of `track_subject` (live mode). The only look-ahead in the offline
    rule is its init box (median over the first 10 frames WITH detections), so this buffers
    frames until 10 detected frames have arrived (or `flush()` at end of stream), then emits
    every frame in order and never revises one. Output == track_subject on the full sequence."""

    def __init__(self, img_wh: Tuple[float, float]):
        self.diag = float(np.hypot(*img_wh))
        self.pending: List[Tuple[np.ndarray, np.ndarray]] = []
        self.track: Optional[np.ndarray] = None
        self.lost_streak = 0
        self.n_emitted = 0
        self.log = dict(lost=0, jumps=0, disagree=0, reacquired=0, first_frame=None)
        self._n_detected_pending = 0

    def _init_from_pending(self) -> bool:
        init_boxes, first = [], None
        for t, (_, bt) in enumerate(self.pending):
            if np.isfinite(bt[0, 0]):
                if first is None:
                    first = t
                areas = bt[:, 2] * bt[:, 3]
                init_boxes.append(bt[int(np.nanargmax(areas))])
                if len(init_boxes) == INIT_FRAMES:
                    break
        if not init_boxes:
            return False
        self.track = np.nanmedian(np.stack(init_boxes), axis=0)[:4]
        self.log["first_frame"] = int(first)
        return True

    def _step(self, kt: np.ndarray, bt: np.ndarray) -> np.ndarray:
        nan = np.full((17, 3), np.nan, dtype=np.float32)
        ok = np.isfinite(bt[:, 0])
        if not ok.any():
            self.log["lost"] += 1
            self.lost_streak += 1
            return nan
        track = self.track
        ious = np.array([_iou(track, c[:4]) if o else -1.0 for c, o in zip(bt, ok)])
        j = int(np.argmax(ious))
        accept = ious[j] >= IOU_ACCEPT
        if not accept:
            dists = np.where(ok, np.hypot(bt[:, 0] - track[0], bt[:, 1] - track[1]), np.inf)
            j = int(np.argmin(dists))
            accept = dists[j] <= DIST_ACCEPT * np.hypot(track[2], track[3])
        if not accept and self.lost_streak >= REACQ_STREAK:
            areas = np.where(ok, bt[:, 2] * bt[:, 3], -1.0)
            j = int(np.argmax(areas))
            accept = True
            self.log["reacquired"] += 1
        if not accept:
            self.log["lost"] += 1
            self.lost_streak += 1
            return nan
        self.lost_streak = 0
        jump = np.hypot(bt[j, 0] - track[0], bt[j, 1] - track[1])
        if jump > JUMP_FRAC * self.diag:
            self.log["jumps"] += 1
        if j != 0:
            self.log["disagree"] += 1
        self.track = bt[j, :4]
        return kt[j].astype(np.float32)

    def push(self, kt: np.ndarray, bt: np.ndarray) -> List[np.ndarray]:
        """Feed one frame's candidates (kt (N,17,3), bt (N,5)); returns the (17,3) tracked
        keypoints of every frame that became final (empty while still buffering for init)."""
        if self.track is None:
            self.pending.append((kt, bt))
            if np.isfinite(bt[0, 0]):
                self._n_detected_pending += 1
            if self._n_detected_pending >= INIT_FRAMES and self._init_from_pending():
                return self._drain()
            return []
        self.n_emitted += 1
        return [self._step(kt, bt)]

    def _drain(self) -> List[np.ndarray]:
        out = [self._step(k, b) for k, b in self.pending]
        self.n_emitted += len(out)
        self.pending = []
        return out

    def flush(self) -> List[np.ndarray]:
        """End of stream: emit anything still buffered (fewer than 10 detected frames ever)."""
        if self.track is None:
            if not self._init_from_pending():
                # No detection in the whole stream. Mirrors track_subject exactly, which returns
                # before its frame loop in this case (so its 'lost' counter stays 0 — a quirk of
                # the frozen reference, kept for byte-identical equivalence; the Phase 3 guard
                # ai.core.guards reports this case explicitly as NO_PERSON instead).
                out = [np.full((17, 3), np.nan, dtype=np.float32) for _ in self.pending]
                self.n_emitted += len(out)
                self.pending = []
                return out
            return self._drain()
        return []
