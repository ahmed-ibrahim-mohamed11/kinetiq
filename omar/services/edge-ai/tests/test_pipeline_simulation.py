"""
Whole-pipeline test on synthetic observations (no video): a member checks in,
is observed at a leg press by two cameras, performs two sets, leaves.
Asserts the event stream a real deployment would send.
"""

from collections import Counter
from pathlib import Path
from uuid import UUID

import httpx

from edge_ai.config import CamerasFile, Settings, load_cameras
from edge_ai.events.queue import PersistentEventQueue
from edge_ai.events.sync import SyncClient
from edge_ai.pipeline.orchestrator import Pipeline
from edge_ai.reps.profiles import RepProfile
from edge_ai.tools.default_profiles import DEFAULT_PROFILES
from edge_ai.tools.simulate import run_simulation

ROOT = Path(__file__).resolve().parents[1]


def _settings(tmp_path: Path) -> Settings:
    return Settings(  # type: ignore[call-arg]
        device_id=UUID("d0000000-0000-4000-8000-000000000001"),
        branch_id=UUID("b0000000-0000-4000-8000-000000000001"),
        device_secret="dev-only-device-secret-change-me",
        ingest_url="http://ingest.test/functions/v1/ingest-events",
        data_dir=tmp_path,
        cameras_file=ROOT / "config" / "cameras.example.yaml",
        _env_file=None,
    )


def _cams() -> CamerasFile:
    return load_cameras(ROOT / "config" / "cameras.example.yaml")


def _profiles() -> dict[str, RepProfile]:
    return {k: RepProfile.from_catalog(v) for k, v in DEFAULT_PROFILES.items()}


def test_simulation_event_stream(tmp_path: Path, monkeypatch):  # type: ignore[no-untyped-def]
    settings = _settings(tmp_path)
    captured: list[dict] = []  # type: ignore[type-arg]

    # Capture the pipeline instance the simulation builds so we can inspect its queue.
    built: list[Pipeline] = []
    orig_init = Pipeline.__init__

    def spy_init(self, *a, **kw):  # type: ignore[no-untyped-def]
        orig_init(self, *a, **kw)
        built.append(self)

    monkeypatch.setattr(Pipeline, "__init__", spy_init)
    rc = run_simulation(settings, _cams(), _profiles(), seconds=60, sync=False)
    assert rc == 0
    pipe = built[0]
    events = pipe.queue.peek_batch(10_000)
    captured.extend(events)
    kinds = Counter(e["event_type"] for e in events)

    assert kinds["identity.checkin"] == 1
    assert kinds["identity.checkout"] == 1
    assert kinds["track.started"] == 1
    assert kinds["equipment.usage.started"] == 1
    assert kinds["exercise.activity.started"] == 1
    assert kinds["set.started"] == 2, kinds
    assert kinds["set.completed"] == 2, kinds
    reps = [e for e in events if e["event_type"] == "rep.counted"]
    # 2 sets × 20 s at 2.5 s/rep = 8 reps each; the second camera must NOT double count.
    assert 14 <= len(reps) <= 16, len(reps)
    assert pipe.dedup.suppressed >= 10, "camera B duplicates were not suppressed"
    set_completed = [e for e in events if e["event_type"] == "set.completed"]
    assert all(7 <= e["payload"]["rep_count"] <= 8 for e in set_completed), [e["payload"] for e in set_completed]
    assert set_completed[0]["payload"]["verified"] is True
    second_set = next(e for e in events if e["event_type"] == "set.started" and e["payload"]["set_index"] == 2)
    assert 10 <= second_set["payload"]["rest_before_seconds"] <= 14
    assert kinds["equipment.usage.ended"] == 1 and kinds["exercise.activity.ended"] == 1
    assert kinds["device.heartbeat"] == 1
    # sequence numbers strictly increasing, no gaps
    seqs = [e["sequence"] for e in events]
    assert seqs == list(range(len(seqs)))
    # every event re-validates against the contract (checked at build time, but be explicit)
    from edge_ai.contracts.events_v1 import parse_event

    for e in events:
        parse_event(e)


def test_simulation_syncs_to_fake_server(tmp_path: Path):
    """Full path including signing against an in-process stand-in for the Edge Function."""
    import hashlib
    import hmac
    import json

    settings = _settings(tmp_path)
    seen: set[str] = set()

    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        h = hashlib.sha256(json.dumps(body["events"], separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
        canon = f"{body['device_id']}\n{body['batch_id']}\n{body['sent_at']}\n{h}"
        exp = hmac.new(settings.device_secret.encode(), canon.encode(), hashlib.sha256).hexdigest()
        if exp != req.headers["x-device-signature"]:
            return httpx.Response(401)
        dups = sum(1 for e in body["events"] if e["event_id"] in seen)
        seen.update(e["event_id"] for e in body["events"])
        return httpx.Response(
            200,
            json={
                "batch_id": body["batch_id"],
                "accepted": len(body["events"]) - dups,
                "duplicates": dups,
                "rejected": [],
            },
        )

    q = PersistentEventQueue(tmp_path / "events.sqlite")
    sync = SyncClient(
        q,
        settings.ingest_url,
        str(settings.device_id),
        settings.device_secret,
        50,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    pipe = Pipeline(settings, _cams().cameras, _cams().exercise_ids, _profiles(), None, None, queue=q, sync=sync)
    pipe.emit_heartbeat()
    pipe.emit_heartbeat()
    results = sync.drain()
    assert all(r.ok for r in results) and q.depth() == 0 and len(seen) == 2
