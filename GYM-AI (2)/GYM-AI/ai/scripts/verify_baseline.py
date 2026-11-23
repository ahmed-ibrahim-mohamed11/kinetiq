#!/usr/bin/env python3
"""Regression gate: recompute the baseline with the shared harness and compare to the locked
manifest (docs/AI_CORE_BASELINE_MANIFEST.json). Exit 1 on any drift.

Checks: product checkpoint hashes, the system fingerprint (persisted artifact by default, or a
fresh refit), and the headline metrics (exact to 4 decimals — counts-based F1 is deterministic).
A suite whose input caches are absent (e.g. on a fresh clone before
ai/scripts/rebuild_pose_caches.py) is SKIPPED with a printed reason, never silently passed.

Usage:  python ai/scripts/verify_baseline.py [--source artifact|refit] [--with-causal] [--skip-hashes]
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from ai.scripts.baseline_lib import MANIFEST_JSON, REPO_ROOT, sha256_file, system_fingerprint  # noqa: E402

SUITE_INPUTS = {   # suite -> a file whose absence means the suite cannot run
    "mendeley_val": os.path.join(REPO_ROOT, "ai", "outputs", "pose_bench", "poses", "motionbert_lite"),
    "internal_sr": os.path.join(REPO_ROOT, "ai", "outputs", "phase28", "pose_motionbert_lite_correct_01.npy"),
    "external": os.path.join(REPO_ROOT, "ai", "outputs", "external_stress_test", "pose_ext_02.npy"),
    "causal": os.path.join(REPO_ROOT, "ai", "models", "motionbert", "FT_MB_lite_MB_ft_h36m_global_lite", "best_epoch.bin"),
}
SUITE_METRICS = {"mendeley_val": ["mendeley_val_iou_f1"],
                 "internal_sr": ["internal_sr_iou_f1", "internal_sr_pull_f1"],
                 "external": ["external_iou_f1", "external_pull_f1"],
                 "causal": ["causal_V1_low_latency_iou_f1", "causal_V2_quality_iou_f1",
                            "causal_V3_budget_15fps_iou_f1"]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=["artifact", "refit"], default="artifact")
    ap.add_argument("--with-causal", action="store_true")
    ap.add_argument("--skip-hashes", action="store_true", help="skip checkpoint hashing (fast mode)")
    args = ap.parse_args()
    man = json.load(open(MANIFEST_JSON, encoding="utf-8"))
    problems, skipped = [], []

    if not args.skip_hashes:
        for m in man["models"]:
            if m["role"] != "product":
                continue
            p = os.path.join(REPO_ROOT, m["path"])
            if not os.path.exists(p):
                problems.append(f"missing product artifact {m['path']} (ai/scripts/download_checkpoints.py)")
            elif sha256_file(p) != m["sha256"]:
                problems.append(f"hash drift {m['path']}")

    from ai.evaluation import harness as H
    if args.source == "artifact":
        from ai.core.model_artifact import load_artifact
        system = load_artifact()
    else:
        system = H.fit_system()
    fp = system_fingerprint(system)
    want_fp = man["product_system"]["fitted_system_sha256"]
    print(f"{'OK' if fp == want_fp else 'DRIFT':5s} system fingerprint ({args.source}) {fp[:16]}")
    if fp != want_fp:
        problems.append(f"fitted-system fingerprint drift {fp[:12]} != {want_fp[:12]}")

    suites = ["mendeley_val", "internal_sr", "external"] + (["causal"] if args.with_causal else [])
    runnable = []
    for s in suites:
        if os.path.exists(SUITE_INPUTS[s]):
            runnable.append(s)
        else:
            skipped.append(s)
            print(f"SKIP  suite {s}: input not present ({os.path.relpath(SUITE_INPUTS[s], REPO_ROOT)})")
    res = H.run_all(system, suites=tuple(runnable),
                    causal_variants=("V1_low_latency", "V2_quality", "V3_budget_15fps"))
    for s in runnable:
        for k in SUITE_METRICS[s]:
            v = res["headline"][k]
            want = man["headline_metrics"].get(k, {}).get("value")
            ok = want is not None and abs(v - want) < 5e-5
            if not ok:
                problems.append(f"{k}: {v} vs manifest {want}")
            print(f"{'OK' if ok else 'DRIFT':5s} {k:32s} {v:.4f}  (manifest {want})")
    if problems:
        print("\nBASELINE VERIFICATION FAILED:")
        for p in problems:
            print(" -", p)
        return 1
    print(f"\nBASELINE VERIFIED ({len(runnable)} suite(s)"
          + (f"; SKIPPED for missing inputs: {', '.join(skipped)}" if skipped else "") + ").")
    return 0


if __name__ == "__main__":
    sys.exit(main())
