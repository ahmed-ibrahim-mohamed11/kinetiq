"""
Privacy controls in the per-frame pipeline:
  * exclusion zones drop detections (foot point OR majority of the box) before tracking, so excluded
    people are never tracked, ReID-embedded or face-identified (R1-16);
  * exclusion zones drawn in the cloud (device-sync) apply without editing cameras.yaml;
  * a `mixed` camera without an entrance zone never runs face identification (R1-27);
  * no face is detected/embedded while the consent gate is closed for everyone (R1-27).
"""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

import numpy as np

from edge_ai.config import CameraConfig, Settings
from edge_ai.detection.base import Detection
from edge_ai.geometry import box_in_exclusion, polygon_box_overlap_fraction
from edge_ai.identity.consent_gate import ConsentGate
from edge_ai.identity.entrance import EntranceIdentifier, IdentificationOutcome
from edge_ai.identity.face_store import EncryptedTemplateStore, FaceMatcher
from edge_ai.pipeline.orchestrator import CameraWorker, Pipeline

MEMBER = "33333333-3333-4333-8333-333333333333"
CONSENT = "55555555-5555-4555-8555-555555555555"
EXCLUSION = [(0.0, 0.0), (400.0, 0.0), (400.0, 1080.0), (0.0, 1080.0)]
INSIDE_BOX = (100.0, 300.0, 200.0, 900.0)  # foot (150, 900) inside the exclusion polygon
OUTSIDE_BOX = (900.0, 300.0, 1000.0, 900.0)  # foot (950, 900) inside the entrance zone
FRAME = np.zeros((1080, 1920, 3), dtype=np.uint8)


def _settings(tmp_path: Path) -> Settings:
    return Settings(  # type: ignore[call-arg]
        device_id=UUID("d0000000-0000-4000-8000-000000000001"),
        branch_id=UUID("b0000000-0000-4000-8000-000000000001"),
        device_secret="dev-only-device-secret-change-me",
        ingest_url="https://ingest.test/functions/v1/ingest-events",
        data_dir=tmp_path,
        _env_file=None,
    )


def _camera(purpose: str, *, entrance_zone: bool, exclusion: bool) -> CameraConfig:
    zones = []
    if entrance_zone:
        zones.append(
            {
                "id": "f0000000-0000-4000-8000-0000000000e1",
                "type": "entrance",
                "polygon": [[600, 200], [1300, 200], [1300, 1000], [600, 1000]],
            }
        )
    if exclusion:
        zones.append({"id": "f0000000-0000-4000-8000-0000000000f1", "type": "exclusion", "polygon": EXCLUSION})
    return CameraConfig.model_validate(
        {
            "id": "c0000000-0000-4000-8000-0000000000a1",
            "name": f"CAM-{purpose}",
            "purpose": purpose,
            "url": "rtsp://192.0.2.10/s",
            "zones": zones,
        }
    )


class FakeDetector:
    model_name = "fake-det"

    def __init__(self, boxes: list[tuple[float, float, float, float]]) -> None:
        self.boxes = boxes

    def detect(self, image):  # type: ignore[no-untyped-def]
        return [Detection(b, 0.9) for b in self.boxes]


class FakeReid:
    model_name = "fake-reid"

    def __init__(self) -> None:
        self.boxes: list[tuple[float, float, float, float]] = []

    def embed(self, image, box):  # type: ignore[no-untyped-def]
        self.boxes.append(tuple(box))
        return [1.0, 0.0, 0.0]


class FakeEntrance:
    def __init__(self) -> None:
        self.boxes: list[tuple[float, float, float, float]] = []
        self.stats = {"matched": 0}
        self.embedder = type("E", (), {"model_name": "fake-face"})()
        self.matcher = type("M", (), {"refresh": staticmethod(lambda: None)})()

    def should_try(self, camera_id: str, local_track_id: int, now: float | None = None) -> bool:
        return True

    def identify(self, image, box):  # type: ignore[no-untyped-def]
        self.boxes.append(tuple(box))
        return IdentificationOutcome(None, 0.0, None, None, "no_match")

    def forget(self, camera_id: str, local_track_id: int) -> None:
        return None


