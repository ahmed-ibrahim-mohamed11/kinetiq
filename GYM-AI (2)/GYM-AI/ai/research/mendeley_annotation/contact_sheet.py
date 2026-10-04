#!/usr/bin/env python3
"""Render contact sheets (frame mosaics with burned-in frame numbers) for visual inspection.

  python contact_sheet.py --video <video_id> --every 15 --out sheet.jpg            # whole video
  python contact_sheet.py --video <video_id> --start 100 --end 160 --every 1 ...   # zoom a window
"""
from __future__ import annotations

import argparse
import math
import os
import sys

import cv2
import numpy as np
import pandas as pd

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
MANIFEST = os.path.join(REPO_ROOT, "ai", "data", "mendeley_manifest.csv")
DATA_ROOT = os.path.join(REPO_ROOT, "ai", "data", "mendeley")


def render(video_id: str, out: str, every: int = 15, start: int = 0, end: int | None = None,
           thumb_h: int = 220, cols: int = 10) -> str:
    man = pd.read_csv(MANIFEST).set_index("video_id")
    vp = os.path.join(DATA_ROOT, man.loc[video_id, "video_path"])
    cap = cv2.VideoCapture(vp)
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    end = min(end if end is not None else n - 1, n - 1)
    idxs = list(range(start, end + 1, every))
    thumbs = []
    for i in idxs:
        cap.set(cv2.CAP_PROP_POS_FRAMES, i)
        ok, fr = cap.read()
        if not ok:
            continue
        h, w = fr.shape[:2]
        tw = int(w * thumb_h / h)
        t = cv2.resize(fr, (tw, thumb_h))
        cv2.rectangle(t, (0, 0), (52, 20), (0, 0, 0), -1)
        cv2.putText(t, str(i), (2, 15), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1)
        thumbs.append(t)
    cap.release()
    if not thumbs:
        raise SystemExit("no frames")
    tw = max(t.shape[1] for t in thumbs)
    thumbs = [cv2.copyMakeBorder(t, 0, 0, 0, tw - t.shape[1], cv2.BORDER_CONSTANT) for t in thumbs]
    rows = math.ceil(len(thumbs) / cols)
    grid = np.zeros((rows * thumb_h, cols * tw, 3), dtype=np.uint8)
    for k, t in enumerate(thumbs):
        r, c = divmod(k, cols)
        grid[r * thumb_h:(r + 1) * thumb_h, c * tw:c * tw + t.shape[1]] = t
    cv2.imwrite(out, grid, [cv2.IMWRITE_JPEG_QUALITY, 85])
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--every", type=int, default=15)
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--end", type=int, default=None)
    ap.add_argument("--thumb_h", type=int, default=220)
    ap.add_argument("--cols", type=int, default=10)
    a = ap.parse_args()
    print(render(a.video, a.out, a.every, a.start, a.end, a.thumb_h, a.cols))
