#!/usr/bin/env python3
"""Automated quality gate for the unseen-athlete validation dataset (spec:
docs/UNSEEN_ATHLETE_DATASET_SPEC.md). Prints a PASS/FAIL report; exit 1 on any FAIL.

  validate_unseen_dataset.py [--root ai/data/unseen_athletes] [--no-video-decode]
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys

import pandas as pd

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
MANIFEST_COLS = ["video_id", "subject_id", "session_id", "view_id", "exercise", "form_type",
                 "deviation_type", "tempo_class", "camera_fps", "width", "height",
                 "lighting_condition", "clothing_category", "camera_distance_m",
                 "camera_height_m", "camera_angle_deg", "recording_timestamp", "annotation_version"]
REP_COLS = ["rep_id", "video_id", "subject_id", "exercise", "form_label", "deviation_type",
            "start_frame", "pull_frame", "end_frame", "start_time_s", "pull_time_s", "end_time_s",
            "annotation_confidence", "notes"]
DEVIATIONS = {"asymmetric_pull", "excessive_trunk_motion", "reduced_rom",
              "delayed_side_onset", "inconsistent_tempo"}


class Report:
    def __init__(self):
        self.items: list[tuple[str, bool, str]] = []

    def check(self, name: str, ok: bool, detail: str = ""):
        self.items.append((name, bool(ok), detail))

    def render(self) -> bool:
        print("DATASET VALIDATION")
        ok_all = True
        for name, ok, detail in self.items:
            print(f"  {name}: {'PASS' if ok else 'FAIL'}" + (f"  [{detail}]" if detail and not ok else ""))
            ok_all &= ok
        print(f"\nOVERALL: {'PASS' if ok_all else 'FAIL'}")
        return ok_all


def dev_subject_ids() -> set:
    ids = {"original_athlete"}
    p = os.path.join(REPO_ROOT, "ai", "data", "mendeley_manifest.csv")
    if os.path.exists(p):
        ids |= set(pd.read_csv(p).subject_id.unique())
    return ids


def validate(root: str, decode_videos: bool = True) -> Report:
    r = Report()
    man_p = os.path.join(root, "manifest.csv")
    ann_p = os.path.join(root, "annotations", "reps.csv")
    if not os.path.exists(man_p) or not os.path.exists(ann_p):
        r.check("Structure", False, "manifest.csv or annotations/reps.csv missing")
        return r
    man = pd.read_csv(man_p)
    ann = pd.read_csv(ann_p)

    missing_cols = [c for c in MANIFEST_COLS if c not in man.columns]
    r.check("Manifest schema", not missing_cols, f"missing {missing_cols}")
    missing_rc = [c for c in REP_COLS if c not in ann.columns]
    r.check("Annotation schema", not missing_rc, f"missing {missing_rc}")
    if missing_cols or missing_rc:
        return r

    r.check("Metadata completeness", not man[MANIFEST_COLS].drop(columns=["recording_timestamp"])
            .isna().any().any(), "null metadata fields")

    subs = sorted(man.subject_id.unique())
    r.check("Subjects >= 10", len(subs) >= 10, f"found {len(subs)}")
    bad_ns = [s for s in subs if not str(s).startswith("ua_")]
    r.check("Subject namespace (ua_)", not bad_ns, f"{bad_ns}")
    leak = set(subs) & dev_subject_ids()
    r.check("Dev-subject leakage", not leak, f"{sorted(leak)}")

    per = man.groupby("subject_id")
    views_ok = (per.view_id.nunique() >= 3).all()
    r.check("Views >= 3 per subject", views_ok,
            str(per.view_id.nunique()[per.view_id.nunique() < 3].to_dict()))
    good_ok = per.apply(lambda g: (g.form_type == "good").any()).all()
    dev_ok = per.apply(lambda g: (g.form_type == "deviated").any()).all()
    r.check("Good-form coverage", good_ok)
    r.check("Deviated-form coverage", dev_ok)
    bad_dev = man[(man.form_type == "deviated") & ~man.deviation_type.isin(DEVIATIONS)]
    r.check("Deviation types documented", len(bad_dev) == 0, f"{len(bad_dev)} rows")
    tempo = man.groupby("subject_id").tempo_class.nunique()
    r.check("Tempo variation (>=2 classes/subject)", (tempo >= 2).all(),
            str(tempo[tempo < 2].to_dict()))

    # files: existence, duplicates, integrity
    hashes = {}
    dup, missing_f, broken, fps_mismatch, frange = [], [], [], [], []
    for _, row in man.iterrows():
        path = os.path.join(root, row.subject_id, row.view_id, row.video_id + ".mp4")
        if not os.path.exists(path):
            missing_f.append(row.video_id)
            continue
        h = hashlib.md5(open(path, "rb").read()).hexdigest()
        if h in hashes:
            dup.append((row.video_id, hashes[h]))
        hashes[h] = row.video_id
        if decode_videos:
            import cv2
            cap = cv2.VideoCapture(path)
            n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            fps = cap.get(cv2.CAP_PROP_FPS)
            ok0, _ = cap.read()
            cap.set(cv2.CAP_PROP_POS_FRAMES, max(n - 2, 0))
            ok1, _ = cap.read()
            cap.release()
            if not (ok0 and ok1 and n > 10):
                broken.append(row.video_id)
            if abs(fps - float(row.camera_fps)) > 1.0:
                fps_mismatch.append((row.video_id, round(fps, 2), row.camera_fps))
            a = ann[ann.video_id == row.video_id]
            if len(a) and (a.end_frame.max() >= n or a.start_frame.min() < 0):
                frange.append(row.video_id)
    r.check("Video files present", not missing_f, f"{missing_f[:5]}")
    r.check("Duplicate videos", not dup, f"{dup[:3]}")
    if decode_videos:
        r.check("Video integrity", not broken, f"{broken[:5]}")
        r.check("FPS metadata matches", not fps_mismatch, f"{fps_mismatch[:3]}")
        r.check("Annotation frame ranges", not frange, f"{frange[:5]}")

    # annotations
    ordered = (ann.start_frame < ann.pull_frame) & (ann.pull_frame < ann.end_frame)
    r.check("Rep ordering start<pull<end", ordered.all(), f"{(~ordered).sum()} bad reps")
    tt = ann.merge(man[["video_id", "camera_fps"]], on="video_id", how="left")
    t_ok = ((tt.start_time_s - tt.start_frame / tt.camera_fps).abs() < 0.05)
    r.check("Timestamps consistent with fps", t_ok.all(), f"{(~t_ok).sum()} rows")
    conf_ok = ann.annotation_confidence.isin(["high", "medium", "low"]).all()
    r.check("Annotation confidence labels", conf_ok)
    unannotated = set(man[man.exercise == "seated_row"].video_id) - set(ann.video_id)
    r.check("All videos annotated", not unannotated, f"{sorted(unannotated)[:5]}")
    return r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=os.path.join(REPO_ROOT, "ai", "data", "unseen_athletes"))
    ap.add_argument("--no-video-decode", action="store_true")
    a = ap.parse_args()
    ok = validate(a.root, decode_videos=not a.no_video_decode).render()
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
