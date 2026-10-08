"""Phase A regression suite for the Phase 2.7/2.8 result-dictionary collision class.

Policy: the original broken pattern is reconstructed HERE as a fixture (it is not reintroduced
into any runner). These tests fail under the old architecture and pass under the canonical
schema, proving the class of bug is structurally prevented."""
import os
import sys

import pytest

from ai.tests.conftest import REPO_ROOT

sys.path.insert(0, os.path.join(REPO_ROOT, "ai", "research", "phase27"))
from result_schema import RESERVED_META, ResultSchemaError, check_metrics, make_row  # noqa: E402
from train_eval import evaluate_dontcare  # noqa: E402


def _old_pattern_build_row(**meta_kwargs):
    """Faithful reconstruction of the pre-fix runner pattern (git d1bbef4 era):
    metric functions returned metadata ('rule') inside the metrics dict, and runners did
    dict(video=..., rule=..., **metrics). This fixture reproduces the exact TypeError."""
    metrics_with_smuggled_meta = {"rule": "iou", "tp": 1, "fp": 0, "fn": 0}
    return dict(**meta_kwargs, **metrics_with_smuggled_meta)


def test_original_failure_is_reproducible_as_fixture():
    with pytest.raises(TypeError, match="multiple values"):
        _old_pattern_build_row(video="v1", rule="iou")


def test_metric_functions_no_longer_emit_reserved_metadata():
    m = evaluate_dontcare([dict(start=1, pull=5, end=9)],
                          [dict(start=0, pull=5, end=10)], [], "iou")
    assert not (RESERVED_META & set(m)), "metrics must not carry runner metadata"
    check_metrics({k: v for k, v in m.items() if k != "matched"})   # passes schema check


def test_make_row_raises_on_collision_instead_of_crashing_late():
    with pytest.raises(ResultSchemaError, match="collision"):
        make_row({"video_id": "v", "n_extra": 1}, {"n_extra": 2, "tp": 1})


def test_make_row_rejects_smuggled_metadata_even_without_collision():
    with pytest.raises(ResultSchemaError, match="reserved"):
        make_row({"video_id": "v"}, {"rule": "iou", "tp": 1})


def test_make_row_happy_path_merges_disjoint_dicts():
    row = make_row({"video_id": "v", "rule": "iou"}, {"tp": 1, "fp": 0})
    assert row == {"video_id": "v", "rule": "iou", "tp": 1, "fp": 0}


def test_runner_outputs_carry_rule_exactly_once(tmp_path):
    # end-to-end shape check on a committed artifact: every row has a single 'rule' column value
    import pandas as pd
    f = os.path.join(REPO_ROOT, "ai", "outputs", "phase27", "motionbert_lite", "videos_val_fixed.csv")
    df = pd.read_csv(f)
    assert "rule" in df.columns and df.rule.isin(["iou", "pull"]).all()
    assert not df.columns.duplicated().any()
