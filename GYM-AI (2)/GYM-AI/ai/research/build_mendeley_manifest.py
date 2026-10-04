#!/usr/bin/env python3
"""
Phase 2.5A: build a machine-readable inventory of the Mendeley multi-view fitness dataset
(doi 10.17632/kgbb3yn47p.3) -> ai/data/mendeley_manifest.csv

Parses ONLY what actually exists on disk (no inferred annotations):
  path layout   Dataset Subject-wise/subject_XXX/<exercise>/<good|bad>/<front|side|diagonal>/*.mp4
  filename      subject_<ID>_<exercise>_<quality>_<view>.mp4   (per dataset README)
Every video is probed with OpenCV for fps / frame_count / duration / resolution.
Files that do not match the naming convention are still listed, with parse_ok=False.

Usage (repo root):
  .venv\Scripts\python.exe ai\research\build_mendeley_manifest.py ^
      --root "ai/data/mendeley/Dataset Subject-wise" --out ai/data/mendeley_manifest.csv
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import sys

import cv2

FNAME_RE = re.compile(
    r"^subject_(?P<sid>\d+)_(?P<exercise>[a-z_]+?)_(?P<quality>good|bad)_(?P<view>front|side|diagonal)"
    r"(?:[ _(]*\d*\)?)?\.mp4$", re.IGNORECASE)


def probe(path: str):
    cap = cv2.VideoCapture(path)
    try:
        if not cap.isOpened():
            return None
        fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
        n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        return dict(fps=round(fps, 3), frame_count=n,
                    duration_sec=round(n / fps, 2) if fps > 0 else None, width=w, height=h)
    finally:
        cap.release()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True, help="extracted 'Dataset Subject-wise' directory")
    ap.add_argument("--out", default=os.path.join("ai", "data", "mendeley_manifest.csv"))
    ap.add_argument("--no_probe", action="store_true", help="skip OpenCV probing (names/paths only)")
    args = ap.parse_args()

    rows = []
    for r, _, files in os.walk(args.root):
        for f in sorted(files):
            if not f.lower().endswith(".mp4"):
                continue
            full = os.path.join(r, f)
            rel = os.path.relpath(full, os.path.dirname(args.root.rstrip("/\\")))
            parts = rel.replace("\\", "/").split("/")
            m = FNAME_RE.match(f)
            row = dict(video_id=os.path.splitext(f)[0], subject_id="", exercise="", camera_view="",
                       form_label="", video_path=rel.replace("\\", "/"), parse_ok=bool(m),
                       fps=None, frame_count=None, duration_sec=None, width=None, height=None)
            if m:
                row.update(subject_id=f"subject_{int(m['sid']):03d}", exercise=m["exercise"].lower(),
                           camera_view=m["view"].lower(), form_label=m["quality"].lower())
            elif len(parts) >= 5:   # fall back to folder structure: subject/exercise/quality/view/file
                row.update(subject_id=parts[-5], exercise=parts[-4], form_label=parts[-3], camera_view=parts[-2])
            if not args.no_probe:
                p = probe(full)
                if p:
                    row.update(p)
            rows.append(row)

    if not rows:
        sys.exit(f"no .mp4 found under {args.root}")
    rows.sort(key=lambda x: (x["subject_id"], x["exercise"], x["form_label"], x["camera_view"], x["video_id"]))
    cols = ["video_id", "subject_id", "exercise", "camera_view", "form_label", "video_path",
            "fps", "frame_count", "duration_sec", "width", "height", "parse_ok"]
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    print(f"{len(rows)} videos -> {args.out}")
    bad = [r for r in rows if not r["parse_ok"]]
    if bad:
        print(f"WARNING: {len(bad)} filenames did not match the documented convention (parse_ok=False):")
        for r in bad[:20]:
            print("  ", r["video_path"])


if __name__ == "__main__":
    main()
