#!/usr/bin/env python3
"""Phase 4 sanity micro-benchmark: per-call cost of the two dominant live-mode stages
(YOLO11n-pose per frame, MotionBERT-Lite per 243-frame window) on CPU vs CUDA, warm, on real
seated-row frames. Informs the experiment plan; the authoritative numbers come from the
end-to-end streaming harness (live_benchmark.py), not from this script."""
from __future__ import annotations

import os
import sys
import time

import numpy as np
import torch

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.join(REPO_ROOT, "ai", "research", "pose_benchmark"))

from lifters import MotionBertLiteLifter, flip_h36m  # noqa: E402

VIDEO = os.path.join(REPO_ROOT, "ai", "data", "seated_row", "correct", "correct_01.mp4")


def sync(dev):
    if dev == "cuda":
        torch.cuda.synchronize()


def bench_yolo(dev, imgsz=640, n=80, warm=10):
    import cv2
    from ultralytics import YOLO
    m = YOLO(os.path.join(REPO_ROOT, "yolo11n-pose.pt"))
    cap = cv2.VideoCapture(VIDEO)
    frames = []
    while len(frames) < n + warm:
        ok, fr = cap.read()
        if not ok:
            break
        frames.append(fr)
    cap.release()
    ts = []
    for i, fr in enumerate(frames):
        sync(dev)
        t0 = time.perf_counter()
        m.predict(fr, device=0 if dev == "cuda" else "cpu", imgsz=imgsz, verbose=False)
        sync(dev)
        if i >= warm:
            ts.append((time.perf_counter() - t0) * 1000)
    return float(np.median(ts)), float(np.percentile(ts, 95))


def bench_lift(dev, tta=True, half=False, n=20, warm=3):
    lf = MotionBertLiteLifter()
    model = lf.model.to(dev)
    if half:
        model = model.half()
    x = torch.randn(1, 243, 17, 3 if not lf.no_conf else 2, device=dev)
    if half:
        x = x.half()
    ts = []
    with torch.no_grad():
        for i in range(n + warm):
            sync(dev)
            t0 = time.perf_counter()
            p = model(x)
            if tta:
                p = (p + flip_h36m(model(flip_h36m(x)))) / 2.0
            sync(dev)
            if i >= warm:
                ts.append((time.perf_counter() - t0) * 1000)
    return float(np.median(ts)), float(np.percentile(ts, 95))


def main():
    devs = ["cpu"] + (["cuda"] if torch.cuda.is_available() else [])
    for dev in devs:
        for imgsz in (640, 480, 320):
            med, p95 = bench_yolo(dev, imgsz)
            print(f"YOLO11n-pose  dev={dev:4s} imgsz={imgsz}: median {med:6.1f} ms  p95 {p95:6.1f} ms")
        for tta in (True, False):
            for half in ((False, True) if dev == "cuda" else (False,)):
                med, p95 = bench_lift(dev, tta, half)
                print(f"MB-Lite 243f  dev={dev:4s} tta={tta!s:5s} fp16={half!s:5s}: "
                      f"median {med:7.1f} ms  p95 {p95:7.1f} ms  (= {med/243:5.2f} ms/frame if lifted once)")


if __name__ == "__main__":
    main()
