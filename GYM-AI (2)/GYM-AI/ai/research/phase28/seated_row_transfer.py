#!/usr/bin/env python3
"""
Phase 2.8: ZERO-SHOT Seated-Row domain transfer (reference evaluation, NOT pristine test).

Question: do the Phase 2.7 systems (phase model + HSMM fitted on Mendeley BACK TRAIN at 15 fps,
config frozen on VAL) transfer to the project's target exercise - seated row, rear view, 30 fps,
one athlete - with NO seated-row training, NO re-tuning, NO fps changes?

Pipeline per system (poseformerv2 PRIMARY, motionbert_lite FALLBACK):
  video -> YOLO11n-pose top-8 area-sorted candidates -> Phase 2.5C deterministic tracker
        -> frozen lifter (fps-agnostic) -> ai.core features at fps=30
        -> Phase 2.7 frozen cfg + TRAIN-refit (deterministic) phase model + HSMM -> reps
GT: ai/outputs/manual_rep_annotations.csv (30 reps; 1-based frames - offset << tolerance).
Matching tolerance is SECONDS-defined (0.533 s -> 16 frames @30 fps); IoU rule unchanged.
Context numbers (different protocol - same-athlete LOVO TRAINED ON THESE VIDEOS):
frozen baseline tag v0.2.0: F1 0.93 (pull +/-0.5s) / 0.97 (IoU).
"""
from __future__ import annotations

import json
import os
import sys

import numpy as np
import pandas as pd

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.join(REPO_ROOT, "ai", "research", "pose_benchmark"))
sys.path.insert(0, os.path.join(REPO_ROOT, "ai", "research", "phase27"))

from ai.core.config import Cfg  # noqa: E402
from ai.core.decoder import predict_video  # noqa: E402
from ai.core.features import prepare_video  # noqa: E402
from lifters import LIFTERS  # noqa: E402
from tracking import track_subject  # noqa: E402
import train_eval as te  # noqa: E402
from result_schema import make_row  # noqa: E402

OUT = os.path.join(REPO_ROOT, "ai", "outputs", "phase28")
SR_FPS = 30.0
PULL_TOL_S = 8 / 15.0


