#!/usr/bin/env python3
"""
REALTIME/CAUSAL FEASIBILITY PROTOTYPE (separate configuration; the validated offline system
is untouched and remains the reference).

Causal audit of the frozen offline pipeline (future-frame requirements):
  - YOLO11n-pose: per-frame, causal (cost dominates - measured separately on real frames).
  - tracker: causal online rules, but init = largest box over first 10 detected frames
    (warm-up); this prototype re-runs the frozen track_subject on the growing buffer each
    chunk (deterministic reuse; cost growth measured -> an incremental port is production work).
  - MB-Lite lifting: 243-frame bidirectional windows (up to 242 future frames offline) +
    crop_scale normalisation over the WHOLE video. Causal variant: sliding window, stride S,
    crop box from the window only, finalize one S-block per window at a chosen position
    (tail = low lag / less right-context, middle = more context / higher lag).
  - features: sosfiltfilt (zero-phase, whole sequence) + 10 s CENTERED median detrend
    (+/-5 s look-ahead!). Causal variant: recompute on the buffer each chunk, COMMIT only
    frames older than the lag D (bounded-lag approximation; right side truncated).
  - decoder: whole-video tempo estimate + full-sequence duration-HSMM Viterbi. Causal
    variant: fixed-lag decode on the buffer every chunk; a rep is committed (emitted, never
    revised) once its END is older than D.
  - form indicators: per completed rep, after commit (latency measured).

Streaming simulation on the 6 internal dev videos (Domain A; GT = manual_rep_annotations).
Variants (look-ahead = lift finalize lag + decode commit lag, reported per variant):
  V1 low-latency : 30 fps, stride 81, tail-finalize,   D = 81 f (2.7 s), flip-TTA ON
  V2 quality     : 30 fps, stride 81, middle-finalize, D = 162 f (5.4 s), flip-TTA ON
  V3 budget      : 15 fps, stride 81, tail-finalize,   D = 81 f (5.4 s @15), flip-TTA OFF
Outputs: ai/outputs/realtime/{accuracy.csv, timing.csv, latency.csv, summary.json}
"""
from __future__ import annotations

import json
import os
import sys
import time

import numpy as np
import pandas as pd
import psutil
import torch

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.join(REPO_ROOT, "ai", "research", "pose_benchmark"))
sys.path.insert(0, os.path.join(REPO_ROOT, "ai", "research", "phase27"))

from ai.core.config import Cfg  # noqa: E402
from ai.core.decoder import predict_video  # noqa: E402
from ai.core.features import prepare_video  # noqa: E402
from ai.core.form_indicators import evaluate_rep, rep_features, thresholds_path  # noqa: E402
from lifters import LIFTERS, coco2h36m, flip_h36m, interp_nan_2d  # noqa: E402
from tracking import track_subject  # noqa: E402
import train_eval as te  # noqa: E402

OUT = os.path.join(REPO_ROOT, "ai", "outputs", "realtime")
P28 = os.path.join(REPO_ROOT, "ai", "outputs", "phase28")
VIDEOS = ["correct_01", "correct_02", "correct_03",
          "asymmetry_01", "asymmetry_02", "asymmetry_03"]
WINDOW, STRIDE = 243, 81
VARIANTS = {
    "V1_low_latency": dict(proc_fps=30.0, finalize="tail", commit_lag_f=81, tta=True),
    "V2_quality": dict(proc_fps=30.0, finalize="middle", commit_lag_f=162, tta=True),
    "V3_budget_15fps": dict(proc_fps=15.0, finalize="tail", commit_lag_f=81, tta=False),
}


def frozen_system():
    c = json.load(open(os.path.join(REPO_ROOT, "ai", "outputs", "phase27",
                                    "motionbert_lite", "config_final.json")))["cfg"]
    cfg = Cfg(smooth_hz=float(c["smooth_hz"]), detrend_s=float(c["detrend_s"]),
              dur_weight=float(c["dur_weight"]), dur_sigma_floor=float(c["dur_sigma_floor"]))
    vids = te.load_videos("motionbert_lite")
    train = [v for v in vids.values()
             if v["split"] == "train" and v["exercise"] == "back" and v["gt"]]
    cache: dict = {}
    model = te.fit_model_masked(train, cfg, cache)
    dec = te.fit_decoder_masked(train, te.crossfit_fused_masked(train, cfg, cache), cfg)
    return cfg, model, dec


