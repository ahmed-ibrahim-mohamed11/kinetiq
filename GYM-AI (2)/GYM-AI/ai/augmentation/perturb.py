"""Synthetic / augmented data generators (Phase 1).

Every function here perturbs ALREADY-LICENSED, already-extracted data (cached (T,17,3) H36M 3D
poses + (T,17) 2D confidence from the frozen pipeline, plus frame-indexed rep GT) — nothing here
downloads, scrapes, or renders new source footage. Every output carries `synthetic=True` in its
metadata and must never be presented as real-world evidence (owner rule). Two call styles:

  - low-level array transforms (`time_warp`, `joint_dropout_conf`, `confidence_noise`,
    `frame_drop`, `fps_downsample`, `positional_jitter`, `yaw_rotate`) — pure functions, no GT.
  - `make_*_transform(...)` factories returning a `(name, pose, conf, fps, gtc, gtu) -> (...)`
    callable, the exact signature `ai/evaluation/harness.py::run_pose_suite(..., transform=...)`
    expects — so a robustness suite is just `run_pose_suite(system, items, make_time_warp_transform(0.7))`.

Design notes / honesty caveats (read before using for anything beyond a first robustness pass):
- **Joint dropout is modeled as confidence loss, not NaN coordinates.** The real pipeline
  (`ai/research/pose_benchmark/tracking.py`, `ai/core/pose.py::yolo_keypoints`) only produces NaN
  coordinates when YOLO detects NO person at all (then the WHOLE frame is interpolated,
  `ai/core/features.py::prepare_video`'s `finite` mask is frame-level, all-17-joints). A single
  occluded joint (e.g. wrist behind the body) still gets a YOLO coordinate — just a low one — with
  low confidence; `ai/core/features.py`'s `valid[a]` mask is exactly how the real system reacts to
  that. So "wrist dropout" here zeroes confidence for the named joint(s) over a window, which
  exercises the REAL confidence-gating code path, not a code path that never fires on real data.
- **Resolution/compression is a keypoint-level PROXY** (`positional_jitter`), not a re-run of the
  2D detector on a resized/recompressed video. True resolution/compression robustness would need
  raw video access and re-running YOLO — a separate, more expensive Phase 3 experiment if the
  proxy here flags something worth escalating. Documented, not hidden.
- **Viewpoint rotation convention, empirically verified** (not assumed) on a real extracted pose
  (`ai/outputs/phase28/pose_motionbert_lite_correct_01.npy`, pelvis-zeroed H36M output): axis 0 is
  lateral (R-L shoulder vector mean [0.71, 0, 0]), axis 1 is vertical (thorax-pelvis mean
  [0, -1.08, 0], std 0.07 — the stable, large-magnitude axis), axis 2 is depth (small, noisier).
  `yaw_rotate` rotates the (lateral, depth) plane about the vertical axis — i.e. simulates the
  same motion filmed from a different camera azimuth. This is a rotation of an ESTIMATED
  monocular 3D pose (not metric mocap), so treat results as a qualitative robustness probe, not a
  claim about true 3D accuracy under a real camera move.
  **Important, found by testing this against the real decoder
  (`ai/tests/test_augmentation.py::test_small_yaw_rotation_is_a_decoder_noop_but_180_is_not`):
  `ai/core/features.py` derives its lateral/vertical axes from the pose itself (rotation-
  equivariant), but derives the SIGN of its backward/"pull" axis from a one-bit comparison
  against the fixed external constant `FACING_IN_CAMERA` (config.py). A small yaw rotation
  leaves that comparison's sign unchanged (no-op on decoder output); a large one (confirmed at
  180°) flips it, non-trivially changing which direction counts as "backward" and therefore the
  decoded reps. So `yaw_rotate` is a faithful sanity/robustness probe only for rotations that
  stay within that sign threshold — it is NOT a general view-augmentation technique, and in
  particular it does **not** approximate "what MotionBERT would have reconstructed from a side/
  rear camera": that weakness (Mendeley side F1 0.383) originates in the 2D→3D LIFTING stage's
  view-dependent reconstruction error, which happens upstream of this synthetic transform and
  this transform never touches.
- **Time-warp / fps-downsample rescale GT frame indices along with the signal** so matching still
  makes sense; `frame_drop` and `confidence_noise` don't change T and leave GT untouched.
"""
from __future__ import annotations

from typing import Callable, List, Optional, Tuple

import numpy as np

