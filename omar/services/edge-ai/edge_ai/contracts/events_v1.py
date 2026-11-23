"""
events.v1 — Python mirror of packages/api-contracts/src/events.ts.

Both sides are validated against packages/api-contracts/fixtures/events_v1_valid.json
in tests/test_contracts.py. Any change here must be made in the TypeScript file too.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Annotated, Any, Literal, Union, cast
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, TypeAdapter, create_model

from edge_ai.constants import EVENTS_SCHEMA_VERSION

Confidence = Annotated[float, Field(ge=0.0, le=1.0)]
NonNegInt = Annotated[int, Field(ge=0)]
PosInt = Annotated[int, Field(gt=0)]

CheckInMethod = Literal["face", "qr", "nfc", "staff_manual"]


# ── Payloads ────────────────────────────────────────────────────────────────


class DeviceHeartbeatPayload(BaseModel):
    software_version: str
    uptime_seconds: NonNegInt
    gpu_utilization: Confidence | None
    gpu_memory_used_mb: Annotated[float, Field(ge=0)] | None
    cpu_utilization: Confidence | None
    queue_depth: NonNegInt
    cameras_online: NonNegInt
    cameras_total: NonNegInt
    avg_pipeline_latency_ms: Annotated[float, Field(ge=0)] | None


class CameraStatusPayload(BaseModel):
    camera_id: UUID
    status: Literal["online", "reconnecting", "offline", "degraded"]
    fps_measured: Annotated[float, Field(ge=0)] | None
    resolution_w: PosInt | None
    resolution_h: PosInt | None
    error: Annotated[str, Field(max_length=500)] | None


class IdentityCheckInPayload(BaseModel):
    member_id: UUID
    method: CheckInMethod
    confidence: Confidence
    camera_id: UUID | None
    global_track_id: str | None
    consent_record_id: UUID | None
    presentation_attack_score: Confidence | None


class IdentityCheckOutPayload(BaseModel):
    member_id: UUID
    method: CheckInMethod
    confidence: Confidence
    camera_id: UUID | None


class TrackStartedPayload(BaseModel):
    global_track_id: str
    member_id: UUID | None
    identity_confidence: Confidence
    tracking_confidence: Confidence
    first_camera_id: UUID


class TrackEndedPayload(BaseModel):
    global_track_id: str
    reason: Literal["left_view", "checkout", "merged", "timeout", "ambiguous_identity"]
    last_camera_id: UUID | None


class TrackingUncertainPayload(BaseModel):
    global_track_id: str
    member_id: UUID | None
    from_: datetime = Field(alias="from")
    to: datetime
    reason: Literal[
        "occlusion", "no_camera_coverage", "identity_ambiguous", "camera_offline", "low_tracking_confidence"
    ]

    model_config = {"populate_by_name": True}


class EquipmentUsageStartedPayload(BaseModel):
    usage_local_id: str
    global_track_id: str
    member_id: UUID | None
    equipment_id: UUID
    zone_id: UUID
    camera_id: UUID
    association_confidence: Confidence


class EquipmentUsageEndedPayload(BaseModel):
    usage_local_id: str
    reason: Literal["left_zone", "track_lost", "checkout", "timeout"]


class ExerciseActivityStartedPayload(BaseModel):
    activity_local_id: str
    usage_local_id: str
    exercise_id: UUID
    classification_confidence: Confidence
    classifier_model_version_id: UUID | None


class ExerciseActivityEndedPayload(BaseModel):
    activity_local_id: str
    active_seconds: Annotated[float, Field(ge=0)]


class SetStartedPayload(BaseModel):
    set_local_id: str
    activity_local_id: str
    set_index: PosInt
    rest_before_seconds: Annotated[float, Field(ge=0)] | None


class RepCountedPayload(BaseModel):
    set_local_id: str
    rep_index: PosInt
    camera_id: UUID
    confidence: Confidence
    quality_label: None = None  # reserved for Phase 9


class SetCompletedPayload(BaseModel):
    set_local_id: str
    rep_count: NonNegInt
    confidence: Confidence
    verified: bool


class BiometricEnrolledPayload(BaseModel):
    member_id: UUID
    consent_record_id: UUID
    template_ref: Annotated[str, Field(min_length=8, max_length=64)]
    key_id: Annotated[str, Field(min_length=4, max_length=64)]
    model_name: Annotated[str, Field(min_length=1, max_length=100)]
    quality: Confidence | None


class BiometricRevokedPayload(BaseModel):
    member_id: UUID
    template_ref: Annotated[str, Field(min_length=8, max_length=64)]
    reason: Literal["consent_withdrawn", "member_request", "staff_request", "key_rotation", "retired_device"]


# ── Envelope ────────────────────────────────────────────────────────────────

EventType = Literal[
    "device.heartbeat",
    "camera.status",
    "identity.checkin",
    "identity.checkout",
    "track.started",
    "track.ended",
    "tracking.uncertain",
    "equipment.usage.started",
    "equipment.usage.ended",
    "exercise.activity.started",
    "exercise.activity.ended",
    "set.started",
    "rep.counted",
    "set.completed",
    "biometric.enrolled",
    "biometric.revoked",
]

PAYLOAD_TYPES: dict[str, type[BaseModel]] = {
    "device.heartbeat": DeviceHeartbeatPayload,
    "camera.status": CameraStatusPayload,
    "identity.checkin": IdentityCheckInPayload,
    "identity.checkout": IdentityCheckOutPayload,
    "track.started": TrackStartedPayload,
    "track.ended": TrackEndedPayload,
    "tracking.uncertain": TrackingUncertainPayload,
    "equipment.usage.started": EquipmentUsageStartedPayload,
    "equipment.usage.ended": EquipmentUsageEndedPayload,
    "exercise.activity.started": ExerciseActivityStartedPayload,
    "exercise.activity.ended": ExerciseActivityEndedPayload,
    "set.started": SetStartedPayload,
    "rep.counted": RepCountedPayload,
    "set.completed": SetCompletedPayload,
    "biometric.enrolled": BiometricEnrolledPayload,
    "biometric.revoked": BiometricRevokedPayload,
}


class _Envelope(BaseModel):
    schema_version: Literal["1"] = EVENTS_SCHEMA_VERSION  # type: ignore[assignment]
    event_id: UUID
    device_id: UUID
    branch_id: UUID
    sequence: NonNegInt
    occurred_at: datetime
    sent_at: datetime


def _make(event_type: str, payload_cls: type[BaseModel]) -> type[BaseModel]:
    fields: dict[str, Any] = {
        "event_type": (Literal[event_type], ...),  # type: ignore[valid-type]
        "payload": (payload_cls, ...),
    }
    model: type[BaseModel] = create_model(f"Event_{event_type.replace('.', '_')}", __base__=_Envelope, **fields)
    return model


_EVENT_MODELS = tuple(_make(t, p) for t, p in PAYLOAD_TYPES.items())
EdgeEvent = Annotated[Union[_EVENT_MODELS], Field(discriminator="event_type")]  # type: ignore[valid-type]  # noqa: UP007
_event_adapter: TypeAdapter[Any] = TypeAdapter(EdgeEvent)


def parse_event(data: dict[str, Any]) -> BaseModel:
    """Validate an envelope dict; raises pydantic.ValidationError on failure."""
    return cast(BaseModel, _event_adapter.validate_python(data))


class EventBatch(BaseModel):
    schema_version: Literal["1"] = EVENTS_SCHEMA_VERSION  # type: ignore[assignment]
    batch_id: UUID
    device_id: UUID
    sent_at: datetime
    events: list[dict[str, Any]] = Field(min_length=1, max_length=500)


def normalize_json(value: Any) -> Any:
    """Make Python JSON byte-identical to JavaScript JSON.stringify for our value domain.

    * integral floats become ints (JS prints 1.0 as 1)
    * other floats are rounded to 4 dp (DB precision is numeric(5,4); avoids exponent notation)
    * dicts keep insertion order, as JSON.parse/stringify does for non-integer keys
    """
    if isinstance(value, bool) or value is None or isinstance(value, (int, str)):
        return value
    if isinstance(value, float):
        if value.is_integer():
            return int(value)
        rounded = round(value, 4)
        # e.g. 0.00001 → 0.0 must serialize as 0 (JS) not 0.0 (Python)
        return int(rounded) if rounded.is_integer() else rounded
    if isinstance(value, dict):
        return {str(k): normalize_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [normalize_json(v) for v in value]
    return value


def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def build_event(
    *,
    event_type: EventType,
    payload: BaseModel,
    device_id: UUID,
    branch_id: UUID,
    sequence: int,
    occurred_at: datetime,
    sent_at: datetime | None = None,
    event_id: UUID | None = None,
) -> dict[str, Any]:
    """Build a JSON-ready envelope dict and validate it against the contract."""
    envelope = {
        "schema_version": EVENTS_SCHEMA_VERSION,
        "event_id": str(event_id or uuid4()),
        "device_id": str(device_id),
        "branch_id": str(branch_id),
        "sequence": sequence,
        "occurred_at": _iso(occurred_at),
        "sent_at": _iso(sent_at or datetime.now(timezone.utc)),
        "event_type": event_type,
        "payload": normalize_json(json.loads(payload.model_dump_json(by_alias=True))),
    }
    parse_event(envelope)  # raise early if we built something invalid
    return envelope


def events_sha256_hex(events: list[dict[str, Any]]) -> str:
    """Hash over the exact JSON the server will see. JSON.stringify compatibility:
    no spaces, keys in insertion order, ensure_ascii=False."""
    body = json.dumps(normalize_json(events), separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def canonical_batch_string(device_id: str, batch_id: str, sent_at: str, events_hash_hex: str) -> str:
    return f"{device_id}\n{batch_id}\n{sent_at}\n{events_hash_hex}"
