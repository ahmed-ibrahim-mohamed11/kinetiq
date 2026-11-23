"""P5.3: AnalysisResult v1.0 — strict, versioned, cross-field-consistent; committed JSON Schema
in sync; maps onto the existing edge-event contract v1.0 (shared/event-schemas)."""
import json
import os
import uuid

import pytest
from pydantic import ValidationError

from ai.core.schema import AnalysisResult, json_schema, to_edge_events
from ai.tests.conftest import REPO_ROOT


def _ind(name="rom_proxy", severity="ok"):
    return dict(indicator=name, value=0.1, normalized=0.5, threshold=0.2, severity=severity,
                message="Within typical range.", confidence="high", confidence_score=0.9)


def _rep(i, s, p, e, fps=30.0, inds=None):
    return dict(index=i, start_frame=s, pull_frame=p, end_frame=e, start_s=s / fps, pull_s=p / fps,
                end_s=e / fps, duration_s=(e - s) / fps, peak_z=0.3, lag_pull_s=0.03, amp_ratio=0.9,
                indicators=inds if inds is not None else [_ind()])


def _result(**over):
    d = dict(mode="after_set", status="OK", guard=dict(status="OK"),
             video=dict(n_frames=300, fps=30.0, width=1080, height=1920, duration_s=10.0),
             versions=dict(artifact_version="1.0.0", artifact_fingerprint="63d36c31761c95a8" * 4,
                           detector="yolo11n-pose", lifter="MotionBERT-Lite"),
             reps=[_rep(1, 10, 30, 60), _rep(2, 80, 100, 130, inds=[_ind(severity="attention")])],
             summary=dict(n_reps=2, n_reps_with_attention=1, mean_rep_duration_s=1.67))
    d.update(over)
    return d


def test_valid_result_roundtrips():
    r = AnalysisResult.model_validate(_result())
    again = AnalysisResult.model_validate_json(r.model_dump_json())
    assert again == r
    assert r.schema_version == "1.0"


@pytest.mark.parametrize("mutate,match", [
    (lambda d: d.update(unexpected="x"), "Extra inputs"),
    (lambda d: d["reps"][0].update(extra_field=1), "Extra inputs"),
    (lambda d: d["reps"][0].update(pull_frame=5), "start < pull <= end"),
    (lambda d: d.update(status="NO_PERSON", guard=dict(status="NO_PERSON")), "pre-decode abstention"),
    (lambda d: d.update(guard=dict(status="UNSUPPORTED_EXERCISE")), "guard.status"),
    (lambda d: d["reps"][1].update(index=5), "numbered 1..n"),
    (lambda d: d["reps"][1].update(end_frame=400, pull_frame=350, start_frame=320), "n_frames"),
    (lambda d: d["summary"].update(n_reps=3), "n_reps"),
    (lambda d: d["summary"].update(n_reps_with_attention=0), "n_reps_with_attention"),
    (lambda d: d["reps"][0]["indicators"][0].update(confidence_score=1.5), "less than or equal"),
    (lambda d: d.update(schema_version="2.0"), "schema_version"),
    (lambda d: d["video"].update(fps=0), "greater than 0"),
])
def test_invalid_results_rejected(mutate, match):
    d = _result()
    mutate(d)
    with pytest.raises(ValidationError, match=match):
        AnalysisResult.model_validate(d)


def test_unsupported_exercise_may_carry_reps_but_status_says_so():
    d = _result(status="UNSUPPORTED_EXERCISE", guard=dict(status="UNSUPPORTED_EXERCISE"))
    AnalysisResult.model_validate(d)


def test_committed_json_schema_is_in_sync():
    p = os.path.join(REPO_ROOT, "ai", "schemas", "analysis_result.v1.json")
    assert os.path.exists(p), "run: python -m ai.core.schema"
    assert json.load(open(p, encoding="utf-8")) == json_schema()


def test_maps_onto_existing_edge_event_contract():
    r = AnalysisResult.model_validate(_result())
    evs = to_edge_events(r, uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), agent_version="0.1.0")
    assert [e.rep_index for e in evs] == [1, 2]
    assert evs[0].frames.pull == 30
    assert evs[0].metrics.duration_ms == pytest.approx(1666.7, abs=0.1)
    assert evs[0].model_version.startswith("1.0.0+")
    # round-trip through the contract's own validation (per-type required fields)
    import sys
    sys.path.insert(0, os.path.join(REPO_ROOT, "shared", "event-schemas", "python"))
    from gym_ai_events import EdgeEvent
    for e in evs:
        EdgeEvent.model_validate_json(e.model_dump_json())
