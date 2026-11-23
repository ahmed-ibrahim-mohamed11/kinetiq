"""Mode A (after-set / offline) — the reliability-first reference pipeline as ONE call (P5.2).

video file -> YoloTopK (top-8 per frame) -> track_subject -> MotionBertLite.lift_video (offline
recipe, flip-TTA) -> prepare_video -> safe_predict_video (persisted artifact, guards incl. the
tracker log) -> form indicators -> validated `AnalysisResult` (ai.core.schema).

Every stage is the production module proven equal to its frozen research reference (Phase 4
tests), and the fitted system is the committed, fingerprint-verified artifact (P5.1) — so this
reproduces the frozen Phase 2.8 evaluation end-to-end from raw video (ai/tests/test_pipeline.py).
Input errors are explicit exceptions (missing file, undecodable video, invalid fps, zero frames);
data-quality problems are explicit abstention STATUSES in a valid result, never garbage.
"""
from __future__ import annotations

import hashlib
import os
import time
from dataclasses import dataclass
from functools import lru_cache
from typing import Optional

import numpy as np

from ai.core.features import prepare_video
from ai.core.form_indicators import evaluate_rep, load_thresholds, rep_features
from ai.core.guards import safe_predict_video
from ai.core.lifting import MBLITE_CHECKPOINT, MotionBertLite
from ai.core.model_artifact import LoadedSystem, load_artifact
from ai.core.pose import YOLO_WEIGHTS, YoloTopK
from ai.core.schema import AnalysisResult
from ai.core.tracking import track_subject

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
STANDING_WARNINGS = [
    "3D pose is estimated from a single camera (monocular); values are proxies, not metric measurements.",
    "Form indicators are coach-attention prompts, not diagnoses or injury assessments.",
    "Seated-row indicator thresholds are PROVISIONAL (calibrated on 15 reps of one athlete).",
    "Validated camera placement: behind the athlete (rear view); other placements are less accurate.",
]


@lru_cache(maxsize=8)
def _sha256(path: str, _mtime: float) -> Optional[str]:
    if not os.path.exists(path):
        return None
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_file(path: str) -> Optional[str]:
    return _sha256(path, os.path.getmtime(path) if os.path.exists(path) else 0.0)


def _git_commit() -> Optional[str]:
    """Best-effort commit id from .git without needing a git binary (provenance only)."""
    try:
        head = open(os.path.join(REPO_ROOT, ".git", "HEAD")).read().strip()
        if not head.startswith("ref:"):
            return head
        ref = head.split(" ", 1)[1]
        p = os.path.join(REPO_ROOT, ".git", *ref.split("/"))
        if os.path.exists(p):
            return open(p).read().strip()
        for line in open(os.path.join(REPO_ROOT, ".git", "packed-refs")):
            if line.strip().endswith(ref):
                return line.split()[0]
    except OSError:
        return None
    return None


@dataclass
class Components:
    detector: YoloTopK
    lifter: MotionBertLite
    system: LoadedSystem
    thresholds: dict
    versions: dict
    device: str


def load_components(device: str = "cpu", artifact_path: Optional[str] = None, imgsz: int = 640,
                    exercise: str = "seated_row") -> Components:
    system = load_artifact(artifact_path)
    versions = dict(artifact_version=system.version, artifact_fingerprint=system.fingerprint,
                    detector=f"yolo11n-pose top-8 @{imgsz} + deterministic tracker",
                    detector_sha256=sha256_file(YOLO_WEIGHTS),
                    lifter="MotionBERT-Lite FT_MB_lite_MB_ft_h36m_global_lite (flip-TTA)",
                    lifter_sha256=sha256_file(MBLITE_CHECKPOINT), code_version=_git_commit())
    return Components(detector=YoloTopK(device=device, imgsz=imgsz),
                      lifter=MotionBertLite(device=device), system=system,
                      thresholds=load_thresholds(exercise), versions=versions, device=device)


@dataclass
class PoseTrack:
    """Output of the expensive stage (decode + detect + track + lift) for one video."""
    pose: np.ndarray
    conf: np.ndarray
    tracker_log: dict
    container_fps: float
    width: int
    height: int
    source_sha256: Optional[str]
    timings_ms: dict

    @property
    def n_frames(self) -> int:
        return int(self.pose.shape[0])


def _valid_fps(fps, what: str) -> float:
    fps = float(fps)
    if not (fps > 0) or not np.isfinite(fps):
        raise ValueError(f"{what}: invalid frame rate {fps!r}")
    return fps


