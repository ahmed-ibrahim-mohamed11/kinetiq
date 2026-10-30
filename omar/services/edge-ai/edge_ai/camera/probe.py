"""
Phase 0 camera compatibility probe.

    edge-ai probe rtsp://192.168.10.21:554/Streaming/Channels/101 --seconds 20
    edge-ai probe --cameras config/cameras.yaml --seconds 20

For each stream it reports: open latency, resolution, codec FOURCC, measured
decode FPS, frame drop count and the first-frame mean brightness (a quick
"is the lens covered / night mode" sanity check). Output is JSON so the results
can be committed to docs/phase0/ as evidence. No frames are stored.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from edge_ai.config import build_authenticated_url, load_camera_credentials, load_cameras, redact_url


@dataclass
class ProbeResult:
    url: str
    ok: bool
    open_latency_ms: float | None
    width: int | None
    height: int | None
    fourcc: str | None
    nominal_fps: float | None
    measured_fps: float | None
    frames_read: int
    read_failures: int
    mean_brightness: float | None
    error: str | None


def probe_stream(url: str, seconds: float = 15.0) -> ProbeResult:
    try:
        import cv2
        import numpy as np
    except ImportError as exc:  # pragma: no cover
        return ProbeResult(
            redact_url(url), False, None, None, None, None, None, None, 0, 0, None, f"opencv missing: {exc}"
        )

    t0 = time.monotonic()
    cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
    if not cap.isOpened():
        return ProbeResult(redact_url(url), False, None, None, None, None, None, None, 0, 0, None, "failed to open")
    open_ms = (time.monotonic() - t0) * 1000
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or None
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or None
    nominal = cap.get(cv2.CAP_PROP_FPS) or None
    fourcc_int = int(cap.get(cv2.CAP_PROP_FOURCC))
    fourcc = "".join(chr((fourcc_int >> (8 * i)) & 0xFF) for i in range(4)).strip() or None

    frames = failures = 0
    brightness: float | None = None
    start = time.monotonic()
    while time.monotonic() - start < seconds:
        ok, img = cap.read()
        if not ok or img is None:
            failures += 1
            time.sleep(0.01)
            continue
        frames += 1
        if brightness is None:
            brightness = float(np.asarray(img, dtype=np.float32).mean())
    cap.release()
    elapsed = time.monotonic() - start
    return ProbeResult(
        url=redact_url(url),
        ok=frames > 0,
        open_latency_ms=round(open_ms, 1),
        width=w,
        height=h,
        fourcc=fourcc,
        nominal_fps=nominal,
        measured_fps=round(frames / elapsed, 2) if elapsed > 0 else None,
        frames_read=frames,
        read_failures=failures,
        mean_brightness=round(brightness, 1) if brightness is not None else None,
        error=None if frames > 0 else "no frames decoded",
    )


def probe_from_config(cameras_file: Path, secrets_file: Path | None, seconds: float) -> list[ProbeResult]:
    cams = load_cameras(cameras_file)
    creds = load_camera_credentials(secrets_file) if secrets_file else {}
    results = []
    for cam in cams.cameras:
        url = build_authenticated_url(cam.url, creds.get(cam.id))
        results.append(probe_stream(url, seconds))
    return results


def results_to_json(results: list[ProbeResult]) -> str:
    return json.dumps([asdict(r) for r in results], indent=2)