Transform = Callable[[str, np.ndarray, np.ndarray, float, List[dict], List[dict]],
                     Tuple[np.ndarray, np.ndarray, float, List[dict], List[dict]]]


def _rng(seed: Optional[int]) -> np.random.Generator:
    return np.random.default_rng(seed)


# ----------------------------------------------------------------------------- low-level transforms
def time_warp(pose: np.ndarray, conf: np.ndarray, speed: float) -> Tuple[np.ndarray, np.ndarray]:
    """Resample T by 1/speed via linear interpolation. speed > 1 -> faster/fewer frames (time-
    compressed, simulates a faster tempo); speed < 1 -> slower/more frames (paused/slow tempo,
    the weakness `EXTERNAL_REAL_WORLD_STRESS_TEST.md` found: decoded tempo 4.2 s vs 1.65 s train
    median). Confidence is interpolated the same way (soft, not thresholded)."""
    if speed <= 0:
        raise ValueError("speed must be > 0")
    T = len(pose)
    new_T = max(2, int(round(T / speed)))
    src = np.linspace(0, T - 1, new_T)
    idx0 = np.floor(src).astype(int)
    idx1 = np.minimum(idx0 + 1, T - 1)
    w = (src - idx0)[:, None, None]
    pose_w = pose[idx0] * (1 - w) + pose[idx1] * w
    wc = (src - idx0)[:, None]
    conf_w = conf[idx0] * (1 - wc) + conf[idx1] * wc
    return pose_w.astype(pose.dtype), conf_w.astype(conf.dtype)


def _warp_frame_index(f: int, T: int, new_T: int) -> int:
    return int(round(f * (new_T - 1) / max(T - 1, 1)))


