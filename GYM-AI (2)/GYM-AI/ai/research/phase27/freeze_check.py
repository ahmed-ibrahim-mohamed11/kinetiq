#!/usr/bin/env python3
"""Phase 2.7 Stage 0: verify the frozen dataset/split and record hashes.
Writes ai/outputs/phase27/freeze_manifest.json. Fails loudly on any violation."""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys

import pandas as pd

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
ANN = os.path.join(REPO_ROOT, "ai", "data", "mendeley_rep_annotations_expanded.csv")
SPLIT = os.path.join(REPO_ROOT, "ai", "data", "mendeley_subject_split.csv")
DUPS = os.path.join(REPO_ROOT, "ai", "outputs", "mendeley_duplicate_groups.json")
OUT = os.path.join(REPO_ROOT, "ai", "outputs", "phase27")

FILES = {
    "annotations": ANN,
    "subject_split": SPLIT,
    "duplicate_groups": DUPS,
    "core_config": os.path.join(REPO_ROOT, "ai", "core", "config.py"),
    "core_decoder": os.path.join(REPO_ROOT, "ai", "core", "decoder.py"),
    "core_features": os.path.join(REPO_ROOT, "ai", "core", "features.py"),
    "core_phase_model": os.path.join(REPO_ROOT, "ai", "core", "phase_model.py"),
    "tracker": os.path.join(REPO_ROOT, "ai", "research", "pose_benchmark", "tracking.py"),
}


def sha256(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()


def main():
    ann = pd.read_csv(ANN)
    split = pd.read_csv(SPLIT)
    errs = []
    m = ann.merge(split[["subject_id", "split"]], on="subject_id", how="left")
    if m.split.isna().any():
        errs.append("subjects missing from split file")
    if (m.groupby("subject_id").split.nunique() > 1).any():
        errs.append("a subject appears in more than one split")
    for col in ("camera_view", "form_label", "video_id"):
        x = m.groupby(["subject_id", col]).split.nunique()
        if (x > 1).any():
            errs.append(f"{col} of one subject crosses splits")
    s = split.set_index("subject_id").split
    for g in json.load(open(DUPS)):
        subs = sorted({mm.group(0) for v in g if (mm := re.match(r"subject_\d+", v))} & set(s.index))
        if len({s[x] for x in subs}) > 1:
            errs.append(f"duplicate-linked subjects split apart: {subs}")
    if errs:
        print("FREEZE CHECK FAILED:")
        for e in errs:
            print(" -", e)
        sys.exit(1)
    os.makedirs(OUT, exist_ok=True)
    manifest = dict(
        checked="2026-10-05",
        n_videos=int(ann.video_id.nunique()), n_reps=len(ann),
        split_counts={k: int(v) for k, v in m.groupby("split").size().items()},
        supervision_policy="verified+auto supervise; uncertain MASKED from all fitting (frames excluded)",
        dontcare_policy="unmatched prediction may absorb at most ONE uncertain GT (one-to-one, same rule); extras are FP",
        hashes={k: sha256(p) for k, p in FILES.items()})
    json.dump(manifest, open(os.path.join(OUT, "freeze_manifest.json"), "w"), indent=1)
    print("freeze check OK; manifest written")
    for k, v in manifest["hashes"].items():
        print(f"  {k}: {v[:16]}...")


if __name__ == "__main__":
    main()
