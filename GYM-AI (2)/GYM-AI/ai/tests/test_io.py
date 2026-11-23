"""ai.core.io — previously completely untested (RISK_REGISTER.md R-01). These tests cover
load_annotations' failure behaviour (Phase 3 P3.7); segment_video/load_model remain the
separate, tracked R-01 issue (no saved-model artifact exists yet to load — Phase 5 scope)."""
import os

import pandas as pd
import pytest

from ai.core.io import load_annotations


def _write_csv(tmp_path, rows):
    p = tmp_path / "ann.csv"
    cols = ["video", "rep", "start_frame", "pull_frame", "end_frame", "label"]
    pd.DataFrame(rows, columns=cols).to_csv(p, index=False)
    return str(p)


def test_load_annotations_normal_case(tmp_path):
    p = _write_csv(tmp_path, [["v1.mp4", 1, 0, 5, 10, "good"],
                              ["v1.mp4", 2, 20, 25, 30, "good"]])
    ann = load_annotations(p)
    assert set(ann) == {"v1"}
    assert ann["v1"] == [dict(start=0, pull=5, end=10), dict(start=20, pull=25, end=30)]


def test_load_annotations_empty_file_raises_explicitly(tmp_path):
    """Phase 3 P3.7: used to silently return {} — indistinguishable from "legitimately zero
    videos". Must raise, naming the path, so a caller doesn't train/evaluate on nothing."""
    p = _write_csv(tmp_path, [])
    with pytest.raises(ValueError, match="no annotations found"):
        load_annotations(p)


def test_load_annotations_bad_ordering_raises():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "ann.csv")
        pd.DataFrame([["v1.mp4", 1, 10, 5, 20, "good"]],   # pull BEFORE start: invalid
                    columns=["video", "rep", "start_frame", "pull_frame", "end_frame", "label"]
                    ).to_csv(p, index=False)
        with pytest.raises(ValueError, match="not ordered"):
            load_annotations(p)
