#!/usr/bin/env python3
"""Export the frozen rep-segmentation system to ai/models/rep_segmentation/ (Phase 5, P5.1).

Refits EXACTLY the frozen product system (Phase 2.7 `config_final.json`, masked supervision on
the Mendeley BACK TRAIN split — the same `ai.evaluation.harness.fit_system` every baseline number
comes from), checks its fingerprint against the locked baseline manifest, and writes the JSON
artifact with provenance. Needs the local (git-ignored) Mendeley pose caches ONCE; everyone
else loads the committed artifact (`ai.core.model_artifact.load_artifact`) and never refits.

Usage:  python ai/scripts/export_model_artifact.py [--version 1.0.0] [--out PATH]
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from ai.core.model_artifact import DEFAULT_ARTIFACT, export_artifact, load_artifact  # noqa: E402
from ai.scripts.baseline_lib import MANIFEST_JSON, REPO_ROOT, git_info, sha256_file  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", default="1.0.0")
    ap.add_argument("--out", default=DEFAULT_ARTIFACT)
    args = ap.parse_args()

    from ai.evaluation import harness as H
    system = H.fit_system()
    man = json.load(open(MANIFEST_JSON, encoding="utf-8"))
    rel = lambda p: os.path.relpath(p, REPO_ROOT).replace("\\", "/")
    models = {m["path"]: m["sha256"] for m in man["models"] if m["role"] == "product"}
    provenance = dict(
        exercise="seated_row (trained on Mendeley 'back' = bent-over dumbbell row, zero-shot to seated row)",
        training=dict(dataset="Mendeley multi-view fitness (doi 10.17632/kgbb3yn47p.3, CC BY 4.0)",
                      split="TRAIN (14 subjects), BACK exercise, verified+auto reps, uncertain masked",
                      n_train_videos=system.meta.get("n_train_videos"),
                      train_videos=system.dec.stats.get("train_videos"),
                      annotations_sha256_lf=sha256_file(os.path.join(REPO_ROOT, "ai", "data",
                                                                     "mendeley_rep_annotations_expanded.csv")),
                      split_sha256_lf=sha256_file(os.path.join(REPO_ROOT, "ai", "data",
                                                               "mendeley_subject_split.csv"))),
        config_source="ai/outputs/phase27/motionbert_lite/config_final.json (selected on VAL, Phase 2.7)",
        pose_inputs=dict(lifter="MotionBERT-Lite FT_MB_lite_MB_ft_h36m_global_lite",
                         lifter_sha256=models.get("ai/models/motionbert/FT_MB_lite_MB_ft_h36m_global_lite/best_epoch.bin"),
                         detector="yolo11n-pose (top-8 by area) + deterministic tracker",
                         detector_sha256=models.get("yolo11n-pose.pt")),
        code_commit=git_info().get("commit"),
        baseline_manifest="docs/AI_CORE_BASELINE_MANIFEST.json",
        headline_metrics={k: v["value"] for k, v in man["headline_metrics"].items()},
        limitations=["3D is estimated monocular (not metric)", "trained on a proxy exercise (bent-over row)",
                     "seated-row evidence: 1 athlete zero-shot + 3 wild athletes; fresh-athlete validation pending"])
    d = export_artifact(system.cfg, system.model, system.dec, args.out, args.version, provenance)
    want = man["product_system"]["fitted_system_sha256"]
    if d["fitted_system_sha256"] != want:
        print(f"FINGERPRINT MISMATCH vs locked manifest: {d['fitted_system_sha256'][:12]} != {want[:12]}")
        return 1
    loaded = load_artifact(args.out)          # round-trip + verification
    print(f"wrote {rel(args.out)}  version {args.version}  fingerprint {loaded.fingerprint[:16]}... "
          f"(matches manifest)  size {os.path.getsize(args.out)} B")
    return 0


if __name__ == "__main__":
    sys.exit(main())
