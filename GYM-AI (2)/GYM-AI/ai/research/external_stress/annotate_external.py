#!/usr/bin/env python3
"""
EXTERNAL_WILD_STRESS_TEST: draft GT boundaries for the quantitative units, verification images
for human review (same assisted-annotation standard as Phase 2.5B: signal drafts anchored by
frame-level human verification; GT is independent of the evaluated system's decoder).

Quantitative units (from the visual audit, docs/EXTERNAL_REAL_WORLD_STRESS_TEST.md):
  ext_01 full · ext_02 full · ext_03 seg 237-330 & 361-470 · ext_08 seg 160-250 & 310-435
Outputs: ai/outputs/external_stress_test/drafts/{unit}.csv + .png
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.join(REPO_ROOT, "ai", "research", "mendeley_annotation"))
sys.path.insert(0, os.path.join(REPO_ROOT, "ai", "research", "pose_benchmark"))

from propose_reps import build_signal_from_kpts, propose, verification_image  # noqa: E402
from tracking import track_subject  # noqa: E402
import propose_reps  # noqa: E402

OUT = os.path.join(REPO_ROOT, "ai", "outputs", "external_stress_test")
UNITS = [
    ("ext_01_full", "ext_01", 0, None),
    ("ext_02_full", "ext_02", 0, None),
    ("ext_03_segA", "ext_03", 237, 330),
    ("ext_03_segB", "ext_03", 361, 470),
    ("ext_08_segB", "ext_08", 160, 250),
    ("ext_08_segC", "ext_08", 310, 435),
]


def main():
    os.makedirs(os.path.join(OUT, "drafts"), exist_ok=True)
    propose_reps.FPS = 30.0          # external videos are ~30 fps (ext_03 is 25; handled below)
    for unit, vid, lo, hi in UNITS:
        z = np.load(os.path.join(OUT, f"2d_{vid}.npz"))
        kpts, _ = track_subject(z["kt"], z["bt"], tuple(z["wh"]))
        hi2 = len(kpts) - 1 if hi is None else hi
        propose_reps.FPS = 25.0 if vid == "ext_03" else 30.0
        s, arms, var = build_signal_from_kpts(kpts)          # full-video signal, absolute frames
        reps = [r for r in propose(s) if lo <= r[1] <= hi2]  # keep pulls inside the unit window
        pd.DataFrame(reps, columns=["start_frame", "pull_frame", "end_frame"]).to_csv(
            os.path.join(OUT, "drafts", unit + ".csv"), index=False)
        verification_image(os.path.join(REPO_ROOT, "ai", "data", "external_stress_test",
                                        "raw", vid + ".mp4"),
                           s, arms, reps,
                           os.path.join(OUT, "drafts", unit + ".png"),
                           f"{unit} (window {lo}-{hi2}; {len(reps)} drafts)")
        print(unit, len(reps), "drafts")


if __name__ == "__main__":
    main()
