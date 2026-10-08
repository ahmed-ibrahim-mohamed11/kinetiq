#!/usr/bin/env python3
"""Subject-cropped contact sheets for manual GT annotation of the external stress-test units.

Crops each frame to the tracked subject's bbox (5-95th pct over the unit window + margin) so
arm/handle position is legible, burns in absolute frame numbers, and tiles ~48 frames per sheet.
Usage: python make_sheets.py [unit ...]   (default: all units)
"""
from __future__ import annotations

import math
import os
import sys

import cv2
import numpy as np

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, os.path.join(REPO_ROOT, "ai", "research", "pose_benchmark"))
from tracking import track_subject  # noqa: E402

OUT = os.path.join(REPO_ROOT, "ai", "outputs", "external_stress_test")
RAW = os.path.join(REPO_ROOT, "ai", "data", "external_stress_test", "raw")

# unit -> (video, lo, hi, stride)
UNITS = {
    "ext_01_full": ("ext_01", 0, 418, 3),
    "ext_02_full": ("ext_02", 0, 800, 4),
    "ext_03_segA": ("ext_03", 237, 330, 2),
    "ext_03_segB": ("ext_03", 361, 470, 2),
    "ext_08_segB": ("ext_08", 160, 250, 2),
    "ext_08_segC": ("ext_08", 310, 435, 2),
}
PER_SHEET, COLS, THUMB_H = 48, 8, 250


def subject_crop(vid, lo, hi):
    z = np.load(os.path.join(OUT, f"2d_{vid}.npz"))
    kpts, _ = track_subject(z["kt"], z["bt"], tuple(z["wh"]))
    k = kpts[lo:hi + 1]
    valid = k[..., 2] > 0.3
    xs, ys = k[..., 0][valid], k[..., 1][valid]
    x0, x1 = np.percentile(xs, 5), np.percentile(xs, 95)
    y0, y1 = np.percentile(ys, 5), np.percentile(ys, 95)
    mx, my = 0.18 * (x1 - x0), 0.15 * (y1 - y0)
    W, H = z["wh"]
    return (max(0, int(x0 - mx)), max(0, int(y0 - my)),
            min(int(W), int(x1 + mx)), min(int(H), int(y1 + my)))


def render(unit):
    vid, lo, hi, stride = UNITS[unit]
    x0, y0, x1, y1 = subject_crop(vid, lo, hi)
    cap = cv2.VideoCapture(os.path.join(RAW, vid + ".mp4"))
    idxs = list(range(lo, hi + 1, stride))
    thumbs = []
    for i in idxs:
        cap.set(cv2.CAP_PROP_POS_FRAMES, i)
        ok, fr = cap.read()
        if not ok:
            continue
        t = fr[y0:y1, x0:x1]
        tw = int(t.shape[1] * THUMB_H / t.shape[0])
        t = cv2.resize(t, (tw, THUMB_H))
        cv2.rectangle(t, (0, 0), (56, 22), (0, 0, 0), -1)
        cv2.putText(t, str(i), (2, 16), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1)
        thumbs.append(t)
    cap.release()
    os.makedirs(os.path.join(OUT, "sheets"), exist_ok=True)
    tw = max(t.shape[1] for t in thumbs)
    thumbs = [cv2.copyMakeBorder(t, 0, 0, 0, tw - t.shape[1], cv2.BORDER_CONSTANT) for t in thumbs]
    n_sheets = math.ceil(len(thumbs) / PER_SHEET)
    for s in range(n_sheets):
        part = thumbs[s * PER_SHEET:(s + 1) * PER_SHEET]
        rows = math.ceil(len(part) / COLS)
        grid = np.zeros((rows * THUMB_H, COLS * tw, 3), dtype=np.uint8)
        for k, t in enumerate(part):
            r, c = divmod(k, COLS)
            grid[r * THUMB_H:(r + 1) * THUMB_H, c * tw:c * tw + t.shape[1]] = t
        p = os.path.join(OUT, "sheets", f"{unit}_s{s}.jpg")
        cv2.imwrite(p, grid, [cv2.IMWRITE_JPEG_QUALITY, 85])
        print(p, grid.shape)


if __name__ == "__main__":
    for u in (sys.argv[1:] or UNITS):
        render(u)
