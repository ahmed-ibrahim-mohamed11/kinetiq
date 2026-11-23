#!/usr/bin/env python3
"""Rebuild the git-ignored MotionBERT-Lite 3D pose caches the evaluation suites read, from the
TRACKED 2D detector caches, with the production tracker + lifter (each proven equal to the
research code that originally produced these caches: ai/tests/test_tracking.py, test_lifting.py).

  python -m ai.scripts.rebuild_pose_caches [--sets mendeley internal external] [--device cpu]
                                          [--out-root DIR] [--force] [--compare]

  mendeley  ai/outputs/mendeley_2d/{vid}.npz            -> ai/outputs/pose_bench/poses/motionbert_lite/{vid}[_conf].npy
  internal  ai/outputs/phase28/2d_{name}.npz            -> ai/outputs/phase28/pose_motionbert_lite_{name}[_conf].npy
  external  ai/outputs/external_stress_test/2d_{v}.npz  -> ai/outputs/external_stress_test/pose_{v}[_conf].npy

CPU is the default and the reference: CUDA output differs numerically (bounded, test_lifting.py)
and would not reproduce the frozen numbers bit-for-bit. `--out-root DIR` writes the same relative
layout under DIR; `--compare` reports, per set, the max abs difference against the caches already
in this repository (clean-clone verification). Needs the MB-Lite checkpoint
(python -m ai.scripts.download_checkpoints).
"""
from __future__ import annotations

import argparse
import glob
import os
import sys
import time

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from ai.core.lifting import MotionBertLite  # noqa: E402
from ai.core.tracking import track_subject  # noqa: E402

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
OUT = os.path.join("ai", "outputs")
SR_VIDEOS = ["correct_01", "correct_02", "correct_03", "asymmetry_01", "asymmetry_02", "asymmetry_03"]


def _jobs(sets):
    """(set, name, 2D input path, img_wh loader, repo-relative output stem)."""
    jobs = []
    if "mendeley" in sets:
        man = pd.read_csv(os.path.join(REPO_ROOT, "ai", "data", "mendeley_manifest.csv")).set_index("video_id")
        vids = sorted(pd.read_csv(os.path.join(REPO_ROOT, "ai", "data",
                                               "mendeley_rep_annotations_expanded.csv")).video_id.unique())
        for v in vids:
            wh = (float(man.loc[v, "width"]), float(man.loc[v, "height"]))
            jobs.append(("mendeley", v, os.path.join(REPO_ROOT, OUT, "mendeley_2d", v + ".npz"),
                         ("kpts_top", "boxes", wh),
                         os.path.join(OUT, "pose_bench", "poses", "motionbert_lite", v)))
    if "internal" in sets:
        for n in SR_VIDEOS:
            jobs.append(("internal", n, os.path.join(REPO_ROOT, OUT, "phase28", f"2d_{n}.npz"),
                         ("kt", "bt", None), os.path.join(OUT, "phase28", f"pose_motionbert_lite_{n}")))
    if "external" in sets:
        for p in sorted(glob.glob(os.path.join(REPO_ROOT, OUT, "external_stress_test", "2d_*.npz"))):
            v = os.path.basename(p)[3:-4]
            jobs.append(("external", v, p, ("kt", "bt", None),
                         os.path.join(OUT, "external_stress_test", f"pose_{v}")))
    return jobs


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--sets", nargs="+", default=["mendeley", "internal", "external"],
                    choices=["mendeley", "internal", "external"])
    ap.add_argument("--device", choices=["cpu", "cuda"], default="cpu")
    ap.add_argument("--out-root", default=REPO_ROOT)
    ap.add_argument("--force", action="store_true", help="overwrite existing outputs")
    ap.add_argument("--compare", action="store_true",
                    help="compare each output with the cache at the same path in this repository")
    args = ap.parse_args(argv)
    jobs = _jobs(args.sets)
    missing = [j[2] for j in jobs if not os.path.exists(j[2])]
    if missing:
        print(f"ERROR: {len(missing)} tracked 2D caches missing, e.g. {missing[0]}", file=sys.stderr)
        return 2
    lifter = MotionBertLite(device=args.device)
    worst = {}
    t0 = time.time()
    for i, (s, name, src, (kkey, bkey, wh), stem) in enumerate(jobs, 1):
        out = os.path.join(args.out_root, stem)
        if os.path.exists(out + ".npy") and not args.force:
            pose, conf = np.load(out + ".npy"), np.load(out + "_conf.npy")
        else:
            z = np.load(src)
            img_wh = wh if wh is not None else tuple(float(x) for x in z["wh"])
            coco, _ = track_subject(z[kkey], z[bkey], img_wh)
            pose, conf = lifter.lift_video(coco, tta=True)
            os.makedirs(os.path.dirname(out), exist_ok=True)
            np.save(out + ".npy", pose.astype(np.float32, copy=False))
            np.save(out + "_conf.npy", conf.astype(np.float32, copy=False))
        if args.compare and os.path.abspath(args.out_root) != REPO_ROOT:
            ref = os.path.join(REPO_ROOT, stem)
            if os.path.exists(ref + ".npy"):
                rp, rc = np.load(ref + ".npy"), np.load(ref + "_conf.npy")
                d = (float(np.nanmax(np.abs(pose - rp))) if pose.shape == rp.shape else float("inf"),
                     float(np.nanmax(np.abs(conf - rc))) if conf.shape == rc.shape else float("inf"))
                worst[s] = tuple(max(a, b) for a, b in zip(worst.get(s, (0.0, 0.0)), d))
            else:
                worst.setdefault(s + " (no reference cache)", (float("nan"), float("nan")))
        if i % 20 == 0 or i == len(jobs):
            print(f"[{i}/{len(jobs)}] {s}/{name}  ({time.time() - t0:.0f} s)", flush=True)
    for s, (dp, dc) in worst.items():
        print(f"compare {s}: max |pose diff| {dp:.3g}, max |conf diff| {dc:.3g}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