class FakeSource:
    resolution = (1920, 1080)

    def start(self) -> None: ...
    def stop(self) -> None: ...
    def latest(self) -> None:
        return None

    def status(self):  # type: ignore[no-untyped-def]
        from edge_ai.camera.rtsp_reader import StreamStatus

        return StreamStatus.ONLINE

    def measured_fps(self) -> float:
        return 10.0


def _run(tmp_path: Path, cam: CameraConfig, boxes, frames: int = 6, config_sync=None):  # type: ignore[no-untyped-def]
    reid = FakeReid()
    entrance = FakeEntrance()
    pipeline = Pipeline(
        _settings(tmp_path),
        [cam],
        {},
        {},
        FakeDetector(boxes),
        None,
        reid=reid,
        entrance=entrance,
        config_sync=config_sync,
        reid_every_n_frames=1,
    )
    if config_sync is not None:
        pipeline.apply_config_sync()
    worker = CameraWorker(pipeline, cam, FakeSource())  # type: ignore[arg-type]
    for i in range(frames):
        worker.process_frame(FRAME, 1_000_000.0 + i * 0.1)
    return pipeline, worker, reid, entrance


# ── geometry ────────────────────────────────────────────────────────────────


def test_polygon_box_overlap_fraction() -> None:
    square = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)]
    assert polygon_box_overlap_fraction(square, (2, 2, 4, 4)) == 1.0
    assert abs(polygon_box_overlap_fraction(square, (5, 0, 15, 10)) - 0.5) < 1e-9
    assert polygon_box_overlap_fraction(square, (20, 20, 30, 30)) == 0.0
    # concave L-shape: the notch (5..10, 5..10) is not covered
    ell = [(0.0, 0.0), (10.0, 0.0), (10.0, 5.0), (5.0, 5.0), (5.0, 10.0), (0.0, 10.0)]
    assert abs(polygon_box_overlap_fraction(ell, (0, 0, 10, 10)) - 0.75) < 1e-9
    assert polygon_box_overlap_fraction(ell, (6, 6, 9, 9)) == 0.0


def test_box_in_exclusion_by_foot_point_or_majority_area() -> None:
    assert box_in_exclusion(INSIDE_BOX, [EXCLUSION])
    assert not box_in_exclusion(OUTSIDE_BOX, [EXCLUSION])
    # foot point (450, 900) is outside, but 75 % of the box area is inside the polygon
    assert box_in_exclusion((300.0, 300.0, 600.0, 900.0), [[(0.0, 0.0), (525.0, 0.0), (525.0, 1080.0), (0.0, 1080.0)]])
    # foot point outside and only a sliver inside → kept
    assert not box_in_exclusion((380.0, 300.0, 480.0, 900.0), [EXCLUSION])
    assert not box_in_exclusion(INSIDE_BOX, [])


# ── pipeline ────────────────────────────────────────────────────────────────


def test_people_in_exclusion_zone_are_never_tracked_embedded_or_identified(tmp_path: Path) -> None:
    cam = _camera("entrance", entrance_zone=False, exclusion=True)  # entrance cam w/o zone: identifies anywhere
    pipeline, worker, reid, entrance = _run(tmp_path, cam, [INSIDE_BOX, OUTSIDE_BOX])

    assert len(worker.tracker.tracks) == 1
    assert worker.tracker.tracks[0].box[0] >= 400  # only the person outside the exclusion zone
    assert reid.boxes and all(b[0] >= 400 for b in reid.boxes)
    assert entrance.boxes and all(b[0] >= 400 for b in entrance.boxes)
    events = pipeline.queue.peek_batch(1000)
    assert [e["event_type"] for e in events].count("track.started") == 1
    assert pipeline.metrics.detections_excluded == 6
    assert pipeline.health()["detections_excluded"] == 6


def test_without_exclusion_zone_both_people_are_processed(tmp_path: Path) -> None:
    cam = _camera("entrance", entrance_zone=False, exclusion=False)
    _pipeline, worker, reid, _entrance = _run(tmp_path, cam, [INSIDE_BOX, OUTSIDE_BOX])
    assert len(worker.tracker.tracks) == 2
    assert any(b[0] < 400 for b in reid.boxes)


