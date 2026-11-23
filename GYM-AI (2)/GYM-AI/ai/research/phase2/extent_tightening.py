#!/usr/bin/env python3
"""P2.2b (docs/EXPERIMENT_REGISTRY.md): minimal fix attempt for the paused-tempo window-extent
weakness characterized in P2.2a (`paused_tempo.py`): a targeted END-hold insertion (0.5 s) alone
drops internal_sr IoU F1 0.841->0.720 — the closest synthetic match to the real external failure
("the HSMM absorbs the long inter-rep extension holds into rep windows", EXTERNAL_REAL_WORLD_
STRESS_TEST.md). A symmetric START-side (squeeze-hold) probe is included too.

Fix (minimal, additive, POST-PROCESSING only — does not touch the fitted phase model, HSMM
emissions, durations, or transitions, so rep COUNT and PULL timing — already good, pull-rule F1
0.842 external — are untouched by construction): for a decoded rep whose PULL or RET segment
exceeds the TRAINED duration law's 90th percentile, re-estimate that boundary as the first point
where the fused phase signal reaches a stable plateau near the trained REST level, instead of
the raw (possibly hold-absorbed) decoder boundary. A normal-length segment is NEVER touched —
the gate is "segment > P90 of its own trained duration law", which by definition only trips on
unusual segments.
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd
from scipy.stats import norm

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, REPO_ROOT)

from ai.augmentation.perturb import make_pause_insertion_transform  # noqa: E402
from ai.core.config import REST  # noqa: E402
from ai.core.features import prepare_video  # noqa: E402
from ai.evaluation import harness as H  # noqa: E402
from ai.evaluation.protocol import evaluate_dontcare, prf, pull_tol_frames  # noqa: E402

OUT = os.path.join(REPO_ROOT, "ai", "outputs", "phase2")
PIPELINE = "motionbert_lite"
MEND_FPS = 15.0
PLATEAU_TOL = 0.15        # fraction of the REST<->PULL phase range; see note in tighten_reps


def _p90_frames(dec, state_i: int, fps: float, scale: float) -> int:
    return int(round(np.exp(dec.dur_mu[state_i] + dec.dur_sigma[state_i] * norm.ppf(0.90))
                     / (1.0 / fps / scale)))


def tighten_reps(res: dict, dec, fps: float) -> list:
    """Post-process predict_video()'s reps: tighten `start` (PULL-segment) and `end`
    (RET-segment) only when that segment exceeds its state's trained P90 duration, by searching
    for the first stable near-REST plateau. `fused` and `scale` come from the SAME decode."""
    fused = res["fused"]
    scale = res["tempo_s"] if res.get("tempo_s") else 1.0
    rest_level = float(dec.mu[REST, 0])
    pull_range = abs(float(dec.pk_mu) - rest_level) or 1.0
    tol = PLATEAU_TOL * pull_range
    p90_pull = _p90_frames(dec, 0, fps, scale)
    p90_ret = _p90_frames(dec, 1, fps, scale)

    def plateau_from_left(a: int, b: int) -> int:
        """[a,b]: first index (relative to a) from which fused stays within `tol` of rest_level
        all the way to b. Returns b-a (no plateau found -> no change) if none exists."""
        seg = fused[a:b + 1]
        near = np.abs(seg - rest_level) <= tol
        for i in range(len(seg)):
            if near[i:].all():
                return i
        return len(seg) - 1

    out = []
    for r in res["reps"]:
        s, p, e = r["start"], r["pull"], r["end"]
        new_s, new_e = s, e
        if (p - s) > p90_pull:
            # PULL segment unusually long (a squeeze-hold BEFORE the peak, held near rest at the
            # START): tighten start forward to the end of that leading plateau.
            seg = fused[s:p + 1]
            near = np.abs(seg - rest_level) <= tol
            j = 0
            while j < len(seg) and near[j]:
                j += 1
            if 0 < j < len(seg):
                new_s = s + j - 1
        if (e - p) > p90_ret:
            # RET segment unusually long (an extension-hold AFTER the return, at the END):
            # tighten end back to the start of the trailing near-rest plateau.
            j = plateau_from_left(p, e)
            if j < (e - p):
                new_e = p + j
        nr = dict(r)
        nr["start"], nr["end"] = new_s, new_e
        out.append(nr)
    return out


def predict_tightened(system, v):
    from ai.core.decoder import predict_video
    res = predict_video(v, system.model, system.dec, system.cfg, None)
    res["reps"] = tighten_reps(res, system.dec, v["fps"])
    return res


def run_suite(system, items, transform, tighten: bool):
    rows = []
    for name, it in items.items():
        pose, conf, fps, gtc, gtu = it["pose"], it["conf"], it["fps"], it["gtc"], it["gtu"]
        if transform is not None:
            pose, conf, fps, gtc, gtu = transform(name, pose, conf, fps, gtc, gtu)
        v = prepare_video(name, pose, fps, conf=conf)
        try:
            res = predict_tightened(system, v) if tighten else system.predict(v)
            pred = [dict(start=r["start"], pull=r["pull"], end=r["end"]) for r in res["reps"]]
        except Exception as e:
            print(f"    [CRASH, 0 pred] {name}: {type(e).__name__}")
            pred = []
        tol = pull_tol_frames(fps)
        m = evaluate_dontcare(pred, gtc, gtu, "iou", "v2", tol)
        rows.append(dict(video=name, tp=m["tp"], fp=m["fp"], fn=m["fn"]))
    df = pd.DataFrame(rows)
    return prf(df.tp.sum(), df.fp.sum(), df.fn.sum())[2], int(df.tp.sum()), int(df.fp.sum()), int(df.fn.sum())


def _mendeley_val_back_raw():
    POSES = os.path.join(REPO_ROOT, "ai", "outputs", "pose_bench", "poses", PIPELINE)
    vids = H.mendeley_videos(PIPELINE)
    out = {}
    for vid, v in vids.items():
        if v["split"] != "val" or v["exercise"] != "back":
            continue
        pose = np.load(os.path.join(POSES, vid + ".npy"))
        conf = np.load(os.path.join(POSES, vid + "_conf.npy"))
        out[vid] = dict(pose=pose, conf=conf, fps=MEND_FPS, gtc=v["gt"], gtu=v["gt_uncertain"])
    return out


def external_items():
    return H.load_external()


def main():
    os.makedirs(OUT, exist_ok=True)
    system = H.fit_system(PIPELINE)
    sr = H.load_internal_sr()
    mv = _mendeley_val_back_raw()
    ext = external_items()

    CASES = [("normal", None), ("end_pause_0.5s", make_pause_insertion_transform(0.5, at="end")),
             ("end_pause_1.0s", make_pause_insertion_transform(1.0, at="end")),
             ("pull_pause_1.0s", make_pause_insertion_transform(1.0, at="pull")),
             ("timewarp_0.7x", None)]  # timewarp handled separately (needs speed, not pause_s)
    from ai.augmentation.perturb import make_time_warp_transform
    CASES[-1] = ("timewarp_0.7x", make_time_warp_transform(0.7))

    rows = []
    for label, tf in CASES:
        for tighten in (False, True):
            f1_sr, *c_sr = run_suite(system, sr, tf, tighten)
            f1_mv, *c_mv = run_suite(system, mv, tf, tighten)
            rows.append(dict(case=label, tightened=tighten, internal_sr_f1=f1_sr, internal_sr=c_sr,
                             mendeley_val_f1=f1_mv, mendeley_val=c_mv))
            print(f"{label:16s} tightened={tighten!s:5s}  internal_sr F1={f1_sr:.4f} {c_sr}  "
                  f"mendeley_val F1={f1_mv:.4f} {c_mv}")

    print("\n=== external (frozen validation check) ===")
    for tighten in (False, True):
        f1, *c = run_suite(system, ext, None, tighten)
        rows.append(dict(case="external", tightened=tighten, internal_sr_f1=None,
                         internal_sr=None, mendeley_val_f1=f1, mendeley_val=c))
        print(f"external          tightened={tighten!s:5s}  IoU F1={f1:.4f} {c}")

    pd.DataFrame(rows).to_csv(os.path.join(OUT, "extent_tightening_results.csv"), index=False)


if __name__ == "__main__":
    main()
