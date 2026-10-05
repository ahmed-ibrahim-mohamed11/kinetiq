"""Phase 2.7: the uncertain-GT don't-care matching rule (required properties from the owner spec)."""
import os
import sys

import pytest

from ai.tests.conftest import REPO_ROOT

sys.path.insert(0, os.path.join(REPO_ROOT, "ai", "research", "phase27"))
from train_eval import evaluate_dontcare  # noqa: E402

CERT = [dict(start=10, pull=20, end=30)]
UNC = [dict(start=100, pull=110, end=120)]


def P(s, p, e):
    return dict(start=s, pull=p, end=e)


def test_uncertain_missed_is_not_fn():
    m = evaluate_dontcare([P(11, 21, 29)], CERT, UNC, "iou")
    assert m["tp"] == 1 and m["fp"] == 0 and m["fn"] == 0          # uncertain miss: no FN


def test_one_prediction_matching_uncertain_is_ignored_not_fp_not_tp():
    m = evaluate_dontcare([P(11, 21, 29), P(101, 111, 119)], CERT, UNC, "iou")
    assert m["tp"] == 1 and m["fp"] == 0 and m["fn"] == 0
    assert m["n_ignored"] == 1                                      # absorbed, never rewarded


def test_multiple_predictions_around_one_uncertain_cannot_all_hide():
    preds = [P(101, 111, 119), P(102, 112, 121), P(99, 109, 118)]
    m = evaluate_dontcare(preds, [], UNC, "iou")
    assert m["n_ignored"] == 1                                      # one-to-one absorption only
    assert m["fp"] == 2                                             # the extras are penalised


def test_certain_matching_takes_priority_over_ignore():
    # a prediction overlapping a certain rep must count as TP, not be absorbed by a nearby uncertain
    cert = [dict(start=100, pull=108, end=118)]
    m = evaluate_dontcare([P(101, 111, 119)], cert, UNC, "iou")
    assert m["tp"] == 1 and m["fp"] == 0 and m["n_ignored"] == 0


def test_far_prediction_is_fp_despite_uncertain_elsewhere():
    m = evaluate_dontcare([P(300, 310, 320)], CERT, UNC, "iou")
    assert m["fp"] == 1 and m["fn"] == 1 and m["n_ignored"] == 0


def test_pull_rule_uses_tolerance():
    m = evaluate_dontcare([P(8, 24, 33)], CERT, [], "pull")         # pull err 4 <= 8 tolerance
    assert m["tp"] == 1
    m = evaluate_dontcare([P(8, 40, 55)], CERT, [], "pull")         # pull err 20 > 8
    assert m["tp"] == 0 and m["fp"] == 1 and m["fn"] == 1


# ---- protocol v2 (corrected): edge don't-care + relaxed uncertain absorption ----
def test_v2_edge_prediction_before_first_annotation_ignored():
    m = evaluate_dontcare([P(1, 4, 8), P(11, 21, 29)], CERT, UNC, "iou", protocol="v2")
    assert m["tp"] == 1 and m["fp"] == 0 and m["n_ignored"] == 1


def test_v2_edge_rule_cannot_hide_mid_span_predictions():
    # between certain (10-30) and uncertain (100-120): inside the annotated span -> still FP
    m = evaluate_dontcare([P(11, 21, 29), P(55, 62, 70)], CERT, UNC, "iou", protocol="v2")
    assert m["tp"] == 1 and m["fp"] == 1


def test_v2_relaxed_uncertain_absorbs_on_pull_only():
    # IoU with uncertain < 0.25 but pull within tolerance -> absorbed under v2, FP under v1
    pred = [P(107, 112, 116)]
    v1 = evaluate_dontcare(pred, [], [dict(start=95, pull=110, end=140)], "iou", protocol="v1")
    v2 = evaluate_dontcare(pred, [], [dict(start=95, pull=110, end=140)], "iou", protocol="v2")
    assert v1["fp"] == 1
    assert v2["fp"] == 0 and v2["n_ignored"] == 1


def test_v2_uncertain_absorption_still_one_to_one():
    preds = [P(101, 111, 119), P(103, 113, 121), P(98, 108, 117)]
    m = evaluate_dontcare(preds, [], UNC, "iou", protocol="v2")
    assert m["n_ignored"] == 1 and m["fp"] == 2