def test_cloud_exclusion_zones_apply_without_editing_cameras_yaml(tmp_path: Path) -> None:
    from edge_ai.cloud.device_sync import SyncSnapshot

    local = _camera("entrance", entrance_zone=False, exclusion=False)
    snapshot = SyncSnapshot.model_validate(
        {
            "generated_at": "2026-11-02T10:00:00Z",
            "device_id": "d0000000-0000-4000-8000-000000000001",
            "branch_id": "b0000000-0000-4000-8000-000000000001",
            "cameras": [
                {
                    "id": str(local.id),
                    "name": "CAM",
                    "purpose": "entrance",
                    "stream_url": "rtsp://192.0.2.10/s",  # public.cameras column name
                    "zones": [
                        {"id": "f0000000-0000-4000-8000-0000000000c1", "type": "exclusion", "polygon": EXCLUSION}
                    ],
                }
            ],
        }
    )

    class FakeSync:
        last_success_at = None
        last_error = None

        def fetch_or_cached(self):  # type: ignore[no-untyped-def]
            return snapshot

        @staticmethod
        def apply_consents(snap, g):  # type: ignore[no-untyped-def]
            g.replace(snap.consents, generated_at=snap.generated_at_epoch)

    _pipeline, worker, reid, entrance = _run(tmp_path, local, [INSIDE_BOX, OUTSIDE_BOX], config_sync=FakeSync())
    assert len(worker.tracker.tracks) == 1
    assert all(b[0] >= 400 for b in reid.boxes + entrance.boxes)


def test_mixed_camera_without_entrance_zone_never_identifies(tmp_path: Path) -> None:
    mixed = _camera("mixed", entrance_zone=False, exclusion=False)
    _p, _w, reid, entrance = _run(tmp_path, mixed, [OUTSIDE_BOX])
    assert reid.boxes, "the person is still tracked anonymously"
    assert entrance.boxes == []

    mixed_with_zone = _camera("mixed", entrance_zone=True, exclusion=False)
    _p, _w, _r, entrance2 = _run(tmp_path / "z", mixed_with_zone, [OUTSIDE_BOX, INSIDE_BOX])
    # only the person whose foot point is inside the entrance zone (x 600..1300) is identified
    assert entrance2.boxes and all(600 <= (b[0] + b[2]) / 2 <= 1300 for b in entrance2.boxes)

    entrance_cam = _camera("entrance", entrance_zone=False, exclusion=False)
    _p, _w, _r, entrance3 = _run(tmp_path / "e", entrance_cam, [OUTSIDE_BOX])
    assert entrance3.boxes, "a dedicated entrance camera without a zone identifies anywhere in view"


class CountingFaceDetector:
    def __init__(self) -> None:
        self.calls = 0

    def detect(self, image):  # type: ignore[no-untyped-def]
        self.calls += 1
        from edge_ai.identity.face_pipeline import FaceBox

        return [FaceBox((10.0, 10.0, 90.0, 90.0), 0.9)]


class CountingEmbedder:
    model_name = "fake-face"

    def __init__(self) -> None:
        self.calls = 0

    def embed_box(self, image, face, pad_ratio: float = 0.15):  # type: ignore[no-untyped-def]
        self.calls += 1
        return [1.0, 0.0, 0.0]


def test_no_face_is_detected_or_embedded_while_the_gate_is_closed(tmp_path: Path) -> None:
    import os

    store = EncryptedTemplateStore(tmp_path / "f.sqlite", os.urandom(32))
    store.enroll(MEMBER, CONSENT, [1.0, 0.0, 0.0], "fake-face")
    det, emb = CountingFaceDetector(), CountingEmbedder()
    gate = ConsentGate(None)
    ident = EntranceIdentifier(det, emb, FaceMatcher(store), gate)
    box = (200.0, 50.0, 400.0, 470.0)
    frame = np.zeros((480, 640, 3), dtype=np.uint8)

    # nothing loaded / stale → no face processing at all
    assert ident.identify(frame, box).reason == "gate_closed"
    # loaded, but nobody holds both consents → still closed
    gate.replace({MEMBER: {"biometric_face_identification": CONSENT}})
    assert ident.identify(frame, box).reason == "gate_closed"
    assert det.calls == 0 and emb.calls == 0
    # a consenting member exists → identification runs
    gate.replace({MEMBER: {"biometric_face_identification": CONSENT, "cctv_workout_tracking": "c2"}})
    out = ident.identify(frame, box)
    assert out.reason == "matched" and det.calls == 1 and emb.calls == 1
    assert ident.stats["gate_closed"] == 2