def inconsistent_tempo(pose: np.ndarray, conf: np.ndarray, fps: float,
                       speed_lo: float = 0.7, speed_hi: float = 1.3,
                       cycle_s: float = 2.0) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Time-warp with a SINUSOIDALLY VARYING local speed (period `cycle_s` s of output time,
    oscillating between speed_lo and speed_hi) — distinct from `time_warp`'s single global
    factor, this simulates rep-to-rep tempo drift (some reps faster, some slower) within ONE
    video (Phase 3 tempo suite's "inconsistent tempo" case). Returns (pose, conf, src_time) —
    `src_time[i]` is the ORIGINAL frame index each output frame `i` was sampled from, needed to
    remap GT boundaries (see `make_inconsistent_tempo_transform`)."""
    T = len(pose)
    cycle_frames = max(2.0, cycle_s * fps)
    src_t, i = [0.0], 0
    while src_t[-1] < T - 1:
        phase = 2 * np.pi * i / cycle_frames
        speed = speed_lo + (speed_hi - speed_lo) * 0.5 * (1 - np.cos(phase))
        src_t.append(min(src_t[-1] + speed, T - 1))
        i += 1
    src = np.array(src_t)
    idx0 = np.floor(src).astype(int)
    idx1 = np.minimum(idx0 + 1, T - 1)
    w = (src - idx0)[:, None, None]
    pose_w = pose[idx0] * (1 - w) + pose[idx1] * w
    wc = (src - idx0)[:, None]
    conf_w = conf[idx0] * (1 - wc) + conf[idx1] * wc
    return pose_w.astype(pose.dtype), conf_w.astype(conf.dtype), src


def joint_dropout_conf(conf: np.ndarray, joints: List[int], frac: float,
                       rng: np.random.Generator) -> np.ndarray:
    """Zero confidence for `joints` over one contiguous window covering `frac` of T (simulates
    sustained occlusion, e.g. an arm swinging behind the torso), leaving coordinates untouched —
    matches how the real pipeline represents an occluded-but-estimated joint."""
    conf = conf.copy()
    T = len(conf)
    win = max(1, int(round(frac * T)))
    start = int(rng.integers(0, max(1, T - win + 1)))
    conf[start:start + win, joints] = 0.0
    return conf


def confidence_noise(conf: np.ndarray, sigma: float, rng: np.random.Generator) -> np.ndarray:
    """Additive Gaussian noise on the confidence channel, clipped to [0, 1] — a noisier detector."""
    return np.clip(conf + rng.normal(0, sigma, conf.shape), 0.0, 1.0).astype(conf.dtype)


def frame_drop(pose: np.ndarray, conf: np.ndarray, frac: float,
               rng: np.random.Generator) -> Tuple[np.ndarray, np.ndarray]:
    """Mark `frac` of frames as full detection failures (conf=0, position linearly interpolated
    from the nearest surviving frames) — the real pipeline's documented response to zero
    detections (`ai/core/pose.py::yolo_keypoints`, `pose_benchmark/lifters.py::interp_nan_2d`)."""
    T = len(pose)
    n_drop = int(round(frac * T))
    if n_drop == 0:
        return pose.copy(), conf.copy()
    drop = rng.choice(T, size=n_drop, replace=False)
    keep = np.ones(T, dtype=bool)
    keep[drop] = False
    if keep.sum() < 2:
        raise ValueError("frame_drop: frac too high, fewer than 2 frames would survive")
    pose_out, conf_out = pose.copy(), conf.copy()
    idx = np.arange(T)
    flat = pose_out.reshape(T, -1)
    for c in range(flat.shape[1]):
        flat[~keep, c] = np.interp(idx[~keep], idx[keep], flat[keep, c])
    conf_out[~keep] = 0.0
    return pose_out, conf_out


def insert_pauses(pose: np.ndarray, conf: np.ndarray, gtc: List[dict], gtu: List[dict],
                  fps: float, pause_s: float, at: str = "pull") -> Tuple[np.ndarray, np.ndarray, List[dict], List[dict]]:
    """Insert a `pause_s`-long hold (the frame at `at` duplicated `n=round(pause_s*fps)` times)
    at EVERY certain rep's `at` boundary. Unlike `time_warp` (uniform rescale of the whole
    signal), this targets the SPECIFIC real-world failure mode documented in
    `EXTERNAL_REAL_WORLD_STRESS_TEST.md` (squeeze/extension holds absorbed into decoded rep
    windows, IoU F1 0.632 vs 0.841) without uniformly slowing down rest periods too."""
    T = len(pose)
    n = int(round(pause_s * fps))
    if n <= 0 or not gtc:
        return pose.copy(), conf.copy(), [dict(r) for r in gtc], [dict(r) for r in gtu]
    inserts = sorted({r[at] for r in gtc})
    pose_parts, conf_parts = [], []
    cursor = 0
    offset_after = {}      # insertion frame -> cumulative inserted-frame count AFTER it
    cum = 0
    for ins in inserts:
        pose_parts.append(pose[cursor:ins + 1])
        conf_parts.append(conf[cursor:ins + 1])
        pose_parts.append(np.repeat(pose[ins:ins + 1], n, axis=0))
        conf_parts.append(np.repeat(conf[ins:ins + 1], n, axis=0))
        cursor = ins + 1
        cum += n
        offset_after[ins] = cum
    pose_parts.append(pose[cursor:T])
    conf_parts.append(conf[cursor:T])
    new_pose = np.concatenate(pose_parts, axis=0).astype(pose.dtype)
    new_conf = np.concatenate(conf_parts, axis=0).astype(conf.dtype)

    sorted_ins = inserts  # ascending

    def shift(frame: int) -> int:
        add = 0
        for ins in sorted_ins:
            if frame > ins:
                add = offset_after[ins]
            else:
                break
        return frame + add

    def shift_gt(gt):
        return [{k: (shift(v) if k in ("start", "pull", "end") else v) for k, v in r.items()} for r in gt]

    return new_pose, new_conf, shift_gt(gtc), shift_gt(gtu)


def fps_downsample(pose: np.ndarray, conf: np.ndarray, fps: float,
                   step: int) -> Tuple[np.ndarray, np.ndarray, float]:
    """Take every `step`-th frame (no interpolation) — matches
    `ai/research/realtime/causal_prototype.py`'s own fps-budget-mode subsampling."""
    if step < 1:
        raise ValueError("step must be >= 1")
    return pose[::step].copy(), conf[::step].copy(), fps / step


def positional_jitter(pose: np.ndarray, sigma_frac: float, rng: np.random.Generator,
                      torso_len: Optional[float] = None) -> np.ndarray:
    """Zero-mean 3D Gaussian noise scaled by the video's own torso length (median |thorax-pelvis|)
    — a RESOLUTION/COMPRESSION PROXY at the keypoint level (see module docstring caveat), not a
    literal pixel-degradation simulation."""
    if torso_len is None:
        torso_len = float(np.median(np.linalg.norm(pose[:, 8] - pose[:, 0], axis=-1)))
    return (pose + rng.normal(0, sigma_frac * torso_len, pose.shape)).astype(pose.dtype)


