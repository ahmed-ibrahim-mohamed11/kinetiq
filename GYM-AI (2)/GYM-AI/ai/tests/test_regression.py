"""Phase 1 behaviour contract: ai.core must reproduce the Phase 0 baseline EXACTLY.

Runs the full nested LOVO through ai.core on the committed pose files and compares the
predictions CSV byte-for-byte (md5) with the one the frozen research script produced for
ai/outputs/baselines/v2_hsmm_nested_lovo.json, then re-checks the pooled metrics.

Slow (~2 min CPU). Deselect with:  pytest -m "not slow"
"""
import hashlib
import json
import os

import numpy as np
import pandas as pd
import pytest

from ai.core.config import GRID
from ai.core.evaluation import run_cv
from ai.core.features import phase_target, prepare_video
from ai.core.io import load_annotations
from ai.tests.conftest import REPO_ROOT

BASELINE = os.path.join(REPO_ROOT, "ai", "outputs", "baselines", "v2_hsmm_nested_lovo.json")
POSES = os.path.join(REPO_ROOT, "ai", "data", "poses3d")
ANN = os.path.join(REPO_ROOT, "ai", "outputs", "manual_rep_annotations.csv")

pytestmark = pytest.mark.slow


@pytest.fixture(scope="module")
def baseline():
    with open(BASELINE) as f:
        return json.load(f)


@pytest.fixture(scope="module")
def core_results():
    ann = load_annotations(ANN)
    vids = []
    for name, gt in ann.items():
        pose = np.load(os.path.join(POSES, name + ".npy"))
        v = prepare_video(name, pose, 30.0, conf=None)      # eval_v2.ps1 passes no conf_dir
        v["gt"], v["y"] = gt, phase_target(v["T"], gt)
        vids.append(v)
    vids.sort(key=lambda v: v["name"])
    groups = {v["name"]: v["name"] for v in vids}
    return vids, run_cv(vids, groups, GRID, 0.5)


def test_predictions_csv_byte_identical(tmp_path, baseline, core_results):
    vids, results = core_results
    rows = []
    for v in vids:                                           # same construction as the frozen script
        for i, r in enumerate(results[v["name"]]["reps"], 1):
            rows.append(dict(video=v["name"], rep=i, **r))
    out = tmp_path / "predicted_reps_with_arm_timing.csv"
    pd.DataFrame(rows).to_csv(out, index=False)
    md5 = hashlib.md5(out.read_bytes()).hexdigest()
    assert md5 == baseline["inputs"]["pred"]["md5"], (
        "ai.core predictions differ from the Phase 0 baseline CSV - behaviour changed!")


def test_pooled_metrics_match_baseline(baseline, core_results):
    _, results = core_results
    tp = sum(results[n]["metrics"]["tp"] for n in results)
    fp = sum(results[n]["metrics"]["fp"] for n in results)
    fn = sum(results[n]["metrics"]["fn"] for n in results)
    pooled = baseline["results"]["iou_0.50"]["pooled"]
    assert (tp, fp, fn) == (pooled["tp"], pooled["fp"], pooled["fn"])


def test_per_video_counts_match_baseline(baseline, core_results):
    _, results = core_results
    per = baseline["results"]["iou_0.50"]["per_video"]
    for name, res in results.items():
        assert res["metrics"]["n_pred"] == per[name]["n_pred"], name
        assert res["metrics"]["tp"] == per[name]["tp"], name
