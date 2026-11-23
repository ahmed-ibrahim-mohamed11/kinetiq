"""The pure protocol (ai.evaluation.protocol) must equal the frozen train_eval implementation."""
import os
import sys

import numpy as np
import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "ai", "research", "phase27"))

import train_eval as te  # noqa: E402

from ai.evaluation.protocol import evaluate_dontcare, pull_tol_frames  # noqa: E402


def _reps(rng, n, T):
    out = []
    for _ in range(n):
        s = int(rng.integers(0, T - 20))
        p = s + int(rng.integers(2, 15))
        e = p + int(rng.integers(2, 15))
        out.append(dict(start=s, pull=p, end=e))
    return sorted(out, key=lambda r: r["start"])


@pytest.mark.parametrize("protocol", ["v1", "v2"])
@pytest.mark.parametrize("rule", ["iou", "pull"])
@pytest.mark.parametrize("tol", [8, 13, 16])
def test_pure_protocol_equals_frozen(protocol, rule, tol):
    rng = np.random.default_rng(1000 + tol)
    old = te.PULL_TOL_F
    try:
        te.PULL_TOL_F = tol
        for _ in range(150):
            T = int(rng.integers(60, 400))
            pred = _reps(rng, int(rng.integers(0, 9)), T)
            gtc = _reps(rng, int(rng.integers(0, 7)), T)
            gtu = _reps(rng, int(rng.integers(0, 3)), T)
            a = te.evaluate_dontcare(pred, gtc, gtu, rule, protocol=protocol)
            b = evaluate_dontcare(pred, gtc, gtu, rule, protocol, tol)
            for k in ("n_gt", "n_unc", "n_pred", "tp", "fp", "fn", "n_ignored", "matched"):
                assert a[k] == b[k], (k, a[k], b[k])
            for k in ("start_mae_frames", "pull_mae_frames", "end_mae_frames"):
                assert (np.isnan(a[k]) and np.isnan(b[k])) or a[k] == pytest.approx(b[k])
    finally:
        te.PULL_TOL_F = old


def test_unknown_rule_and_protocol_raise():
    with pytest.raises(ValueError):
        evaluate_dontcare([], [], [], "bogus", "v2", 8)
    with pytest.raises(ValueError):
        evaluate_dontcare([], [], [], "iou", "v3", 8)


def test_seconds_defined_tolerance():
    assert pull_tol_frames(15.0) == 8
    assert pull_tol_frames(30.0) == 16
    assert pull_tol_frames(25.0) == 13