def extract_pose(path: str, comp: Components) -> PoseTrack:
    import cv2
    if not os.path.exists(path):
        raise FileNotFoundError(f"video not found: {path}")
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        raise ValueError(f"cannot decode video: {path}")
    try:
        fps = _valid_fps(cap.get(cv2.CAP_PROP_FPS) or 0.0, path)
        W, H = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        timings = {}
        t0 = time.perf_counter()
        kts, bts = [], []
        while True:
            ok, fr = cap.read()
            if not ok:
                break
            k, b = comp.detector(fr)
            kts.append(k)
            bts.append(b)
    finally:
        cap.release()
    timings["decode_and_detect"] = (time.perf_counter() - t0) * 1000.0
    if not kts:
        raise ValueError(f"{path}: no decodable frames")

    t0 = time.perf_counter()
    coco, tlog = track_subject(np.stack(kts), np.stack(bts), (float(W), float(H)))
    timings["track"] = (time.perf_counter() - t0) * 1000.0
    t0 = time.perf_counter()
    pose, conf = comp.lifter.lift_video(coco, tta=True)
    timings["lift"] = (time.perf_counter() - t0) * 1000.0
    return PoseTrack(pose=pose, conf=conf, tracker_log=tlog, container_fps=fps, width=W, height=H,
                     source_sha256=sha256_file(path), timings_ms=timings)


def analyze_video(path: str, comp: Components, name: Optional[str] = None,
                  fps: Optional[float] = None) -> AnalysisResult:
    """`fps=None` uses the container's reported (average) frame rate; pass `fps` to override it
    (wrong metadata, or reproducing an evaluation done at a nominal rate)."""
    name = name or os.path.splitext(os.path.basename(path))[0]
    return analyze_pose(extract_pose(path, comp), comp, name=name, fps=fps)


def analyze_pose(track: PoseTrack, comp: Components, name: str = "video",
                 fps: Optional[float] = None) -> AnalysisResult:
    T, pose, conf, tlog = track.n_frames, track.pose, track.conf, track.tracker_log
    W, H = track.width, track.height
    warnings = list(STANDING_WARNINGS)
    if fps is None:
        fps = track.container_fps
    else:
        fps = _valid_fps(fps, f"{name}: fps override")
        warnings.append(f"Frame rate overridden: container reports {track.container_fps:.3f} fps, "
                        f"analysis used {fps:.3f} fps.")
    timings = dict(track.timings_ms)

    t0 = time.perf_counter()
    v = prepare_video(name, pose, fps, conf=conf)
    res = safe_predict_video(v, comp.system.model, comp.system.dec, comp.system.cfg, tracker_log=tlog)
    timings["decode_reps"] = (time.perf_counter() - t0) * 1000.0

    t0 = time.perf_counter()
    reps = []
    for i, r in enumerate(res["reps"], 1):
        f = rep_features(v, None, r["start"], r["end"], fps, conf=conf)
        inds = [ind.to_dict() for ind in evaluate_rep(f, comp.thresholds)]
        reps.append(dict(index=i, start_frame=int(r["start"]), pull_frame=int(r["pull"]),
                         end_frame=int(r["end"]), start_s=r["start"] / fps, pull_s=r["pull"] / fps,
                         end_s=r["end"] / fps, duration_s=(r["end"] - r["start"]) / fps,
                         truncated=bool(r["truncated"]), peak_z=float(r["peak_z"]),
                         lag_pull_s=float(f["lr_lag"]), amp_ratio=float(f["lr_amplitude_ratio"]),
                         indicators=inds))
    timings["form"] = (time.perf_counter() - t0) * 1000.0

    g = res["guard"]
    diag = {k: (round(float(v_), 6) if isinstance(v_, (float, np.floating)) else v_)
            for k, v_ in g.diagnostics.items()}
    durs = [r["duration_s"] for r in reps]
    if tlog.get("lost"):
        warnings.append(f"Tracking was lost on {tlog['lost']} of {T} frames (interpolated).")
    return AnalysisResult(
        mode="after_set", status=res["status"],
        guard=dict(status=g.status, reason=g.reason, diagnostics=diag),
        video=dict(n_frames=T, fps=fps, width=W or None, height=H or None, duration_s=T / fps,
                   source_sha256=track.source_sha256),
        versions=comp.versions, reps=reps,
        summary=dict(n_reps=len(reps),
                     n_reps_with_attention=sum(any(i["severity"] == "attention" for i in r["indicators"])
                                               for r in reps),
                     mean_rep_duration_s=float(np.mean(durs)) if durs else None),
        warnings=warnings, timings_ms={k: round(v_, 1) for k, v_ in timings.items()})
