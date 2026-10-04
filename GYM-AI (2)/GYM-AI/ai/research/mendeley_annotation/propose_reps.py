#!/usr/bin/env python3
"""
Phase 2.5B: propose DRAFT rep boundaries (START/PULL/END) for the 34 benchmark videos, to be
visually verified and corrected before anything becomes ground truth.

Signal (annotation aid only — never a model): per arm, wrist height above the hip line in image
coordinates, normalised by shoulder-hip (torso) length:   s_arm(t) = (hip_y - wrist_y) / torso_len
At the bottom of a row the wrist hangs far below the hips (s low); at peak contraction the wrist
comes up toward the torso (s high). Works in side/diagonal/front because portrait phone video keeps
the vertical axis meaningful. The working arm is chosen by variance (single-arm rows); both-arm mean
is used when both move (bilateral rows).

PULL candidates  = peaks of the smoothed signal (min separation 0.8 s, adaptive prominence)
START candidate  = last local minimum (or low plateau) before the peak
END candidate    = first local minimum after the peak
Candidates are written to drafts/{video_id}.csv (0-BASED frame indices) plus one verification
image per video: signal plot + a thumbnail strip at every candidate PULL frame.

Honesty note for the record: the aid uses YOLO11n-pose wrists. Final annotations are human/visually
verified against RAW FRAMES, so they are not a pure echo of the pose model; the residual circularity
risk is documented in docs/DATASET_MENDELEY.md and the phase report.
"""
from __future__ import annotations

import argparse
import os
import sys

import cv2
import numpy as np
import pandas as pd
from scipy.signal import butter, find_peaks, sosfiltfilt

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, REPO_ROOT)
import matplotlib  # noqa: E402
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

SUBSET = os.path.join(REPO_ROOT, "ai", "data", "mendeley_annotation_subset_PROPOSED.csv")
DATA_ROOT = os.path.join(REPO_ROOT, "ai", "data", "mendeley")
CACHE = os.path.join(REPO_ROOT, "ai", "outputs", "mendeley_2d")
DRAFTS = os.path.join(REPO_ROOT, "ai", "outputs", "mendeley_2d", "drafts")

L_SHO, R_SHO, L_WRI, R_WRI, L_HIP, R_HIP = 5, 6, 9, 10, 11, 12
FPS = 15.0


def tracked_kpts(d) -> tuple[np.ndarray, int]:
    """Largest-box person selection over the cached top-3 candidates.

    The Phase 2.5B audit showed the baseline 'highest-conf box' rule switches to background
    people (up to 110 jumps/video), and nearest-to-previous tracking can lock onto a static
    bystander after one bad hop (seen on subject_005_back_good_side). In THIS dataset the
    exercising subject is always the large foreground person, so the ANNOTATION AID simply
    takes the largest box per frame. Verified visually per video via the draft images.
    Returns (T,17,3) keypoints and the number of frames where largest != conf-best."""
    kt, bt = d["kpts_top"], d["boxes"]           # (T,3,17,3), (T,3,5) cx,cy,w,h,conf
    T = len(kt)
    out = np.full((T, 17, 3), np.nan, dtype=np.float32)
    disagree = 0
    for t in range(T):
        areas = bt[t, :, 2] * bt[t, :, 3]
        if not np.isfinite(areas).any():
            continue
        j = int(np.nanargmax(areas))
        out[t] = kt[t, j]
        if j != 0:
            disagree += 1
    return out, disagree


def interp_nan(x: np.ndarray) -> np.ndarray:
    x = x.astype(float).copy()
    ok = np.isfinite(x)
    if ok.sum() < 2:
        return np.zeros_like(x)
    idx = np.arange(len(x))
    x[~ok] = np.interp(idx[~ok], idx[ok], x[ok])
    return x


def smooth(x: np.ndarray, hz: float = 2.0) -> np.ndarray:
    if len(x) < 30:
        return x
    sos = butter(2, min(hz / (FPS / 2.0), 0.9), output="sos")
    return sosfiltfilt(sos, x)


