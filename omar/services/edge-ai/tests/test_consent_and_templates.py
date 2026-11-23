"""
Consent and biometric-template lifecycle on the edge:
  * ConsentGate staleness is measured from the snapshot's generated_at; a cached snapshot applied
    while offline is never re-stamped as fresh (R1-18), consents expire offline;
  * the snapshot cache holds no member roster (C6) and is encrypted with a key derived from the
    biometric key (R1-25);
  * templates are reconciled against active_template_refs after non-cached syncs; orphans the cloud
    rejected are deleted (R1-19 edge half, R1-22);
  * identities whose cctv_workout_tracking consent disappears are unbound (R1-21 edge half);
  * a running matcher reloads after another process changes the store (R2-26);
  * deletion never decrypts; undecryptable rows are skipped and deletable (R2-27);
  * deleted ciphertext does not remain in the database or WAL files (R2-28).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx
import pytest

from edge_ai.cloud.device_sync import DeviceSyncClient, SyncSnapshot
from edge_ai.config import Settings, load_cameras
from edge_ai.events.builder import EventBuilder
from edge_ai.events.queue import PersistentEventQueue
from edge_ai.events.sync import SyncClient
from edge_ai.identity.consent_gate import ConsentGate
from edge_ai.identity.enrollment import (
    apply_cloud_revocations,
    enroll_member,
    reconcile_templates,
    revoke_member,
    revoke_undecryptable,
)
from edge_ai.identity.face_pipeline import FaceBox
from edge_ai.identity.face_store import EncryptedTemplateStore, FaceMatcher
from edge_ai.pipeline.orchestrator import Pipeline
from edge_ai.reps.profiles import RepProfile
from edge_ai.tools.default_profiles import DEFAULT_PROFILES

ROOT = Path(__file__).resolve().parents[1]
DEVICE = "d0000000-0000-4000-8000-000000000001"
BRANCH = "b0000000-0000-4000-8000-000000000001"
MEMBER = "33333333-3333-4333-8333-333333333333"
OTHER = "44444444-4444-4444-8444-444444444444"
BIO = "55555555-5555-4555-8555-555555555555"
CCTV = "55555555-5555-4555-8555-555555555556"
SECRET = "dev-only-device-secret-change-me"


def _iso(offset_s: float = 0.0) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=offset_s)).isoformat().replace("+00:00", "Z")


def _snapshot(generated_offset_s: float = 0.0, **over: Any) -> dict[str, Any]:
    snap: dict[str, Any] = {
        "schema_version": 1,
        "generated_at": _iso(generated_offset_s),
        "device_id": DEVICE,
        "branch_id": BRANCH,
        "cameras": [],
        "equipment": [],
        "exercises": [],
        # C6 shape: only biometric + cctv, granted and unexpired, active members of the branch.
        "consents": [
            {
                "member_id": MEMBER,
                "consent_type": "biometric_face_identification",
                "consent_record_id": BIO,
                "expires_at": None,
            },
            {
                "member_id": MEMBER,
                "consent_type": "cctv_workout_tracking",
                "consent_record_id": CCTV,
                "expires_at": _iso(30 * 86400),
            },
        ],
        "revoked_template_refs": [],
        "active_template_refs": [],
    }
    snap.update(over)
    return snap


class FakeCloud:
    """device-sync stand-in: verifies the request signature, serves `snapshot`, can go offline."""

    def __init__(self, snapshot: dict[str, Any]) -> None:
        self.snapshot = snapshot
        self.online = True
        self.status = 200
        self.body: dict[str, Any] | None = None

    def __call__(self, req: httpx.Request) -> httpx.Response:
        if not self.online:
            raise httpx.ConnectError("offline")
        canon = f"{req.headers['x-device-id']}\n{req.headers['x-device-timestamp']}\nGET /device-sync"
        if hmac.new(SECRET.encode(), canon.encode(), hashlib.sha256).hexdigest() != req.headers["x-device-signature"]:
            return httpx.Response(401, json={"code": "unauthorized"})
        if self.status != 200:
            return httpx.Response(self.status, json=self.body or {})
        return httpx.Response(200, json=self.snapshot)


def _client(tmp_path: Path, cloud: FakeCloud, key: bytes | None) -> DeviceSyncClient:
    return DeviceSyncClient(
        "https://sync.test/functions/v1/device-sync",
        DEVICE,
        SECRET,
        tmp_path / "device-sync.json",
        client=httpx.Client(transport=httpx.MockTransport(cloud)),
        cache_key=key,
    )


def _settings(tmp_path: Path) -> Settings:
    return Settings(  # type: ignore[call-arg]
        device_id=UUID(DEVICE),
        branch_id=UUID(BRANCH),
        device_secret=SECRET,
        ingest_url="https://ingest.test/functions/v1/ingest-events",
        data_dir=tmp_path,
        _env_file=None,
    )


def _profiles() -> dict[str, RepProfile]:
    return {k: RepProfile.from_catalog(v) for k, v in DEFAULT_PROFILES.items()}


# ── R1-18: staleness from generated_at, through apply_config_sync ────────────


def test_cached_snapshot_is_never_restamped_as_fresh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cloud = FakeCloud(_snapshot(generated_offset_s=-3600))  # generated 1 h ago
    gate = ConsentGate(None, max_age_seconds=2 * 3600)
    pipeline = Pipeline(
        _settings(tmp_path),
        [],
        {},
        {},
        None,
        None,
        config_sync=_client(tmp_path, cloud, os.urandom(32)),
        consent_gate=gate,
    )
    assert pipeline.apply_config_sync() is True
    assert gate.allows_face(MEMBER) == BIO and pipeline.health()["consent_cache_stale"] is False

    # Internet goes down; 90 minutes later the snapshot is 2.5 h old although it was re-applied from cache.
    cloud.online = False
    real = time.time
    monkeypatch.setattr(time, "time", lambda: real() + 90 * 60)
    assert pipeline.apply_config_sync() is True  # cached snapshot applied
    assert gate.stale is True
    assert gate.allows_face(MEMBER) is None and gate.allows_tracking(MEMBER) is None
    assert pipeline.health()["consent_cache_stale"] is True


def test_fresh_fetch_of_an_old_snapshot_is_stale(tmp_path: Path) -> None:
    gate = ConsentGate(None)
    DeviceSyncClient.apply_consents(SyncSnapshot.model_validate(_snapshot(generated_offset_s=-25 * 3600)), gate)
    assert gate.stale and gate.allows_face(MEMBER) is None


def test_consent_expiring_while_offline_is_denied() -> None:
    snap = _snapshot()
    snap["consents"][1]["expires_at"] = _iso(-60)  # cctv consent expired a minute ago
    gate = ConsentGate(None)
    DeviceSyncClient.apply_consents(SyncSnapshot.model_validate(snap), gate)
    assert gate.allows_face(MEMBER) == BIO
    assert gate.allows_tracking(MEMBER) is None


def test_device_clock_behind_server_cannot_extend_freshness() -> None:
    gate = ConsentGate(None, max_age_seconds=3600)
    gate.replace({MEMBER: {"cctv_workout_tracking": CCTV}}, generated_at=time.time() + 10 * 3600)
    assert gate.generated_at <= time.time()


# ── C6 timestamps exactly as Postgres writes them to jsonb ──────────────────

# to_jsonb(timestamptz) trims trailing zeros of the fraction; Python 3.10's fromisoformat rejects these.
PG_TIMESTAMPS = [
    ("2099-12-02T20:18:41.15+02:00", datetime(2099, 12, 2, 18, 18, 41, 150000, tzinfo=timezone.utc)),
    ("2099-12-02T18:18:41.15123+00:00", datetime(2099, 12, 2, 18, 18, 41, 151230, tzinfo=timezone.utc)),
    ("2099-12-02T18:18:41+00:00", datetime(2099, 12, 2, 18, 18, 41, tzinfo=timezone.utc)),
    ("2099-12-02T18:18:41.1Z", datetime(2099, 12, 2, 18, 18, 41, 100000, tzinfo=timezone.utc)),
]


def _pg_snapshot(expires_at: str) -> dict[str, Any]:
    snap = _snapshot()
    snap["generated_at"] = (
        (datetime.now(timezone.utc).replace(microsecond=150000)).isoformat().replace(".150000", ".15")
    )
    snap["consents"][1]["expires_at"] = expires_at
    return snap


@pytest.mark.parametrize(("raw", "want"), PG_TIMESTAMPS)
def test_snapshot_accepts_postgres_timestamp_formats(raw: str, want: datetime) -> None:
    snap = SyncSnapshot.model_validate(_pg_snapshot(raw))
    assert snap.consents[MEMBER] == {"biometric_face_identification": BIO, "cctv_workout_tracking": CCTV}
    assert snap.consent_expires[MEMBER]["cctv_workout_tracking"] == pytest.approx(want.timestamp())


@pytest.mark.parametrize(("raw", "want"), PG_TIMESTAMPS)
def test_device_sync_fetch_accepts_postgres_timestamp_formats(tmp_path: Path, raw: str, want: datetime) -> None:
    client = _client(tmp_path, FakeCloud(_pg_snapshot(raw)), os.urandom(32))
    snap = client.fetch()
    assert snap is not None and client.last_error is None
    gate = ConsentGate(None)
    client.apply_consents(snap, gate)
    assert gate.allows_tracking(MEMBER) == CCTV
    cached = client.load_cached()  # the cache round-trip keeps the expiry
    assert cached is not None
    assert cached.consent_expires[MEMBER]["cctv_workout_tracking"] == pytest.approx(want.timestamp())


def test_postgres_formatted_past_expiry_is_enforced(tmp_path: Path) -> None:
    past = (datetime.now(timezone.utc) - timedelta(minutes=5)).replace(microsecond=150000)
    raw = past.isoformat().replace(".150000", ".15")
    snap = _client(tmp_path, FakeCloud(_pg_snapshot(raw)), os.urandom(32)).fetch()
    assert snap is not None
    gate = ConsentGate(None)
    DeviceSyncClient.apply_consents(snap, gate)
    assert gate.allows_face(MEMBER) == BIO and gate.allows_tracking(MEMBER) is None


def test_malformed_consent_items_are_skipped_not_fatal(tmp_path: Path) -> None:
    snap = _snapshot(revoked_template_refs=["tpl-revoked"], active_template_refs=["tpl-active"])
    snap["consents"] += [
        "not-an-object",
        None,
        {"consent_type": "cctv_workout_tracking", "consent_record_id": "c9"},  # no member_id
        {"member_id": OTHER, "consent_type": "biometric_face_identification"},  # no record id
        {"member_id": OTHER, "consent_type": "cctv_workout_tracking", "consent_record_id": "c3", "expires_at": "soon"},
        {"member_id": OTHER, "consent_type": "cctv_workout_tracking", "consent_record_id": "c4", "expires_at": True},
    ]
    client = _client(tmp_path, FakeCloud(snap), os.urandom(32))
    got = client.fetch()
    assert got is not None and client.last_error is None
    # the good rows and the rest of the snapshot still apply; the malformed ones grant nothing
    assert got.consents == {MEMBER: {"biometric_face_identification": BIO, "cctv_workout_tracking": CCTV}}
    assert got.revoked_template_refs == ["tpl-revoked"] and got.active_template_refs == ["tpl-active"]


def test_non_object_snapshot_is_reported_not_raised(tmp_path: Path) -> None:
    cloud = FakeCloud(_snapshot())
    cloud.snapshot = ["not", "a", "snapshot"]  # type: ignore[assignment]
    client = _client(tmp_path, cloud, os.urandom(32))
    assert client.fetch() is None and (client.last_error or "").startswith("invalid snapshot")


# ── R1-25 / C6: cache without roster, encrypted at rest ─────────────────────


def test_snapshot_cache_has_no_roster_and_is_encrypted(tmp_path: Path) -> None:
    legacy_shape = _snapshot(
        consents={MEMBER: {"biometric_face_identification": BIO, "cctv_workout_tracking": CCTV, "marketing": "m1"}},
        members=[{"id": MEMBER, "member_code": "GYM-TEST-M2A7", "full_name": "Test Member"}],
    )
    cloud = FakeCloud(legacy_shape)
    client = _client(tmp_path, cloud, os.urandom(32))
    snap = client.fetch()
    assert snap is not None and not snap.from_cache
    assert snap.consents == {MEMBER: {"biometric_face_identification": BIO, "cctv_workout_tracking": CCTV}}
    assert not hasattr(snap, "members")
    raw = (tmp_path / "device-sync.json").read_bytes()
    for needle in (MEMBER, BIO, "Test Member", "GYM-TEST-M2A7", "marketing"):
        assert needle.encode() not in raw
    cloud.online = False
    cached = client.fetch_or_cached()
    assert cached is not None and cached.from_cache
    assert cached.consents == snap.consents and cached.generated_at == snap.generated_at

    # a different key cannot read it
    assert _client(tmp_path, cloud, os.urandom(32)).load_cached() is None


def test_snapshot_cache_without_key_keeps_configuration_only(tmp_path: Path) -> None:
    client = _client(tmp_path, FakeCloud(_snapshot(active_template_refs=["tpl-aaaaaaaaaaaa"])), None)
    assert client.fetch() is not None
    raw = (tmp_path / "device-sync.json").read_text(encoding="utf-8")
    assert MEMBER not in raw and "tpl-aaaaaaaaaaaa" not in raw
    cached = client.load_cached()
    assert cached is not None and cached.consents == {} and cached.active_template_refs is None


def test_legacy_plaintext_cache_is_rewritten_minimised_and_encrypted(tmp_path: Path) -> None:
    legacy = _snapshot(members=[{"id": MEMBER, "member_code": "GYM-1", "full_name": "Test Member"}])
    (tmp_path / "device-sync.json").write_text(json.dumps(legacy), encoding="utf-8")
    client = _client(tmp_path, FakeCloud(legacy), os.urandom(32))
    cached = client.load_cached()
    assert cached is not None and cached.from_cache and cached.consents[MEMBER]["cctv_workout_tracking"] == CCTV
    assert b"Test Member" not in (tmp_path / "device-sync.json").read_bytes()
    assert (tmp_path / "device-sync.json").read_bytes().startswith(b"SGC1")


def test_device_sync_403_device_retired_closes_the_gate(tmp_path: Path) -> None:
    cloud = FakeCloud(_snapshot())
    client = _client(tmp_path, cloud, os.urandom(32))
    gate = ConsentGate(None)
    pipeline = Pipeline(_settings(tmp_path), [], {}, {}, None, None, config_sync=client, consent_gate=gate)
    assert pipeline.apply_config_sync() and gate.allows_face(MEMBER) == BIO
    cloud.status, cloud.body = 403, {"code": "device_retired"}
    assert pipeline.apply_config_sync() is False
    assert client.retired and pipeline.health()["device_retired"] is True
    assert gate.allows_face(MEMBER) is None  # the cached snapshot is NOT used by a retired device


# ── R1-19 / R1-22: template reconciliation and orphan cleanup ────────────────


class FakeFaceDetector:
    def detect(self, image):  # type: ignore[no-untyped-def]
        return [FaceBox((10.0, 10.0, 190.0, 190.0), 0.9)]


class FakeEmbedder:
    model_name = "fake-face"

    def embed_box(self, image, face, pad_ratio: float = 0.15):  # type: ignore[no-untyped-def]
        return [1.0, 0.0, 0.0]


def test_reconcile_templates_rules(tmp_path: Path) -> None:
    store = EncryptedTemplateStore(tmp_path / "f.sqlite", os.urandom(32))
    active = store.enroll(MEMBER, BIO, [1.0, 0.0], "m")
    orphan = store.enroll(MEMBER, BIO, [0.9, 0.1], "m")  # cloud never registered / registry row deleted
    pending = store.enroll(OTHER, "c2", [0.0, 1.0], "m")  # biometric.enrolled still queued
    revoked = store.enroll(OTHER, "c2", [0.1, 0.9], "m")
    time.sleep(0.05)  # Windows clock ticks are ~16 ms; "before" is strict on purpose
    cutoff = time.time()
    time.sleep(0.05)
    newer = store.enroll(OTHER, "c2", [0.5, 0.5], "m")  # enrolled after the snapshot was requested
    n = reconcile_templates(
        store=store, active_refs=[active], revoked_refs=[revoked], pending_refs={pending}, enrolled_before=cutoff
    )
    assert n == 2
    remaining = {r for r, _, _ in store.list_refs()}
    assert remaining == {active, pending, newer} and orphan not in remaining and revoked not in remaining


def test_config_sync_reconciles_only_after_a_fresh_fetch(tmp_path: Path) -> None:
    key = os.urandom(32)
    store = EncryptedTemplateStore(tmp_path / "f.sqlite", key)
    kept = store.enroll(MEMBER, BIO, [1.0, 0.0], "m")
    gone = store.enroll(OTHER, "c2", [0.0, 1.0], "m")  # e.g. member deleted: no registry row any more
    time.sleep(0.05)  # coarse Windows clock: the sync request must start strictly after the enrolment
    cloud = FakeCloud(_snapshot(active_template_refs=[kept]))
    client = _client(tmp_path, cloud, key)
    pipeline = Pipeline(
        _settings(tmp_path),
        [],
        {},
        {},
        None,
        None,
        config_sync=client,
        consent_gate=ConsentGate(None),
        template_store=store,
    )

    # Offline, cached snapshot → no reconciliation from possibly old data (only explicit revocations).
    assert client.fetch() is not None  # writes the cache …
    cloud.online = False  # … then the internet goes down before the pipeline syncs
    assert pipeline.apply_config_sync() is True
    assert store.count() == 2

    cloud.online = True
    assert pipeline.apply_config_sync() is True
    assert [r for r, _, _ in store.list_refs()] == [kept]
    assert pipeline.health()["biometric_templates_deleted_by_sync"] == 1
    assert gone not in {r for r, _, _ in store.list_refs()}


def test_template_with_pending_enrolment_event_survives_reconciliation(tmp_path: Path) -> None:
    key = os.urandom(32)
    store = EncryptedTemplateStore(tmp_path / "f.sqlite", key)
    q = PersistentEventQueue(tmp_path / "events.sqlite")
    gate = ConsentGate(None)
    gate.replace({MEMBER: {"biometric_face_identification": BIO, "cctv_workout_tracking": CCTV}})
    res = enroll_member(
        member_id=MEMBER,
        images=[object()],
        detector=FakeFaceDetector(),
        embedder=FakeEmbedder(),
        store=store,
        consent=gate,
        emitter=EventBuilder(q, UUID(DEVICE), UUID(BRANCH)),
    )
    pipeline = Pipeline(
        _settings(tmp_path),
        [],
        {},
        {},
        None,
        None,
        queue=q,
        config_sync=_client(tmp_path, FakeCloud(_snapshot(active_template_refs=[])), key),
        consent_gate=ConsentGate(None),
        template_store=store,
    )
    assert pipeline.apply_config_sync() is True
    assert [r for r, _, _ in store.list_refs()] == [res.template_ref]  # not yet synced → kept


def test_enrolment_event_is_queued_before_the_template_is_stored(tmp_path: Path) -> None:
    q = PersistentEventQueue(tmp_path / "events.sqlite")
    seen: list[list[str]] = []

    class SpyStore(EncryptedTemplateStore):
        def enroll(self, *a: Any, **kw: Any) -> str:
            seen.append([str(p.get("template_ref")) for p in q.pending_payloads("biometric.enrolled")])
            return super().enroll(*a, **kw)

    store = SpyStore(tmp_path / "f.sqlite", os.urandom(32))
    gate = ConsentGate(None)
    gate.replace({MEMBER: {"biometric_face_identification": BIO}})
    res = enroll_member(
        member_id=MEMBER,
        images=[object()],
        detector=FakeFaceDetector(),
        embedder=FakeEmbedder(),
        store=store,
        consent=gate,
        emitter=EventBuilder(q, UUID(DEVICE), UUID(BRANCH)),
    )
    assert seen == [[res.template_ref]]  # the event was pending when the row was written
    assert [r for r, _, _ in store.list_refs()] == [res.template_ref]


def test_failed_template_write_queues_a_revocation(tmp_path: Path) -> None:
    q = PersistentEventQueue(tmp_path / "events.sqlite")

    class BrokenStore(EncryptedTemplateStore):
        def enroll(self, *a: Any, **kw: Any) -> str:
            raise OSError("disk full")

    gate = ConsentGate(None)
    gate.replace({MEMBER: {"biometric_face_identification": BIO}})
    with pytest.raises(OSError):
        enroll_member(
            member_id=MEMBER,
            images=[object()],
            detector=FakeFaceDetector(),
            embedder=FakeEmbedder(),
            store=BrokenStore(tmp_path / "f.sqlite", os.urandom(32)),
            consent=gate,
            emitter=EventBuilder(q, UUID(DEVICE), UUID(BRANCH)),
        )
    events = q.peek_batch(10)
    assert [e["event_type"] for e in events] == ["biometric.enrolled", "biometric.revoked"]
    assert events[0]["payload"]["template_ref"] == events[1]["payload"]["template_ref"]


def test_enrolment_by_another_process_during_config_sync_survives(tmp_path: Path) -> None:
    """`edge-ai enroll` runs while the pipeline is between its pending-ref read and the snapshot."""
    key = os.urandom(32)
    store = EncryptedTemplateStore(tmp_path / "biometrics.sqlite", key)
    cli_store = EncryptedTemplateStore(tmp_path / "biometrics.sqlite", key)
    cli_queue = PersistentEventQueue(tmp_path / "events.sqlite")
    enrolled: list[str] = []

    class RacingQueue(PersistentEventQueue):
        def pending_payloads(self, event_type: str) -> list[dict[str, Any]]:
            out = super().pending_payloads(event_type)
            if not enrolled:  # the CLI enrols right after the pipeline read the queue
                gate = ConsentGate(None)
                gate.replace({MEMBER: {"biometric_face_identification": BIO}})
                res = enroll_member(
                    member_id=MEMBER,
                    images=[object()],
                    detector=FakeFaceDetector(),
                    embedder=FakeEmbedder(),
                    store=cli_store,
                    consent=gate,
                    emitter=EventBuilder(cli_queue, UUID(DEVICE), UUID(BRANCH)),
                )
                enrolled.append(res.template_ref)
                time.sleep(0.05)  # coarse Windows clock: the request starts strictly later
            return out

    pipeline = Pipeline(
        _settings(tmp_path),
        [],
        {},
        {},
        None,
        None,
        queue=RacingQueue(tmp_path / "events.sqlite"),
        config_sync=_client(tmp_path, FakeCloud(_snapshot(active_template_refs=[])), key),
        consent_gate=ConsentGate(None),
        template_store=store,
    )
    assert pipeline.apply_config_sync() is True
    assert enrolled and [r for r, _, _ in store.list_refs()] == enrolled  # not reconciled away


def test_enroll_cli_refuses_on_a_retired_device_even_with_a_fresh_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import edge_ai.__main__ as cli

    key = os.urandom(32)
    cloud = FakeCloud(_snapshot())
    client = _client(tmp_path, cloud, key)
    assert client.fetch() is not None  # fresh cache with a granted biometric consent
    cloud.status, cloud.body = 403, {"code": "device_retired"}
    store = EncryptedTemplateStore(tmp_path / "biometrics.sqlite", key)
    monkeypatch.setattr(cli, "_build_identity_stack", lambda s: (ConsentGate(None), store, None, client))
    for name, value in {
        "EDGE_DEVICE_ID": DEVICE,
        "EDGE_BRANCH_ID": BRANCH,
        "EDGE_DEVICE_SECRET": SECRET,
        "EDGE_INGEST_URL": "https://ingest.test/functions/v1/ingest-events",
        "EDGE_DATA_DIR": str(tmp_path),
        "EDGE_FACE_MODEL_PATH": str(tmp_path / "face.onnx"),
    }.items():
        monkeypatch.setenv(name, value)
    assert cli.main(["enroll", "--member", MEMBER, "--image", str(tmp_path / "x.jpg")]) == 1
    assert "retired" in capsys.readouterr().err
    assert store.count() == 0 and PersistentEventQueue(tmp_path / "events.sqlite").depth() == 0


def _ingest(rejections: dict[str, str]):  # type: ignore[no-untyped-def]
    """ingest-events stand-in rejecting events by type with a C4 code."""
    calls: Counter[str] = Counter()

    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        rejected = [
            {"event_id": e["event_id"], "code": rejections[e["event_type"]], "reason": "A fixed sentence."}
            for e in body["events"]
            if e["event_type"] in rejections
        ]
        for e in body["events"]:
            calls[e["event_type"]] += 1
        return httpx.Response(
            200,
            json={"accepted": len(body["events"]) - len(rejected), "duplicates": 0, "rejected": rejected},
        )

    return handler, calls


@pytest.mark.parametrize("code", ["no_consent", "unknown_reference", "internal"])
def test_rejected_enrolment_deletes_the_orphan_template(tmp_path: Path, code: str) -> None:
    store = EncryptedTemplateStore(tmp_path / "f.sqlite", os.urandom(32))
    q = PersistentEventQueue(tmp_path / "events.sqlite")
    handler, _ = _ingest({"biometric.enrolled": code})
    sync = SyncClient(
        q, "https://ingest.test", DEVICE, SECRET, client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    pipeline = Pipeline(_settings(tmp_path), [], {}, {}, None, None, queue=q, sync=sync, template_store=store)
    gate = ConsentGate(None)
    gate.replace({MEMBER: {"biometric_face_identification": BIO}})
    enroll_member(
        member_id=MEMBER,
        images=[object()],
        detector=FakeFaceDetector(),
        embedder=FakeEmbedder(),
        store=store,
        consent=gate,
        emitter=pipeline.emitter,
    )
    assert store.count() == 1
    results = pipeline.sync.drain()
    assert results and results[0].rejected == 1
    assert store.count() == 0
    assert q.depth() == 0 and q.dead_letter_depth() == 1
    assert q.dead_letters()[0]["code"] == code


# ── R1-21: unbind when cctv_workout_tracking consent disappears ─────────────


def test_identity_is_unbound_when_tracking_consent_disappears(tmp_path: Path) -> None:
    cams = load_cameras(ROOT / "config" / "cameras.example.yaml")
    entrance = next(c for c in cams.cameras if c.purpose == "entrance")
    floor = next(c for c in cams.cameras if c.purpose == "floor")
    zone = next(z for z in floor.zones if z.exercise_slug == "leg-press")
    xs, ys = [p[0] for p in zone.polygon], [p[1] for p in zone.polygon]
    cx, cy = sum(xs) / len(xs), sum(ys) / len(ys)
    box = (cx - 40, cy - 150, cx + 40, cy)

    snapshots = [_snapshot()]

    class FakeSync:
        last_success_at = None
        last_error = None

        def fetch_or_cached(self) -> SyncSnapshot:
            return SyncSnapshot.model_validate(snapshots[-1])

        @staticmethod
        def apply_consents(snap: SyncSnapshot, g: ConsentGate) -> None:
            DeviceSyncClient.apply_consents(snap, g)

    gate = ConsentGate(None)
    pipeline = Pipeline(
        _settings(tmp_path),
        cams.cameras,
        dict(cams.exercise_ids),
        _profiles(),
        None,
        None,
        config_sync=FakeSync(),
        consent_gate=gate,
    )
    assert pipeline.apply_config_sync()
    t = 1_000_000.0
    pipeline.on_observation(entrance, 1, (900.0, 300.0, 1000.0, 900.0), 0.95, t, None, None)
    gid = pipeline.registry.global_for_local(str(entrance.id), 1).global_id  # type: ignore[union-attr]
    assert pipeline.bind_member(gid, MEMBER, 0.95, "face", entrance.id, BIO, None)
    reg = pipeline.registry
    reg._local_to_global[(str(floor.id), 7)] = gid  # noqa: SLF001 — attach the floor track (ReID stand-in)
    reg.get(gid).local_tracks.add((str(floor.id), 7))  # type: ignore[union-attr]
    for i in range(30):
        pipeline.on_observation(floor, 7, box, 0.95, t + 1 + i * 0.1, None, None)
    events = pipeline.queue.peek_batch(1000)
    started = [e for e in events if e["event_type"] == "equipment.usage.started"]
    assert len(started) == 1 and started[0]["payload"]["member_id"] == MEMBER

    # The member withdraws CCTV consent: the next snapshot no longer lists it.
    snapshots.append(_snapshot(consents=[snapshots[0]["consents"][0]]))
    assert pipeline.apply_config_sync()
    assert reg.get(gid) is None
    assert pipeline.health()["identities_unbound_no_consent"] == 1
    events = pipeline.queue.peek_batch(1000)
    kinds = [e["event_type"] for e in events]
    assert kinds.count("equipment.usage.ended") == 1 and kinds.count("exercise.activity.ended") == 1
    ended = [e for e in events if e["event_type"] == "track.ended" and e["payload"]["global_track_id"] == gid]
    assert len(ended) == 1

    # The person is still in view: they continue as a NEW anonymous track, nothing carries member_id.
    before = len(events)
    for i in range(30):
        pipeline.on_observation(floor, 7, box, 0.95, t + 10 + i * 0.1, None, None)
    later = pipeline.queue.peek_batch(1000)[before:]
    assert [e["event_type"] for e in later].count("track.started") == 1
    assert all(e["payload"].get("member_id") in (None,) for e in later if "member_id" in e["payload"])
    assert all(e["payload"].get("global_track_id") != gid for e in later)


# ── R2-26: matcher reloads after another process changed the store ──────────


def test_matcher_reloads_after_revoke_in_another_process(tmp_path: Path) -> None:
    key = os.urandom(32)
    service_store = EncryptedTemplateStore(tmp_path / "biometrics.sqlite", key)
    service_store.enroll(MEMBER, BIO, [1.0, 0.0, 0.0], "m")
    matcher = FaceMatcher(service_store, match_threshold=0.6)
    assert matcher.match([0.99, 0.05, 0.0]).member_id == MEMBER  # gallery cached in the running service

    cli_store = EncryptedTemplateStore(tmp_path / "biometrics.sqlite", key)  # `edge-ai revoke` process
    assert revoke_member(member_id=MEMBER, store=cli_store, emitter=None, reason="member_request") == 1
    assert matcher.match([0.99, 0.05, 0.0]).member_id is None

    cli_store.enroll(OTHER, "c2", [0.0, 1.0, 0.0], "m")  # `edge-ai enroll` process
    assert matcher.match([0.0, 0.99, 0.05]).member_id == OTHER


# ── R2-27: deletion never decrypts ──────────────────────────────────────────


def test_deletion_works_for_undecryptable_rows(tmp_path: Path) -> None:
    old_key, new_key = os.urandom(32), os.urandom(32)
    old = EncryptedTemplateStore(tmp_path / "f.sqlite", old_key)
    a1 = old.enroll(MEMBER, BIO, [1.0, 0.0], "m")
    a2 = old.enroll(MEMBER, BIO, [0.9, 0.1], "m")
    b1 = old.enroll(OTHER, "c2", [0.0, 1.0], "m")
    store = EncryptedTemplateStore(tmp_path / "f.sqlite", new_key)  # key changed / backup restored
    assert store.all_templates() == [] and store.undecryptable == 3
    # a cloud revocation of ONE ref deletes exactly that ref, not the member's other templates
    assert apply_cloud_revocations(revoked_refs=[a1], store=store) == 1
    assert {r for r, _, _ in store.list_refs()} == {a2, b1}
    # member revocation works without the old key
    q = PersistentEventQueue(tmp_path / "q.sqlite")
    emitter = EventBuilder(q, UUID(DEVICE), UUID(BRANCH))
    assert revoke_member(member_id=MEMBER, store=store, emitter=emitter, reason="member_request") == 1
    assert [e["payload"]["template_ref"] for e in q.peek_batch(10)] == [a2]
    # key rotation clean-up
    new_ref = store.enroll(OTHER, "c2", [0.0, 1.0], "m")
    assert revoke_undecryptable(store=store, emitter=emitter) == 1
    assert [r for r, _, _ in store.list_refs()] == [new_ref]
    revoked = [e["payload"] for e in q.peek_batch(10) if e["payload"]["template_ref"] == b1]
    assert revoked and revoked[0]["reason"] == "key_rotation"


# ── R2-28: deleted ciphertext is gone from the files ────────────────────────


def test_deleted_ciphertext_is_not_left_in_db_or_wal(tmp_path: Path) -> None:
    import sqlite3

    db = tmp_path / "biometrics.sqlite"
    store = EncryptedTemplateStore(db, os.urandom(32))
    store.enroll(OTHER, "c2", [0.3] * 64, "m")
    ref = store.enroll(MEMBER, BIO, [0.1] * 512, "m")
    ct = (
        sqlite3.connect(str(db))
        .execute("select ciphertext from templates where template_ref = ?", (ref,))
        .fetchone()[0]
    )
    assert len(ct) > 100
    store.revoke_member(MEMBER)
    for f in (db, db.with_name(db.name + "-wal")):
        if f.exists():
            data = f.read_bytes()
            assert ct[:64] not in data and ct[-64:] not in data, f.name
    assert store.count() == 1


def test_biometric_key_file_takes_precedence(tmp_path: Path) -> None:
    key = os.urandom(32)
    (tmp_path / "key").write_text(base64.b64encode(key).decode(), encoding="utf-8")
    s = Settings(  # type: ignore[call-arg]
        device_id=UUID(DEVICE),
        branch_id=UUID(BRANCH),
        device_secret=SECRET,
        ingest_url="https://ingest.test/functions/v1/ingest-events",
        biometric_key_file=tmp_path / "key",
        biometric_key_b64=base64.b64encode(os.urandom(32)).decode(),
        _env_file=None,
    )
    assert s.biometric_key == key
