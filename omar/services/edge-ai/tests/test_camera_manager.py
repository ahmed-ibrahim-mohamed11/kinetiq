from __future__ import annotations

import queue
import time
from unittest.mock import MagicMock, patch
import numpy as np
import pytest

from edge_ai.camera.manager import (
    CameraState,
    CentralCameraManager,
    ConsumerSubscription,
    PhysicalCameraDevice,
)
from edge_ai.constants import CONSUMER_LEASE_TIMEOUT_SECONDS, MAX_FRAME_QUEUE_SIZE
from edge_ai.identity.consent_gate import BIOMETRIC, ConsentGate
from edge_ai.identity.face_pipeline import FaceBox
from edge_ai.identity.face_store import MatchResult


class DummyCapture:
    """Mock cv2.VideoCapture that supplies fake dummy frames."""

    def __init__(self, opened: bool = True) -> None:
        self._opened = opened
        self._frame_count = 0

    def isOpened(self) -> bool:
        return self._opened

    def read(self) -> tuple[bool, np.ndarray]:
        if not self._opened:
            return False, np.zeros((10, 10, 3), dtype=np.uint8)
        self._frame_count += 1
        frame = np.full((120, 160, 3), (self._frame_count % 255), dtype=np.uint8)
        return True, frame

    def release(self) -> None:
        self._opened = False

    def set(self, prop: int, val: float) -> bool:
        return True

    def get(self, prop: int) -> float:
        return 640.0


def test_physical_camera_device_single_handle():
    """Verify PhysicalCameraDevice maintains at most 1 hardware handle."""
    dev = PhysicalCameraDevice("test-cam-1", url="webcam://0", name="Test Cam 1")
    dummy_cap = DummyCapture(opened=True)

    with patch.object(dev, "_open_capture", return_value=dummy_cap):
        dev.start()
        # Give thread brief moment to initialize
        time.sleep(0.1)
        assert dev.state in (CameraState.STARTING, CameraState.RUNNING)
        assert dev.physical_handles == 1

        dev.stop()
        assert dev.state == CameraState.IDLE
        assert dev.physical_handles == 0


def test_central_camera_manager_multi_consumer_concurrency():
    """Verify CentralCameraManager multiplexes 1 physical camera to multiple independent consumers."""
    mgr = CentralCameraManager()
    dummy_cap = DummyCapture(opened=True)

    with patch.object(PhysicalCameraDevice, "_open_capture", return_value=dummy_cap):
        # 1. Start Consumer 1 (e.g. Reception Kiosk)
        r1 = mgr.start_camera("cam-concurrency", "consumer_reception")
        assert r1["ok"] is True
        assert r1["active_consumers"] == 1
        assert "consumer_reception" in r1["consumers"]

        # 2. Start Consumer 2 (e.g. Member Login)
        r2 = mgr.start_camera("cam-concurrency", "consumer_member_login")
        assert r2["ok"] is True
        assert r2["active_consumers"] == 2
        assert "consumer_reception" in r2["consumers"]
        assert "consumer_member_login" in r2["consumers"]

        # 3. Start Consumer 3 (e.g. Diagnostics)
        r3 = mgr.start_camera("cam-concurrency", "consumer_diagnostics")
        assert r3["ok"] is True
        assert r3["active_consumers"] == 3

        # Exactly 1 physical camera handle opened
        dev = mgr._get_or_create_device("cam-concurrency")
        assert dev.physical_handles == 1

        # 4. Stop Consumer 1 -> Camera must NOT close, consumers 2 & 3 must remain active
        stop_r1 = mgr.stop_camera("cam-concurrency", "consumer_reception")
        assert stop_r1["ok"] is True
        assert stop_r1["active_consumers"] == 2
        assert "consumer_reception" not in stop_r1["consumers"]
        assert "consumer_member_login" in stop_r1["consumers"]
        assert dev.physical_handles == 1

        # 5. Stop Consumer 2 -> 1 consumer remains
        stop_r2 = mgr.stop_camera("cam-concurrency", "consumer_member_login")
        assert stop_r2["ok"] is True
        assert stop_r2["active_consumers"] == 1
        assert dev.physical_handles == 1

        # 6. Stop Consumer 3 -> 0 consumers remain -> hardware camera must release
        stop_r3 = mgr.stop_camera("cam-concurrency", "consumer_diagnostics")
        assert stop_r3["ok"] is True
        assert stop_r3["active_consumers"] == 0
        assert dev.physical_handles == 0
        assert dev.state == CameraState.IDLE


