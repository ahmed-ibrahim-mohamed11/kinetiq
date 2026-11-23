from __future__ import annotations

import base64
import json
import urllib.request
import time
from pathlib import Path
from uuid import uuid4

import cv2
import numpy as np
import pytest

from edge_ai.config import CameraConfig, Settings
from edge_ai.detection.base import Detection
from edge_ai.health import serve_health
from edge_ai.identity.consent_gate import BIOMETRIC, TRACKING, ConsentGate
from edge_ai.identity.face_pipeline import FaceBox
from edge_ai.identity.face_store import EncryptedTemplateStore, MatchResult
from edge_ai.pipeline.orchestrator import CameraWorker, Pipeline
from edge_ai.pose.base import Keypoint, Pose
from edge_ai.reps.profiles import RepProfile
from edge_ai.tools.default_profiles import DEFAULT_PROFILES

MEMBER_1 = "11111111-1111-4111-8111-111111111111"
MEMBER_2 = "22222222-2222-4222-8222-222222222222"
CONSENT_1 = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"


class FakeDetector:
    model_name = "fake-yolox"
    active_provider = "CPUExecutionProvider"

    def __init__(self, boxes: list[tuple[float, float, float, float]]) -> None:
        self.boxes = boxes

    def detect(self, image):  # type: ignore[no-untyped-def]
        return [Detection(b, 0.95) for b in self.boxes]


class FakePoseEstimator:
    model_name = "fake-rtmpose"

    def estimate(self, image, boxes):  # type: ignore[no-untyped-def]
        # Return a standard 17-keypoint standing pose for each box
        poses = []
        for b in boxes:
            x_mid = (b[0] + b[2]) / 2
            kps = [
                Keypoint(x_mid, b[1] + 20, 0.9),  # nose
                Keypoint(x_mid - 10, b[1] + 15, 0.9),  # eye
                Keypoint(x_mid + 10, b[1] + 15, 0.9),
                Keypoint(x_mid - 20, b[1] + 20, 0.9),
                Keypoint(x_mid + 20, b[1] + 20, 0.9),
                Keypoint(x_mid - 40, b[1] + 100, 0.9),  # shoulders
                Keypoint(x_mid + 40, b[1] + 100, 0.9),
                Keypoint(x_mid - 50, b[1] + 180, 0.9),  # elbows
                Keypoint(x_mid + 50, b[1] + 180, 0.9),
                Keypoint(x_mid - 50, b[1] + 250, 0.9),  # wrists
                Keypoint(x_mid + 50, b[1] + 250, 0.9),
                Keypoint(x_mid - 30, b[1] + 300, 0.9),  # hips
                Keypoint(x_mid + 30, b[1] + 300, 0.9),
                Keypoint(x_mid - 30, b[1] + 450, 0.9),  # knees
                Keypoint(x_mid + 30, b[1] + 450, 0.9),
                Keypoint(x_mid - 30, b[1] + 600, 0.9),  # ankles
                Keypoint(x_mid + 30, b[1] + 600, 0.9),
            ]
            poses.append(Pose(tuple(kps)))
        return poses


class FakeSource:
    def __init__(self) -> None:
        self.started = False

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.started = False

    def status(self):  # type: ignore[no-untyped-def]
        from edge_ai.stream.base import StreamStatus
        return StreamStatus.ONLINE if self.started else StreamStatus.OFFLINE

    def measured_fps(self) -> float:
        return 30.0

    def latest(self):  # type: ignore[no-untyped-def]
        return None


class FakeQueue:
    def depth(self) -> int:
        return 0

    def oldest_age_seconds(self) -> float:
        return 0.0

    def dead_letter_depth(self) -> int:
        return 0

    def append(self, build_fn):  # type: ignore[no-untyped-def]
        return build_fn(1)

    def pending_payloads(self, *args, **kwargs) -> list:  # type: ignore[no-untyped-def]
        return []


def _make_pipeline(tmp_path: Path):
    s = Settings(
        device_id=uuid4(),
        branch_id=uuid4(),
        data_dir=tmp_path,
        camera_secrets_file=tmp_path / "camera_secrets.yaml",
        device_secret="dummy_device_secret_12345",
        sync_batch_size=10,
    )
    cam = CameraConfig(
        id=uuid4(),
        name="Laptop Webcam",
        url="webcam://0",
        purpose="floor",
        zones=[],  # Zone-free camera!
    )
    det = FakeDetector([(100.0, 100.0, 300.0, 700.0), (400.0, 100.0, 600.0, 700.0)])
    pose = FakePoseEstimator()

    now = time.time()
    gate = ConsentGate(tmp_path / "consents.json")
    gate.replace(
        {MEMBER_1: {BIOMETRIC: CONSENT_1, TRACKING: CONSENT_1}},
        generated_at=now,
        expires={MEMBER_1: {BIOMETRIC: now + 3600.0, TRACKING: now + 3600.0}},
    )

    import os
    key = os.urandom(32)
    store = EncryptedTemplateStore(tmp_path / "templates.sqlite", key)

    class MockFaceDetector:
        def detect(self, img):
            return [FaceBox((50.0, 50.0, 150.0, 150.0), 0.95)]

    class MockFaceEmbedder:
        model_name = "mock-sface"

        def embed_box(self, img, box):
            return [0.1] * 128

        def embed(self, img):
            return [0.1] * 128

    class MockMatcher:
        match_threshold = 0.60

        def match(self, emb):
            return MatchResult(member_id=MEMBER_1, confidence=0.92, ambiguous=False, template_ref="tmpl-001")

        def refresh(self):
            pass

    mock_entrance = type(
        "EntranceMock",
        (),
        {
            "detector": MockFaceDetector(),
            "embedder": MockFaceEmbedder(),
            "matcher": MockMatcher(),
            "liveness": type("LivenessMock", (), {"score": staticmethod(lambda img, f: None)})(),
            "liveness_threshold": 0.5,
            "_attempts": {},
        },
    )()

    p = Pipeline(
        settings=s,
        cameras=[cam],
        exercise_ids={},
        profiles=DEFAULT_PROFILES,
        detector=det,
        pose=pose,
        reid=None,
        entrance=mock_entrance,
        consent_gate=gate,
        template_store=store,
        queue=FakeQueue(),
    )
    p._save_member_name(MEMBER_1, "Ahmed Trainee")
    return p, cam


