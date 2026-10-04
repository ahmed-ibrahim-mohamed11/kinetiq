#!/usr/bin/env python3
"""Phase 2.5C pose-quality proxies per pipeline, computed on the SAME videos for all pipelines.

Per video (then aggregated): positions are pelvis-relative, divided by the video-median torso
length, so units are comparable across pipelines/athletes.
  jitter_<joint>   median ||second temporal difference|| (torso-lengths): elbows, wrists, shoulders
  depth_jitter     same but z-component only (depth stability; MediaPipe z included, documented)
  lr_corr_elbow    Pearson r of the left vs right elbow BACKWARD (z) signals - meaningful on
                   bilateral back videos only; single-arm rows reduce it legitimately (caveat)
  lowconf_frac     fraction of frames with conf < 0.3 on elbows/wrists
Writes ai/outputs/pose_bench/stability.csv (one row per pipeline x video).
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, REPO_ROOT)

from ai.core.config import L_ELB, L_SHO, L_WRI, PELVIS, R_ELB, R_SHO, R_WRI, THORAX  # noqa: E402

POSES = os.path.join(REPO_ROOT, "ai", "outputs", "pose_bench", "poses")
ANN = os.path.join(REPO_ROOT, "ai", "data", "mendeley_rep_annotations.csv")


def video_stats(pose: np.ndarray, conf: np.ndarray) -> dict:
    pelvis = pose[:, PELVIS:PELVIS + 1]
    torso = np.linalg.norm(pose[:, THORAX] - pose[:, PELVIS], axis=1)
    tl = max(float(np.median(torso)), 1e-6)
    rel = (pose - pelvis) / tl
    out = {}
    for name, (jl, jr) in (("elbow", (L_ELB, R_ELB)), ("wrist", (L_WRI, R_WRI)),
                           ("shoulder", (L_SHO, R_SHO))):
        acc = np.diff(rel[:, [jl, jr]], n=2, axis=0)
        out[f"jitter_{name}"] = float(np.median(np.linalg.norm(acc, axis=-1)))
        out[f"depth_jitter_{name}"] = float(np.median(np.abs(acc[..., 2])))
    zl, zr = rel[:, L_ELB, 2], rel[:, R_ELB, 2]
    if np.std(zl) > 1e-6 and np.std(zr) > 1e-6:
        out["lr_corr_elbow"] = float(np.corrcoef(zl, zr)[0, 1])
    else:
        out["lr_corr_elbow"] = np.nan
    out["lowconf_frac"] = float((conf[:, [L_ELB, R_ELB, L_WRI, R_WRI]] < 0.3).mean())
    return out


def main():
    ann = pd.read_csv(ANN)
    meta = ann.groupby("video_id").first()[["subject_id", "exercise", "camera_view", "form_label"]]
    rows = []
    for pipeline in sorted(os.listdir(POSES)):
        pdir = os.path.join(POSES, pipeline)
        if not os.path.isdir(pdir):
            continue
        for vid in meta.index:
            p = os.path.join(pdir, vid + ".npy")
            if not os.path.exists(p):
                continue
            pose = np.load(p)
            conf = np.load(os.path.join(pdir, vid + "_conf.npy"))
            rows.append(dict(pipeline=pipeline, video_id=vid, **meta.loc[vid].to_dict(),
                             **video_stats(pose, conf)))
    df = pd.DataFrame(rows)
    out = os.path.join(REPO_ROOT, "ai", "outputs", "pose_bench", "stability.csv")
    df.to_csv(out, index=False)
    print(df.groupby("pipeline")[["jitter_elbow", "jitter_wrist", "jitter_shoulder",
                                  "lr_corr_elbow", "lowconf_frac"]].median().round(4).to_string())
    print("->", out)


if __name__ == "__main__":
    main()
