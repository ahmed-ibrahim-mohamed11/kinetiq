#!/usr/bin/env python3
"""P2.6 (docs/EXPERIMENT_REGISTRY.md): real 3D ground-truth accuracy (MPJPE/PA-MPJPE) and
real multi-view orientation check for MotionBERT-Lite, using CMU Panoptic Studio sample sequence
`171204_pose1_sample` (free, no account, research-only license, CMU-Perceptual-Computing-Lab/
panoptic-toolbox README — verified 2026-10-09, see docs/LICENSING.md). This is the project's
FIRST comparison against independently-captured 3D ground truth (prior evidence was proxy
metrics only — bone-length CV, jitter — never true 3D accuracy; RISK_REGISTER R-11).

Three HD camera views downloaded (of 31 available), chosen by azimuth around the subject at a
representative frame (computed below, not guessed): cam 30 (~25 deg from one reference
direction = roughly FRONT), cam 15 (~74 deg = roughly SIDE), cam 0 (~166 deg = roughly BACK/REAR)
— giving a real front/side/rear spread of the SAME motion, independently reconstructed by our
frozen pipeline (YOLO11n-pose top-8 -> tracker -> MotionBERT-Lite) each time. This directly tests
whether MotionBERT-Lite's OWN reconstruction is consistent across real camera placements — the
thing P2.1's SYNTHETIC yaw rotation could not test (it rotates one already-reconstructed pose,
see perturb.py's docstring caveat).

Ground truth: joints19 (COCO19) -> H36M-17 subset mapping (11 joints in common; no 'spine'/'head'
equivalent in joints19, so those two H36M joints are excluded from the comparison, not
fabricated). Panoptic world units are centimeters; our pipeline outputs an uncalibrated root-
relative scale, so MPJPE is reported BOTH with and without a fitted Procrustes scale (the
standard PA-MPJPE protocol for monocular lifters, and a plain translation-only MPJPE for
transparency about just how off the raw scale is).
"""
from __future__ import annotations

import json
import os
import sys

import numpy as np
import pandas as pd

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.join(REPO_ROOT, "ai", "research", "pose_benchmark"))

from ai.core.features import prepare_video  # noqa: E402
from lifters import MotionBertLiteLifter  # noqa: E402
from tracking import track_subject  # noqa: E402

DATA = os.path.join(REPO_ROOT, "ai", "data", "panoptic", "171204_pose1_sample")
OUT = os.path.join(REPO_ROOT, "ai", "outputs", "phase2")
CAMS = {"front": 30, "side": 15, "back": 0}
N_FRAMES = 101  # 00000000..00000100, confirmed on disk

# joints19 (COCO19, Panoptic) -> name, VERIFIED order (leehsiu/PanopticStudioTools README,
# cross-checked against the official CMU-Perceptual-Computing-Lab/panoptic-toolbox file layout)
J19 = ["Neck", "Nose", "BodyCenter", "lShoulder", "lElbow", "lWrist", "lHip", "lKnee", "lAnkle",
      "rShoulder", "rElbow", "rWrist", "rHip", "rKnee", "rAnkle", "rEye", "lEye", "rEar", "lEar"]
# H36M-17 index -> joints19 name, for every H36M joint THAT HAS a direct joints19 equivalent.
# Excluded (no direct equivalent, not fabricated): 7 spine, 10 head.
H36M_FROM_J19 = {0: "BodyCenter", 1: "rHip", 2: "rKnee", 3: "rAnkle", 4: "lHip", 5: "lKnee",
                6: "lAnkle", 9: "Nose", 11: "lShoulder", 12: "lElbow", 13: "lWrist",
                14: "rShoulder", 15: "rElbow", 16: "rWrist"}


def load_gt(frame: int) -> np.ndarray | None:
    p = os.path.join(DATA, "hdPose3d_stage1_coco19", f"body3DScene_{frame:08d}.json")
    d = json.load(open(p))
    if not d["bodies"]:
        return None
    j = np.array(d["bodies"][0]["joints19"], dtype=float).reshape(19, 4)[:, :3]
    return j  # (19,3), cm, Panoptic world frame


def gt_to_h36m_subset(j19: np.ndarray):
    idx = sorted(H36M_FROM_J19)
    pts = np.stack([j19[J19.index(H36M_FROM_J19[i])] for i in idx])
    return idx, pts  # (11,) h36m indices, (11,3) cm


def gt_to_h36m_full(j19: np.ndarray) -> np.ndarray:
    """Full (17,3) H36M array from joints19, deriving the 2 joints with no direct equivalent
    (spine, thorax) the SAME way ai.core.pose.coco2h36m derives them from COCO (midpoints) —
    so ai.core.features.prepare_video can run on GT exactly as it runs on a prediction."""
    g = lambda name: j19[J19.index(name)]
    out = np.zeros((17, 3))
    out[0] = g("BodyCenter")                      # pelvis
    out[1], out[2], out[3] = g("rHip"), g("rKnee"), g("rAnkle")
    out[4], out[5], out[6] = g("lHip"), g("lKnee"), g("lAnkle")
    out[8] = (g("lShoulder") + g("rShoulder")) / 2  # thorax = mid-shoulder (derived, not fabricated GT)
    out[7] = (out[0] + out[8]) / 2                  # spine = mid pelvis-thorax
    out[9] = g("Nose")
    out[10] = g("Nose")                             # no head-top in joints19; reuse Nose (documented)
    out[11], out[12], out[13] = g("lShoulder"), g("lElbow"), g("lWrist")
    out[14], out[15], out[16] = g("rShoulder"), g("rElbow"), g("rWrist")
    return out