class CausalLifter:
    """Sliding-window MB-Lite with per-window crop box and one finalized stride-block."""

    def __init__(self, tta: bool):
        self.lifter = LIFTERS["motionbert_lite"]()
        self.tta = tta

    def lift_window(self, coco_win, t_ms):
        from ai.core.lifting import crop_scale
        t0 = time.perf_counter()
        h36m = coco2h36m(interp_nan_2d(coco_win))
        norm = crop_scale(h36m, scale_range=[1, 1]).astype(np.float32)
        if self.lifter.no_conf:
            norm = norm[..., :2]
        clip = torch.from_numpy(norm).float().unsqueeze(0)
        with torch.no_grad():
            pred = self.lifter.model(clip)
            if self.tta:
                pred = (pred + flip_h36m(self.lifter.model(flip_h36m(clip)))) / 2.0
        p = pred.squeeze(0).numpy().astype(np.float32)
        t_ms.append((time.perf_counter() - t0) * 1000)
        return p - p[:, :1], h36m[:, :, 2]


def stream_video(name, var, cfg, model, dec, thr_sr):
    """Simulate streaming; returns committed reps, timing, latencies."""
    z = np.load(os.path.join(P28, f"2d_{name}.npz"))
    kt, bt, wh = z["kt"], z["bt"], tuple(z["wh"])
    step = int(round(30.0 / var["proc_fps"]))
    kt, bt = kt[::step], bt[::step]
    fps = var["proc_fps"]
    T = len(kt)
    lifter = CausalLifter(var["tta"])
    pose = np.full((T, 17, 3), np.nan, dtype=np.float32)
    conf = np.full((T, 17), np.nan, dtype=np.float32)
    finalized = 0
    committed, commit_t = [], []
    tm = dict(track=[], lift=[], feat_decode=[], form=[])
    D = var["commit_lag_f"]
    ts = [min(t, T - 1) for t in range(WINDOW - 1, T + STRIDE, STRIDE)]
    if not ts or ts[-1] != T - 1:                   # videos shorter than one window
        ts.append(T - 1)
    for t in sorted(set(ts)):
        t0 = time.perf_counter()
        coco, _ = track_subject(kt[:t + 1], bt[:t + 1], wh)      # frozen logic on buffer
        tm["track"].append((time.perf_counter() - t0) * 1000)
        lo = max(0, t - WINDOW + 1)                              # short videos: one window
        p3d, c2d = lifter.lift_window(coco[lo:t + 1], tm["lift"])
        last = t >= T - 1
        if finalized == 0:                                       # first window: backfill
            fin_lo = 0
            fin_hi = t if (var["finalize"] == "tail" or last) \
                else min(lo + 2 * STRIDE - 1, t)
        elif var["finalize"] == "tail" or last:                  # stream end: flush all
            fin_lo, fin_hi = finalized, t
        else:                                                    # middle block
            fin_lo = max(finalized, lo + STRIDE)
            fin_hi = min(lo + 2 * STRIDE - 1, t)
        if fin_hi >= fin_lo:
            pose[fin_lo:fin_hi + 1] = p3d[fin_lo - lo:fin_hi - lo + 1]
            conf[fin_lo:fin_hi + 1] = c2d[fin_lo - lo:fin_hi - lo + 1]
            finalized = fin_hi + 1
        commit_to = finalized if last else min(finalized, t + 1 - D)   # end-of-stream flush
        t0 = time.perf_counter()
        v = prepare_video(name, pose[:finalized], fps, conf=conf[:finalized])
        res = predict_video(v, model, dec, cfg, None)
        tm["feat_decode"].append((time.perf_counter() - t0) * 1000)
        tol_f = int(round((8 / 15.0) * fps))
        for r in res["reps"]:
            if r["end"] < commit_to and not any(
                    abs(r["pull"] - c["pull"]) <= tol_f or
                    te._iou((r["start"], r["end"]), (c["start"], c["end"])) >= 0.5
                    for c in committed):
                committed.append(dict(start=r["start"], pull=r["pull"], end=r["end"]))
                commit_t.append(t)
                t0 = time.perf_counter()
                f = rep_features(v, None, r["start"], r["end"], fps)
                evaluate_rep(f, thr_sr)
                tm["form"].append((time.perf_counter() - t0) * 1000)
    lat_s = [(ct - c["end"]) / fps for c, ct in zip(committed, commit_t)]
    scale = step  # map processed-frame indices back to native 30 fps frames
    committed = [{k: int(r[k] * scale) for k in r} for r in committed]
    return committed, tm, lat_s, T


