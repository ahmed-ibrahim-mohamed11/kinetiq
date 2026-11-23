#!/usr/bin/env python3
"""Phase 4 end-to-end live-mode benchmark (rerunnable on any target hardware).

Streams the REAL dev videos frame by frame (cv2 decode = the camera), through the production
Mode B pipeline (ai.core.live.LiveStreamer: YoloTopK -> OnlineTracker -> MotionBertLite sliding
window -> fixed-lag decode with guards -> form indicators), and measures, from ONE timed run per
config — so speed and accuracy can never come from different code paths:

  speed    per-stage ms (capture, detect, track, lift, decode, form), sustainable fps,
           realtime factor (sustainable / processed fps), warm-up, look-ahead
  live     a simulated camera queue: frame j arrives at j/fps, is processed when the pipeline is
           free; per-frame latency median/p95/max, final latency (bounded queue <=> realtime),
           rep-event latency (commit time - arrival of the rep's END frame)
  accuracy committed events vs the manual GT, protocol v2 (IoU + pull rules, seconds-defined tol)
  resources peak RSS, mean process CPU utilisation, peak CUDA memory

Usage:
  python ai/research/phase4/live_benchmark.py                    # full matrix (see CONFIGS)
  python ai/research/phase4/live_benchmark.py --configs cuda_V2  # one config
  python ai/research/phase4/live_benchmark.py --long-session cuda_V2   # 12-video continuous stream
Never run alongside another heavy job (laptop timings are load- and thermal-sensitive).
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import sys
import time
from dataclasses import asdict

import numpy as np
import pandas as pd
import psutil
import torch

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, REPO_ROOT)

from ai.core.form_indicators import load_thresholds  # noqa: E402
from ai.core.lifting import MotionBertLite  # noqa: E402
from ai.core.live import LiveConfig, LiveStreamer  # noqa: E402
from ai.core.pose import YoloTopK  # noqa: E402
from ai.evaluation import harness as H  # noqa: E402
from ai.evaluation.protocol import evaluate_dontcare, prf, pull_tol_frames  # noqa: E402

OUT = os.path.join(REPO_ROOT, "ai", "outputs", "phase4")
VIDEOS = {n: os.path.join(REPO_ROOT, "ai", "data", "seated_row",
                          "correct" if n.startswith("correct") else "asymmetry", n + ".mp4")
          for n in H.SR_VIDEOS}

# name -> (LiveConfig, device, yolo_imgsz, lift_fp16)
CONFIGS = {
    "cpu_V2":               (LiveConfig.preset("V2_quality"), "cpu", 640, False),
    "cuda_V2":              (LiveConfig.preset("V2_quality"), "cuda", 640, False),
    "cuda_V2_fp16":         (LiveConfig.preset("V2_quality"), "cuda", 640, True),
    "cuda_V2_notta":        (LiveConfig(tta=False), "cuda", 640, False),
    "cuda_V1":              (LiveConfig.preset("V1_low_latency"), "cuda", 640, False),
    "cpu_V2_notta":         (LiveConfig(tta=False), "cpu", 640, False),
    "cpu_V2_notta_320":     (LiveConfig(tta=False), "cpu", 320, False),
    "cpu_V2_notta_det2":    (LiveConfig(tta=False, detect_every=2), "cpu", 640, False),
    "cpu_V3":               (LiveConfig.preset("V3_budget_15fps"), "cpu", 640, False),
}


def build(cfg_name: str, system):
    cfg, device, imgsz, fp16 = CONFIGS[cfg_name]
    if device == "cuda" and not torch.cuda.is_available():
        raise RuntimeError(f"{cfg_name} needs CUDA; none available on this machine")
    det = YoloTopK(device=device, imgsz=imgsz)
    lifter = MotionBertLite(device=device, half=fp16)
    # warm-up (model load, CUDA context, cudnn autotune) — excluded from all timings
    import cv2
    cap = cv2.VideoCapture(VIDEOS["correct_01"])
    ok, fr = cap.read()
    cap.release()
    for _ in range(5):
        det(fr)
    lifter.lift_window(np.random.default_rng(0).uniform(0, 1000, (cfg.window, 17, 3)).astype(np.float32),
                       tta=cfg.tta)
    return cfg, det, lifter


def stream_one(cfg, det, lifter, system, thr, path, name):
    import cv2
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        raise FileNotFoundError(f"cannot open video {path}")
    fps = cap.get(cv2.CAP_PROP_FPS)
    wh = (cap.get(cv2.CAP_PROP_FRAME_WIDTH), cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    st = LiveStreamer(cfg, det, lifter, system, wh, fps, thresholds=thr, name=name)
    capture_ms, commit_proc = {}, {}
    pending_capture = 0.0
    while True:
        t0 = time.perf_counter()
        ok, fr = cap.read()
        dt = (time.perf_counter() - t0) * 1000.0
        if not ok:
            break
        pending_capture += dt
        before = st.n_proc
        evs = st.push_frame(fr)
        if st.n_proc > before:                 # this camera frame was processed
            capture_ms[st.n_proc - 1] = pending_capture
            pending_capture = 0.0
        for e in evs:
            commit_proc[id(e)] = st.n_proc - 1
    for e in st.finish():
        commit_proc[id(e)] = st.n_proc - 1
    cap.release()
    return st, fps, capture_ms, commit_proc


def frame_costs(st, capture_ms):
    cost = np.zeros(st.n_proc)
    for j, ms in capture_ms.items():
        cost[j] += ms
    for stage, rows in st.stage_ms.items():
        for j, ms in rows:
            cost[j] += ms
    return cost


def simulate_queue(cost_ms, proc_fps):
    """Camera frame j arrives at j/fps; the single pipeline worker processes frames in order."""
    arrival = np.arange(len(cost_ms)) / proc_fps
    finish = np.zeros(len(cost_ms))
    free = 0.0
    for j, c in enumerate(cost_ms / 1000.0):
        start = max(arrival[j], free)
        free = finish[j] = start + c
    return arrival, finish


def run_config(cfg_name, system, thr):
    cfg, det, lifter = build(cfg_name, system)
    items = H.load_internal_sr()
    ac_start = power_ok()
    proc = psutil.Process()
    if torch.cuda.is_available():
        torch.cuda.reset_peak_memory_stats()
    cpu0, wall0 = proc.cpu_times(), time.perf_counter()
    rss_peak = proc.memory_info().rss
    rows, stage_tot, all_cost, lat_frames, ev_lat = [], {}, [], [], []
    rule_rows = []
    for name, path in VIDEOS.items():
        st, fps, capture_ms, commit_proc = stream_one(cfg, det, lifter, system, thr, path, name)
        rss_peak = max(rss_peak, proc.memory_info().rss)
        cost = frame_costs(st, capture_ms)
        pfps = fps / cfg.proc_fps_div
        arrival, finish = simulate_queue(cost, pfps)
        lat = finish - arrival
        all_cost.append(cost)
        lat_frames.append(lat)
        for e in st.events:
            jc = commit_proc[id(e)]
            j_end = min(e.end // cfg.proc_fps_div, st.n_proc - 1)
            ev_lat.append(finish[jc] - arrival[j_end])
        for stage, r in st.stage_ms.items():
            stage_tot.setdefault(stage, []).extend(ms for _, ms in r)
        stage_tot.setdefault("capture", []).extend(capture_ms.values())
        gtc = items[name]["gtc"]
        pred = [dict(start=e.start, pull=e.pull, end=e.end) for e in st.events]
        tol = pull_tol_frames(fps)
        for rule in ("iou", "pull"):
            m = evaluate_dontcare(pred, gtc, [], rule, "v2", tol)
            rule_rows.append(dict(config=cfg_name, video=name, rule=rule, tp=m["tp"], fp=m["fp"],
                                  fn=m["fn"], n_gt=m["n_gt"], n_pred=m["n_pred"]))
        rows.append(dict(config=cfg_name, video=name, frames_proc=st.n_proc, n_events=len(st.events),
                         sust_fps=round(st.n_proc / (cost.sum() / 1000.0), 2),
                         final_latency_s=round(float(lat[-1]), 3),
                         chunk_status=";".join(sorted(set(st.chunk_status)))))
    wall = time.perf_counter() - wall0
    cpu1 = proc.cpu_times()
    cost = np.concatenate(all_cost)
    lat = np.concatenate(lat_frames)
    pfps = 30.0 / cfg.proc_fps_div
    rr = pd.DataFrame(rule_rows)
    summ = dict(config=cfg_name, timing_validity=validity(cost, ac_start, power_ok()),
                device=CONFIGS[cfg_name][1], yolo_imgsz=CONFIGS[cfg_name][2],
                lift_fp16=CONFIGS[cfg_name][3], **{f"cfg_{k}": v for k, v in asdict(cfg).items()},
                proc_fps=pfps, frames_proc=int(len(cost)),
                ms_per_frame_mean=round(float(cost.mean()), 2),
                ms_per_frame_p95=round(float(np.percentile(cost, 95)), 2),
                ms_per_frame_max=round(float(cost.max()), 1),
                sustainable_fps=round(1000.0 / float(cost.mean()), 2),
                realtime_factor=round((1000.0 / float(cost.mean())) / pfps, 3),
                latency_median_s=round(float(np.median(lat)), 3),
                latency_p95_s=round(float(np.percentile(lat, 95)), 3),
                latency_max_s=round(float(lat.max()), 3),
                event_latency_median_s=round(float(np.median(ev_lat)), 2) if ev_lat else None,
                event_latency_max_s=round(float(np.max(ev_lat)), 2) if ev_lat else None,
                lookahead_s=round(cfg.commit_lag_f / pfps, 2),
                warmup_s=round(cfg.window / pfps, 1),
                rss_peak_mb=round(rss_peak / 2**20, 0),
                cpu_util_pct=round(100.0 * ((cpu1.user - cpu0.user) + (cpu1.system - cpu0.system))
                                   / wall / psutil.cpu_count(), 1),
                cuda_peak_mb=round(torch.cuda.max_memory_allocated() / 2**20, 0)
                if torch.cuda.is_available() else 0)
        # per-stage means (ms per occurrence) and amortised per processed frame
    for stage, v in stage_tot.items():
        summ[f"{stage}_ms_mean"] = round(float(np.mean(v)), 2) if v else 0.0
        summ[f"{stage}_ms_per_frame"] = round(float(np.sum(v)) / len(cost), 2) if v else 0.0
    for rule in ("iou", "pull"):
        g = rr[rr.rule == rule]
        P, R, F = prf(g.tp.sum(), g.fp.sum(), g.fn.sum())
        summ[f"{rule}_f1"] = round(F, 4)
        summ[f"{rule}_tp_fp_fn"] = f"{g.tp.sum()}/{g.fp.sum()}/{g.fn.sum()}"
    return summ, pd.DataFrame(rows), rr


def run_long_session(cfg_name, system, thr, repeats=2):
    """All dev videos back-to-back, `repeats` times, as ONE continuous stream (timing only):
    steady-state throughput, queue boundedness over minutes, decode-cost growth with buffer."""
    import cv2

    class Concat:
        def __init__(self, paths):
            self.paths, self.i, self.cap = list(paths), -1, None
        def read(self):
            while True:
                if self.cap is not None:
                    ok, fr = self.cap.read()
                    if ok:
                        return True, fr
                    self.cap.release()
                self.i += 1
                if self.i >= len(self.paths):
                    return False, None
                self.cap = cv2.VideoCapture(self.paths[self.i])

    cfg, det, lifter = build(cfg_name, system)
    ac_start = power_ok()
    paths = [p for _ in range(repeats) for p in VIDEOS.values()]
    src = Concat(paths)
    st = LiveStreamer(cfg, det, lifter, system, (1080.0, 1920.0), 30.0, thresholds=thr, name="long")
    capture_ms, pending = {}, 0.0
    proc = psutil.Process()
    rss = []
    while True:
        t0 = time.perf_counter()
        ok, fr = src.read()
        dt = (time.perf_counter() - t0) * 1000.0
        if not ok:
            break
        pending += dt
        before = st.n_proc
        st.push_frame(fr)
        if st.n_proc > before:
            capture_ms[st.n_proc - 1] = pending
            pending = 0.0
            if st.n_proc % 500 == 0:
                rss.append(proc.memory_info().rss / 2**20)
    st.finish()
    cost = frame_costs(st, capture_ms)
    pfps = 30.0 / cfg.proc_fps_div
    arrival, finish = simulate_queue(cost, pfps)
    lat = finish - arrival
    dec = [ms for _, ms in st.stage_ms["decode"]]
    n = len(cost)
    q = n // 4
    return dict(config=cfg_name, timing_validity=validity(cost, ac_start, power_ok()),
                frames_proc=n, stream_s=round(n / pfps, 1),
                realtime_factor=round((1000.0 / float(cost.mean())) / pfps, 3),
                realtime_factor_first_quarter=round((1000.0 / float(cost[:q].mean())) / pfps, 3),
                realtime_factor_last_quarter=round((1000.0 / float(cost[-q:].mean())) / pfps, 3),
                latency_median_s=round(float(np.median(lat)), 3),
                latency_p95_s=round(float(np.percentile(lat, 95)), 3),
                latency_max_s=round(float(lat.max()), 3),
                latency_final_s=round(float(lat[-1]), 3),
                decode_ms_first=round(dec[0], 1) if dec else None,
                decode_ms_last=round(dec[-1], 1) if dec else None,
                decode_ms_growth_x=round(dec[-1] / dec[0], 2) if len(dec) > 1 and dec[0] > 0 else None,
                rss_start_mb=round(rss[0], 0) if rss else None,
                rss_end_mb=round(rss[-1], 0) if rss else None,
                n_events=len(st.events))


def environment():
    b = psutil.sensors_battery()
    env = dict(platform=platform.platform(), processor=platform.processor(),
               cpu_logical=psutil.cpu_count(), ram_gb=round(psutil.virtual_memory().total / 2**30, 1),
               torch=torch.__version__, cuda=torch.version.cuda,
               gpu=torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
               on_ac_power=None if b is None else bool(b.power_plugged))
    return env


# --------------------------------------------------------------------------- measurement hygiene
# Learned the hard way (2026-10-09 run, discarded): the laptop slept mid-run and switched to
# battery, producing an 18,765 s "latency" and throttled timings. A timing run is now INVALID
# unless it stays on AC power, never suspends, and has no single frame cost above SUSPEND_GAP_MS.
SUSPEND_GAP_MS = 10_000.0


def keep_awake(on: bool):
    """Process-scoped Windows sleep inhibition (ES_CONTINUOUS|ES_SYSTEM_REQUIRED); released
    automatically when the process exits — no persistent change to the user's power settings."""
    if os.name != "nt":
        return
    import ctypes
    ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
    ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | (ES_SYSTEM_REQUIRED if on else 0))


