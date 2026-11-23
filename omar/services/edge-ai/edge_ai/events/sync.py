"""
Cloud synchronization: drain the persistent queue to the ingest Edge Function.

* Signs each batch with HMAC-SHA256 (see contracts.events_v1.canonical_batch_string).
* 200 → ack accepted+duplicate events. Every rejection carries a C4 `code`
  (branch_mismatch, no_consent, unknown_reference, invalid_value, invalid_state,
  internal) and is permanent: infrastructure faults never come back as
  rejections (ingest_events aborts the call → 5xx → the whole batch is retried),
  `internal` marks a deterministic business-rule failure, and the server has
  already claimed the event_id, so a re-send would only be counted as a
  duplicate. Rejected events move to the queue's dead-letter table; codes are
  logged and counted for /healthz and /metrics.
* 401 → credentials/clock problem: back off hard, do NOT drop anything.
* 403 `device_retired` → stop syncing (the queue is kept) and report it in health.
* 5xx / network error → exponential backoff with jitter, retry forever. The
  queue is the buffer that rides out internet outages.
* Strict FIFO: the queue never lets newer events overtake a deferred one.
* 400/413 halve the batch to isolate a poison event; successful batches grow it
  back to the configured size.
* A sent_at timestamp is refreshed on every attempt so clock-skew replay
  protection on the server does not reject old retries.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import random
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import httpx
import structlog

from edge_ai.contracts.events_v1 import canonical_batch_string, events_sha256_hex, normalize_json

from .queue import PersistentEventQueue

log = structlog.get_logger(__name__)

# (event envelope, code, reason) for every event the cloud rejected for good.
RejectionListener = Callable[[dict[str, Any], "str | None", "str | None"], None]


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
        self._max_batch_size = batch_size
        self._client = client or httpx.Client(timeout=timeout_s)
        self._consecutive_failures = 0
        self.last_success_at: float | None = None
        self.last_error: str | None = None
        self.retired = False
        self.rejections_by_code: Counter[str] = Counter()
        self._rejection_listeners: list[RejectionListener] = []

    def add_rejection_listener(self, fn: RejectionListener) -> None:
        self._rejection_listeners.append(fn)

    @property
    def backoff_seconds(self) -> float:
        base = min(60.0, 2.0 ** min(self._consecutive_failures, 6))
        return base + random.uniform(0, base * 0.2)  # noqa: S311 — jitter, not security

    def _handle_rejections(self, events: list[dict[str, Any]], rejected: list[dict[str, Any]]) -> None:
        by_id = {str(r.get("event_id")): r for r in rejected}
        bodies = {str(e["event_id"]): e for e in events}
        dead = [i for i in by_id if i in bodies]
        for i, r in by_id.items():
            code = r.get("code")
            self.rejections_by_code[str(code or "unknown")] += 1
            log.error(
                "sync.event_rejected",
                event_id=i,
                event_type=bodies.get(i, {}).get("event_type"),
                code=code,
                reason=r.get("reason"),
            )
        if dead:
            self.queue.drop(dead, reasons={i: (by_id[i].get("code"), by_id[i].get("reason")) for i in dead})
            for i in dead:
                for fn in self._rejection_listeners:
                    try:
                        fn(bodies[i], by_id[i].get("code"), by_id[i].get("reason"))
                    except Exception:  # noqa: BLE001 — a listener must never break syncing
                        log.exception("sync.rejection_listener_error", event_id=i)

    def sync_once(self) -> SyncResult | None:
        if self.retired:
            return None  # the cloud retired this device: keep the queue, stop calling
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
            if self._consecutive_failures == 1:
                log.warning(
                    "sync.offline",
                    msg=f"Backend at {self.ingest_url} is unreachable ({exc}). Storing events locally in persistent buffer.",
                    depth=self.queue.depth(),
                    next_retry_s=round(self.backoff_seconds, 1),
                )
            else:
                log.debug(
                    "sync.offline_retry",
                    error=str(exc),
                    failures=self._consecutive_failures,
                    backoff=self.backoff_seconds,
                    depth=self.queue.depth(),
                )
            return SyncResult(len(events), 0, 0, 0, False, None, self.last_error)

        if resp.status_code == 200:
            data = resp.json()
            rejected = data.get("rejected", []) or []
            rejected_ids = {str(r.get("event_id")) for r in rejected}
            self.queue.ack([i for i in ids if i not in rejected_ids])
            self._handle_rejections(events, rejected)
            if self._consecutive_failures > 0:
                log.info(
                    "sync.online",
                    msg=f"Backend connection restored! Drained batch of {len(events)} events to {self.ingest_url}.",
                )
            self._consecutive_failures = 0
            if self.batch_size < self._max_batch_size:
                self.batch_size = min(self._max_batch_size, self.batch_size * 2)
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
                self.queue.drop(ids, code="poison", reasons={ids[0]: ("poison", resp.text[:300])})
            else:
                self.batch_size = max(1, self.batch_size // 2)
                self.queue.nack(ids, self.last_error, 1.0)
            return SyncResult(len(events), 0, 0, 0, False, resp.status_code, self.last_error)

        if resp.status_code == 403 and _error_code(resp) == "device_retired":
            self.retired = True
            self.last_error = "device_retired"
            log.error("sync.device_retired", depth=self.queue.depth(), msg="sync stopped; queue kept for an operator")
            return SyncResult(len(events), 0, 0, 0, False, 403, self.last_error)

        # 401 (auth/clock), 403, 429, 5xx → keep everything, back off.
        self._consecutive_failures += 1
        self.last_error = f"server {resp.status_code}: {resp.text[:300]}"
        backoff = self.backoff_seconds * (3 if resp.status_code == 401 else 1)
        self.queue.nack(ids, self.last_error, backoff)
        log.warning("sync.server_error", status=resp.status_code, backoff=backoff, depth=self.queue.depth())
        return SyncResult(len(events), 0, 0, 0, False, resp.status_code, self.last_error)

    def health(self) -> dict[str, Any]:
        return {
            "device_retired": self.retired,
            "batch_size": self.batch_size,
            "rejections_by_code": dict(self.rejections_by_code),
            "dead_letter_depth": self.queue.dead_letter_depth(),
        }

    def drain(self, max_batches: int = 50) -> list[SyncResult]:
        results: list[SyncResult] = []
        for i in range(max_batches):
            if i > 0:
                time.sleep(0.05)
            r = self.sync_once()
            if r is None:
                break
            results.append(r)
            if not r.ok:
                break
        return results


def _error_code(resp: httpx.Response) -> str | None:
    try:
        body = resp.json()
    except ValueError:
        return None
    return str(body.get("code")) if isinstance(body, dict) and body.get("code") is not None else None
