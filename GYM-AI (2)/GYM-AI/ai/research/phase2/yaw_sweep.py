#!/usr/bin/env python3
"""P2.1a (docs/EXPERIMENT_REGISTRY.md): characterize the decoder's sensitivity to a synthetic
yaw rotation of the already-lifted 3D pose, on DEV data only (internal_sr + Mendeley VAL back).

This does NOT claim to simulate real different-camera reconstruction quality (Phase 1's
perturb.py docstring caveat stands: yaw_rotate perturbs an already-reconstructed pose, it does
not re-run 2D->3D lifting from a different real viewpoint). It measures exactly one thing: how
sensitive is ai.core.decoder's output to the FACING_IN_CAMERA sign-disambiguation threshold
found in Phase 1 (E-01)? That bounds how much of the side/rear-view weakness COULD be a sign
artifact vs. a genuine low-signal problem, without yet claiming which.

Writes ai/outputs/phase2/yaw_sweep_internal_sr.csv and yaw_sweep_mendeley_val.csv.
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, REPO_ROOT)

from ai.augmentation.perturb import make_yaw_rotation_transform, yaw_rotate  # noqa: E402
from ai.core.features import prepare_video  # noqa: E402
from ai.evaluation import harness as H  # noqa: E402
from ai.evaluation.protocol import evaluate_dontcare, prf, pull_tol_frames  # noqa: E402

OUT = os.path.join(REPO_ROOT, "ai", "outputs", "phase2")
ANGLES = [0, 15, 30, 45, 60, 75, 90, 105, 120, 135, 150, 165, 180]
PIPELINE = "motionbert_lite"
MEND_FPS = 15.0


def sweep_internal_sr(system):
    items = H.load_internal_sr()
    rows = []
    for angle in ANGLES:
        tf = make_yaw_rotation_transform(float(angle)) if angle else None
        r = H.run_pose_suite(system, items, transform=tf)["pooled"]
        rows.append(dict(angle=angle, iou_f1=r["iou"]["f1"], iou_tp=r["iou"]["tp"],
                         iou_fp=r["iou"]["fp"], iou_fn=r["iou"]["fn"],
                         pull_f1=r["pull"]["f1"], pull_mae_s=r["pull"]["pull_mae_s"]))
    return pd.DataFrame(rows)


def _raw_mendeley_val_back():
    """Raw (pose, conf) + metadata for Mendeley VAL back videos — mirrors train_eval.load_videos'
    own loading, kept separate so we can rotate BEFORE prepare_video (the prepared v-dict's
    arm/trunk are already torso-projected and not invertible)."""
    POSES = os.path.join(REPO_ROOT, "ai", "outputs", "pose_bench", "poses", PIPELINE)
    vids = H.mendeley_videos(PIPELINE)
    out = {}
    for vid, v in vids.items():
        if v["split"] != "val" or v["exercise"] != "back":
            continue
        pose = np.load(os.path.join(POSES, vid + ".npy"))
        conf = np.load(os.path.join(POSES, vid + "_conf.npy"))
        out[vid] = dict(pose=pose, conf=conf, gt=v["gt"], gt_uncertain=v["gt_uncertain"],
                        view=v["view"], subject=v["subject"])
    return out


def sweep_mendeley_val(system):
    raw = _raw_mendeley_val_back()
    rows = []
    for angle in ANGLES:
        for vid, d in raw.items():
            pose = yaw_rotate(d["pose"], float(angle)) if angle else d["pose"]
            v = prepare_video(vid, pose, MEND_FPS, conf=d["conf"])
            res = system.predict(v)
            pred = [dict(start=r["start"], pull=r["pull"], end=r["end"]) for r in res["reps"]]
            tol = pull_tol_frames(MEND_FPS)
            m = evaluate_dontcare(pred, d["gt"], d["gt_uncertain"], "iou", "v2", tol)
            rows.append(dict(angle=angle, video=vid, view=d["view"], subject=d["subject"],
                             tp=m["tp"], fp=m["fp"], fn=m["fn"]))
    df = pd.DataFrame(rows)
    pooled = df.groupby("angle")[["tp", "fp", "fn"]].sum().reset_index()
    pooled["f1"] = [prf(r.tp, r.fp, r.fn)[2] for r in pooled.itertuples()]
    side = df[df.view == "side"].groupby("angle")[["tp", "fp", "fn"]].sum().reset_index()
    side["f1"] = [prf(r.tp, r.fp, r.fn)[2] for r in side.itertuples()]
    return df, pooled, side


def main():
    os.makedirs(OUT, exist_ok=True)
    system = H.fit_system(PIPELINE)

    print("=== internal_sr yaw sweep ===")
    sr = sweep_internal_sr(system)
    sr.to_csv(os.path.join(OUT, "yaw_sweep_internal_sr.csv"), index=False)
    print(sr.to_string(index=False))

    print("\n=== mendeley_val (back) yaw sweep ===")
    detail, pooled, side = sweep_mendeley_val(system)
    detail.to_csv(os.path.join(OUT, "yaw_sweep_mendeley_val_detail.csv"), index=False)
    pooled.to_csv(os.path.join(OUT, "yaw_sweep_mendeley_val_pooled.csv"), index=False)
    side.to_csv(os.path.join(OUT, "yaw_sweep_mendeley_val_side.csv"), index=False)
    print("-- pooled (all views) --")
    print(pooled.to_string(index=False))
    print("-- side view only --")
    print(side.to_string(index=False))


if __name__ == "__main__":
    main()