def yaw_rotate(pose: np.ndarray, yaw_deg: float) -> np.ndarray:
    """Rotate the (lateral, depth) = (axis 0, axis 2) plane about the vertical axis (axis 1) by
    `yaw_deg` — a rigid rotation of the 3D pose, axis convention empirically verified (module
    docstring). Bone-length-preserving by construction; `ai/tests/test_augmentation.py` checks
    this. Decoder-level effect is NOT uniform across angles — see the module docstring's
    "Important" note: small angles are a decoder no-op, large ones (confirmed at 180 deg) are
    not, because of how `ai/core/features.py` disambiguates its backward axis's sign."""
    th = np.deg2rad(yaw_deg)
    c, s = np.cos(th), np.sin(th)
    R = np.array([[c, 0.0, s], [0.0, 1.0, 0.0], [-s, 0.0, c]], dtype=pose.dtype)
    return pose @ R.T


# ----------------------------------------------------------------------------- harness-compatible factories
def _shift_gt(gt: List[dict], T: int, new_T: int) -> List[dict]:
    return [{k: (_warp_frame_index(v, T, new_T) if k in ("start", "pull", "end") else v)
            for k, v in r.items()} for r in gt]


def make_pause_insertion_transform(pause_s: float, at: str = "pull") -> Transform:
    def _t(name, pose, conf, fps, gtc, gtu):
        p, c, new_gtc, new_gtu = insert_pauses(pose, conf, gtc, gtu, fps, pause_s, at)
        return p, c, fps, new_gtc, new_gtu
    return _t


def make_time_warp_transform(speed: float) -> Transform:
    def _t(name, pose, conf, fps, gtc, gtu):
        T = len(pose)
        p, c = time_warp(pose, conf, speed)
        new_T = len(p)
        return p, c, fps, _shift_gt(gtc, T, new_T), _shift_gt(gtu, T, new_T)
    return _t


def make_inconsistent_tempo_transform(speed_lo: float = 0.7, speed_hi: float = 1.3,
                                      cycle_s: float = 2.0) -> Transform:
    def _t(name, pose, conf, fps, gtc, gtu):
        p, c, src = inconsistent_tempo(pose, conf, fps, speed_lo, speed_hi, cycle_s)
        remap = lambda f: int(np.searchsorted(src, f))          # original frame -> output frame
        shift = lambda gt: [{k: (remap(v) if k in ("start", "pull", "end") else v)
                             for k, v in r.items()} for r in gt]
        return p, c, fps, shift(gtc), shift(gtu)
    return _t


def make_joint_dropout_transform(joints: List[int], frac: float, seed: Optional[int] = None) -> Transform:
    def _t(name, pose, conf, fps, gtc, gtu):
        return pose, joint_dropout_conf(conf, joints, frac, _rng(seed)), fps, gtc, gtu
    return _t


def make_confidence_noise_transform(sigma: float, seed: Optional[int] = None) -> Transform:
    def _t(name, pose, conf, fps, gtc, gtu):
        return pose, confidence_noise(conf, sigma, _rng(seed)), fps, gtc, gtu
    return _t


def make_frame_drop_transform(frac: float, seed: Optional[int] = None) -> Transform:
    def _t(name, pose, conf, fps, gtc, gtu):
        p, c = frame_drop(pose, conf, frac, _rng(seed))
        return p, c, fps, gtc, gtu
    return _t


def make_fps_downsample_transform(step: int) -> Transform:
    def _t(name, pose, conf, fps, gtc, gtu):
        T = len(pose)
        p, c, f = fps_downsample(pose, conf, fps, step)
        new_T = len(p)
        return p, c, f, _shift_gt(gtc, T, new_T), _shift_gt(gtu, T, new_T)
    return _t


def make_positional_jitter_transform(sigma_frac: float, seed: Optional[int] = None) -> Transform:
    def _t(name, pose, conf, fps, gtc, gtu):
        return positional_jitter(pose, sigma_frac, _rng(seed)), conf, fps, gtc, gtu
    return _t


def make_yaw_rotation_transform(yaw_deg: float) -> Transform:
    def _t(name, pose, conf, fps, gtc, gtu):
        return yaw_rotate(pose, yaw_deg), conf, fps, gtc, gtu
    return _t


def compose(*transforms: Transform) -> Transform:
    """Chain transforms left to right, e.g. compose(make_time_warp_transform(0.7),
    make_confidence_noise_transform(0.1)) for a combined slow-tempo + noisy-confidence probe."""
    def _t(name, pose, conf, fps, gtc, gtu):
        for t in transforms:
            pose, conf, fps, gtc, gtu = t(name, pose, conf, fps, gtc, gtu)
        return pose, conf, fps, gtc, gtu
    return _t
