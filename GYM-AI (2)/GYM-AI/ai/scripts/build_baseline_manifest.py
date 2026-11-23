#!/usr/bin/env python3
"""Build docs/AI_CORE_BASELINE_MANIFEST.json: the locked AI-core baseline (Phase 0).

Records commit, environment, checkpoint/config/data hashes, the fitted-system fingerprint and
the headline metrics recomputed NOW by the shared harness (ai/evaluation/harness.py). Every later
change is compared against these numbers with ai/scripts/verify_baseline.py.

Usage:  python ai/scripts/build_baseline_manifest.py [--with-causal] [--run-tests]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from ai.scripts.baseline_lib import (CONFIGS, DATA, MANIFEST_JSON, MODELS, REPO_ROOT,  # noqa: E402
                                     environment, git_info, sha256_file, system_fingerprint)

BASELINE_LINEAGE = "92c0bcd2fc9133e8f0f4ba8bf3c152bdecc7a7e3"

# Values as documented at 92c0bcd (docs/PROJECT_STATE.md, REALTIME_FEASIBILITY.md) - the
# manifest records whether today's recomputation reproduces them.
DOCUMENTED = {
    "mendeley_val_iou_f1": 0.914, "internal_sr_iou_f1": 0.841, "internal_sr_pull_f1": 0.853,
    "external_iou_f1": 0.632, "external_pull_f1": 0.842,
    "causal_V1_low_latency_iou_f1": 0.740, "causal_V2_quality_iou_f1": 0.806,
    "causal_V3_budget_15fps_iou_f1": 0.812,
}


def run_tests() -> dict:
    p = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
                       cwd=REPO_ROOT, capture_output=True, text=True)
    tail = p.stdout.strip().splitlines()[-1] if p.stdout.strip() else ""
    m = re.search(r"(\d+) passed", tail)
    f = re.search(r"(\d+) failed", tail)
    return dict(command="python -m pytest -q", summary=tail, passed=int(m.group(1)) if m else 0,
                failed=int(f.group(1)) if f else 0, exit_code=p.returncode)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--with-causal", action="store_true", help="also run the Mode B causal suite (~1-3 min CPU)")
    ap.add_argument("--run-tests", action="store_true")
    args = ap.parse_args()

    from ai.evaluation import harness as H
    system = H.fit_system()
    suites = ("mendeley_val", "internal_sr", "external") + (("causal",) if args.with_causal else ())
    res = H.run_all(system, suites=suites,
                    causal_variants=("V1_low_latency", "V2_quality", "V3_budget_15fps"))
    head = res["headline"]

    models = []
    for role, kind, rel, src, lic in MODELS:
        p = os.path.join(REPO_ROOT, rel)
        models.append(dict(role=role, kind=kind, path=rel, present=os.path.exists(p),
                           bytes=os.path.getsize(p) if os.path.exists(p) else None,
                           sha256=sha256_file(p) if os.path.exists(p) else None,
                           source=src, license=lic))
    configs = [dict(path=c, sha256_lf=sha256_file(os.path.join(REPO_ROOT, c))) for c in CONFIGS]
    data = [dict(name=n, path=p, sha256_lf=sha256_file(os.path.join(REPO_ROOT, p))) for n, p in DATA]

    metrics = {}
    for k, v in head.items():
        doc = DOCUMENTED.get(k)
        metrics[k] = dict(value=v, documented=doc,
                          reproduces_documented=(doc is not None and abs(round(v, 3) - doc) < 1e-9))
    pooled = {s: res[s]["pooled"] for s in ("mendeley_val", "internal_sr", "external")}
    if "causal" in res:
        pooled["causal"] = {vn: r["pooled"] for vn, r in res["causal"].items()}

    manifest = dict(
        manifest_version="1.0",
        created_utc=dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        baseline_lineage_commit=BASELINE_LINEAGE,
        git=git_info(),
        environment=environment(),
        product_system=dict(
            description="YOLO11n-pose top-8 -> deterministic tracker -> COCO->H36M -> MotionBERT-Lite "
                        "(243-frame chunks, flip-TTA) -> torso-frame features -> ridge phase model + "
                        "explicit-duration HSMM (masked supervision, refit on Mendeley BACK TRAIN) -> "
                        "reps START/PULL/END -> form indicators v1 (seated_row thresholds PROVISIONAL)",
            cfg=system.cfg.__dict__, n_train_videos=system.meta["n_train_videos"],
            fitted_system_sha256=system_fingerprint(system)),
        models=models, configs=configs, data=data,
        protocol=dict(name="v2", pull_tolerance_s=round(8 / 15.0, 4), iou_threshold=0.5,
                      mae_pooling="over matched pairs (seconds); legacy docs used per-video means",
                      locked_sets=["Mendeley TEST (5 subjects; one run per frozen candidate)",
                                   "Domain C fresh athletes (not yet recorded)"]),
        headline_metrics=metrics,
        pooled=pooled,
        split_roles=dict(
            mendeley_train="TRAIN (14 subjects) - fitting only",
            mendeley_val="DEV (5 subjects) - model/config selection (used since Phase 2.7)",
            mendeley_test="LOCKED TEST (5 subjects) - single runs only; used once in Phase 2.7",
            internal_sr="DEV / CONTAMINATED - chose MB-Lite as product primary + provisional SR form thresholds",
            external="STRESS REFERENCE - eval-only, never tuned on; Instagram reels (licensing caveat, see LICENSING.md)",
            domain_c="FINAL TEST - future >=10 fresh athletes; untouched"),
    )
    if args.run_tests:
        manifest["tests"] = run_tests()
    with open(MANIFEST_JSON, "w", encoding="utf-8", newline="\n") as f:
        json.dump(manifest, f, indent=1, default=str)
    print(json.dumps({k: v["value"] for k, v in metrics.items()}, indent=1))
    print("fitted_system_sha256", manifest["product_system"]["fitted_system_sha256"])
    print("written", os.path.relpath(MANIFEST_JSON, REPO_ROOT))


if __name__ == "__main__":
    main()
