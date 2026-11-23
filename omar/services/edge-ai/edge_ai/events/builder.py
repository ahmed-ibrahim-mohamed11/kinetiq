"""Convenience wrapper: build a validated envelope and persist it in one call."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from pydantic import BaseModel

from edge_ai.contracts.events_v1 import EventType, build_event

from .queue import PersistentEventQueue


class EventBuilder:
    def __init__(self, queue: PersistentEventQueue, device_id: UUID, branch_id: UUID) -> None:
        self.queue = queue
        self.device_id = device_id
        self.branch_id = branch_id

    def emit(
        self, event_type: EventType, payload: BaseModel, occurred_at: float | datetime | None = None
    ) -> dict[str, Any]:
        if occurred_at is None:
            occurred = datetime.now(timezone.utc)
        elif isinstance(occurred_at, datetime):
            occurred = occurred_at
        else:
            occurred = datetime.fromtimestamp(occurred_at, tz=timezone.utc)

        def _build(seq: int) -> dict[str, Any]:
            return build_event(
                event_type=event_type,
                payload=payload,
                device_id=self.device_id,
                branch_id=self.branch_id,
                sequence=seq,
                occurred_at=occurred,
            )

        return self.queue.append(_build)
