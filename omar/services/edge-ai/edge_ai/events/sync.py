"""
Cloud synchronization: drain the persistent queue to the ingest Edge Function.

* Signs each batch with HMAC-SHA256 (see contracts.events_v1.canonical_batch_string).
* 200 → ack accepted+duplicate events; drop events the server rejected as
  invalid (they are logged — retrying a schema violation never helps).
* 401 → credentials/clock problem: back off hard, do NOT drop anything.
* 5xx / network error → exponential backoff with jitter, retry forever. The
  queue is the buffer that rides out internet outages.
* A sent_at timestamp is refreshed on every attempt so clock-skew replay
  protection on the server does not reject old retries.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import random
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import httpx
import structlog

from edge_ai.contracts.events_v1 import canonical_batch_string, events_sha256_hex, normalize_json

from .queue import PersistentEventQueue

log = structlog.get_logger(__name__)


@dataclass
class SyncResult:
    sent: int
    accepted: int
    duplicates: int
    rejected: int
    ok: bool
    status_code: int | None
    error: str | None = None


def sign_batch(secret: str, device_id: str, batch_id: str, sent_at: str, events: list[dict[str, Any]]) -> str:
    canonical = canonical_batch_string(device_id, batch_id, sent_at, events_sha256_hex(events))
    return hmac.new(secret.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256).hexdigest()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class SyncClient:
    def __init__(
        self,
        queue: PersistentEventQueue,
        ingest_url: str,
        device_id: str,
        device_secret: str,
        batch_size: int = 200,
        timeout_s: float = 15.0,
        client: httpx.Client | None = None,
    ) -> None:
        self.queue = queue
        self.ingest_url = ingest_url
        self.device_id = device_id
        self.device_secret = device_secret
        self.batch_size = batch_size
        self._client = client or httpx.Client(timeout=timeout_s)
        self._consecutive_failures = 0
        self.last_success_at: float | None = None
        self.last_error: str | None = None

    @property
    def backoff_seconds(self) -> float:
        base = min(60.0, 2.0 ** min(self._consecutive_failures, 6))
        return base + random.uniform(0, base * 0.2)  # noqa: S311 — jitter, not security

    def sync_once(self) -> SyncResult | None:
        events = self.queue.peek_batch(self.batch_size)
        if not events:
            return None
        events = normalize_json(events)
        batch_id = str(uuid4())
        sent_at = _now_iso()
        body = {
            "schema_version": "1",
            "batch_id": batch_id,
            "device_id": self.device_id,
            "sent_at": sent_at,
            "events": events,
        }
        signature = sign_batch(self.device_secret, self.device_id, batch_id, sent_at, events)
        ids = [e["event_id"] for e in events]
        try:
            resp = self._client.post(
                self.ingest_url,
                content=json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode("utf-8"),
                headers={
                    "content-type": "application/json",
                    "x-device-id": self.device_id,
                    "x-device-signature": signature,
                },
            )
        except httpx.HTTPError as exc:
            self._consecutive_failures += 1
            self.last_error = f"network: {exc}"
            self.queue.nack(ids, self.last_error, self.backoff_seconds)
            log.warning("sync.network_error", error=str(exc), backoff=self.backoff_seconds, depth=self.queue.depth())
            return SyncResult(len(events), 0, 0, 0, False, None, self.last_error)

        if resp.status_code == 200:
            data = resp.json()
            rejected = data.get("rejected", []) or []
            rejected_ids = [r["event_id"] for r in rejected]
            for r in rejected:
                log.error("sync.event_rejected", event_id=r.get("event_id"), reason=r.get("reason"))
            self.queue.ack([i for i in ids if i not in rejected_ids])
            self.queue.drop(rejected_ids)
            self._consecutive_failures = 0
            self.last_success_at = time.time()
            self.last_error = None
            return SyncResult(
                len(events), int(data.get("accepted", 0)), int(data.get("duplicates", 0)), len(rejected), True, 200
            )

        if resp.status_code in (400, 413):
            # Our batch is malformed → shrink and retry smaller; if a single event is bad, drop it.
            self._consecutive_failures += 1
            self.last_error = f"client error {resp.status_code}: {resp.text[:300]}"
            if len(events) == 1:
                log.error("sync.poison_event_dropped", event_id=ids[0], body=resp.text[:300])
                self.queue.drop(ids)
            else:
                self.batch_size = max(1, self.batch_size // 2)
                self.queue.nack(ids, self.last_error, 1.0)
            return SyncResult(len(events), 0, 0, 0, False, resp.status_code, self.last_error)

        # 401 (auth/clock), 429, 5xx → keep everything, back off.
        self._consecutive_failures += 1
        self.last_error = f"server {resp.status_code}: {resp.text[:300]}"
        backoff = self.backoff_seconds * (3 if resp.status_code == 401 else 1)
        self.queue.nack(ids, self.last_error, backoff)
        log.warning("sync.server_error", status=resp.status_code, backoff=backoff, depth=self.queue.depth())
        return SyncResult(len(events), 0, 0, 0, False, resp.status_code, self.last_error)

    def drain(self, max_batches: int = 50) -> list[SyncResult]:
        results: list[SyncResult] = []
        for _ in range(max_batches):
            r = self.sync_once()
            if r is None:
                break
            results.append(r)
            if not r.ok:
                break
        return results
