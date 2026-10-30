"""The Python contract must accept exactly the fixtures the TypeScript contract accepts."""

import json
from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest
from conftest import FIXTURES
from pydantic import ValidationError

from edge_ai.contracts.events_v1 import (
    PAYLOAD_TYPES,
    RepCountedPayload,
    build_event,
    events_sha256_hex,
    normalize_json,
    parse_event,
)


@pytest.fixture(scope="module")
def fixtures() -> list[dict]:  # type: ignore[type-arg]
    return json.loads(FIXTURES.read_text(encoding="utf-8"))


def test_all_shared_fixtures_parse(fixtures):  # type: ignore[no-untyped-def]
    for ev in fixtures:
        parse_event(ev)


def test_every_event_type_has_payload_model():  # type: ignore[no-untyped-def]
    assert len(PAYLOAD_TYPES) == 16


def test_rejects_unknown_schema_version(fixtures):  # type: ignore[no-untyped-def]
    bad = dict(fixtures[0], schema_version="2")
    with pytest.raises(ValidationError):
        parse_event(bad)


def test_rejects_quality_label(fixtures):  # type: ignore[no-untyped-def]
    rep = next(e for e in fixtures if e["event_type"] == "rep.counted")
    bad = dict(rep, payload=dict(rep["payload"], quality_label="correct"))
    with pytest.raises(ValidationError):
        parse_event(bad)


def test_rejects_confidence_out_of_range(fixtures):  # type: ignore[no-untyped-def]
    ci = next(e for e in fixtures if e["event_type"] == "identity.checkin")
    bad = dict(ci, payload=dict(ci["payload"], confidence=1.2))
    with pytest.raises(ValidationError):
        parse_event(bad)


def test_build_event_roundtrip():  # type: ignore[no-untyped-def]
    env = build_event(
        event_type="rep.counted",
        payload=RepCountedPayload(
            set_local_id="s-1", rep_index=3, camera_id=UUID("44444444-4444-4444-8444-444444444445"), confidence=1.0
        ),
        device_id=uuid4(),
        branch_id=uuid4(),
        sequence=5,
        occurred_at=datetime(2026, 10, 27, 8, 0, 0, 123000, tzinfo=timezone.utc),
    )
    assert env["occurred_at"] == "2026-10-27T08:00:00.123Z"
    assert env["payload"]["quality_label"] is None
    # integral float must serialize like JS (1, not 1.0)
    assert json.dumps(env["payload"]["confidence"]) == "1"
    parse_event(env)


def test_normalize_json_matches_js_stringify_rules():  # type: ignore[no-untyped-def]
    assert normalize_json({"a": 1.0, "b": 0.93, "c": [2.0, 0.123456], "d": True, "e": None}) == {
        "a": 1,
        "b": 0.93,
        "c": [2, 0.1235],
        "d": True,
        "e": None,
    }
    # hash must be stable regardless of float spelling, including values that round to an integer
    assert events_sha256_hex([{"x": 1.0}]) == events_sha256_hex([{"x": 1}])
    assert normalize_json({"tiny": 0.00001, "almost": 2.00004}) == {"tiny": 0, "almost": 2}
