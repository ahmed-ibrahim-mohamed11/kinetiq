"""Mode B (live / causal) streaming pipeline — production module (Phase 4).

Frame-by-frame implementation of the bounded-lag streaming design validated in
ai/research/realtime/causal_prototype.py (REALTIME_FEASIBILITY.md): sliding-window MB-Lite
lifting (crop box from the window only), one finalized block per window, fixed-lag decode, and
COMMIT-ONCE rep events (a rep is emitted only once its END is `commit_lag_f` processed frames
old, and is never revised). Mode A (offline, ai.core.pipeline) stays the reliability-first
reference — nothing here changes it (owner rule: never degrade Mode A to make Mode B fit).

Differences from the prototype (all measured in docs/LIVE_MODE.md, never assumed):
  - the tracker runs ONLINE (`ai.core.tracking.OnlineTracker`, proven equal to the offline
    tracker), instead of re-running `track_subject` on the growing buffer every chunk;
  - frames arrive one at a time from a detector callable (live camera), not from a 2D cache;
  - optional `detect_every=N`: run the 2D detector on every Nth processed frame and fill the
    skipped frames by linear interpolation of x, y AND confidence between the neighbouring
    detections (forward-fill at the live edge) — valid inside the bounded-lag design because a
    skipped frame is only lifted/committed after the next detection has arrived;
  - decode goes through `ai.core.guards.safe_predict_video` (explicit abstention status per
    chunk) — byte-identical reps to `predict_video` whenever the status is OK.
Components are injected (detector, lifter, rep system) so the same streamer runs on CPU or CUDA,
with or without TTA/fp16, and so tests can drive it from cached 2D.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np

from ai.core.features import prepare_video
from ai.core.form_indicators import evaluate_rep, rep_features
from ai.core.guards import safe_predict_video
from ai.core.tracking import OnlineTracker

PULL_TOL_S = 8 / 15.0


@dataclass(frozen=True)
class LiveConfig:
    window: int = 243           # MB-Lite clip length (frames)
    stride: int = 81            # lift every `stride` processed frames
    finalize: str = "middle"    # "middle" (V2 quality) | "tail" (V1/V3 low latency)
    commit_lag_f: int = 162     # D: a rep is committed once its END is D processed frames old
    proc_fps_div: int = 1       # process every Nth camera frame (2 -> 15 fps from 30, V3 budget)
    detect_every: int = 1       # run the 2D detector every N processed frames (interpolate between)
    tta: bool = True            # MB-Lite flip test-time augmentation

    def __post_init__(self):
        if self.finalize not in ("middle", "tail"):
            raise ValueError(f"LiveConfig.finalize must be 'middle' or 'tail', got {self.finalize!r}")
        for name in ("window", "stride", "commit_lag_f", "proc_fps_div", "detect_every"):
            if getattr(self, name) < 1:
                raise ValueError(f"LiveConfig.{name} must be >= 1")
        if self.stride > self.window:
            raise ValueError("LiveConfig.stride must be <= window")

    @staticmethod
    def preset(name: str) -> "LiveConfig":
        """The three variants measured in REALTIME_FEASIBILITY.md (accuracy on dev videos)."""
        return {"V1_low_latency": LiveConfig(finalize="tail", commit_lag_f=81),
                "V2_quality": LiveConfig(finalize="middle", commit_lag_f=162),
                "V3_budget_15fps": LiveConfig(finalize="tail", commit_lag_f=81, proc_fps_div=2,
                                              tta=False)}[name]


@dataclass
class LiveEvent:
    start: int                  # NATIVE camera-frame indices (comparable to annotations)
    pull: int
    end: int
    committed_at: int           # native frame index at which the event was emitted
    indicators: List[dict] = field(default_factory=list)
    status: str = "OK"          # guard status of the decode that produced this event


class LiveStreamer:
    """push_frame(frame) -> newly committed LiveEvents (never revised); finish() at end of stream.

    detector(frame_bgr) -> (kpts (K,17,3), boxes (K,5))      e.g. ai.core.pose.YoloTopK
    lifter.lift_window(coco_win, tta) -> (pose3d, conf)       e.g. ai.core.lifting.MotionBertLite
    system: object with .cfg, .model, .dec (fitted rep-segmentation system)
    """

    def __init__(self, cfg: LiveConfig, detector: Callable, lifter, system, img_wh: Tuple[float, float],
                 native_fps: float, thresholds: Optional[dict] = None, name: str = "live",
                 clock: Callable[[], float] = time.perf_counter):
        if not (native_fps > 0):
            raise ValueError(f"native_fps must be positive, got {native_fps!r}")
        self.cfg, self.detector, self.lifter, self.system = cfg, detector, lifter, system
        self.thresholds, self.name, self.clock = thresholds, name, clock
        self.fps = native_fps / cfg.proc_fps_div
        self.tracker = OnlineTracker(img_wh)
        self.coco: List[np.ndarray] = []          # per processed frame, (17,3) or NaN
        self.detected: List[bool] = []
        self._pushed: List[int] = []              # processed indices fed to the tracker, in order
        self._n_assigned = 0                      # how many of _pushed have a tracker output
        self.pose = np.zeros((0, 17, 3), dtype=np.float32)
        self.conf = np.zeros((0, 17), dtype=np.float32)
        self.finalized = 0
        self.events: List[LiveEvent] = []
        self.n_native = 0
        self.n_proc = 0
        self._last_chunk_t = -1
        self.chunk_status: List[str] = []
        # per-stage timings (ms), keyed by processed frame index so callers can rebuild per-frame cost
        self.stage_ms: Dict[str, List[Tuple[int, float]]] = {
            "detect": [], "track": [], "lift": [], "decode": [], "form": []}

    # ------------------------------------------------------------------ helpers
    def _time(self, stage: str, j: int, t0: float):
        self.stage_ms[stage].append((j, (self.clock() - t0) * 1000.0))

    def _assign(self, emitted: List[np.ndarray]):
        for out in emitted:
            self.coco[self._pushed[self._n_assigned]] = out
            self._n_assigned += 1

    def _window_coco(self, lo: int, hi: int) -> np.ndarray:
        """Tracked keypoints for processed frames lo..hi with skipped (not-detected) frames filled
        by interpolation of x, y, conf between neighbouring detected frames (forward-fill at the
        live edge). Tracking-LOST frames stay NaN -> the lifter's interp policy (conf 0) applies."""
        win = np.stack(self.coco[lo:hi + 1]).astype(np.float32).copy()
        if self.cfg.detect_every == 1:
            return win
        det_idx = [i for i in range(len(self.coco)) if self.detected[i]]
        for j in range(lo, hi + 1):
            if self.detected[j]:
                continue
            prev = max((i for i in det_idx if i < j), default=None)
            nxt = min((i for i in det_idx if i > j), default=None)
            a = self.coco[prev] if prev is not None else None
            b = self.coco[nxt] if nxt is not None else None
            if a is not None and b is not None and np.isfinite(a).all() and np.isfinite(b).all():
                w = (j - prev) / (nxt - prev)
                win[j - lo] = (1 - w) * a + w * b
            elif a is not None and np.isfinite(a).all():
                win[j - lo] = a
        return win

    def _is_chunk_time(self, t: int) -> bool:
        w, s = self.cfg.window, self.cfg.stride
        return t >= w - 1 and (t - (w - 1)) % s == 0

    # ------------------------------------------------------------------ public API
    def push_frame(self, frame_bgr: np.ndarray) -> List[LiveEvent]:
        native_i = self.n_native
        self.n_native += 1
        if native_i % self.cfg.proc_fps_div:
            return []
        j = self.n_proc
        self.n_proc += 1
        self.coco.append(np.full((17, 3), np.nan, dtype=np.float32))
        if j % self.cfg.detect_every == 0:
            t0 = self.clock()
            kt, bt = self.detector(frame_bgr)
            self._time("detect", j, t0)
            t0 = self.clock()
            self._pushed.append(j)
            self._assign(self.tracker.push(kt, bt))
            self._time("track", j, t0)
            self.detected.append(True)
        else:
            self.detected.append(False)
        if self._is_chunk_time(j):
            return self._chunk(j, last=False)
        return []

    def finish(self) -> List[LiveEvent]:
        self._assign(self.tracker.flush())
        t = self.n_proc - 1
        if t < 0 or t == self._last_chunk_t:
            return []
        return self._chunk(t, last=True)

    def _chunk(self, t: int, last: bool) -> List[LiveEvent]:
        cfg = self.cfg
        self._last_chunk_t = t
        lo = max(0, t - cfg.window + 1)
        t0 = self.clock()
        p3d, c2d = self.lifter.lift_window(self._window_coco(lo, t), tta=cfg.tta)
        self._time("lift", t, t0)
        S = cfg.stride
        if self.finalized == 0:
            fin_lo, fin_hi = 0, (t if (cfg.finalize == "tail" or last) else min(lo + 2 * S - 1, t))
        elif cfg.finalize == "tail" or last:
            fin_lo, fin_hi = self.finalized, t
        else:
            fin_lo, fin_hi = max(self.finalized, lo + S), min(lo + 2 * S - 1, t)
        if fin_hi >= fin_lo:
            n_new = fin_hi + 1
            if len(self.pose) < n_new:
                self.pose = np.concatenate([self.pose, np.zeros((n_new - len(self.pose), 17, 3), np.float32)])
                self.conf = np.concatenate([self.conf, np.zeros((n_new - len(self.conf), 17), np.float32)])
            self.pose[fin_lo:fin_hi + 1] = p3d[fin_lo - lo:fin_hi - lo + 1]
            self.conf[fin_lo:fin_hi + 1] = c2d[fin_lo - lo:fin_hi - lo + 1]
            self.finalized = fin_hi + 1
        commit_to = self.finalized if last else min(self.finalized, t + 1 - cfg.commit_lag_f)

        t0 = self.clock()
        v = prepare_video(self.name, self.pose[:self.finalized], self.fps, conf=self.conf[:self.finalized])
        res = safe_predict_video(v, self.system.model, self.system.dec, self.system.cfg)
        self._time("decode", t, t0)
        self.chunk_status.append(res["status"])

        tol_f = int(round(PULL_TOL_S * self.fps))
        div = cfg.proc_fps_div
        new: List[LiveEvent] = []
        for r in res["reps"]:
            if r["end"] >= commit_to:
                continue
            dup = any(abs(r["pull"] * div - e.pull) <= tol_f * div or
                      _iou((r["start"] * div, r["end"] * div), (e.start, e.end)) >= 0.5
                      for e in self.events)
            if dup:
                continue
            t1 = self.clock()
            inds = []
            if self.thresholds is not None:
                f = rep_features(v, None, r["start"], r["end"], self.fps, conf=self.conf[:self.finalized])
                inds = [i.to_dict() for i in evaluate_rep(f, self.thresholds)]
            self._time("form", t, t1)
            ev = LiveEvent(start=int(r["start"] * div), pull=int(r["pull"] * div), end=int(r["end"] * div),
                           committed_at=int(t * div), indicators=inds, status=res["status"])
            self.events.append(ev)
            new.append(ev)
        return new


def _iou(a, b) -> float:
    inter = max(0, min(a[1], b[1]) - max(a[0], b[0]))
    union = max(a[1], b[1]) - min(a[0], b[0])
    return inter / union if union > 0 else 0.0
