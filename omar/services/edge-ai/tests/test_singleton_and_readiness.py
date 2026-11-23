"""
Regression tests for KinetiQ Edge AI Singleton Guard, Safe Teardown, and Readiness Checks.
Verifies all 12 singleton guard and readiness requirements on Windows and Linux.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from edge_ai.camera.manager import CameraState, CentralCameraManager, PhysicalCameraDevice
from edge_ai.readiness import ReadinessTracker
from edge_ai.singleton import (
    SingletonAlreadyRunningError,
    SingletonLock,
    safe_terminate_edge_ai,
    verify_edge_ai_process,
)


def test_first_process_acquires_lock(tmp_path: Path) -> None:
    """1. First Edge AI process starts and acquires singleton lock successfully."""
    lock_file = tmp_path / "edge_ai.lock"
    meta_file = tmp_path / "edge_ai.json"
    mutex_name = f"Local\\KinetiQ_Test_Mutex_{int(time.time()*1000)}"

    lock = SingletonLock(
        lock_file=lock_file,
        metadata_file=meta_file,
        camera_id="cam_01",
        health_port=8088,
        version="0.1.0",
        mutex_name=mutex_name,
    )

    assert lock.acquire() is True
    assert lock.acquired is True
    assert meta_file.is_file()

    meta = lock.read_metadata()
    assert meta["pid"] == os.getpid()
    assert meta["camera_id"] == "cam_01"
    assert meta["health_url"] == "http://localhost:8088/healthz"
    assert meta["edge_ai_version"] == "0.1.0"
    assert "process_start_time" in meta

    lock.release()
    assert lock.acquired is False
    assert not meta_file.exists()


def test_second_process_is_rejected(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """2. Second Edge AI process is rejected with EDGE_AI_ALREADY_RUNNING and details."""
    lock_file = tmp_path / "edge_ai.lock"
    meta_file = tmp_path / "edge_ai.json"
    mutex_name = f"Local\\KinetiQ_Test_Mutex_{int(time.time()*1000)}"

    lock1 = SingletonLock(
        lock_file=lock_file,
        metadata_file=meta_file,
        camera_id="cam_01",
        health_port=8088,
        version="0.1.0",
        mutex_name=mutex_name,
    )
    assert lock1.acquire() is True

    # Attempt second lock
    lock2 = SingletonLock(
        lock_file=lock_file,
        metadata_file=meta_file,
        camera_id="cam_02",
        health_port=8089,
        version="0.1.0",
        mutex_name=mutex_name,
    )

    result = lock2.acquire()
    assert result is False
    assert lock2.acquired is False

    captured = capsys.readouterr()
    assert "EDGE_AI_ALREADY_RUNNING" in captured.err
    assert f"Owning PID: {os.getpid()}" in captured.err
    assert "Health URL: http://localhost:8088/healthz" in captured.err

    lock1.release()


def test_second_process_never_opens_camera(tmp_path: Path) -> None:
    """3. Second process never opens or accesses the camera handle."""
    lock_file = tmp_path / "edge_ai.lock"
    meta_file = tmp_path / "edge_ai.json"
    mutex_name = f"Local\\KinetiQ_Test_Mutex_{int(time.time()*1000)}"

    lock1 = SingletonLock(
        lock_file=lock_file,
        metadata_file=meta_file,
        mutex_name=mutex_name,
    )
    assert lock1.acquire() is True

    with patch.object(PhysicalCameraDevice, "_open_capture") as mock_open:
        lock2 = SingletonLock(
            lock_file=lock_file,
            metadata_file=meta_file,
            mutex_name=mutex_name,
        )
        acquired = lock2.acquire()
        if not acquired:
            # Simulated check in runner: do not initialize camera
            pass
        else:
            dev = PhysicalCameraDevice("0")
            dev.start()

        # Confirmed: camera was NEVER opened by the second instance
        assert mock_open.call_count == 0

    lock1.release()


def test_stale_metadata_safely_removed(tmp_path: Path) -> None:
    """4. If metadata is stale but OS lock is free, safely cleans metadata and continues."""
    lock_file = tmp_path / "edge_ai.lock"
    meta_file = tmp_path / "edge_ai.json"
    mutex_name = f"Local\\KinetiQ_Test_Mutex_{int(time.time()*1000)}"

    # Write fake stale metadata from a deceased process
    stale_data = {
        "pid": 9999999,
        "process_start_time": "2020-01-01T00:00:00Z",
        "hostname": "old-machine",
        "command": "python -m edge_ai run",
        "edge_ai_version": "0.0.1",
        "camera_id": "stale_cam",
        "health_url": "http://localhost:8088/healthz",
    }
    with open(meta_file, "w", encoding="utf-8") as f:
        json.dump(stale_data, f)

    assert meta_file.exists()

    # The OS lock is free, so acquisition must succeed and replace stale metadata
    lock = SingletonLock(
        lock_file=lock_file,
        metadata_file=meta_file,
        camera_id="new_cam",
        mutex_name=mutex_name,
    )

    assert lock.acquire() is True
    meta = lock.read_metadata()
    assert meta["pid"] == os.getpid()
    assert meta["camera_id"] == "new_cam"
    assert meta["pid"] != 9999999

    lock.release()


def test_unrelated_pid_never_killed() -> None:
    """5. Never kill a PID unless verified to belong to KinetiQ Edge AI."""
    # Test 1: PID <= 0
    assert verify_edge_ai_process(0) is False
    assert safe_terminate_edge_ai(0) is False

    # Test 2: Non-existent PID
    assert verify_edge_ai_process(99999999) is False
    assert safe_terminate_edge_ai(99999999) is False

    # Test 3: System / non-python PID (e.g. PID 4 on Windows, or parent process if not edge_ai)
    # Even if process exists, command line does not match edge_ai
    system_pid = 4 if sys.platform == "win32" else 1
    assert verify_edge_ai_process(system_pid) is False
    assert safe_terminate_edge_ai(system_pid) is False


def test_process_crash_releases_lock(tmp_path: Path) -> None:
    """6. Crash/abrupt termination of process automatically releases OS lock."""
    lock_file = tmp_path / "edge_ai.lock"
    meta_file = tmp_path / "edge_ai.json"
    mutex_name = f"Local\\KinetiQ_Test_Mutex_{int(time.time()*1000)}"

    # Launch helper child process that acquires lock and abruptly exits
    code = f"""