def measure_yolo(n=60):
    """Real per-frame YOLO cost on this hardware (the streaming front-end cost)."""
    import cv2
    from ultralytics import YOLO
    m = YOLO(os.path.join(REPO_ROOT, "yolo11n-pose.pt"))
    cap = cv2.VideoCapture(os.path.join(REPO_ROOT, "ai", "data", "seated_row",
                                        "correct", "correct_01.mp4"))
    ts = []
    for i in range(n):
        ok, fr = cap.read()
        if not ok:
            break
        t0 = time.perf_counter()
        m.predict(fr, device="cpu", verbose=False)
        ts.append((time.perf_counter() - t0) * 1000)
    cap.release()
    ts = ts[5:]                                                  # drop warm-up calls
    return float(np.median(ts)), float(np.percentile(ts, 95))


def main():
    global VIDEOS, VARIANTS
    if "--smoke" in sys.argv:                       # 1 video x 1 variant, no artifact writes
        VIDEOS = VIDEOS[:1]
        VARIANTS = {"V1_low_latency": VARIANTS["V1_low_latency"]}
    os.makedirs(OUT, exist_ok=True)
    gt = pd.read_csv(os.path.join(REPO_ROOT, "ai", "outputs", "manual_rep_annotations.csv"))
    gt["video"] = gt.video.str.replace(".mp4", "", regex=False)
    cfg, model, dec = frozen_system()
    yolo_med, yolo_p95 = measure_yolo()
    print(f"YOLO11n-pose CPU per frame: median {yolo_med:.1f} ms, p95 {yolo_p95:.1f} ms")

    offline = {}
    for name in VIDEOS:
        pose = np.load(os.path.join(P28, f"pose_motionbert_lite_{name}.npy"))
        cf = np.load(os.path.join(P28, f"pose_motionbert_lite_{name}_conf.npy"))
        v = prepare_video(name, pose, 30.0, conf=cf)
        offline[name] = predict_video(v, model, dec, cfg, None)["reps"]

    acc_rows, tim_rows, lat_rows = [], [], []
    proc = psutil.Process()
    thr_sr = json.load(open(thresholds_path("seated_row")))["thresholds"]
    for vname, var in VARIANTS.items():
        all_tm = {k: [] for k in ("track", "lift", "feat_decode", "form")}
        for name in VIDEOS:
            reps, tm, lat_s, T = stream_video(name, var, cfg, model, dec, thr_sr)
            for k in all_tm:
                all_tm[k] += tm[k]
            te.PULL_TOL_F = 16
            g = [dict(start=int(r.start_frame), pull=int(r.pull_frame), end=int(r.end_frame))
                 for r in gt[gt.video == name].itertuples()]
            m_gt = te.evaluate_dontcare(reps, g, [], "iou", protocol="v2")
            pairs = te._match(range(len(reps)), offline[name], reps, "pull")
            dp = [abs(reps[i]["pull"] - offline[name][j]["pull"]) for i, j in pairs]
            acc_rows.append(dict(variant=vname, video=name, n_gt=len(g),
                                 n_offline=len(offline[name]), n_causal=len(reps),
                                 tp=m_gt["tp"], fp=m_gt["fp"], fn=m_gt["fn"],
                                 pull_mae_f=round(m_gt["pull_mae_frames"], 2)
                                 if not np.isnan(m_gt["pull_mae_frames"]) else np.nan,
                                 matched_offline=len(pairs),
                                 pull_dev_vs_offline_f=round(float(np.mean(dp)), 2) if dp else 0.0,
                                 median_event_lat_s=round(float(np.median(lat_s)), 2)
                                 if lat_s else np.nan))
            print(f"{vname} {name}: causal {len(reps)} vs offline {len(offline[name])} "
                  f"vs GT {len(g)} | TP {m_gt['tp']} FP {m_gt['fp']} FN {m_gt['fn']}")
        fps_proc = var["proc_fps"]
        lift_ms = float(np.median(all_tm["lift"]))
        track_ms = float(np.median(all_tm["track"]))
        fd_ms = float(np.median(all_tm["feat_decode"]))
        per_frame_ms = yolo_med + (lift_ms + track_ms + fd_ms) / STRIDE
        tim_rows.append(dict(variant=vname, proc_fps=fps_proc,
                             yolo_ms_per_frame=round(yolo_med, 1),
                             track_ms_per_chunk=round(track_ms, 1),
                             track_ms_last_chunk=round(float(np.max(all_tm["track"])), 1),
                             lift_ms_per_chunk=round(lift_ms, 1),
                             feat_decode_ms_per_chunk=round(fd_ms, 1),
                             feat_decode_ms_last_chunk=round(float(np.max(all_tm["feat_decode"])), 1),
                             form_ms_per_rep=round(float(np.median(all_tm["form"])), 1)
                             if all_tm["form"] else np.nan,
                             per_frame_budget_ms=round(per_frame_ms, 1),
                             sustainable_fps=round(1000.0 / per_frame_ms, 1),
                             realtime_factor=round((1000.0 / per_frame_ms) / fps_proc, 2),
                             lookahead_s=round(var["commit_lag_f"] / fps_proc, 2),
                             warmup_s=round(WINDOW / fps_proc, 1),
                             rss_mb=round(proc.memory_info().rss / 2**20, 0)))
        lat_rows.append(dict(variant=vname,
                             p50_chunk_ms=round(track_ms + lift_ms + fd_ms, 1),
                             p95_chunk_ms=round(float(np.percentile(
                                 np.array(all_tm["track"]) + np.array(all_tm["lift"][:len(all_tm['track'])])
                                 + np.array(all_tm["feat_decode"][:len(all_tm['track'])]), 95)), 1)))
    pd.DataFrame(acc_rows).to_csv(os.path.join(OUT, "accuracy.csv"), index=False)
    pd.DataFrame(tim_rows).to_csv(os.path.join(OUT, "timing.csv"), index=False)
    pd.DataFrame(lat_rows).to_csv(os.path.join(OUT, "latency.csv"), index=False)
    acc = pd.DataFrame(acc_rows)
    summary = dict(yolo_ms=dict(median=yolo_med, p95=yolo_p95),
                   hardware="CPU-only laptop (torch CPU), Windows 11",
                   variants={})
    for vname in VARIANTS:
        g = acc[acc.variant == vname]
        tp, fp, fn = int(g.tp.sum()), int(g.fp.sum()), int(g.fn.sum())
        P, R, F = te.prf(tp, fp, fn)
        summary["variants"][vname] = dict(
            tp=tp, fp=fp, fn=fn, f1=round(F, 3),
            median_event_latency_s=round(float(g.median_event_lat_s.median()), 2),
            timing=tim_rows[[r["variant"] for r in tim_rows].index(vname)])
        print(f"{vname}: GT-F1 {F:.3f} (TP {tp} FP {fp} FN {fn})")
    json.dump(summary, open(os.path.join(OUT, "summary.json"), "w"), indent=1, default=float)


if __name__ == "__main__":
    main()