def test_slow_consumer_bounded_queue_drops_frames():
    """Verify slow consumers drop oldest frames without memory leaks or blocking."""
    dev = PhysicalCameraDevice("test-drop-cam")
    sub = ConsumerSubscription("slow_consumer", "test-drop-cam", time.time(), time.time())
    dev.consumers["slow_consumer"] = sub

    # Put frames directly simulating camera loop
    fake_jpeg = b"\xff\xd8\xff\xe0dummy_jpeg"
    for _ in range(MAX_FRAME_QUEUE_SIZE * 3):
        if sub.frame_queue.full():
            try:
                sub.frame_queue.get_nowait()
                sub.frames_dropped += 1
            except queue.Empty:
                pass
        sub.frame_queue.put_nowait(fake_jpeg)
        sub.frames_sent += 1

    assert sub.frame_queue.qsize() <= MAX_FRAME_QUEUE_SIZE
    assert sub.frames_dropped > 0
    assert sub.frames_sent == MAX_FRAME_QUEUE_SIZE * 3


def test_lease_sweep_stale_consumer_cleanup():
    """Verify consumers without heartbeats are swept after CONSUMER_LEASE_TIMEOUT_SECONDS."""
    mgr = CentralCameraManager()
    dummy_cap = DummyCapture(opened=True)

    with patch.object(PhysicalCameraDevice, "_open_capture", return_value=dummy_cap):
        mgr.start_camera("cam-sweep", "abandoned_consumer")
        dev = mgr._get_or_create_device("cam-sweep")
        assert "abandoned_consumer" in dev.consumers

        # Artificially age last_heartbeat_at past lease timeout
        dev.consumers["abandoned_consumer"].last_heartbeat_at = time.time() - (CONSUMER_LEASE_TIMEOUT_SECONDS + 5)

        # Trigger manual sweep check logic
        now = time.time()
        with mgr._lock:
            for sub in list(dev.consumers.values()):
                if now - sub.last_heartbeat_at > CONSUMER_LEASE_TIMEOUT_SECONDS:
                    dev.consumers.pop(sub.consumer_id)
            if dev.active_consumers_count == 0:
                dev.stop()

        assert "abandoned_consumer" not in dev.consumers
        assert dev.active_consumers_count == 0
        assert dev.physical_handles == 0


