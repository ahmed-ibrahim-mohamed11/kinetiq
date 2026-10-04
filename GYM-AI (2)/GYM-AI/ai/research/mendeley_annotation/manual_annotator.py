#!/usr/bin/env python3
"""
Phase 2.5B: interactive START/PULL/END annotator for the Mendeley benchmark subset.
Adapted from ai/research/legacy_2d/manual_rep_annotator.py (same keybindings) instead of
duplicating a new UI convention; differences: video list comes from the subset CSV, output
schema is the Phase 2.5B one, and frame indices are 0-BASED (unlike the legacy 1-based CSV).

Keys (as in the legacy tool):
  SPACE  play/pause          A / D   step one frame back / forward
  M      mark next boundary of the current rep (START -> PULL -> END, then the rep is saved)
  U      undo last mark      N       skip to next video
  Q      quit (saves)
Output: appends to ai/data/mendeley_rep_annotations.csv
  video_id, subject_id, exercise, camera_view, form_label, rep_id, start_frame, pull_frame, end_frame, fps
Existing rows for a video are loaded and shown; re-annotating a video replaces its rows on save.
"""
from __future__ import annotations

import os
import sys

import cv2
import pandas as pd

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, REPO_ROOT)

SUBSET = os.path.join(REPO_ROOT, "ai", "data", "mendeley_annotation_subset_PROPOSED.csv")
DATA_ROOT = os.path.join(REPO_ROOT, "ai", "data", "mendeley")
OUT = os.path.join(REPO_ROOT, "ai", "data", "mendeley_rep_annotations.csv")
COLS = ["video_id", "subject_id", "exercise", "camera_view", "form_label",
        "rep_id", "start_frame", "pull_frame", "end_frame", "fps"]


def load_existing() -> pd.DataFrame:
    if os.path.exists(OUT):
        return pd.read_csv(OUT)
    return pd.DataFrame(columns=COLS)


def annotate_video(row, existing: pd.DataFrame) -> list | None:
    path = os.path.join(DATA_ROOT, row.video_path)
    cap = cv2.VideoCapture(path)
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    reps, marks = [], []
    frame_i, playing = 0, False
    stage = ["START", "PULL", "END"]
    win = "annotate (M=mark A/D=step SPACE=play U=undo N=next Q=quit)"
    while True:
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_i)
        ok, fr = cap.read()
        if not ok:
            frame_i = max(0, min(frame_i, n - 1))
            playing = False
            continue
        h, w = fr.shape[:2]
        if h > 900:
            fr = cv2.resize(fr, (int(w * 900 / h), 900))
        txt = f"{row.video_id}  f={frame_i}/{n-1}  reps={len(reps)}  next mark: {stage[len(marks)]}"
        cv2.rectangle(fr, (0, 0), (fr.shape[1], 26), (0, 0, 0), -1)
        cv2.putText(fr, txt, (6, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1)
        cv2.imshow(win, fr)
        key = cv2.waitKey(66 if playing else 0) & 0xFF
        if playing:
            frame_i = min(frame_i + 1, n - 1)
        if key == ord(" "):
            playing = not playing
        elif key == ord("a"):
            frame_i = max(0, frame_i - 1); playing = False
        elif key == ord("d"):
            frame_i = min(n - 1, frame_i + 1); playing = False
        elif key == ord("m"):
            marks.append(frame_i)
            if len(marks) == 3:
                s, p, e = marks
                if s < p < e:
                    reps.append((s, p, e))
                else:
                    print(f"  rejected: need start<pull<end, got {marks}")
                marks = []
        elif key == ord("u"):
            if marks:
                marks.pop()
            elif reps:
                reps.pop()
        elif key == ord("n"):
            break
        elif key == ord("q"):
            cap.release(); cv2.destroyAllWindows()
            return reps if reps else None
    cap.release()
    cv2.destroyAllWindows()
    return reps


def main():
    sub = pd.read_csv(SUBSET)
    df = load_existing()
    for _, row in sub.iterrows():
        done = df[df.video_id == row.video_id]
        print(f"{row.video_id}: {len(done)} existing reps - ENTER to annotate, s+ENTER to skip")
        if input().strip().lower() == "s":
            continue
        reps = annotate_video(row, df)
        if reps:
            df = df[df.video_id != row.video_id]
            new = [dict(video_id=row.video_id, subject_id=row.subject_id, exercise=row.exercise,
                        camera_view=row.camera_view, form_label=row.form_label, rep_id=i + 1,
                        start_frame=s, pull_frame=p, end_frame=e, fps=row.fps)
                   for i, (s, p, e) in enumerate(reps)]
            df = pd.concat([df, pd.DataFrame(new)], ignore_index=True)
            df[COLS].to_csv(OUT, index=False)
            print(f"  saved {len(reps)} reps -> {OUT}")


if __name__ == "__main__":
    main()
