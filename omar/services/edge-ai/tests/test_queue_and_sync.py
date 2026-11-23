import hashlib
import hmac
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

import httpx

from edge_ai.contracts.events_v1 import DeviceHeartbeatPayload, canonical_batch_string, events_sha256_hex
from edge_ai.events.builder import EventBuilder
from edge_ai.events.queue import PersistentEventQueue
from edge_ai.events.sync import SyncClient, sign_batch

DEVICE = UUID("11111111-1111-4111-8111-111111111111")
BRANCH = UUID("22222222-2222-4222-8222-222222222222")
SECRET = "dev-only-device-secret-change-me"


def hb() -> DeviceHeartbeatPayload:
    return DeviceHeartbeatPayload(
        software_version="0.1.0",
        uptime_seconds=1,
        gpu_utilization=None,
        gpu_memory_used_mb=None,
        cpu_utilization=None,
        queue_depth=0,
        cameras_online=0,
        cameras_total=0,
        avg_pipeline_latency_ms=None,
    )


def test_queue_sequences_survive_restart(tmp_path: Path):
    db = tmp_path / "q.sqlite"
    q = PersistentEventQueue(db)
    b = EventBuilder(q, DEVICE, BRANCH)
    e1 = b.emit("device.heartbeat", hb())
    e2 = b.emit("device.heartbeat", hb())
    assert (e1["sequence"], e2["sequence"]) == (0, 1)
    q.close()
    q2 = PersistentEventQueue(db)
    e3 = EventBuilder(q2, DEVICE, BRANCH).emit("device.heartbeat", hb())
    assert e3["sequence"] == 2
    assert q2.depth() == 3
    batch = q2.peek_batch(10)
    assert [e["sequence"] for e in batch] == [0, 1, 2]
    assert q2.ack([e["event_id"] for e in batch]) == 3
    assert q2.depth() == 0


def test_nack_backoff_hides_events_until_due(tmp_path: Path):
    q = PersistentEventQueue(tmp_path / "q.sqlite")
    e = EventBuilder(q, DEVICE, BRANCH).emit("device.heartbeat", hb())
    q.nack([e["event_id"]], "boom", backoff_seconds=60)
    assert q.peek_batch(10) == []
    import time

    assert len(q.peek_batch(10, now=time.time() + 61)) == 1


def _server(secret: str, store: dict):  # type: ignore[type-arg,no-untyped-def]
    """Fake ingest Edge Function: verifies signature exactly like the Deno implementation."""

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        events_hash = hashlib.sha256(
            json.dumps(body["events"], separators=(",", ":"), ensure_ascii=False).encode()
        ).hexdigest()
        canonical = canonical_batch_string(body["device_id"], body["batch_id"], body["sent_at"], events_hash)
        expected = hmac.new(secret.encode(), canonical.encode(), hashlib.sha256).hexdigest()
        if request.headers["x-device-signature"] != expected or request.headers["x-device-id"] != body["device_id"]:
            return httpx.Response(401, json={"code": "unauthorized"})
        if store.get("fail_next"):
            store["fail_next"] = False
            return httpx.Response(503, json={"code": "unavailable"})
        if body["batch_id"] in store.setdefault("batches", set()):
            return httpx.Response(
                200,
                json={"batch_id": body["batch_id"], "accepted": 0, "duplicates": len(body["events"]), "rejected": []},
            )
        store["batches"].add(body["batch_id"])
        accepted = 0
        dups = 0
        rejected = []
        for ev in body["events"]:
            if ev["event_id"] in store.setdefault("seen", set()):
                dups += 1
            elif ev["event_type"] == "camera.status" and ev["payload"].get("status") == "bogus":
                rejected.append({"event_id": ev["event_id"], "reason": "bad status"})
            else:
                store["seen"].add(ev["event_id"])
                accepted += 1
        return httpx.Response(
            200,
            json={
                "batch_id": body["batch_id"],
                "accepted": accepted,
                "duplicates": dups,
                "rejected": rejected,
                "server_time": datetime.now(timezone.utc).isoformat(),
            },
        )

    return handler


def test_sync_signs_and_acks(tmp_path: Path):
    q = PersistentEventQueue(tmp_path / "q.sqlite")
    b = EventBuilder(q, DEVICE, BRANCH)
    for _ in range(5):
        b.emit("device.heartbeat", hb())
    store: dict = {}  # type: ignore[type-arg]
    client = httpx.Client(transport=httpx.MockTransport(_server(SECRET, store)))
    s = SyncClient(q, "http://ingest.test/functions/v1/ingest-events", str(DEVICE), SECRET, batch_size=2, client=client)
    results = s.drain()
    assert [r.ok for r in results] == [True, True, True]
    assert sum(r.accepted for r in results) == 5
    assert q.depth() == 0


def test_sync_wrong_secret_keeps_events(tmp_path: Path):
    q = PersistentEventQueue(tmp_path / "q.sqlite")
    EventBuilder(q, DEVICE, BRANCH).emit("device.heartbeat", hb())
    client = httpx.Client(transport=httpx.MockTransport(_server("other-secret", {})))
    s = SyncClient(q, "http://ingest.test", str(DEVICE), SECRET, client=client)
    r = s.sync_once()
    assert r is not None and r.ok is False and r.status_code == 401
    assert q.depth() == 1  # nothing lost


def test_sync_retries_after_server_error_without_duplicates(tmp_path: Path):
    q = PersistentEventQueue(tmp_path / "q.sqlite")
    b = EventBuilder(q, DEVICE, BRANCH)
    for _ in range(3):
        b.emit("device.heartbeat", hb())
    store: dict = {"fail_next": True}  # type: ignore[type-arg]
    client = httpx.Client(transport=httpx.MockTransport(_server(SECRET, store)))
    s = SyncClient(q, "http://ingest.test", str(DEVICE), SECRET, client=client)
    r1 = s.sync_once()
    assert r1 is not None and not r1.ok and r1.status_code == 503
    import time

    ids = [e["event_id"] for e in q.peek_batch(10, now=time.time() + 10_000)]
    assert len(ids) == 3
    # retry: force due
    q.nack(ids, "retry", 0)
    r2 = s.sync_once()
    assert r2 is not None and r2.ok and r2.accepted == 3
    assert q.depth() == 0
    assert len(store["seen"]) == 3


def test_sync_network_error_backs_off(tmp_path: Path):
    q = PersistentEventQueue(tmp_path / "q.sqlite")
    EventBuilder(q, DEVICE, BRANCH).emit("device.heartbeat", hb())

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host")

    s = SyncClient(
        q, "http://ingest.test", str(DEVICE), SECRET, client=httpx.Client(transport=httpx.MockTransport(boom))
    )
    r = s.sync_once()
    assert r is not None and not r.ok and r.status_code is None
    assert q.depth() == 1
    assert s.sync_once() is None  # backed off: nothing due


def test_signature_matches_reference_vector():
    events = [{"event_id": str(uuid4()), "x": 1}]
    sig = sign_batch(SECRET, "d", "b", "t", events)
    canonical = f"d\nb\nt\n{events_sha256_hex(events)}"
    assert sig == hmac.new(SECRET.encode(), canonical.encode(), hashlib.sha256).hexdigest()