def yolo_top8(video_path):
    from ultralytics import YOLO
    import cv2
    model = YOLO(os.path.join(REPO_ROOT, "yolo11n-pose.pt"))
    cap = cv2.VideoCapture(video_path)
    wh = (cap.get(cv2.CAP_PROP_FRAME_WIDTH), cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()
    kt, bt = [], []
    for r in model.predict(source=video_path, stream=True, device="cpu", verbose=False):
        k = np.full((8, 17, 3), np.nan, dtype=np.float32)
        b = np.full((8, 5), np.nan, dtype=np.float32)
        if r.boxes is not None and len(r.boxes) > 0 and r.keypoints is not None:
            conf = r.boxes.conf.cpu().numpy()
            xywh = r.boxes.xywh.cpu().numpy()
            order = np.argsort(-(xywh[:, 2] * xywh[:, 3]))[:8]
            for s, i in enumerate(order):
                k[s] = r.keypoints.data[int(i)].cpu().numpy()
                b[s] = [*xywh[int(i)], conf[int(i)]]
        kt.append(k)
        bt.append(b)
    return np.stack(kt), np.stack(bt), wh


def main():
    os.makedirs(OUT, exist_ok=True)
    gt = pd.read_csv(os.path.join(REPO_ROOT, "ai", "outputs", "manual_rep_annotations.csv"))
    gt["video"] = gt.video.str.replace(".mp4", "", regex=False)
    videos = {}
    for sub in ("correct", "asymmetry"):
        d = os.path.join(REPO_ROOT, "ai", "data", "seated_row", sub)
        for f in sorted(os.listdir(d)):
            if f.endswith(".mp4"):
                videos[f[:-4]] = os.path.join(d, f)

    results = {}
    for pipeline in ("poseformerv2", "motionbert_lite"):
        cfg_file = json.load(open(os.path.join(REPO_ROOT, "ai", "outputs", "phase27",
                                               pipeline, "config_final.json")))
        c = cfg_file["cfg"]
        cfg = Cfg(smooth_hz=float(c["smooth_hz"]),
                  detrend_s=None if c["detrend_s"] in (None, "None") else float(c["detrend_s"]),
                  dur_weight=float(c["dur_weight"]), dur_sigma_floor=float(c["dur_sigma_floor"]))
        vids27 = te.load_videos(pipeline)
        train = [v for v in vids27.values() if v["split"] == "train" and v["exercise"] == "back" and v["gt"]]
        cache: dict = {}
        model = te.fit_model_masked(train, cfg, cache)
        dec = te.fit_decoder_masked(train, te.crossfit_fused_masked(train, cfg, cache), cfg)
        lifter = LIFTERS[pipeline]()
        rows = []
        for name, path in videos.items():
            cache_npz = os.path.join(OUT, f"2d_{name}.npz")
            if os.path.exists(cache_npz):
                z = np.load(cache_npz)
                kt, bt, wh = z["kt"], z["bt"], tuple(z["wh"])
            else:
                kt, bt, wh = yolo_top8(path)
                np.savez_compressed(cache_npz, kt=kt, bt=bt, wh=np.array(wh))
            coco, tlog = track_subject(kt, bt, wh)
            pose_f = os.path.join(OUT, f"pose_{pipeline}_{name}.npy")
            if os.path.exists(pose_f):
                pose3d = np.load(pose_f)
                conf = np.load(pose_f.replace(".npy", "_conf.npy"))
            else:
                pose3d, conf = lifter.lift(coco, wh)
                np.save(pose_f, pose3d)
                np.save(pose_f.replace(".npy", "_conf.npy"), conf)
            v = prepare_video(name, pose3d, SR_FPS, conf=conf)
            res = predict_video(v, model, dec, cfg, None)
            pred = [dict(start=r["start"], pull=r["pull"], end=r["end"]) for r in res["reps"]]
            g = [dict(start=int(r.start_frame), pull=int(r.pull_frame), end=int(r.end_frame))
                 for r in gt[gt.video == name].itertuples()]
            te.PULL_TOL_F = int(round(PULL_TOL_S * SR_FPS))      # seconds-defined tolerance @30fps
            for rule in ("iou", "pull"):
                m = te.evaluate_dontcare(pred, g, [], rule, protocol="v2")
                rows.append(make_row(dict(video=name, rule=rule, tempo_s=res["tempo_s"]),
                                     {k: vv for k, vv in m.items() if k != "matched"}))
            print(f"  {pipeline} {name}: GT {len(g)} pred {len(pred)} "
                  f"(track lost {tlog['lost']}, jumps {tlog['jumps']})")
        df = pd.DataFrame(rows)
        df.to_csv(os.path.join(OUT, f"seated_row_{pipeline}.csv"), index=False)
        for rule in ("iou", "pull"):
            g = df[df.rule == rule]
            tp, fp, fn = g.tp.sum(), g.fp.sum(), g.fn.sum()
            P = tp / max(tp + fp, 1)
            R = tp / max(tp + fn, 1)
            F = 2 * P * R / max(P + R, 1e-9)
            mae = {k: float(np.nanmean(g[f"{k}_mae_frames"])) for k in ("start", "pull", "end")}
            print(f"{pipeline} [{rule}]: TP {tp} FP {fp} FN {fn} P {P:.2f} R {R:.2f} F1 {F:.3f} "
                  f"MAE s/p/e {mae['start']/30:.2f}/{mae['pull']/30:.2f}/{mae['end']/30:.2f} s")
            results[f"{pipeline}_{rule}"] = dict(tp=int(tp), fp=int(fp), fn=int(fn), f1=round(F, 3))
    json.dump(results, open(os.path.join(OUT, "summary.json"), "w"), indent=1)


if __name__ == "__main__":
    main()
