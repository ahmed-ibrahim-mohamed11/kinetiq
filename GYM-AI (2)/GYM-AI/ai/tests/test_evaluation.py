import numpy as np
import pytest

from ai.core.evaluation import _iou, composite, evaluate_video, match_reps


def test_iou():
    assert _iou((0, 10), (0, 10)) == 1.0
    assert _iou((0, 10), (5, 15)) == pytest.approx(5 / 15)
    assert _iou((0, 10), (20, 30)) == 0.0


def test_match_reps_one_to_one_greedy():
    gt = [dict(start=0, end=10), dict(start=20, end=30)]
    pred = [dict(start=1, end=11), dict(start=2, end=12), dict(start=21, end=31)]
    pairs = match_reps(pred, gt, 0.5)
    assert len(pairs) == 2
    assert (0, 0) in pairs and (2, 1) in pairs       # best IoU wins; second overlapping pred unmatched


def test_evaluate_video_counts_and_errors():
    gt = [dict(start=0, pull=5, end=10), dict(start=20, pull=25, end=30)]
    pred = [dict(start=1, pull=7, end=11)]
    m = evaluate_video(pred, gt, 0.5)
    assert (m["tp"], m["fp"], m["fn"]) == (1, 0, 1)
    assert m["P"] == 1.0 and m["R"] == 0.5
    assert m["err"]["pull"] == [2]
    assert m["pull_err_frac"] == pytest.approx(2 / 10)


def test_evaluate_video_empty_pred():
    m = evaluate_video([], [dict(start=0, pull=5, end=10)], 0.5)
    assert (m["tp"], m["fp"], m["fn"]) == (0, 0, 1) and m["F1"] == 0.0
    assert np.isnan(m["pull_err_frac"])


def test_composite_penalises_timing():
    good = dict(F1=1.0, pull_err_frac=0.0)
    late = dict(F1=1.0, pull_err_frac=0.4)
    assert composite(good) > composite(late)
    nanm = dict(F1=1.0, pull_err_frac=float("nan"))
    assert composite(nanm) == pytest.approx(1.0 - 0.25)
