"""
Queue and sync-client robustness:
  * strict FIFO: nothing newer overtakes a batch that is backing off (R2-25);
  * C4 rejection codes are logged, counted for health/metrics and dead-lettered (R2-29);
  * the batch size grows back after a 400/413 shrink (R2-32);
  * 403 device_retired stops syncing and is reported in health (C5);
  * cloud URLs must be https except on loopback (R2-33);
  * the queue fsyncs on commit (synchronous=FULL) (R2-34).
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx
import pytest
from pydantic import ValidationError

from edge_ai.config import CameraConfig, Settings
from edge_ai.contracts.events_v1 import DeviceHeartbeatPayload
from edge_ai.events.builder import EventBuilder
from edge_ai.events.queue import PersistentEventQueue
from edge_ai.events.sync import SyncClient
from edge_ai.health import _prom

DEVICE = UUID("11111111-1111-4111-8111-111111111111")
BRANCH = UUID("22222222-2222-4222-8222-222222222222")
SECRET = "dev-only-device-secret-change-me"


def hb(n: int = 0) -> DeviceHeartbeatPayload:
    return DeviceHeartbeatPayload(
        software_version="0.1.0",
        uptime_seconds=n,
        gpu_utilization=None,
        gpu_memory_used_mb=None,
        cpu_utilization=None,
        queue_depth=0,
        cameras_online=0,
        cameras_total=0,
        avg_pipeline_latency_ms=None,
    )


class Server:
    """Scriptable ingest-events stand-in. `responses` is consumed first; then 200/accept-all."""

    def __init__(self) -> None:
        self.responses: list[httpx.Response | Exception] = []
        self.reject: dict[int, str] = {}  # uptime_seconds marker → C4 code
        self.batches: list[list[int]] = []  # uptime_seconds markers per received batch

    def __call__(self, req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        markers = [e["payload"]["uptime_seconds"] for e in body["events"]]
        if self.responses:
            r = self.responses.pop(0)
            if isinstance(r, Exception):
                raise r
            return r
        self.batches.append(markers)
        rejected = [
            {"event_id": e["event_id"], "code": self.reject[m], "reason": "A fixed sentence."}
            for e, m in zip(body["events"], markers, strict=True)
            if m in self.reject
        ]
        return httpx.Response(
            200, json={"accepted": len(markers) - len(rejected), "duplicates": 0, "rejected": rejected}
        )


def _setup(tmp_path: Path, batch_size: int = 200) -> tuple[PersistentEventQueue, EventBuilder, Server, SyncClient]:
    q = PersistentEventQueue(tmp_path / "q.sqlite")
    b = EventBuilder(q, DEVICE, BRANCH)
    server = Server()
    s = SyncClient(
        q,
        "https://ingest.test",
        str(DEVICE),
        SECRET,
        batch_size=batch_size,
        client=httpx.Client(transport=httpx.MockTransport(server)),
    )
    return q, b, server, s


def _travel(monkeypatch: pytest.MonkeyPatch, seconds: float) -> None:
    real = time.time
    monkeypatch.setattr(time, "time", lambda: real() + seconds)


# ── R2-25: strict FIFO ──────────────────────────────────────────────────────


def test_newer_events_never_overtake_a_deferred_batch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    q, b, server, s = _setup(tmp_path, batch_size=2)
    b.emit("device.heartbeat", hb(1))  # e.g. exercise.activity.started …
    b.emit("device.heartbeat", hb(2))  # … set.started
    server.responses.append(httpx.ConnectError("internet down"))
    r = s.sync_once()
    assert r is not None and not r.ok
    for n in (3, 4, 5):  # rep.counted … appended while the first batch backs off
        b.emit("device.heartbeat", hb(n))
    assert s.sync_once() is None  # nothing newer is sent ahead of the deferred head
    assert server.batches == []
    _travel(monkeypatch, 3600)
    s.drain()
    assert server.batches == [[1, 2], [3, 4], [5]]
    assert q.depth() == 0


def test_server_error_keeps_order_across_retries(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    q, b, server, s = _setup(tmp_path, batch_size=3)
    for n in (1, 2, 3):
        b.emit("device.heartbeat", hb(n))
    server.responses.append(httpx.Response(503, json={"code": "unavailable"}))
    s.sync_once()
    b.emit("device.heartbeat", hb(4))
    assert s.sync_once() is None
    _travel(monkeypatch, 3600)
    s.drain()
    assert server.batches == [[1, 2, 3], [4]] and q.depth() == 0


# ── R2-29: C4 codes ─────────────────────────────────────────────────────────


def test_permanent_codes_are_dead_lettered_and_counted(tmp_path: Path) -> None:
    q, b, server, s = _setup(tmp_path)
    for n in range(1, 6):
        b.emit("device.heartbeat", hb(n))
    server.reject = {1: "unknown_reference", 2: "no_consent", 3: "branch_mismatch", 4: "invalid_value"}
    r = s.sync_once()
    assert r is not None and r.ok and r.rejected == 4
    assert q.depth() == 0 and q.dead_letter_depth() == 4
    assert {d["code"] for d in q.dead_letters()} == {
        "unknown_reference",
        "no_consent",
        "branch_mismatch",
        "invalid_value",
    }
    h = s.health()
    assert h["rejections_by_code"] == {
        "unknown_reference": 1,
        "no_consent": 1,
        "branch_mismatch": 1,
        "invalid_value": 1,
    }
    assert h["dead_letter_depth"] == 4
    prom = _prom({"sync": h})
    assert 'edge_sync_rejected_total{code="no_consent"} 1' in prom
    assert "edge_dead_letter_depth 4" in prom


def test_metrics_labels_are_allow_listed_and_escaped() -> None:
    prom = _prom(
        {
            "sync": {"rejections_by_code": {"no_consent": 2, 'evil"} 1\nx': 3, "weird": 1, "poison": 1}},
            "cameras": {'cam"1': {"status": "online", "name": 'Floor "A"\\\nB', "fps": 9.5}},
        }
    )
    assert 'edge_sync_rejected_total{code="no_consent"} 2' in prom
    assert 'edge_sync_rejected_total{code="other"} 4' in prom
    assert 'edge_sync_rejected_total{code="poison"} 1' in prom
    assert "evil" not in prom and "weird" not in prom
    assert 'edge_camera_online{camera_id="cam\\"1",name="Floor \\"A\\"\\\\\\nB"} 1' in prom
    # every sample line is well-formed: no raw newline split a label value
    for line in prom.splitlines():
        assert line.startswith("# TYPE ") or line.startswith("edge_"), line


def test_internal_code_is_dead_lettered_and_does_not_block_the_queue(tmp_path: Path) -> None:
    # C4/0008: infrastructure faults abort the call (HTTP 5xx → batch retried); `internal` marks a
    # deterministic failure whose event_id the server already recorded, so a re-send is a no-op duplicate.
    q, b, server, s = _setup(tmp_path, batch_size=2)
    for n in (1, 2, 3):
        b.emit("device.heartbeat", hb(n))
    server.reject = {1: "internal"}
    s.drain()
    assert server.batches == [[1, 2], [3]]
    assert q.depth() == 0 and q.dead_letter_depth() == 1
    dl = q.dead_letters()[0]
    assert dl["code"] == "internal" and dl["reason"] == "A fixed sentence." and dl["event_type"] == "device.heartbeat"
    assert s.rejections_by_code["internal"] == 1


def test_rejections_without_code_keep_the_old_behaviour(tmp_path: Path) -> None:
    q, b, _server, _s = _setup(tmp_path)
    b.emit("device.heartbeat", hb(1))

    def legacy(req: httpx.Request) -> httpx.Response:
        ev = json.loads(req.content)["events"][0]
        return httpx.Response(
            200, json={"accepted": 0, "duplicates": 0, "rejected": [{"event_id": ev["event_id"], "reason": "x"}]}
        )

    s = SyncClient(
        q, "https://ingest.test", str(DEVICE), SECRET, client=httpx.Client(transport=httpx.MockTransport(legacy))
    )
    s.sync_once()
    assert q.depth() == 0 and q.dead_letter_depth() == 1 and s.rejections_by_code["unknown"] == 1


# ── R2-32: batch size recovers ──────────────────────────────────────────────


def test_batch_size_grows_back_after_a_poison_event(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    q, b, server, s = _setup(tmp_path, batch_size=8)
    for n in range(1, 30):
        b.emit("device.heartbeat", hb(n))
    server.responses += [httpx.Response(400, json={"code": "validation_error"})] * 4  # 8 → 4 → 2 → 1 → drop
    offset = 0.0
    real = time.time
    for _ in range(4):
        monkeypatch.setattr(time, "time", lambda o=offset: real() + o)
        s.sync_once()
        offset += 10
    assert s.batch_size == 1 and q.dead_letter_depth() == 1
    monkeypatch.setattr(time, "time", lambda: real() + offset)
    s.drain()
    assert s.batch_size == 8
    assert [len(x) for x in server.batches][:4] == [1, 2, 4, 8]
    assert q.depth() == 0


# ── C5: 403 device_retired ──────────────────────────────────────────────────


def test_device_retired_stops_syncing_and_keeps_the_queue(tmp_path: Path) -> None:
    q, b, server, s = _setup(tmp_path)
    b.emit("device.heartbeat", hb(1))
    server.responses.append(httpx.Response(403, json={"code": "device_retired"}))
    r = s.sync_once()
    assert r is not None and r.status_code == 403 and not r.ok
    assert s.retired and s.health()["device_retired"] is True
    assert s.sync_once() is None and s.drain() == []
    assert server.batches == [] and q.depth() == 1
    assert "edge_device_retired 1" in _prom({"device_retired": True})


def test_other_403_backs_off_like_401(tmp_path: Path) -> None:
    q, b, server, s = _setup(tmp_path)
    b.emit("device.heartbeat", hb(1))
    server.responses.append(httpx.Response(403, json={"code": "forbidden"}))
    s.sync_once()
    assert not s.retired and q.depth() == 1


# ── R2-33: TLS ──────────────────────────────────────────────────────────────


def _settings(**kw: Any) -> Settings:
    return Settings(device_id=DEVICE, branch_id=BRANCH, device_secret=SECRET, _env_file=None, **kw)  # type: ignore[call-arg]


def test_cloud_urls_must_use_https_except_on_loopback() -> None:
    with pytest.raises(ValidationError, match="https"):
        _settings(ingest_url="http://gym-lan.example/functions/v1/ingest-events")
    with pytest.raises(ValidationError, match="https"):
        _settings(ingest_url="https://x.supabase.co/functions/v1/ingest-events", sync_url="http://x/device-sync")
    assert _settings(ingest_url="https://x.supabase.co/functions/v1/ingest-events")
    assert _settings(ingest_url="http://127.0.0.1:54321/functions/v1/ingest-events")
    assert _settings(ingest_url="http://localhost:54321/functions/v1/ingest-events")
    assert _settings(ingest_url="http://gym-lan.example/functions/v1/ingest-events", allow_insecure_http=True)


def test_empty_path_settings_mean_not_configured(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("EDGE_BIOMETRIC_KEY_FILE=\nEDGE_FACE_MODEL_PATH=\nEDGE_DETECTOR_MODEL_PATH=\n", encoding="utf-8")
    s = Settings(  # type: ignore[call-arg]
        device_id=DEVICE, branch_id=BRANCH, device_secret=SECRET, ingest_url="https://x/ingest-events", _env_file=env
    )
    assert s.biometric_key_file is None and s.face_model_path is None and s.detector_model_path is None
    assert s.biometric_key is None


def test_camera_config_accepts_device_sync_stream_url() -> None:
    cam = CameraConfig.model_validate(
        {"id": "c0000000-0000-4000-8000-000000000001", "name": "c", "stream_url": "rtsp://192.0.2.1/s"}
    )
    assert cam.url == "rtsp://192.0.2.1/s"
    with pytest.raises(ValidationError):
        CameraConfig.model_validate(
            {"id": "c0000000-0000-4000-8000-000000000001", "name": "c", "stream_url": "rtsp://a:b@192.0.2.1/s"}
        )


# ── R2-34: durability ───────────────────────────────────────────────────────


def test_queue_uses_synchronous_full(tmp_path: Path) -> None:
    q = PersistentEventQueue(tmp_path / "q.sqlite")
    assert q._db.execute("pragma synchronous").fetchone()[0] == 2  # noqa: SLF001 — 2 = FULL
    assert q._db.execute("pragma journal_mode").fetchone()[0] == "wal"  # noqa: SLF001
    assert isinstance(sqlite3.sqlite_version, str)
