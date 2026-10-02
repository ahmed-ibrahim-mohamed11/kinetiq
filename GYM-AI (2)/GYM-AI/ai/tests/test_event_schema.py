"""Keeps the three event-contract mirrors in sync: JSON Schema (source of truth),
pydantic models, TypeScript types. Every pydantic-valid example must validate against
the JSON Schema; per-type required fields must reject incomplete events in BOTH."""
import json
import os
import sys
import uuid

import pytest

from ai.tests.conftest import REPO_ROOT

sys.path.insert(0, os.path.join(REPO_ROOT, "shared", "event-schemas", "python"))

jsonschema = pytest.importorskip("jsonschema")
pydantic = pytest.importorskip("pydantic")

from gym_ai_events import EdgeEvent, EventType  # noqa: E402

SCHEMA = json.load(open(os.path.join(REPO_ROOT, "shared", "event-schemas", "v1", "events.schema.json")))
DEV = str(uuid.uuid4())
SES = str(uuid.uuid4())
MAC = str(uuid.uuid4())

EXAMPLES = {
    EventType.rep_completed: dict(
        session_id=SES, machine_id=MAC, rep_index=4, exercise="seated_row",
        frames=dict(start=143, pull=156, end=174),
        metrics=dict(duration_ms=1033, pull_duration_ms=433, return_duration_ms=600,
                     pull_left_frame=158, pull_right_frame=157, lag_pull_ms=-33.5,
                     lag_onset_ms=0.0, amp_ratio=0.63, peak=0.57, peak_z=-1.34),
        model_version="rep-seg-core-hsmm-0.2.0"),
    EventType.session_started: dict(session_id=SES, machine_id=MAC, exercise="seated_row"),
    EventType.session_ended: dict(session_id=SES),
    EventType.device_heartbeat: dict(heartbeat=dict(camera_ok=True, capture_fps=29.8,
                                                    inference_fps=12.0, latency_ms=350.0, queue_depth=0)),
    EventType.camera_error: dict(error=dict(code="capture_timeout", message="no frame for 5s")),
    EventType.alert_created: dict(session_id=SES, alert=dict(
        alert_type="asymmetry_review", severity="warning",
        message="Potential movement deviation - coach review")),
    EventType.model_loaded: dict(model_version="rep-seg-core-hsmm-0.2.0"),
}


@pytest.mark.parametrize("etype", list(EventType))
def test_pydantic_examples_validate_against_json_schema(etype):
    ev = EdgeEvent(event_type=etype, device_id=DEV, agent_version="edge-agent-0.1.0", **EXAMPLES[etype])
    payload = json.loads(ev.model_dump_json(exclude_none=True))
    jsonschema.validate(payload, SCHEMA)          # raises on mismatch


@pytest.mark.parametrize("etype,missing", [
    (EventType.rep_completed, "frames"),
    (EventType.session_started, "machine_id"),
    (EventType.device_heartbeat, "heartbeat"),
    (EventType.alert_created, "alert"),
    (EventType.model_loaded, "model_version"),
])
def test_both_sides_reject_missing_required(etype, missing):
    kw = {k: v for k, v in EXAMPLES[etype].items() if k != missing}
    with pytest.raises(pydantic.ValidationError):
        EdgeEvent(event_type=etype, device_id=DEV, agent_version="x", **kw)
    ok = EdgeEvent(event_type=etype, device_id=DEV, agent_version="x", **EXAMPLES[etype])
    payload = json.loads(ok.model_dump_json(exclude_none=True))
    payload.pop(missing)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(payload, SCHEMA)


def test_schema_rejects_unknown_event_type_and_extra_fields():
    base = dict(schema_version="1.0", event_type="model_loaded", event_uuid=str(uuid.uuid4()),
                device_id=DEV, agent_version="x", model_version="m")
    jsonschema.validate(base, SCHEMA)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({**base, "event_type": "nonsense"}, SCHEMA)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({**base, "surprise": 1}, SCHEMA)
