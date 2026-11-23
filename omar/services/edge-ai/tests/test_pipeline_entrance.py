"""
Pipeline-level test of entrance identification and config sync wiring, with fake
detector / identifier objects (no models). Proves that:
  * a person detected in the entrance zone of an entrance camera gets identified,
    the global identity is bound, and `identity.checkin` is emitted exactly once;
  * identification is NOT attempted on floor cameras;
  * a config-sync snapshot refreshes consents, exercise ids and profiles.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from uuid import UUID

import numpy as np

from edge_ai.config import Settings, load_cameras
from edge_ai.detection.base import Detection
from edge_ai.identity.consent_gate import ConsentGate
from edge_ai.identity.entrance import IdentificationOutcome
from edge_ai.pipeline.orchestrator import CameraWorker, Pipeline
from edge_ai.reps.profiles import RepProfile
from edge_ai.tools.default_profiles import DEFAULT_PROFILES

ROOT = Path(__file__).resolve().parents[1]
MEMBER = "33333333-3333-4333-8333-333333333333"
CONSENT = "55555555-5555-4555-8555-555555555555"


class FakeDetector:
    model_name = "fake-det"

    def __init__(self, boxes: list[tuple[float, float, float, float]]) -> None:
        self.boxes = boxes

    def detect(self, image):  # type: ignore[no-untyped-def]
        return [Detection(b, 0.9) for b in self.boxes]


class FakeEntrance:
    """Stands in for EntranceIdentifier: same public surface (should_try / identify / forget / stats / matcher)."""

    def __init__(self, outcome: IdentificationOutcome) -> None:
        self.outcome = outcome
        self.calls = 0
        self.stats = {"matched": 0}
        self.embedder = type("E", (), {"model_name": "fake-face"})()
        self.matcher = type("M", (), {"refresh": staticmethod(lambda: None)})()

    def should_try(self, camera_id: str, local_track_id: int, now: float | None = None) -> bool:
        return True

    def identify(self, image, box):  # type: ignore[no-untyped-def]
        self.calls += 1
        self.stats["matched"] += 1
        return self.outcome

    def forget(self, camera_id: str, local_track_id: int) -> None:
        return None


class FakeSource:
    def __init__(self) -> None:
        self.resolution = (1920, 1080)

    def start(self) -> None: ...
    def stop(self) -> None: ...
    def latest(self):  # type: ignore[no-untyped-def]
        return None

    def status(self):  # type: ignore[no-untyped-def]
        from edge_ai.camera.rtsp_reader import StreamStatus

        return StreamStatus.ONLINE

    def measured_fps(self) -> float:
        return 10.0

    def last_error(self) -> None:
        return None


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


def test_entrance_camera_identifies_and_binds_once(tmp_path: Path):
    cams = load_cameras(ROOT / "config" / "cameras.example.yaml")
    profiles = {k: RepProfile.from_catalog(v) for k, v in DEFAULT_PROFILES.items()}
    entrance_cam = next(c for c in cams.cameras if c.purpose == "entrance")
    floor_cam = next(c for c in cams.cameras if c.purpose == "floor")
    gate = ConsentGate(tmp_path / "consent.json")
    gate.replace({MEMBER: {"biometric_face_identification": CONSENT, "cctv_workout_tracking": "c2"}})
    fake = FakeEntrance(IdentificationOutcome(MEMBER, 0.93, CONSENT, None, "matched"))

    # Person box whose foot point (x=950, y=900) is inside the entrance zone polygon [[600,200],[1300,200],[1300,1000],[600,1000]]
    box = (900.0, 300.0, 1000.0, 900.0)
    pipeline = Pipeline(
        _settings(tmp_path),
        cams.cameras,
        cams.exercise_ids,
        profiles,
        FakeDetector([box]),
        None,
        entrance=fake,
        consent_gate=gate,
    )
    worker = CameraWorker(pipeline, entrance_cam, FakeSource())
    frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
    t = 1_000_000.0
    for i in range(6):  # tracker needs min_hits=3 to confirm; then identification happens
        worker.process_frame(frame, t + i * 0.1)

    events = pipeline.queue.peek_batch(100)
    kinds = Counter(e["event_type"] for e in events)
    assert kinds["identity.checkin"] == 1, kinds
    checkin = next(e for e in events if e["event_type"] == "identity.checkin")
    assert checkin["payload"]["member_id"] == MEMBER
    assert checkin["payload"]["method"] == "face"
    assert checkin["payload"]["consent_record_id"] == CONSENT
    assert checkin["payload"]["presentation_attack_score"] is None
    assert checkin["payload"]["camera_id"] == str(entrance_cam.id)
    # once bound, no further identification attempts for that identity
    calls_after_bind = fake.calls
    worker.process_frame(frame, t + 1.0)
    assert fake.calls == calls_after_bind
    g = pipeline.registry.global_for_local(str(entrance_cam.id), 1)
    assert g is not None and g.member_id == MEMBER and g.identity_confidence == 0.93

    # floor cameras never run face identification
    floor_worker = CameraWorker(pipeline, floor_cam, FakeSource())
    for i in range(6):
        floor_worker.process_frame(frame, t + 10 + i * 0.1)
    assert fake.calls == calls_after_bind
    assert pipeline.health()["identification"] == {"matched": 1}


def test_config_sync_updates_consents_and_catalogue(tmp_path: Path):
    from edge_ai.cloud.device_sync import SyncSnapshot

    cams = load_cameras(ROOT / "config" / "cameras.example.yaml")
    gate = ConsentGate(tmp_path / "consent.json")
    snapshot = SyncSnapshot.model_validate(
        {
            "generated_at": "2026-10-30T10:00:00Z",
            "device_id": "d0000000-0000-4000-8000-000000000001",
            "branch_id": "b0000000-0000-4000-8000-000000000001",
            "cameras": [],
            "exercises": [
                {
                    "id": "99999999-9999-4999-8999-999999999999",
                    "slug": "hack-squat",
                    "name": "Hack Squat",
                    "rep_profile": {
                        "version": 1,
                        "signal": "knee_angle_mean",
                        "top_threshold": 160,
                        "bottom_threshold": 95,
                        "min_rep_seconds": 1,
                        "max_rep_seconds": 8,
                        "start_state": "top",
                    },
                }
            ],
            "consents": {MEMBER: {"cctv_workout_tracking": "c2"}},
        }
    )

    class FakeSync:
        last_success_at = 1.0
        last_error = None

        def fetch_or_cached(self):  # type: ignore[no-untyped-def]
            return snapshot

        @staticmethod
        def apply_consents(snap, g):  # type: ignore[no-untyped-def]
            g.replace(snap.consents)

    pipeline = Pipeline(
        _settings(tmp_path),
        cams.cameras,
        dict(cams.exercise_ids),
        {},
        None,
        None,
        config_sync=FakeSync(),
        consent_gate=gate,
    )
    assert pipeline.apply_config_sync() is True
    assert gate.allows_tracking(MEMBER) == "c2"
    assert gate.allows_face(MEMBER) is None
    assert pipeline.exercise_ids["hack-squat"] == UUID("99999999-9999-4999-8999-999999999999")
    assert "hack-squat" in pipeline.profiles
    assert pipeline.health()["consent_cache_stale"] is False