def build_signal(k: np.ndarray):
    """k: (T,17,3) COCO. Returns smoothed s(t) and the per-arm smoothed signals."""
    conf = k[:, :, 2]
    def joint_y(j):
        y = k[:, j, 1].copy()
        y[conf[:, j] < 0.2] = np.nan
        return interp_nan(y)
    hip_y = 0.5 * (joint_y(L_HIP) + joint_y(R_HIP))
    sho_y = 0.5 * (joint_y(L_SHO) + joint_y(R_SHO))
    torso = np.abs(hip_y - sho_y)
    torso_len = max(float(np.nanmedian(torso)), 1e-3)
    arms = {}
    for name, j in (("L", L_WRI), ("R", R_WRI)):
        arms[name] = smooth((hip_y - joint_y(j)) / torso_len)
    var = {a: float(np.var(v)) for a, v in arms.items()}
    hi, lo = max(var, key=var.get), min(var, key=var.get)
    # single-arm row: one wrist barely moves -> use the working arm; bilateral: use the mean
    s = arms[hi] if var[hi] > 4.0 * var[lo] else 0.5 * (arms["L"] + arms["R"])
    return s, arms, var


def propose(s: np.ndarray):
    rng = float(np.percentile(s, 95) - np.percentile(s, 5))
    pk, _ = find_peaks(s, distance=int(0.8 * FPS), prominence=0.25 * rng)
    valleys, _ = find_peaks(-s, distance=int(0.4 * FPS))
    reps = []
    for p in pk:
        before = valleys[valleys < p]
        after = valleys[valleys > p]
        start = int(before[-1]) if len(before) else max(0, p - int(1.5 * FPS))
        end = int(after[0]) if len(after) else min(len(s) - 1, p + int(1.5 * FPS))
        reps.append((start, int(p), end))
    return reps


def verification_image(video_path: str, s, arms, reps, out_png: str, title: str):
    cap = cv2.VideoCapture(video_path)
    thumbs = []
    for (st, p, en) in reps:
        cap.set(cv2.CAP_PROP_POS_FRAMES, p)
        ok, fr = cap.read()
        if not ok:
            continue
        h, w = fr.shape[:2]
        t = cv2.resize(fr, (int(w * 180 / h), 180))
        cv2.rectangle(t, (0, 0), (60, 18), (0, 0, 0), -1)
        cv2.putText(t, f"P{p}", (2, 14), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 255), 1)
        thumbs.append(cv2.cvtColor(t, cv2.COLOR_BGR2RGB))
    cap.release()
    fig = plt.figure(figsize=(16, 7))
    ax = fig.add_subplot(2, 1, 1)
    t = np.arange(len(s))
    ax.plot(t, s, "k", lw=1.8, label="signal (chosen)")
    for a, v in arms.items():
        ax.plot(t, v, lw=0.8, alpha=0.6, label=f"arm {a}")
    for (st, p, en) in reps:
        ax.axvline(p, color="r", lw=1)
        ax.axvline(st, color="g", ls=":", lw=1)
        ax.axvline(en, color="b", ls="--", lw=0.8)
    ax.set_title(f"{title} - {len(reps)} draft reps (green:start red:pull blue:end)")
    ax.legend(fontsize=7, ncol=4)
    ax.set_xlabel("frame (15 fps)")
    if thumbs:
        hmax = max(th.shape[0] for th in thumbs)
        strip = np.hstack([np.pad(th, ((0, hmax - th.shape[0]), (0, 0), (0, 0))) for th in thumbs])
        ax2 = fig.add_subplot(2, 1, 2)
        ax2.imshow(strip)
        ax2.axis("off")
        ax2.set_title("frame at each draft PULL", fontsize=9)
    fig.tight_layout()
    fig.savefig(out_png, dpi=90)
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default=None, help="comma-separated video_ids")
    args = ap.parse_args()
    os.makedirs(DRAFTS, exist_ok=True)
    sub = pd.read_csv(SUBSET)
    if args.only:
        sub = sub[sub.video_id.isin(args.only.split(","))]
    for _, row in sub.iterrows():
        npz = os.path.join(CACHE, row.video_id + ".npz")
        if not os.path.exists(npz):
            print(f"{row.video_id}: no 2D cache yet, skip")
            continue
        d = np.load(npz)
        k, disagree = tracked_kpts(d)
        s, arms, var = build_signal(k)
        reps = propose(s)
        pd.DataFrame(reps, columns=["start_frame", "pull_frame", "end_frame"]).to_csv(
            os.path.join(DRAFTS, row.video_id + ".csv"), index=False)
        verification_image(os.path.join(DATA_ROOT, row.video_path), s, arms, reps,
                           os.path.join(DRAFTS, row.video_id + ".png"),
                           f"{row.video_id} (arm var L={var['L']:.3f} R={var['R']:.3f}, track!=conf {disagree}f)")
        print(f"{row.video_id}: {len(reps)} draft reps (tracking disagreed with conf-best on {disagree} frames)")
    print("drafts in", DRAFTS)


if __name__ == "__main__":
    main()
