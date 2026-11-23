"""
Record a labelled rep-counting clip from a local webcam (Phase 6 data collection, development).

Labels come from a metronome: the tool beeps once per rep at a fixed cadence and the person
finishes each rep ON the beep (back in the profile's start position: arms straight for a curl,
standing tall for a squat). Each beep time is written as `label_rep_end=1` on the nearest sample,
so the CSV is exactly what `edge_ai.tools.evaluate_reps` expects:

    t,value,confidence,label_rep_end

The signal is produced by the same code as the live pipeline: person detector → largest person
→ pose estimator → `extract_signal(profile.signal)`. Nothing leaves the machine; video is only
written when --save-video is given (your own footage, stored under data/, which git ignores).

Usage (from services/edge-ai, with the `run` pipeline stopped so the webcam is free):

    python -m edge_ai.tools.record_reps --exercise biceps-curl --reps 10 --cadence 3 \
        --out data/eval/biceps-curl_01.csv

Then:  python -m edge_ai.tools.evaluate_reps --profile biceps-curl --csv data/eval/biceps-curl_01.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sys
import threading
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from edge_ai.pose.angles import extract_signal
from edge_ai.reps.profiles import RepProfile
from edge_ai.reps.state_machine import RepCounter
from edge_ai.tools.default_profiles import DEFAULT_PROFILES
from edge_ai.tools.evaluate_reps import evaluate

DEFAULT_DETECTOR = "models/yolox_nano.onnx"
DEFAULT_POSE = "models/rtmpose-m_simcc-body7_256x192.onnx"


def _beep(freq: int, ms: int) -> None:
    try:
        import winsound

        winsound.Beep(freq, ms)
    except Exception:  # noqa: BLE001 — non-Windows / no audio device: terminal bell instead
        sys.stdout.write("\a")
        sys.stdout.flush()


def _metronome(t0: float, lead_in: float, cadence: float, reps: int, stop: threading.Event) -> list[float]:
    """Schedule countdown beeps (low) during the lead-in and one rep beep (high) per cadence."""
    schedule: list[tuple[float, int, int]] = []
    for k in range(int(lead_in), 0, -1):  # 3-2-1 style countdown, one per second
        schedule.append((lead_in - k, 440, 120))
    rep_times = [lead_in + cadence * (i + 1) for i in range(reps)]
    schedule += [(rt, 1000, 150) for rt in rep_times]

    def run() -> None:
        for at, freq, ms in schedule:
            delay = t0 + at - time.monotonic()
            if delay > 0 and stop.wait(delay):
                return
            threading.Thread(target=_beep, args=(freq, ms), daemon=True).start()

    threading.Thread(target=run, name="metronome", daemon=True).start()
    return rep_times


def _open_camera(index: int) -> Any:
    import cv2

    backend = cv2.CAP_DSHOW if os.name == "nt" else cv2.CAP_ANY
    cap = cv2.VideoCapture(index, backend)
    if not cap.isOpened():
        raise SystemExit(
            f"could not open webcam {index}: is `python -m edge_ai run` (or another app) still using it?"
        )
    return cap


def _label_samples(times: list[float], rep_times: list[float]) -> list[int]:
    labels = [0] * len(times)
    for rt in rep_times:
        if not times or rt > times[-1] + 0.5:
            continue  # beep after the recording ended: not observed, not labelled
        i = min(range(len(times)), key=lambda j: abs(times[j] - rt))
        labels[i] = 1
    return labels


def record(args: argparse.Namespace) -> int:
    import cv2

    from edge_ai.detection.onnx_detector import create_detector
    from edge_ai.pose.factory import create_pose_estimator

    profile = RepProfile.from_catalog(DEFAULT_PROFILES[args.exercise])
    if not profile.count_reps or profile.occupancy_only:
        raise SystemExit(f"{args.exercise} does not count reps")
    detector = create_detector(args.detector, layout="yolox" if "yolox" in Path(args.detector).name else "auto")
    pose = create_pose_estimator(args.pose)

    cap = _open_camera(args.camera)
    writer = None
    duration = args.lead_in + args.cadence * args.reps + args.tail

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    counter = RepCounter(profile=profile)
    samples: list[tuple[float, float, float]] = []
    joint = "elbows" if "elbow" in profile.signal else "knees" if "knee" in profile.signal else "hips"
    start_band = f"{joint} {'straight' if profile.start_state == 'top' else 'fully bent'} on the beep"
    print(
        f"[record] {args.exercise}: signal={profile.signal} start_state={profile.start_state} "
        f"top>={profile.top_threshold} bottom<={profile.bottom_threshold}",
        flush=True,
    )
    print(
        f"[record] {args.lead_in:.0f}s countdown (low beeps), then {args.reps} reps, one every "
        f"{args.cadence:.1f}s: finish each rep ON the high beep ({start_band}).",
        flush=True,
    )

    stop = threading.Event()
    t0 = time.monotonic()
    rep_times = _metronome(t0, args.lead_in, args.cadence, args.reps, stop)
    last_print = 0.0
    try:
        while True:
            ok, frame = cap.read()
            t = time.monotonic() - t0
            if t > duration:
                break
            if not ok or frame is None:
                time.sleep(0.01)
                continue
            if args.save_video and writer is None:
                fh_, fw_ = frame.shape[:2]
                writer = cv2.VideoWriter(
                    str(out.with_suffix(".mp4")), cv2.VideoWriter.fourcc(*"mp4v"), args.video_fps, (fw_, fh_)
                )
            if writer is not None:
                writer.write(frame)
            dets = detector.detect(frame)
            value, conf = math.nan, 0.0
            if dets:
                person = max(dets, key=lambda d: d.area)
                poses = pose.estimate(frame, [person.box])
                if poses and poses[0] is not None:
                    value, conf = extract_signal(profile.signal, poses[0])
            samples.append((t, value, conf))
            if counter.update(value, conf, t):
                print(f"[record] t={t:5.1f}s  REP {counter.count} counted", flush=True)
            if t - last_print >= 1.0:
                last_print = t
                shown = "  --  " if math.isnan(value) else f"{value:6.1f}"
                print(
                    f"[record] t={t:5.1f}s  {profile.signal}={shown}  conf={conf:.2f}  people={len(dets)}",
                    flush=True,
                )
    finally:
        stop.set()
        cap.release()
        if writer is not None:
            writer.release()

    times = [s[0] for s in samples]
    labels = _label_samples(times, rep_times)
    with out.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["t", "value", "confidence", "label_rep_end"])
        for (t, v, c), lab in zip(samples, labels, strict=True):
            w.writerow([f"{t:.3f}", "nan" if math.isnan(v) else f"{v:.2f}", f"{c:.3f}", lab])

    valid = [v for _, v, c in samples if not math.isnan(v) and c >= profile.min_signal_confidence]
    span = times[-1] - times[0] if len(times) > 1 else 0.0
    summary = {
        "csv": str(out),
        "samples": len(samples),
        "sample_rate_hz": round((len(samples) - 1) / span, 2) if span else 0.0,
        "visible_fraction": round(len(valid) / len(samples), 3) if samples else 0.0,
        "signal_min": round(min(valid), 1) if valid else None,
        "signal_max": round(max(valid), 1) if valid else None,
        "live_count": counter.count,
        "evaluation": asdict(evaluate(list(zip(times, [s[1] for s in samples], [s[2] for s in samples], labels,
                                               strict=True)), profile, args.tolerance)),
    }
    print(json.dumps(summary, indent=2), flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--exercise", required=True, choices=sorted(DEFAULT_PROFILES))
    p.add_argument("--out", required=True, help="CSV path, e.g. data/eval/biceps-curl_01.csv")
    p.add_argument("--reps", type=int, default=10)
    p.add_argument("--cadence", type=float, default=3.0, help="seconds per rep (metronome period)")
    p.add_argument("--lead-in", type=float, default=5.0, help="countdown seconds before the first rep")
    p.add_argument("--tail", type=float, default=2.0, help="seconds recorded after the last beep")
    p.add_argument("--camera", type=int, default=0, help="webcam device index")
    p.add_argument("--detector", default=DEFAULT_DETECTOR)
    p.add_argument("--pose", default=DEFAULT_POSE)
    p.add_argument("--tolerance", type=float, default=0.75, help="± seconds between beep and counted rep")
    p.add_argument("--save-video", action="store_true", help="also write <out>.mp4 (stays local)")
    p.add_argument("--video-fps", type=float, default=10.0)
    return record(p.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
