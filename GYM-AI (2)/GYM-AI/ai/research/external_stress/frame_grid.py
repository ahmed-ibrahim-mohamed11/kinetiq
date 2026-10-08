#!/usr/bin/env python3
"""Render specific frames of one external video as a large-tile labeled grid (annotation aid).

Usage: python frame_grid.py <vid> <out.jpg> <frame> [frame ...]  [--h 400] [--cols 6]
Crop follows the tracked-subject box like make_sheets.py.
"""
from __future__ import annotations

import math
import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
from make_sheets import OUT, RAW, subject_crop  # noqa: E402


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    vid, out = args[0], args[1]
    frames = [int(a) for a in args[2:]]
    h = next((int(a.split("=")[1]) for a in sys.argv if a.startswith("--h=")), 400)
    cols = next((int(a.split("=")[1]) for a in sys.argv if a.startswith("--cols=")), 6)
    x0, y0, x1, y1 = subject_crop(vid, min(frames), max(frames))
    cap = cv2.VideoCapture(os.path.join(RAW, vid + ".mp4"))
    thumbs = []
    for i in frames:
        cap.set(cv2.CAP_PROP_POS_FRAMES, i)
        ok, fr = cap.read()
        if not ok:
            continue
        t = fr[y0:y1, x0:x1]
        t = cv2.resize(t, (int(t.shape[1] * h / t.shape[0]), h))
        cv2.rectangle(t, (0, 0), (74, 28), (0, 0, 0), -1)
        cv2.putText(t, str(i), (2, 21), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 255, 255), 2)
        thumbs.append(t)
    cap.release()
    tw = max(t.shape[1] for t in thumbs)
    thumbs = [cv2.copyMakeBorder(t, 0, 0, 0, tw - t.shape[1], cv2.BORDER_CONSTANT) for t in thumbs]
    rows = math.ceil(len(thumbs) / cols)
    grid = np.zeros((rows * h, cols * tw, 3), dtype=np.uint8)
    for k, t in enumerate(thumbs):
        r, c = divmod(k, cols)
        grid[r * h:(r + 1) * h, c * tw:c * tw + t.shape[1]] = t
    cv2.imwrite(out, grid, [cv2.IMWRITE_JPEG_QUALITY, 88])
    print(out, grid.shape)


if __name__ == "__main__":
    main()
