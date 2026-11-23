"""Real decode path: RtspReader and the Phase 0 probe against a locally generated video file."""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from edge_ai.camera.probe import probe_stream  # noqa: E402
from edge_ai.camera.rtsp_reader import RtspReader, StreamStatus  # noqa: E402


@pytest.fixture(scope="module")
def video_file(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("video") / "synthetic.avi"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 20.0, (320, 240))
    assert writer.isOpened(), "OpenCV VideoWriter could not open (codec missing)"
    for i in range(60):  # 3 seconds
        frame = np.zeros((240, 320, 3), dtype=np.uint8)
        cv2.rectangle(frame, (10 + i * 3, 60), (60 + i * 3, 200), (0, 255, 0), -1)
        writer.write(frame)
    writer.release()
    return path


def test_probe_reads_synthetic_video(video_file: Path):
    r = probe_stream(str(video_file), seconds=1.5)
    assert r.ok
    assert (r.width, r.height) == (320, 240)
    assert r.frames_read > 10
    assert r.mean_brightness is not None and r.mean_brightness > 0


def test_rtsp_reader_delivers_frames_and_reports_status(video_file: Path):
    statuses: list[StreamStatus] = []
    reader = RtspReader(
        url=str(video_file), name="file", on_status=lambda s, _e: statuses.append(s), read_timeout_s=1.0
    )
    reader.start()
    try:
        deadline = time.monotonic() + 5
        frame = None
        while time.monotonic() < deadline:
            frame = reader.latest()
            if frame is not None and frame.frame_index >= 5:
                break
            time.sleep(0.05)
        assert frame is not None and frame.frame_index >= 5
        assert frame.image.shape == (240, 320, 3)
        assert StreamStatus.ONLINE in statuses
        assert reader.resolution == (320, 240)
    finally:
        reader.stop()
    assert reader.status() == StreamStatus.OFFLINE