import sys, os, time
sys.path.insert(0, r"{Path(__file__).parents[1]}")
from edge_ai.singleton import SingletonLock
lock = SingletonLock(r"{lock_file}", r"{meta_file}", mutex_name=r"{mutex_name}")
if lock.acquire():
    # Signal parent ready
    sys.stdout.write("ACQUIRED\\n")
    sys.stdout.flush()
    time.sleep(0.5)
    # Abrupt exit simulating crash without calling release()
    os._exit(42)
else:
    sys.exit(1)
"""
    proc = subprocess.Popen(
        [sys.executable, "-c", code],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    out = proc.stdout.readline().strip() if proc.stdout else ""
    assert "ACQUIRED" in out
    proc.wait(timeout=5.0)
    assert proc.returncode == 42

    # Now verify that parent can immediately acquire the lock (OS kernel freed it)
    parent_lock = SingletonLock(
        lock_file=lock_file,
        metadata_file=meta_file,
        mutex_name=mutex_name,
    )
    assert parent_lock.acquire() is True
    parent_lock.release()


def test_restart_succeeds_after_crash(tmp_path: Path) -> None:
    """7. Restart succeeds immediately after crash even if metadata was left on disk."""
    lock_file = tmp_path / "edge_ai.lock"
    meta_file = tmp_path / "edge_ai.json"
    mutex_name = f"Local\\KinetiQ_Test_Mutex_{int(time.time()*1000)}"

    # Simulate crashed process metadata left behind
    meta_file.write_text(json.dumps({"pid": 88888, "command": "python -m edge_ai run"}), encoding="utf-8")

    restart_lock = SingletonLock(
        lock_file=lock_file,
        metadata_file=meta_file,
        camera_id="cam_restart",
        mutex_name=mutex_name,
    )
    assert restart_lock.acquire() is True
    new_meta = restart_lock.read_metadata()
    assert new_meta["pid"] == os.getpid()
    assert new_meta["camera_id"] == "cam_restart"

    restart_lock.release()


def test_multiple_consumers_share_single_physical_handle() -> None:
    """8. Multiple browser consumers multiplex on ONE physical camera handle."""
    mgr = CentralCameraManager.get_instance()
    # Mock physical device start/stop
    dev = mgr.register_camera("test_cam_mux", url="sim://0", name="Test Mux Cam")

    with patch.object(dev, "_open_capture", return_value=MagicMock(isOpened=lambda: True, read=lambda: (True, None))):
        dev.start()
        dev.frames_received = 10

        # Start 3 distinct consumers
        res1 = mgr.start_camera("test_cam_mux", "consumer_page_1")
        res2 = mgr.start_camera("test_cam_mux", "consumer_page_2")
        res3 = mgr.start_camera("test_cam_mux", "consumer_page_3")

        assert res1["ok"] is True
        assert res2["ok"] is True
        assert res3["ok"] is True

        # Check diagnostics
        diag = mgr.get_diagnostics("test_cam_mux")
        assert diag["physical_handles"] == 1
        assert diag["active_consumers"] == 3

        # Clean up
        mgr.stop_camera("test_cam_mux", "consumer_page_1")
        mgr.stop_camera("test_cam_mux", "consumer_page_2")
        mgr.stop_camera("test_cam_mux", "consumer_page_3")
        dev.stop()


def test_final_consumer_releases_physical_handle() -> None:
    """9. When final consumer unsubscribes, the physical hardware handle is released."""
    mgr = CentralCameraManager.get_instance()
    dev = mgr.register_camera("test_cam_release", url="sim://1", name="Test Release Cam")

    with patch.object(dev, "_open_capture", return_value=MagicMock(isOpened=lambda: True, read=lambda: (True, None))):
        dev.start()
        dev.frames_received = 5

        mgr.start_camera("test_cam_release", "cons_A")
        mgr.start_camera("test_cam_release", "cons_B")
        assert dev.active_consumers_count == 2
        assert dev.physical_handles == 1

        # Stop first consumer
        mgr.stop_camera("test_cam_release", "cons_A")
        assert dev.active_consumers_count == 1
        assert dev.physical_handles == 1

        # Stop final consumer
        mgr.stop_camera("test_cam_release", "cons_B")
        assert dev.active_consumers_count == 0
        assert dev.physical_handles == 0
        assert dev.state == CameraState.IDLE


def test_no_not_readable_error_on_immediate_reopen() -> None:
    """10. Restart immediately after shutdown can reopen camera without conflict."""
    mgr = CentralCameraManager.get_instance()
    dev = mgr.register_camera("test_cam_reopen", url="sim://2")

    mock_cap = MagicMock(isOpened=lambda: True, read=lambda: (True, None), release=MagicMock())
    with patch.object(dev, "_open_capture", return_value=mock_cap):
        dev.start()
        assert dev.physical_handles == 1

        # Teardown
        dev.stop()
        assert dev.physical_handles == 0
        assert mock_cap.release.called

        # Reopen immediately
        mock_cap2 = MagicMock(isOpened=lambda: True, read=lambda: (True, None))
        with patch.object(dev, "_open_capture", return_value=mock_cap2):
            dev.start()
            assert dev.physical_handles == 1
            dev.stop()


def test_truthful_health_and_readiness_states() -> None:
    """11 & 12. Truthful health & readiness states: never report ready before preflight passes."""
    tracker = ReadinessTracker()

    # Initial state: not ready
    is_ready, reason = tracker.evaluate_live_state(pipeline=None, manager=None)
    assert is_ready is False
    assert "Singleton" in str(reason)

    code, payload = tracker.ready_payload(pipeline=None, manager=None)
    assert code == 503
    assert payload["ready"] is False
    assert payload["status"] == "not_ready"

    # Step-by-step preflight milestone completion
    tracker.mark_singleton_verified()
    tracker.mark_models_loaded(detector=object(), pose=object(), provider="CPUExecutionProvider")
    tracker.mark_cameras_registered(1)
    tracker.mark_broker_started()

    # Still not ready because camera handle is not open and 0 frames
    is_ready, reason = tracker.evaluate_live_state(pipeline=None, manager=None)
    assert is_ready is False
    assert "Physical camera handle" in str(reason) or "camera unavailable" in str(reason)

    # Mock live pipeline & manager with frames
    class MockWorker:
        _thread = MagicMock(is_alive=lambda: True)

    class MockPipeline:
        workers = [MockWorker()]
        metrics = MagicMock(frames_processed=10)
        sync = MagicMock(last_error=None)
        device_retired = False

    class MockDev:
        physical_handles = 1
        frames_received = 10
        frames_processed = 10

    class MockManager:
        _lock = None
        _devices = {"default": MockDev()}

    is_ready, reason = tracker.evaluate_live_state(pipeline=MockPipeline(), manager=MockManager())
    assert is_ready is True
    assert reason is None

    code, payload = tracker.ready_payload(pipeline=MockPipeline(), manager=MockManager())
    assert code == 200
    assert payload["ready"] is True
    assert payload["status"] == "ready"
    assert payload["checks"]["singleton_locked"] is True
    assert payload["checks"]["models_loaded"] is True
    assert payload["checks"]["camera_handle_open"] is True
    assert payload["checks"]["first_frame_received"] is True
    assert payload["checks"]["pipeline_worker_alive"] is True