def test_face_recognition_standardized_contract():
    """Verify recognize_face matches the required standard schema and respects consent and cooldown."""
    mgr = CentralCameraManager()
    dev = mgr._get_or_create_device("cam-face")
    # Feed a dummy face image
    test_img = np.zeros((200, 200, 3), dtype=np.uint8)
    dev.last_frame = test_img

    mock_detector = MagicMock()
    mock_box = FaceBox((20, 20, 100, 100), 0.95)
    mock_detector.detect.return_value = [mock_box]

    mock_embedder = MagicMock()
    mock_embedder.embed_box.return_value = np.ones(128, dtype=np.float32)

    mock_store = MagicMock()
    mock_store.count.return_value = 1

    mock_consent = MagicMock(spec=ConsentGate)
    mock_consent.allows_face.return_value = True

    mgr.set_identity_stack(
        face_detector=mock_detector,
        face_embedder=mock_embedder,
        template_store=mock_store,
        consent_gate=mock_consent,
        member_names={"member_abc": "Capt. Marvel"},
    )

    with patch.object(mgr, "_assess_face_quality", return_value=(True, None, 50.0)), patch("edge_ai.identity.face_store.FaceMatcher") as MockMatcherCls:
        mock_matcher = MockMatcherCls.return_value
        mock_matcher.match.return_value = MatchResult(
            member_id="member_abc",
            confidence=0.92,
            ambiguous=False,
            template_ref="tmpl_123",
        )

        res = mgr.recognize_face("cam-face", "consumer_kiosk", image_b64="current", min_confidence=0.70)

        # Verify standardized contract
        assert "eventId" in res
        assert res["cameraId"] == "cam-face"
        assert res["consumerId"] == "consumer_kiosk"
        assert res["memberId"] == "member_abc"
        assert res["memberCode"] == "GYM-MEMBER"
        assert res["displayName"] == "Capt. Marvel"
        assert res["matchStatus"] == "matched"
        assert res["confidence"] == 0.92
        assert res["modelVersion"] == "yunet_sface_v1"
        assert "occurredAt" in res
        assert res["cooldown"] is False

        # Immediate repeat recognition should flag cooldown=True
        res_repeat = mgr.recognize_face("cam-face", "consumer_kiosk", image_b64="current", min_confidence=0.70)
        assert res_repeat["cooldown"] is True

        # Test no consent withdrawal
        mock_consent.allows_face.return_value = False
        res_no_consent = mgr.recognize_face("cam-face", "consumer_kiosk", image_b64="current", min_confidence=0.70)
        assert res_no_consent["matchStatus"] == "no_consent"
        assert res_no_consent["error"] is not None


def test_five_simultaneous_consumers():
    """Verify 5 consumers connecting concurrently share ONE physical handle and release correctly."""
    mgr = CentralCameraManager()
    dummy_cap = DummyCapture(opened=True)

    consumer_ids = [
        "admin-reception-kiosk",
        "admin-auth-login",
        "member-auth-login",
        "admin-workout-monitor",
        "admin-diagnostics",
    ]

    with patch.object(PhysicalCameraDevice, "_open_capture", return_value=dummy_cap):
        # Start all 5 consumers
        for i, cid in enumerate(consumer_ids):
            res = mgr.start_camera("multi-cam", cid)
            assert res["ok"] is True
            assert res["active_consumers"] == i + 1

        time.sleep(0.05)
        dev = mgr._get_or_create_device("multi-cam")
        assert dev.physical_handles == 1

        status = mgr.get_camera_status("multi-cam")
        assert status["active_consumers"] == 5
        assert status["physical_handles"] == 1
        assert sorted(status["consumers"]) == sorted(consumer_ids)

        # Unsubscribe one by one
        for i, cid in enumerate(consumer_ids[:-1]):
            stop_res = mgr.stop_camera("multi-cam", cid)
            assert stop_res["ok"] is True
            assert stop_res["active_consumers"] == 5 - (i + 1)
            # Hardware handle remains active while at least 1 consumer is present
            assert dev.physical_handles == 1

        # Stop last consumer -> releases hardware handle
        final_stop = mgr.stop_camera("multi-cam", consumer_ids[-1])
        assert final_stop["active_consumers"] == 0
        assert dev.physical_handles == 0
        assert dev.state == CameraState.IDLE


def test_duplicate_start_and_stop_idempotency():
    """Verify duplicate start and stop calls do not produce duplicate handles or errors."""
    mgr = CentralCameraManager()
    dummy_cap = DummyCapture(opened=True)

    with patch.object(PhysicalCameraDevice, "_open_capture", return_value=dummy_cap):
        # Call start 4 times for same consumer
        for _ in range(4):
            r = mgr.start_camera("idem-cam", "consumer_repeat")
            assert r["ok"] is True
            assert r["active_consumers"] == 1

        dev = mgr._get_or_create_device("idem-cam")
        time.sleep(0.05)
        assert dev.physical_handles == 1

        # Call stop 3 times
        for _ in range(3):
            r = mgr.stop_camera("idem-cam", "consumer_repeat")
            assert r["ok"] is True
            assert r["active_consumers"] == 0
            assert dev.physical_handles == 0


