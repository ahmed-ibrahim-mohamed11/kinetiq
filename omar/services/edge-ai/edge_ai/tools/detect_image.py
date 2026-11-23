"""
Run the person detector (and optionally the pose model) on one image and print JSON.

    python -m edge_ai.tools.detect_image --detector models/yolox_s.onnx --layout yolox --image frame.jpg
    python -m edge_ai.tools.detect_image --detector models/yolox_s.onnx --layout yolox \
        --pose models/rtmpose-m.onnx --pose-kind rtmpose --image frame.jpg --out annotated.png --repeat 20

Output: image size, ONNX Runtime providers per model, per-stage latency (mean/p50/p95 in ms over
--repeat runs, after one untimed warm-up), detections (box xyxy in image pixels, score) and, with
--pose, per-person knee/elbow angles from edge_ai/pose/angles.py plus the 17 COCO keypoints.
Nothing leaves the machine; the image is only read, and --out writes an annotated copy.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
import time
from pathlib import Path
from typing import Any

from edge_ai.detection.base import Detection
from edge_ai.pose.base import COCO_KEYPOINTS, Pose

# COCO-17 limbs for the annotated image.
SKELETON: tuple[tuple[int, int], ...] = (
    (5, 7), (7, 9), (6, 8), (8, 10), (5, 6), (5, 11), (6, 12), (11, 12),
    (11, 13), (13, 15), (12, 14), (14, 16), (0, 1), (0, 2), (1, 3), (2, 4),
)  # fmt: skip


def latency_stats(samples_ms: list[float]) -> dict[str, float | int]:
    s = sorted(samples_ms)
    return {
        "runs": len(s),
        "mean": round(statistics.fmean(s), 3),
        "p50": round(statistics.median(s), 3),
        "p95": round(s[min(len(s) - 1, int(math.ceil(0.95 * len(s))) - 1)], 3),
    }


def _num(v: float) -> float | None:
    return None if math.isnan(v) else round(v, 2)


def pose_summary(pose: Pose | None, min_score: float) -> dict[str, Any] | None:
    from edge_ai.pose.angles import elbow_angle_mean, knee_angle_mean

    if pose is None:
        return None
    knee, knee_conf = knee_angle_mean(pose, min_score)
    elbow, elbow_conf = elbow_angle_mean(pose, min_score)
    return {
        "knee_angle_deg": _num(knee),
        "knee_confidence": round(knee_conf, 3),
        "elbow_angle_deg": _num(elbow),
        "elbow_confidence": round(elbow_conf, 3),
        "keypoints": {
            name: [round(k.x, 1), round(k.y, 1), round(k.score, 3)]
            for name, k in zip(COCO_KEYPOINTS, pose.keypoints, strict=True)
        },
    }


def annotate(image: Any, dets: list[Detection], poses: list[Pose | None], min_score: float) -> Any:
    import cv2

    out = image.copy()
    for i, d in enumerate(dets):
        x1, y1, x2, y2 = (int(round(v)) for v in d.box)
        cv2.rectangle(out, (x1, y1), (x2, y2), (0, 200, 0), 2)
        cv2.putText(out, f"#{i} {d.score:.2f}", (x1, max(12, y1 - 4)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 200, 0), 1)
        pose = poses[i] if i < len(poses) else None
        if pose is None:
            continue
        kps = pose.keypoints
        for a, b in SKELETON:
            if kps[a].score >= min_score and kps[b].score >= min_score:
                pa = (int(round(kps[a].x)), int(round(kps[a].y)))
                pb = (int(round(kps[b].x)), int(round(kps[b].y)))
                cv2.line(out, pa, pb, (255, 160, 0), 2)
        for k in kps:
            if k.score >= min_score:
                cv2.circle(out, (int(round(k.x)), int(round(k.y))), 3, (0, 0, 255), -1)
    return out


def run(args: argparse.Namespace) -> dict[str, Any]:
    import cv2

    from edge_ai.detection.onnx_detector import create_detector

    image = cv2.imread(str(args.image), cv2.IMREAD_COLOR)
    if image is None:
        raise SystemExit(f"cannot read image: {args.image}")
    providers = args.providers.split(",") if args.providers else None
    detector = create_detector(str(args.detector), layout=args.layout, providers=providers, conf_threshold=args.conf)
    pose_est = None
    if args.pose:
        from edge_ai.pose.factory import create_pose_estimator

        pose_est = create_pose_estimator(str(args.pose), kind=args.pose_kind, providers=providers)

    def once() -> tuple[list[Detection], list[Pose | None], float, float | None]:
        t0 = time.perf_counter()
        dets = detector.detect(image)
        t1 = time.perf_counter()
        poses: list[Pose | None] = []
        pose_ms = None
        if pose_est is not None:
            poses = pose_est.estimate(image, [d.box for d in dets]) if dets else []
            pose_ms = (time.perf_counter() - t1) * 1000.0
        return dets, poses, (t1 - t0) * 1000.0, pose_ms

    once()  # warm-up: session initialisation / CUDA kernels are not part of steady-state latency
    det_ms: list[float] = []
    pose_ms_all: list[float] = []
    total_ms: list[float] = []
    dets: list[Detection] = []
    poses: list[Pose | None] = []
    for _ in range(max(1, args.repeat)):
        dets, poses, d_ms, p_ms = once()
        det_ms.append(d_ms)
        total_ms.append(d_ms + (p_ms or 0.0))
        if p_ms is not None:
            pose_ms_all.append(p_ms)

    latency: dict[str, Any] = {"detect": latency_stats(det_ms)}
    if pose_est is not None:
        latency["pose"] = latency_stats(pose_ms_all)
        latency["total"] = latency_stats(total_ms)
    result: dict[str, Any] = {
        "image": {"path": str(args.image), "width": int(image.shape[1]), "height": int(image.shape[0])},
        "detector": {"model": detector.model_name, "layout": detector.layout, "input_size": list(detector.input_size)},
        "providers": {"detector": detector.providers},
        "latency_ms": latency,
        "detections": [{"box": [round(v, 1) for v in d.box], "score": round(d.score, 4)} for d in dets],
    }
    if pose_est is not None:
        result["pose"] = {"model": pose_est.model_name, "kind": type(pose_est).__name__}
        result["providers"]["pose"] = getattr(pose_est, "providers", None)
        result["persons"] = [
            {"detection": i, **(pose_summary(p, args.min_kp_score) or {"pose": None})} for i, p in enumerate(poses)
        ]
    if args.out:
        cv2.imwrite(str(args.out), annotate(image, dets, poses, args.min_kp_score))
        result["annotated"] = str(args.out)
    return result


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Detect persons (and pose) in one image with the edge ONNX adapters.")
    p.add_argument("--detector", required=True, type=Path, help="person detector ONNX file")
    p.add_argument("--layout", choices=["auto", "yolox"], default=None, help="detector output layout (default auto)")
    p.add_argument("--pose", type=Path, help="pose ONNX file (optional)")
    p.add_argument("--pose-kind", choices=["auto", "rtmpose", "yolo_pose"], default=None, help="default auto")
    p.add_argument("--image", required=True, type=Path)
    p.add_argument("--out", type=Path, help="write an annotated PNG here")
    p.add_argument("--repeat", type=int, default=1, help="timed runs (after one warm-up)")
    p.add_argument("--conf", type=float, default=0.35, help="detector confidence threshold")
    p.add_argument("--min-kp-score", type=float, default=0.3, help="keypoint score needed for an angle")
    p.add_argument("--providers", help="comma-separated ONNX Runtime providers (default CUDA→CPU)")
    args = p.parse_args(argv)
    print(json.dumps(run(args), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
