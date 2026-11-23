from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path
from uuid import UUID

import httpx
import pytest

from edge_ai.cloud.device_sync import DeviceSyncClient, SyncSnapshot, sign_sync_request
from edge_ai.events.builder import EventBuilder
from edge_ai.events.queue import PersistentEventQueue
from edge_ai.identity.consent_gate import ConsentGate
from edge_ai.identity.enrollment import EnrollmentError, apply_cloud_revocations, enroll_member, revoke_member
from edge_ai.identity.entrance import EntranceIdentifier
from edge_ai.identity.face_pipeline import FaceBox
from edge_ai.identity.face_store import EncryptedTemplateStore, FaceMatcher

DEVICE = "d0000000-0000-4000-8000-000000000001"
BRANCH = "b0000000-0000-4000-8000-000000000001"
MEMBER = "33333333-3333-4333-8333-333333333333"
CONSENT = "55555555-5555-4555-8555-555555555555"
SECRET = "dev-only-device-secret-change-me"

SNAPSHOT = {
    "schema_version": 1,
    "generated_at": "2026-10-30T10:00:00Z",
    "device_id": DEVICE,
    "branch_id": BRANCH,
    "cameras": [
        {
            "id": "c0000000-0000-4000-8000-000000000002",
            "name": "CAM-FLOOR-A",
            "purpose": "floor",
            "url": "rtsp://192.0.2.11:554/s",
            "inference_fps": 10,
            "zones": [
                {
                    "id": "f0000000-0000-4000-8000-000000000002",
                    "type": "equipment",
                    "equipment_id": "e0000000-0000-4000-8000-000000000001",
                    "exercise_slug": "lat-pulldown",
                    "polygon": [[0, 0], [10, 0], [10, 10], [0, 10]],
                }
            ],
        }
    ],
    "equipment": [],
    "exercises": [
        {
            "id": "88888888-8888-4888-8888-888888888888",
            "slug": "lat-pulldown",
            "name": "Lat Pulldown",
            "rep_profile": {
                "version": 1,
                "signal": "elbow_angle_mean",
                "top_threshold": 150,
                "bottom_threshold": 75,
                "min_rep_seconds": 0.8,
                "max_rep_seconds": 6,
                "start_state": "top",
            },
        }
    ],
    "consents": {
        MEMBER: {
            "biometric_face_identification": CONSENT,
            "cctv_workout_tracking": "55555555-5555-4555-8555-555555555556",
        }
    },
    "members": [{"id": MEMBER, "member_code": "GYM-TEST-M2A7", "full_name": "Test Member"}],
    "revoked_template_refs": [],
    "active_template_refs": [],
}


def _server(secret: str):  # type: ignore[no-untyped-def]
    def handler(req: httpx.Request) -> httpx.Response:
        ts = req.headers["x-device-timestamp"]
        canon = f"{req.headers['x-device-id']}\n{ts}\nGET /device-sync"
        exp = hmac.new(secret.encode(), canon.encode(), hashlib.sha256).hexdigest()
        if exp != req.headers["x-device-signature"]:
            return httpx.Response(401, json={"code": "unauthorized"})
        return httpx.Response(200, json=SNAPSHOT)

    return handler


def test_sync_client_fetches_caches_and_feeds_consent_gate(tmp_path: Path):
    client = DeviceSyncClient(
        "http://sync.test/functions/v1/device-sync",
        DEVICE,
        SECRET,
        tmp_path / "sync.json",
        client=httpx.Client(transport=httpx.MockTransport(_server(SECRET))),
    )
    snap = client.fetch()
    assert snap is not None
    assert snap.exercise_ids()["lat-pulldown"] == UUID("88888888-8888-4888-8888-888888888888")
    assert "lat-pulldown" in snap.profiles()
    assert snap.cameras[0].zones[0].exercise_slug == "lat-pulldown"
    gate = ConsentGate(tmp_path / "consent.json")
    DeviceSyncClient.apply_consents(snap, gate)
    assert gate.allows_face(MEMBER) == CONSENT
    # offline: cached snapshot still loads
    offline = DeviceSyncClient(
        "http://sync.test/x",
        DEVICE,
        SECRET,
        tmp_path / "sync.json",
        client=httpx.Client(transport=httpx.MockTransport(lambda r: (_ for _ in ()).throw(httpx.ConnectError("down")))),
    )
    assert offline.fetch() is None
    cached = offline.fetch_or_cached()
    assert cached is not None and cached.device_id == UUID(DEVICE)


def test_sync_client_wrong_secret_is_rejected(tmp_path: Path):
    client = DeviceSyncClient(
        "http://sync.test/d",
        DEVICE,
        "wrong",
        tmp_path / "s.json",
        client=httpx.Client(transport=httpx.MockTransport(_server(SECRET))),
    )
    assert client.fetch() is None
    assert client.last_error is not None and "401" in client.last_error


def test_sign_sync_request_vector():
    assert sign_sync_request("k", "d", "t") == hmac.new(b"k", b"d\nt\nGET /device-sync", hashlib.sha256).hexdigest()


# ── enrollment / entrance with fake detector + embedder ──────────────────────


class FakeDetector:
    def __init__(self, faces_per_image: list[int]) -> None:
        self.faces_per_image = faces_per_image
        self.calls = 0

    def detect(self, image):  # type: ignore[no-untyped-def]
        n = self.faces_per_image[min(self.calls, len(self.faces_per_image) - 1)]
        self.calls += 1
        return [FaceBox((10.0, 10.0, 190.0, 190.0), 0.9) for _ in range(n)]