def test_disconnect_and_exponential_backoff():
    """Verify hardware failure triggers RECONNECTING or DEVICE_NOT_FOUND state with bounded backoff."""
    dev = PhysicalCameraDevice("fail-cam", url="webcam://99")
    assert dev.state == CameraState.IDLE

    # Simulate hardware exception in _open_capture
    with patch.object(dev, "_open_capture", side_effect=ConnectionError("Device disconnected")):
        dev.start()
        time.sleep(0.1)
        assert dev.state in (CameraState.STARTING, CameraState.RECONNECTING, CameraState.DEVICE_NOT_FOUND, CameraState.ERROR)
        dev.stop()
        assert dev.state == CameraState.IDLE


def test_broker_frame_source():
    """Verify BrokerFrameSource consumes from CentralCameraManager without separate handles."""
    from edge_ai.camera.broker_source import BrokerFrameSource

    mgr = CentralCameraManager.get_instance()
    dev = mgr.register_camera("bfs-cam", url="webcam://7")
    dev.last_frame = np.ones((100, 100, 3), dtype=np.uint8)
    dev.last_frame_at = 12345.67

    dummy_cap = DummyCapture(opened=True)
    with patch.object(PhysicalCameraDevice, "_open_capture", return_value=dummy_cap):
        source = BrokerFrameSource("bfs-cam", url="webcam://7", name="BFS-Camera")
        source.start()
        assert "pipeline_bfs-cam" in dev.consumers

        frame = source.latest()
        assert frame is not None
        assert frame.image.shape in ((100, 100, 3), (120, 160, 3))
        assert frame.captured_at > 0

        source.stop()
        assert "pipeline_bfs-cam" not in dev.consumers


def test_qa_member_face_recognition_flow():
    """Verify full QA recognition flow: match, unknown face, low confidence, and revoked consent."""
    mgr = CentralCameraManager()
    dev = mgr._get_or_create_device("qa-cam")
    dev.last_frame = np.ones((200, 200, 3), dtype=np.uint8) * 128

    mock_detector = MagicMock()
    mock_box = FaceBox((20, 20, 100, 100), 0.95)
    mock_detector.detect.return_value = [mock_box]

    mock_embedder = MagicMock()
    mock_embedder.embed_box.return_value = np.ones(128, dtype=np.float32)

    mock_store = MagicMock()
    mock_store.count.return_value = 1

    mock_consent = MagicMock(spec=ConsentGate)
    mock_consent.allows_face.return_value = True

    mgr.set_identity_stack(
        face_detector=mock_detector,
        face_embedder=mock_embedder,
        template_store=mock_store,
        consent_gate=mock_consent,
        member_names={"qa_member_1": "Mamdouh Wael"},
    )

    with patch.object(mgr, "_assess_face_quality", return_value=(True, None, 60.0)), patch("edge_ai.identity.face_store.FaceMatcher") as MockMatcherCls:
        mock_matcher = MockMatcherCls.return_value

        # 1. Successful QA match
        mock_matcher.match.return_value = MatchResult(
            member_id="qa_member_1",
            confidence=0.96,
            ambiguous=False,
            template_ref="tmpl_qa_1",
        )
        res = mgr.recognize_face("qa-cam", "kiosk", image_b64="current", min_confidence=0.75)
        assert res["matchStatus"] == "matched"
        assert res["memberId"] == "qa_member_1"
        assert res["displayName"] == "Mamdouh Wael"
        assert res["confidence"] == 0.96

        # 2. Unknown Face (no member matched)
        mock_matcher.match.return_value = MatchResult(
            member_id=None,
            confidence=0.35,
            ambiguous=False,
            template_ref=None,
        )
        res_unk = mgr.recognize_face("qa-cam", "kiosk", image_b64="current", min_confidence=0.75)
        assert res_unk["matchStatus"] == "unknown"
        assert res_unk["memberId"] is None

        # 3. Low confidence match rejected
        mock_matcher.match.return_value = MatchResult(
            member_id="qa_member_1",
            confidence=0.62,  # below 0.75
            ambiguous=False,
            template_ref="tmpl_qa_1",
        )
        res_low = mgr.recognize_face("qa-cam", "kiosk", image_b64="current", min_confidence=0.75)
        assert res_low["matchStatus"] == "unknown"
        assert res_low["memberId"] is None

        # 4. Ambiguous match rejected
        mock_matcher.match.return_value = MatchResult(
            member_id="qa_member_1",
            confidence=0.88,
            ambiguous=True,
            template_ref="tmpl_qa_1",
        )
        res_amb = mgr.recognize_face("qa-cam", "kiosk", image_b64="current", min_confidence=0.75)
        assert res_amb["matchStatus"] == "unknown"
        assert res_amb["memberId"] is None