def power_ok() -> bool:
    b = psutil.sensors_battery()
    return b is None or bool(b.power_plugged)     # desktops report no battery


def validity(cost_ms: np.ndarray, ac_start: bool, ac_end: bool) -> str:
    if not (ac_start and ac_end):
        return "INVALID_BATTERY"
    if cost_ms.size and float(cost_ms.max()) > SUSPEND_GAP_MS:
        return "INVALID_SUSPEND_GAP"
    return "VALID"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--configs", nargs="*", default=list(CONFIGS))
    ap.add_argument("--long-session", nargs="*", default=[])
    ap.add_argument("--repeats", type=int, default=2)
    ap.add_argument("--allow-battery", action="store_true",
                    help="run anyway on battery (timings will be marked INVALID_BATTERY)")
    args = ap.parse_args()
    if not power_ok() and not args.allow_battery:
        print("REFUSING: on battery power - laptop CPU/GPU are throttled and may sleep, so timings "
              "would be invalid. Plug in, or pass --allow-battery (results marked INVALID_BATTERY).")
        sys.exit(2)
    keep_awake(True)
    os.makedirs(OUT, exist_ok=True)
    system = H.fit_system()
    thr = load_thresholds("seated_row")
    summaries, per_video, per_rule = [], [], []
    for name in args.configs:
        print(f"=== {name} ===", flush=True)
        s, pv, pr = run_config(name, system, thr)
        summaries.append(s)
        per_video.append(pv)
        per_rule.append(pr)
        print(f"  [{s['timing_validity']}] {s['ms_per_frame_mean']} ms/frame, sustainable {s['sustainable_fps']} fps, "
              f"realtime x{s['realtime_factor']}, latency med/p95/max {s['latency_median_s']}/"
              f"{s['latency_p95_s']}/{s['latency_max_s']} s, IoU F1 {s['iou_f1']} ({s['iou_tp_fp_fn']}), "
              f"pull F1 {s['pull_f1']}", flush=True)
    if summaries:
        tag = "_".join(args.configs) if len(args.configs) < len(CONFIGS) else "matrix"
        pd.DataFrame(summaries).to_csv(os.path.join(OUT, f"live_benchmark_{tag}.csv"), index=False)
        pd.concat(per_video).to_csv(os.path.join(OUT, f"live_benchmark_{tag}_videos.csv"), index=False)
        pd.concat(per_rule).to_csv(os.path.join(OUT, f"live_benchmark_{tag}_accuracy.csv"), index=False)
    longs = []
    for name in args.long_session:
        print(f"=== long session: {name} ===", flush=True)
        r = run_long_session(name, system, thr, args.repeats)
        longs.append(r)
        print("  " + json.dumps(r), flush=True)
    if longs:
        pd.DataFrame(longs).to_csv(os.path.join(OUT, "live_benchmark_long_session.csv"), index=False)
    json.dump(environment(), open(os.path.join(OUT, "environment.json"), "w"), indent=1)


if __name__ == "__main__":
    main()
