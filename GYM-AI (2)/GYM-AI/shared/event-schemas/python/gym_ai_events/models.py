"""Pydantic mirror of shared/event-schemas/v1/events.schema.json (schema_version 1.0).

Used by the edge agent (Phase 5) and the event simulator (Phase 4). Keep in sync with the
JSON Schema and the TypeScript types; the sync is asserted by ai/tests/test_event_schema.py.
"""
from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

SCHEMA_VERSION = "1.0"


class EventType(str, enum.Enum):
    rep_completed = "rep_completed"
    session_started = "session_started"
    session_ended = "session_ended"
    device_heartbeat = "device_heartbeat"
    camera_error = "camera_error"
    alert_created = "alert_created"
    model_loaded = "model_loaded"


class Frames(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start: int = Field(ge=0)
    pull: int = Field(ge=0)
    end: int = Field(ge=0)


class RepMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")
    duration_ms: Optional[float] = None
    pull_duration_ms: Optional[float] = None
    return_duration_ms: Optional[float] = None
    pull_left_frame: Optional[int] = None
    pull_right_frame: Optional[int] = None
    lag_pull_ms: Optional[float] = None
    lag_onset_ms: Optional[float] = None
    amp_ratio: Optional[float] = None
    peak: Optional[float] = None
    peak_z: Optional[float] = None


class HeartbeatPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    camera_ok: bool
    capture_fps: Optional[float] = None
    inference_fps: Optional[float] = None
    latency_ms: Optional[float] = None
    queue_depth: Optional[int] = None
    error_code: Optional[str] = None


class AlertPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    alert_type: str
    severity: str  # info | warning | critical
    message: str
    rep_event_uuid: Optional[uuid.UUID] = None


class CameraErrorPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str
    message: str


class EdgeEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: str = SCHEMA_VERSION
    event_type: EventType
    event_uuid: uuid.UUID = Field(default_factory=uuid.uuid4)
    device_id: uuid.UUID
    ts: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    agent_version: str
    model_version: Optional[str] = None
    session_id: Optional[uuid.UUID] = None
    machine_id: Optional[uuid.UUID] = None
    exercise: Optional[str] = None
    rep_index: Optional[int] = Field(default=None, ge=1)
    frames: Optional[Frames] = None
    metrics: Optional[RepMetrics] = None
    heartbeat: Optional[HeartbeatPayload] = None
    alert: Optional[AlertPayload] = None
    error: Optional[CameraErrorPayload] = None

    _REQUIRED = {
        EventType.rep_completed: ("session_id", "machine_id", "rep_index", "frames", "metrics", "model_version"),
        EventType.session_started: ("session_id", "machine_id"),
        EventType.session_ended: ("session_id",),
        EventType.device_heartbeat: ("heartbeat",),
        EventType.camera_error: ("error",),
        EventType.alert_created: ("session_id", "alert"),
        EventType.model_loaded: ("model_version",),
    }

    @model_validator(mode="after")
    def _per_type_required(self):
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError(f"unsupported schema_version {self.schema_version}")
        for field in self._REQUIRED[self.event_type]:
            if getattr(self, field) is None:
                raise ValueError(f"{self.event_type.value} requires '{field}'")
        return self
