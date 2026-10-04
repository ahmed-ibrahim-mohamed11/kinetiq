#!/usr/bin/env python3
"""
Phase 2.5B: cache YOLO11n-pose 2D keypoints for the 34 proposed benchmark videos, and record
per-frame detection statistics for the person-selection audit (gym background clutter).

Output per video (ai/outputs/mendeley_2d/{video_id}.npz, git-ignored):
  kpts     (T,17,3) COCO x,y,conf of the HIGHEST-CONF person (NaN row if none) [baseline rule, audited]
  kpts_top (T,K,17,3) up to K=8 candidates SORTED BY BOX AREA desc (NaN-padded)
  boxes    (T,K,5)  candidate boxes as cx,cy,w,h,conf (NaN/0-padded), same area ordering
  (K=8 by area since Phase 2.5C: top-3-by-conf sometimes excluded the subject in crowded gyms)
  n_det    (T,)   number of detected persons
  conf1    (T,)   box conf of the highest-conf person (0 if none)
  conf2    (T,)   box conf of the runner-up (0 if <2)
  cx, cy   (T,)   highest-conf box centre (pixels; NaN if none)
NOTE: diagnostic/annotation aid only. The frozen baseline is NOT modified.
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, REPO_ROOT)

import argparse as _ap
_p = _ap.ArgumentParser(); _p.add_argument("--subset", default=os.path.join(REPO_ROOT, "ai", "data", "mendeley_annotation_subset_PROPOSED.csv"))
SUBSET = _p.parse_args().subset
DATA_ROOT = os.path.join(REPO_ROOT, "ai", "data", "mendeley")
OUT = os.path.join(REPO_ROOT, "ai", "outputs", "mendeley_2d")


def main():
    from ultralytics import YOLO
    model = YOLO(os.path.join(REPO_ROOT, "yolo11n-pose.pt"))
    sub = pd.read_csv(SUBSET)
    os.makedirs(OUT, exist_ok=True)
    for _, row in sub.iterrows():
        out_path = os.path.join(OUT, row.video_id + ".npz")
        if os.path.exists(out_path):
            print(f"{row.video_id}: cached, skip")
            continue
        vp = os.path.join(DATA_ROOT, row.video_path)
        K = 8
        kpts, ktop, btop, n_det, c1, c2, cx, cy = [], [], [], [], [], [], [], []
        for r in model.predict(source=vp, stream=True, device="cpu", verbose=False):
            kt = np.full((K, 17, 3), np.nan, dtype=np.float32)
            bt = np.zeros((K, 5), dtype=np.float32)
            bt[:, :4] = np.nan
            if r.boxes is not None and len(r.boxes) > 0 and r.keypoints is not None:
                conf = r.boxes.conf.cpu().numpy()
                xywh = r.boxes.xywh.cpu().numpy()
                order = np.argsort(-(xywh[:, 2] * xywh[:, 3]))          # by AREA desc
                best = int(np.argmax(conf))
                for s, i in enumerate(order[:K]):
                    kt[s] = r.keypoints.data[int(i)].cpu().numpy()
                    bt[s] = [*xywh[int(i)], conf[int(i)]]
                kpts.append(r.keypoints.data[best].cpu().numpy())
                n_det.append(len(conf))
                c1.append(float(conf[best]))
                c2.append(float(np.sort(conf)[-2]) if len(conf) > 1 else 0.0)
                box = r.boxes.xywh[best].cpu().numpy()
                cx.append(float(box[0])); cy.append(float(box[1]))
            else:
                kpts.append(np.full((17, 3), np.nan, dtype=np.float32))
                n_det.append(0); c1.append(0.0); c2.append(0.0); cx.append(np.nan); cy.append(np.nan)
            ktop.append(kt); btop.append(bt)
        np.savez_compressed(out_path, kpts=np.stack(kpts).astype(np.float32),
                            kpts_top=np.stack(ktop), boxes=np.stack(btop),
                            n_det=np.array(n_det, dtype=np.int16), conf1=np.array(c1, dtype=np.float32),
                            conf2=np.array(c2, dtype=np.float32), cx=np.array(cx, dtype=np.float32),
                            cy=np.array(cy, dtype=np.float32))
        print(f"{row.video_id}: {len(n_det)} frames, multi-person frames: {int((np.array(n_det) > 1).sum())}")
    print("done.")


if __name__ == "__main__":
    main()