class FakeEmbedder:
    model_name = "fake-face"

    def __init__(self, vec: list[float]) -> None:
        self.vec = vec

    def embed_box(self, image, face, pad_ratio: float = 0.15):  # type: ignore[no-untyped-def]
        return list(self.vec)


def _gate(tmp_path: Path, members: dict[str, dict[str, str]]) -> ConsentGate:
    g = ConsentGate(tmp_path / "consent.json")
    g.replace(members)
    return g


def test_enrollment_requires_consent_and_single_face(tmp_path: Path):
    store = EncryptedTemplateStore(tmp_path / "faces.sqlite", os.urandom(32))
    q = PersistentEventQueue(tmp_path / "q.sqlite")
    emitter = EventBuilder(q, UUID(DEVICE), UUID(BRANCH))
    img = object()
    with pytest.raises(EnrollmentError, match="consent"):
        enroll_member(
            member_id=MEMBER,
            images=[img],
            detector=FakeDetector([1]),
            embedder=FakeEmbedder([1, 0, 0]),
            store=store,
            consent=_gate(tmp_path, {}),
            emitter=emitter,
        )
    gate = _gate(tmp_path, {MEMBER: {"biometric_face_identification": CONSENT}})
    with pytest.raises(EnrollmentError, match="no usable face"):
        enroll_member(
            member_id=MEMBER,
            images=[img, img],
            detector=FakeDetector([0, 2]),
            embedder=FakeEmbedder([1, 0, 0]),
            store=store,
            consent=gate,
            emitter=emitter,
        )
    res = enroll_member(
        member_id=MEMBER,
        images=[img, img, img],
        detector=FakeDetector([1, 1, 2]),
        embedder=FakeEmbedder([1, 0, 0]),
        store=store,
        consent=gate,
        emitter=emitter,
    )
    assert res.images_used == 2 and res.template_ref.startswith("tpl-")
    assert store.count() == 1
    events = q.peek_batch(10)
    assert [e["event_type"] for e in events] == ["biometric.enrolled"]
    assert events[0]["payload"]["consent_record_id"] == CONSENT
    assert events[0]["payload"]["key_id"] == store.key_id

    # revocation emits one event per template and deletes ciphertext
    assert revoke_member(member_id=MEMBER, store=store, emitter=emitter, reason="consent_withdrawn") == 1
    assert store.count() == 0
    assert q.peek_batch(10)[-1]["event_type"] == "biometric.revoked"


def test_cloud_revocations_delete_local_templates(tmp_path: Path):
    store = EncryptedTemplateStore(tmp_path / "f.sqlite", os.urandom(32))
    ref = store.enroll(MEMBER, CONSENT, [0.0, 1.0], "m")
    assert apply_cloud_revocations(revoked_refs=["tpl-unknown", ref], store=store) == 1
    assert store.count() == 0


def test_entrance_identifier_binds_only_consented_unambiguous_matches(tmp_path: Path):
    import numpy as np

    store = EncryptedTemplateStore(tmp_path / "f.sqlite", os.urandom(32))
    store.enroll(MEMBER, CONSENT, [1.0, 0.0, 0.0], "fake-face")
    store.enroll("44444444-4444-4444-8444-444444444444", "c2", [0.0, 1.0, 0.0], "fake-face")
    matcher = FaceMatcher(store, match_threshold=0.6, ambiguity_margin=0.1)
    gate = _gate(tmp_path, {MEMBER: {"biometric_face_identification": CONSENT, "cctv_workout_tracking": "c-cctv"}})
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    box = (200.0, 50.0, 400.0, 470.0)

    ident = EntranceIdentifier(FakeDetector([1]), FakeEmbedder([0.98, 0.1, 0.0]), matcher, gate)
    out = ident.identify(frame, box)
    assert (
        out.reason == "matched"
        and out.member_id == MEMBER
        and out.consent_record_id == CONSENT
        and out.pad_score is None
    )

    # the other enrolled person has no consent in the registry → never bound
    ident2 = EntranceIdentifier(FakeDetector([1]), FakeEmbedder([0.0, 0.99, 0.05]), matcher, gate)
    assert ident2.identify(frame, box).reason == "no_consent"

    # between two people → ambiguous
    ident3 = EntranceIdentifier(FakeDetector([1]), FakeEmbedder([0.72, 0.69, 0.0]), matcher, gate)
    assert ident3.identify(frame, box).reason == "ambiguous"

    # stranger
    ident4 = EntranceIdentifier(FakeDetector([1]), FakeEmbedder([0.0, 0.0, 1.0]), matcher, gate)
    assert ident4.identify(frame, box).reason == "no_match"

    # rate limiting per track
    assert ident.should_try("cam", 1, now=100.0) is True
    assert ident.should_try("cam", 1, now=100.5) is False
    assert ident.should_try("cam", 1, now=101.5) is True
    for t in range(2, 10):
        ident.should_try("cam", 1, now=101.5 + t * 2)
    assert ident.should_try("cam", 1, now=200.0) is False  # max_attempts reached
    ident.forget("cam", 1)
    assert ident.should_try("cam", 1, now=300.0) is True


def test_snapshot_validation_rejects_credentialed_urls():
    bad = json.loads(json.dumps(SNAPSHOT))
    bad["cameras"][0]["url"] = "rtsp://admin:pw@192.0.2.11/s"
    with pytest.raises(ValueError):
        SyncSnapshot.model_validate(bad)
