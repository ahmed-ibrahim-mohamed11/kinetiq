#!/usr/bin/env python3
"""
Phase 2.7 diagnostics (Problems 2, 3, 7): computed on TRAIN+VAL only (TEST untouched).

Per pipeline x video:
  3D validity proxies (Problem 7 - honest proxies, NOT ground-truth accuracy):
    bone_cv        median CV of upper-body bone lengths (sho-elb, elb-wri, pelvis-thorax) over time
    lr_bone_asym   median |L-R| / mean bone length (upper arm + forearm)
    accel_p99      99th pct of joint acceleration (torso-lengths/frame^2, elbows+wrists)
    accel_outliers fraction of frames with acceleration > 5x video median
    torso_jitter   median |second diff| of the pelvis->thorax unit vector
    depth_rom_ratio z-range / xy-range of the elbows (depth plausibility, rear/side views)
  2D / tracking link (Problems 2-3): nan_frac + jumps from tracking_log, conf stats per view.
Writes ai/outputs/phase27/diagnostics.csv and prints per-view medians.
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, REPO_ROOT)
from ai.core.config import (L_ELB, L_SHO, L_WRI, PELVIS, R_ELB, R_SHO, R_WRI, THORAX)  # noqa: E402

POSES = os.path.join(REPO_ROOT, "ai", "outputs", "pose_bench", "poses")
ANN = os.path.join(REPO_ROOT, "ai", "data", "mendeley_rep_annotations_expanded.csv")
SPLIT = os.path.join(REPO_ROOT, "ai", "data", "mendeley_subject_split.csv")
TRACK = os.path.join(REPO_ROOT, "ai", "outputs", "pose_bench", "tracking_log.csv")
PIPELINES = ["motionbert_lite", "motionbert", "poseformerv2"]
BONES = [(L_SHO, L_ELB), (R_SHO, R_ELB), (L_ELB, L_WRI), (R_ELB, R_WRI), (PELVIS, THORAX)]


def video_proxies(P: np.ndarray) -> dict:
    torso = np.linalg.norm(P[:, THORAX] - P[:, PELVIS], axis=1)
    tl = max(float(np.median(torso)), 1e-6)
    out = {}
    cvs, lens = [], {}
    for a, b in BONES:
        L = np.linalg.norm(P[:, a] - P[:, b], axis=1)
        cvs.append(float(np.std(L) / max(np.mean(L), 1e-6)))
        lens[(a, b)] = float(np.mean(L))
    out["bone_cv"] = float(np.median(cvs))
    ua = abs(lens[(L_SHO, L_ELB)] - lens[(R_SHO, R_ELB)]) / max((lens[(L_SHO, L_ELB)] + lens[(R_SHO, R_ELB)]) / 2, 1e-6)
    fa = abs(lens[(L_ELB, L_WRI)] - lens[(R_ELB, R_WRI)]) / max((lens[(L_ELB, L_WRI)] + lens[(R_ELB, R_WRI)]) / 2, 1e-6)
    out["lr_bone_asym"] = float((ua + fa) / 2)
    acc = np.diff(P[:, [L_ELB, R_ELB, L_WRI, R_WRI]] / tl, n=2, axis=0)
    mag = np.linalg.norm(acc, axis=-1)
    out["accel_p99"] = float(np.percentile(mag, 99))
    out["accel_outliers"] = float((mag > 5 * np.median(mag)).mean())
    u = (P[:, THORAX] - P[:, PELVIS])
    u = u / (np.linalg.norm(u, axis=1, keepdims=True) + 1e-9)
    out["torso_jitter"] = float(np.median(np.linalg.norm(np.diff(u, n=2, axis=0), axis=1)))
    e = P[:, [L_ELB, R_ELB]] / tl
    z_rom = float(np.percentile(e[..., 2], 95) - np.percentile(e[..., 2], 5))
    xy_rom = float(np.percentile(np.linalg.norm(e[..., :2], axis=-1), 95) -
                   np.percentile(np.linalg.norm(e[..., :2], axis=-1), 5))
    out["depth_rom_ratio"] = z_rom / max(xy_rom, 1e-6)
    return out


def main():
    ann = pd.read_csv(ANN).groupby("video_id").first()[["subject_id", "exercise", "camera_view", "form_label"]]
    split = pd.read_csv(SPLIT).set_index("subject_id").split
    track = pd.read_csv(TRACK).set_index("video_id")
    rows = []
    for p in PIPELINES:
        for vid, meta in ann.iterrows():
            if split[meta.subject_id] == "test":
                continue                       # TEST untouched during development
            f = os.path.join(POSES, p, vid + ".npy")
            if not os.path.exists(f):
                continue
            d = video_proxies(np.load(f))
            tr = track.loc[vid] if vid in track.index else None
            rows.append(dict(pipeline=p, video_id=vid, split=split[meta.subject_id], **meta.to_dict(), **d,
                             nan_frac=float(tr.nan_frac) if tr is not None else np.nan,
                             jumps=int(tr.jumps) if tr is not None else -1))
    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(REPO_ROOT, "ai", "outputs", "phase27", "diagnostics.csv"), index=False)
    print("=== 3D proxies by pipeline (TRAIN+VAL medians) ===")
    print(df.groupby("pipeline")[["bone_cv", "lr_bone_asym", "accel_p99", "accel_outliers",
                                  "torso_jitter", "depth_rom_ratio"]].median().round(4).to_string())
    print("\n=== by view (motionbert_lite) ===")
    print(df[df.pipeline == "motionbert_lite"].groupby("camera_view")
          [["bone_cv", "accel_outliers", "torso_jitter", "nan_frac", "jumps"]].median().round(4).to_string())


if __name__ == "__main__":
    main()
