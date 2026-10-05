#!/usr/bin/env python3
"""Phase 2.5C: run one pose pipeline over all benchmark videos.
Writes ai/outputs/pose_bench/poses/{pipeline}/{video}.npy (T,17,3) + {video}_conf.npy (T,17)
and deployment.json (model size, load time, per-video lift seconds, fps, peak RSS delta).

  .venv\\Scripts\\python.exe ai\\research\\pose_benchmark\\run_lifting.py --pipeline motionbert
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

import numpy as np
import pandas as pd
import psutil

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

TRACKED = os.path.join(REPO_ROOT, "ai", "outputs", "pose_bench", "tracked2d")
OUT = os.path.join(REPO_ROOT, "ai", "outputs", "pose_bench", "poses")
ANN = os.environ.get("PB_ANNOTATIONS", os.path.join(REPO_ROOT, "ai", "data", "mendeley_rep_annotations.csv"))
MAN = os.path.join(REPO_ROOT, "ai", "data", "mendeley_manifest.csv")
DATA_ROOT = os.path.join(REPO_ROOT, "ai", "data", "mendeley")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pipeline", required=True)
    args = ap.parse_args()
    from lifters import LIFTERS
    proc = psutil.Process()
    rss0 = proc.memory_info().rss

    t0 = time.time()
    lifter = LIFTERS[args.pipeline]()
    load_s = time.time() - t0

    vids = sorted(pd.read_csv(ANN).video_id.unique())
    man = pd.read_csv(MAN).set_index("video_id")
    odir = os.path.join(OUT, args.pipeline)
    os.makedirs(odir, exist_ok=True)
    per_video, peak_rss = {}, rss0
    for v in vids:
        if os.path.exists(os.path.join(odir, v + ".npy")):
            print(f"{v}: exists, skip")
            continue
        wh = (float(man.loc[v, "width"]), float(man.loc[v, "height"]))
        t1 = time.time()
        if args.pipeline == "mediapipe":
            pose3d, conf = lifter.lift_video(os.path.join(DATA_ROOT, man.loc[v, "video_path"]))
        else:
            coco = np.load(os.path.join(TRACKED, v + ".npz"))["kpts"]
            pose3d, conf = lifter.lift(coco, wh)
        dt = time.time() - t1
        np.save(os.path.join(odir, v + ".npy"), pose3d)
        np.save(os.path.join(odir, v + "_conf.npy"), conf)
        per_video[v] = dict(seconds=round(dt, 2), frames=int(len(pose3d)),
                            fps=round(len(pose3d) / dt, 1))
        peak_rss = max(peak_rss, proc.memory_info().rss)
        print(f"{v}: {len(pose3d)}f in {dt:.1f}s ({len(pose3d)/dt:.1f} fps)")

    total_f = sum(d["frames"] for d in per_video.values())
    total_s = sum(d["seconds"] for d in per_video.values())
    dep = dict(pipeline=args.pipeline, model_size_mb=round(lifter.model_size_mb(), 1),
               load_seconds=round(load_s, 2), total_frames=total_f,
               total_seconds=round(total_s, 1), fps=round(total_f / total_s, 1),
               ms_per_frame=round(1000 * total_s / total_f, 2),
               peak_rss_delta_mb=round((peak_rss - rss0) / 1e6, 1),
               vram_mb=0.0, device="cpu", per_video=per_video)
    json.dump(dep, open(os.path.join(odir, "deployment.json"), "w"), indent=1)
    print(f"\n{args.pipeline}: {dep['fps']} fps lifting, {dep['model_size_mb']} MB model, "
          f"peak RSS delta {dep['peak_rss_delta_mb']} MB")


if __name__ == "__main__":
    main()