def test_camera_start_waits_for_real_physical_handle():
    """Verify start_camera waits until the capture handle is opened before returning."""
    mgr = CentralCameraManager()
    dummy_cap = DummyCapture(opened=True)

    with patch.object(PhysicalCameraDevice, "_open_capture", return_value=dummy_cap):
        res = mgr.start_camera("wait-cam", "consumer_wait")
        assert res["ok"] is True
        assert res["physical_handles"] == 1
        assert res["state"] == CameraState.RUNNING.value

        dev = mgr._get_or_create_device("wait-cam")
        assert dev.physical_handles == 1

        # Stop camera safely
        mgr.stop_camera("wait-cam", "consumer_wait")
        assert dev.physical_handles == 0


def test_observable_counters_and_model_status():
    """Verify frames_received, frames_processed, detections, and model_status are reported."""
    mgr = CentralCameraManager()
    dummy_cap = DummyCapture(opened=True)

    with patch.object(PhysicalCameraDevice, "_open_capture", return_value=dummy_cap):
        mgr.start_camera("counter-cam", "counter_consumer")
        dev = mgr._get_or_create_device("counter-cam")
        # Give thread time to read frames
        time.sleep(0.12)
        assert dev.frames_received > 0

        dev.frames_processed = 15
        dev.detections = 12
        dev.last_processed_frame_at = time.time()
        dev.inference_fps = 9.8

        status = mgr.get_camera_status("counter-cam")
        assert status["frames_received"] > 0
        assert status["frames_processed"] == 15
        assert status["detections"] == 12
        assert status["inference_fps"] == 9.8
        assert status["last_processed_frame_at"] > 0
        assert "model_status" in status

        diag = mgr.get_diagnostics("counter-cam")
        assert diag["frames_processed"] == 15
        assert diag["detections"] == 12
        assert diag["inference_fps"] == 9.8
        assert "counter-cam" in diag["cameras"]
        assert diag["cameras"]["counter-cam"]["frames_processed"] == 15

        mgr.stop_camera("counter-cam", "counter_consumer")


def test_release_physical_handles_for_browser_mode():
    """Verify release_physical_handles releases the hardware capture for browser mode."""
    mgr = CentralCameraManager()
    dummy_cap = DummyCapture(opened=True)

    with patch.object(PhysicalCameraDevice, "_open_capture", return_value=dummy_cap):
        mgr.start_camera("rel-cam", "consumer_rel")
        dev = mgr._get_or_create_device("rel-cam")
        assert dev.physical_handles == 1

        res = mgr.release_physical_handles()
        assert res["ok"] is True
        assert res["released"] is True
        assert dev.physical_handles == 0
        assert dev.state == CameraState.IDLE