def extract_2d(video_path: str) -> tuple[np.ndarray, np.ndarray, tuple]:
    """Top-8 YOLO candidates per frame + frame size, same recipe as phase28/seated_row_transfer.py."""
    from ultralytics import YOLO
    import cv2
    model = YOLO(os.path.join(REPO_ROOT, "yolo11n-pose.pt"))
    cap = cv2.VideoCapture(video_path)
    wh = (cap.get(cv2.CAP_PROP_FRAME_WIDTH), cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()
    kt, bt = [], []
    for r in model.predict(source=video_path, stream=True, device="cpu", verbose=False):
        k = np.full((8, 17, 3), np.nan, dtype=np.float32)
        b = np.full((8, 5), np.nan, dtype=np.float32)
        if r.boxes is not None and len(r.boxes) > 0 and r.keypoints is not None:
            conf = r.boxes.conf.cpu().numpy()
            xywh = r.boxes.xywh.cpu().numpy()
            order = np.argsort(-(xywh[:, 2] * xywh[:, 3]))[:8]
            for s, i in enumerate(order):
                k[s] = r.keypoints.data[int(i)].cpu().numpy()
                b[s] = [*xywh[int(i)], conf[int(i)]]
        kt.append(k)
        bt.append(b)
    return np.stack(kt), np.stack(bt), wh


def procrustes_align(pred: np.ndarray, gt: np.ndarray):
    """Kabsch/Procrustes: find scale s, rotation R, translation t minimizing ||s*R@pred+t - gt||.
    pred, gt: (N,3) ALREADY root-centered (translation handled by re-centering, not by t here for
    clarity) point sets, same point correspondence. Returns aligned pred and the per-joint error."""
    mu_p, mu_g = pred.mean(0), gt.mean(0)
    p0, g0 = pred - mu_p, gt - mu_g
    U, S, Vt = np.linalg.svd(p0.T @ g0)
    d = np.sign(np.linalg.det(Vt.T @ U.T))
    D = np.diag([1, 1, d])
    R = Vt.T @ D @ U.T
    s = (S * np.diag(D)).sum() / (p0 ** 2).sum()
    aligned = s * (R @ p0.T).T + mu_g
    return aligned, s, R


def mpjpe(pred: np.ndarray, gt: np.ndarray) -> float:
    return float(np.mean(np.linalg.norm(pred - gt, axis=-1)))


def backward_signal(pose17: np.ndarray, conf17: np.ndarray | None, fps: float) -> np.ndarray:
    """mean L/R elbow-vs-pelvis 'backward' component from ai.core.features.prepare_video — the
    exact feature the production pipeline's depth-sign convention (FACING_IN_CAMERA) resolves."""
    v = prepare_video("x", pose17, fps, conf=conf17)
    return np.mean([v["arm"]["L"][:, 2], v["arm"]["R"][:, 2]], axis=0)


def main():
    os.makedirs(OUT, exist_ok=True)
    lifter = MotionBertLiteLifter()
    rows, sign_rows = [], []
    for view, cam in CAMS.items():
        vid = os.path.join(DATA, "hd_videos", f"hd_00_{cam:02d}.mp4")
        print(f"=== {view} (cam {cam}) ===")
        kt, bt, wh = extract_2d(vid)
        coco, tlog = track_subject(kt, bt, wh)
        pose3d, conf = lifter.lift(coco, wh)      # (T,17,3) H36M, pelvis-zeroed, root-relative
        print(f"  tracking: lost={tlog['lost']} jumps={tlog['jumps']} T={len(pose3d)}")
        T = min(N_FRAMES, len(pose3d))
        gt_full = np.stack([gt_to_h36m_full(load_gt(f)) for f in range(T)])   # (T,17,3) cm
        pred_sig = backward_signal(pose3d[:T], conf[:T], 29.97)
        gt_sig = backward_signal(gt_full, None, 29.97)
        r = float(np.corrcoef(pred_sig, gt_sig)[0, 1])
        sign_rows.append(dict(view=view, cam=cam, backward_signal_corr=r))
        print(f"  backward-signal correlation (pred vs GT, same sign convention) = {r:+.3f}")
        for frame in range(T):
            j19 = load_gt(frame)
            if j19 is None:
                continue
            h36m_idx, gt_pts = gt_to_h36m_subset(j19)
            pred_pts = pose3d[frame][h36m_idx]
            gt_c = gt_pts - gt_pts[list(h36m_idx).index(0)]           # root-center on pelvis(=0)
            pred_c = pred_pts - pred_pts[list(h36m_idx).index(0)]
            raw_mpjpe_cm = mpjpe(pred_c, gt_c)                        # no scale fit - exposes raw scale mismatch
            aligned, s, R = procrustes_align(pred_c, gt_c)
            pa_mpjpe_cm = mpjpe(aligned, gt_c)
            rows.append(dict(view=view, cam=cam, frame=frame, raw_mpjpe_cm=raw_mpjpe_cm,
                             pa_mpjpe_cm=pa_mpjpe_cm, fitted_scale=s))
    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(OUT, "panoptic_mpjpe_per_frame.csv"), index=False)
    print("\n=== pooled (median over frames; MPJPE in cm) ===")
    g = df.groupby("view")[["raw_mpjpe_cm", "pa_mpjpe_cm", "fitted_scale"]].median()
    print(g.to_string())
    g.to_csv(os.path.join(OUT, "panoptic_mpjpe_summary.csv"))
    pd.DataFrame(sign_rows).to_csv(os.path.join(OUT, "panoptic_sign_consistency.csv"), index=False)


if __name__ == "__main__":
    main()
