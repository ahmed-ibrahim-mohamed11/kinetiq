#!/usr/bin/env python3
"""P3.1 + P3.2 (docs/EXPERIMENT_REGISTRY.md): systematic failure-injection and tempo robustness
sweep on dev data (internal_sr + mendeley_val back), through the frozen decoder, unchanged.
Reports failure boundaries for docs/ROBUSTNESS_REPORT.md. Nothing here changes ai/core.
"""
from __future__ import annotations

import os
import sys

import pandas as pd

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, REPO_ROOT)

from ai.augmentation.perturb import (make_confidence_noise_transform,  # noqa: E402
                                     make_fps_downsample_transform, make_frame_drop_transform,
                                     make_inconsistent_tempo_transform,
                                     make_joint_dropout_transform,
                                     make_pause_insertion_transform,
                                     make_positional_jitter_transform, make_time_warp_transform)
from ai.core.config import L_ELB, L_SHO, R_ELB, R_SHO, L_WRI, R_WRI  # noqa: E402
from ai.evaluation import harness as H  # noqa: E402

OUT = os.path.join(REPO_ROOT, "ai", "outputs", "phase3")
PIPELINE = "motionbert_lite"


def score(system, items, tf):
    r = H.run_pose_suite(system, items, transform=tf)["pooled"]["iou"]
    return r["f1"], r["tp"], r["fp"], r["fn"]


def main():
    os.makedirs(OUT, exist_ok=True)
    system = H.fit_system(PIPELINE)
    sr = H.load_internal_sr()
    from ai.research.phase2.paused_tempo import _mendeley_val_back_raw
    mv = _mendeley_val_back_raw()

    CASES = [("baseline", None)]
    # --- P3.1 failure injection ---
    for frac in (0.05, 0.10, 0.20):
        CASES.append((f"frame_drop_{int(frac*100)}pct", make_frame_drop_transform(frac, seed=0)))
    for sigma in (0.1, 0.3, 0.5):
        CASES.append((f"conf_noise_sigma{sigma}", make_confidence_noise_transform(sigma, seed=0)))
    for step, label in ((2, "fps_15"), (4, "fps_7.5")):
        CASES.append((f"fps_downsample_{label}", make_fps_downsample_transform(step)))
    for sigma in (0.02, 0.05, 0.10):
        CASES.append((f"res_jitter_sigma{sigma}", make_positional_jitter_transform(sigma, seed=0)))
    JOINT_SETS = {
        "wrist_both": [L_WRI, R_WRI], "elbow_one_L": [L_ELB], "elbow_one_R": [R_ELB],
        "shoulder_one_L": [L_SHO], "shoulder_one_R": [R_SHO],
        "arm_one_L_edge_crop": [L_SHO, L_ELB], "arm_one_R_edge_crop": [R_SHO, R_ELB],
    }
    for label, joints in JOINT_SETS.items():
        for frac, flabel in ((0.3, "30pct"), (1.0, "100pct_persistent")):
            CASES.append((f"dropout_{label}_{flabel}",
                          make_joint_dropout_transform(joints, frac, seed=0)))
    # --- P3.2 tempo suite ---
    for speed, label in ((1.5, "fast_1.5x"), (0.7, "slow_0.7x"), (0.4, "veryslow_0.4x")):
        CASES.append((f"tempo_{label}", make_time_warp_transform(speed)))
    for pause_s, at in ((1.0, "pull"), (1.0, "end")):
        CASES.append((f"tempo_paused_{at}_{pause_s}s", make_pause_insertion_transform(pause_s, at=at)))
    CASES.append(("tempo_inconsistent", make_inconsistent_tempo_transform(0.6, 1.5, cycle_s=1.5)))

    rows = []
    for label, tf in CASES:
        f1_sr, tp_sr, fp_sr, fn_sr = score(system, sr, tf)
        f1_mv, tp_mv, fp_mv, fn_mv = score(system, mv, tf)
        rows.append(dict(case=label, internal_sr_f1=round(f1_sr, 4), internal_sr_tp=tp_sr,
                         internal_sr_fp=fp_sr, internal_sr_fn=fn_sr,
                         mendeley_val_f1=round(f1_mv, 4), mendeley_val_tp=tp_mv,
                         mendeley_val_fp=fp_mv, mendeley_val_fn=fn_mv))
        print(f"{label:34s} internal_sr F1={f1_sr:.4f} ({tp_sr},{fp_sr},{fn_sr})  "
              f"mendeley_val F1={f1_mv:.4f} ({tp_mv},{fp_mv},{fn_mv})")
    pd.DataFrame(rows).to_csv(os.path.join(OUT, "failure_injection_sweep.csv"), index=False)


if __name__ == "__main__":
    main()
