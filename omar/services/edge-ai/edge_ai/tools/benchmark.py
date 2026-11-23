"""
Throughput / latency benchmark for the per-frame pipeline (Phase 2 acceptance tool).

    python -m edge_ai.tools.benchmark --detector models/detector.onnx --streams 4 --seconds 30 [--width 1920 --height 1080]
    python -m edge_ai.tools.benchmark --streams 4 --seconds 10          # no model: measures framework overhead only
    python -m edge_ai.tools.benchmark --detector models/yolox_s.onnx --layout yolox --streams 4

Per-stage latency of detector + top-down pose (RTMPose) on a real image: edge_ai.tools.detect_image.

Simulates N concurrent camera workers on synthetic frames (or --video FILE decoded in a loop,
which exercises the real decode path) and reports per-stream inference fps, p50/p95 frame
latency and GPU stats. Results are JSON so they can be stored in model_versions.benchmark.
"""

from __future__ import annotations

import argparse
import json
import statistics
import threading
import time
from dataclasses import asdict, dataclass
from uuid import UUID, uuid4

from edge_ai.camera.rtsp_reader import SyntheticFrameSource
from edge_ai.config import CameraConfig, Settings
from edge_ai.pipeline.orchestrator import Pipeline, _gpu_stats
from edge_ai.reps.profiles import RepProfile
from edge_ai.tools.default_profiles import DEFAULT_PROFILES


@dataclass
class BenchResult:
    streams: int
    seconds: float
    frames_processed: int
    frames_per_second_total: float
    frames_per_second_per_stream: float
    latency_ms_p50: float | None
    latency_ms_p95: float | None
    detector: str | None
    detector_layout: str | None
    gpu_utilization: float | None
    gpu_memory_used_mb: float | None


def run(
    detector_path: str | None,
    streams: int,
    seconds: float,
    width: int,
    height: int,
    inference_fps: float,
    layout: str | None = None,
) -> BenchResult:
    settings = Settings(  # type: ignore[call-arg]
        device_id=UUID("d0000000-0000-4000-8000-000000000001"),
        branch_id=UUID("b0000000-0000-4000-8000-000000000001"),
        device_secret="benchmark-not-a-real-secret",
        ingest_url="http://127.0.0.1:1/unused",
        data_dir=__import__("pathlib").Path(__import__("tempfile").mkdtemp()),
        inference_fps=inference_fps,
        _env_file=None,
    )
    detector = None
    if detector_path:
        from edge_ai.detection.onnx_detector import create_detector

        detector = create_detector(detector_path, layout=layout)
    cams = [
        CameraConfig(id=uuid4(), name=f"bench-{i}", url=f"rtsp://192.0.2.{i + 1}/bench", inference_fps=inference_fps)
        for i in range(streams)
    ]
    profiles = {k: RepProfile.from_catalog(v) for k, v in DEFAULT_PROFILES.items()}
    pipeline = Pipeline(settings, cams, {}, profiles, detector, None)
    for cam in cams:
        pipeline.add_worker(cam, SyntheticFrameSource(width, height, fps=25))
    # Only start workers (no sync/heartbeat threads) so the benchmark measures the frame path.
    for w in pipeline.workers:
        w.start()
    time.sleep(seconds)
    for w in pipeline.workers:
        w.stop()
    lat = sorted(pipeline.metrics.latencies_ms)
    gpu_util, gpu_mem = _gpu_stats()
    frames = pipeline.metrics.frames_processed
    return BenchResult(
        streams=streams,
        seconds=seconds,
        frames_processed=frames,
        frames_per_second_total=round(frames / seconds, 2),
        frames_per_second_per_stream=round(frames / seconds / streams, 2),
        latency_ms_p50=round(statistics.median(lat), 2) if lat else None,
        latency_ms_p95=round(lat[int(0.95 * (len(lat) - 1))], 2) if lat else None,
        detector=detector.model_name if detector else None,
        detector_layout=detector.layout if detector else None,
        gpu_utilization=gpu_util,
        gpu_memory_used_mb=gpu_mem,
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--detector")
    p.add_argument("--layout", choices=["auto", "yolox"], default=None, help="detector output layout (default auto)")
    p.add_argument("--streams", type=int, default=4)
    p.add_argument("--seconds", type=float, default=20.0)
    p.add_argument("--width", type=int, default=1920)
    p.add_argument("--height", type=int, default=1080)
    p.add_argument("--inference-fps", type=float, default=10.0)
    args = p.parse_args(argv)
    threading.current_thread().name = "benchmark"
    print(
        json.dumps(
            asdict(
                run(
                    args.detector,
                    args.streams,
                    args.seconds,
                    args.width,
                    args.height,
                    args.inference_fps,
                    layout=args.layout,
                )
            ),
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