def test_identify_face_base64_success(tmp_path: Path):
    pipeline, cam = _make_pipeline(tmp_path)
    # Generate test image
    test_img = np.zeros((200, 200, 3), dtype=np.uint8)
    _, buf = cv2.imencode(".jpg", test_img)
    b64_str = base64.b64encode(buf.tobytes()).decode("ascii")

    res = pipeline.identify_face_base64(b64_str)
    print("RES IS:", res)
    assert res["ok"] is True
    assert res["matched"] is True
    assert res["member_id"] == MEMBER_1
    assert res["member_name"] == "Ahmed Trainee"
    assert res["confidence"] == 0.92
    assert res["template_ref"] == "tmpl-001"


def test_identify_face_base64_no_face(tmp_path: Path):
    pipeline, cam = _make_pipeline(tmp_path)
    # Mock face detector returning no faces
    pipeline.entrance.detector.detect = lambda img: []

    test_img = np.zeros((100, 100, 3), dtype=np.uint8)
    _, buf = cv2.imencode(".jpg", test_img)
    b64_str = base64.b64encode(buf.tobytes()).decode("ascii")

    res = pipeline.identify_face_base64(b64_str)
    print("RES IS:", res)
    assert res["ok"] is True
    assert res["matched"] is False
    assert res["reason"] == "no_face"


def test_zone_free_multiperson_tracking(tmp_path: Path):
    pipeline, cam = _make_pipeline(tmp_path)
    worker = CameraWorker(pipeline, cam, FakeSource())

    # Multi-person frame with 2 people
    frame = np.zeros((800, 800, 3), dtype=np.uint8)
    now = time.time()
    for i in range(4):
        worker.process_frame(frame, now + i * 0.1)

    live = pipeline.live_state()
    assert live["people_count"] == 2
    # Verify both people are tracked independently
    assert len(live["people"]) == 2
    for p in live["people"]:
        assert "completed_reps" in p
        assert "valid_reps" in p
        assert "knee_angle_deg" in p
        assert "form_verdict" in p
        assert p["exercise"] == "squat"


def test_http_server_endpoints(tmp_path: Path):
    pipeline, cam = _make_pipeline(tmp_path)
    pipeline._latest_jpeg = b"test_jpeg_bytes"

    # Start health server on random free port
    server = serve_health(0, pipeline.health, pipeline=pipeline)
    port = server.server_port

    try:
        # GET /live
        req = urllib.request.urlopen(f"http://127.0.0.1:{port}/live")
        assert req.status == 200
        live_data = json.loads(req.read().decode())
        assert "camera_owner" in live_data
        assert "people" in live_data

        # GET /camera/frame.jpg
        req = urllib.request.urlopen(f"http://127.0.0.1:{port}/camera/frame.jpg")
        assert req.status == 200
        assert req.read() == b"test_jpeg_bytes"

        # POST /camera/release
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/camera/release",
            data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode())
            assert data["ok"] is True
            assert data["camera_owner"] == "browser"
            assert data["camera_paused"] is True

        # POST /camera/acquire
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/camera/acquire",
            data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode())
            assert data["ok"] is True
            assert data["camera_owner"] == "edge-ai"
            assert data["camera_paused"] is False

        # POST /api/face/identify
        test_img = np.zeros((100, 100, 3), dtype=np.uint8)
        _, buf = cv2.imencode(".jpg", test_img)
        b64_str = base64.b64encode(buf.tobytes()).decode("ascii")
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/face/identify",
            data=json.dumps({"image": b64_str}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode())
            assert data["ok"] is True
            assert data["matched"] is True
            assert data["member_id"] == MEMBER_1
            assert data["member_name"] == "Ahmed Trainee"

        # POST /api/workout/reset
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/workout/reset",
            data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode())
            assert data["ok"] is True

        # GET /healthz
        req = urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz")
        assert req.status in (200, 503)
        health_data = json.loads(req.read().decode())
        assert "frames_processed" in health_data
        assert "detections" in health_data
        assert "model_status" in health_data

        # GET /api/camera/status
        req = urllib.request.urlopen(f"http://127.0.0.1:{port}/api/camera/status")
        assert req.status == 200
        cam_status = json.loads(req.read().decode())
        assert "physical_handles" in cam_status
        assert "frames_received" in cam_status
        assert "frames_processed" in cam_status
        assert "detections" in cam_status
        assert "model_status" in cam_status

        # GET /api/camera/diagnostics
        req = urllib.request.urlopen(f"http://127.0.0.1:{port}/api/camera/diagnostics")
        assert req.status == 200
        cam_diag = json.loads(req.read().decode())
        assert "physicalHandleCount" in cam_diag or "physical_handles" in cam_diag
        assert "frames_processed" in cam_diag
        assert "detections" in cam_diag
        assert "model_status" in cam_diag

    finally:
        server.shutdown()
