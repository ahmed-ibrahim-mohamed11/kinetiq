#!/usr/bin/env python3
"""P2.1b (docs/EXPERIMENT_REGISTRY.md): per-video, LABEL-FREE automatic depth-sign resolution.

ai/core/features.py::prepare_video resolves its backward ("pull") axis's SIGN from a single
comparison against the fixed external constant FACING_IN_CAMERA ("athlete faces away from a
rear camera"). Phase 1 (E-01) found this is a discrete, all-or-nothing choice: it doesn't matter
for a small rotation, but flips the decoded reps for a large one. P2.1a's yaw sweep (see
docs/EXPERIMENT_REGISTRY.md) confirmed: (a) internal_sr (true rear-view videos) has a SINGLE
shared sign threshold and the fixed convention is already on the correct side of it; (b)
Mendeley VAL pools several different camera placements (front/side/diagonal), each with ITS OWN
threshold, so a single GLOBAL flip helps some views and hurts others — motivating a mechanism
that decides the sign PER VIDEO instead of applying one fixed rule to everyone.

Mechanism (no retraining, no new labels): for a given video, build its features under BOTH sign
conventions (the as-reconstructed one, and its flip — a cheap column-negation post-processing
step on prepare_video's own output, not a re-derivation), run the FROZEN phase model + HSMM on
each, and keep whichever gives the higher decode log-likelihood (`hsmm_decode(...,
return_score=True)`). This is a per-video selection the frozen model already "believes" more,
using only information the model already had — never the ground truth.
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, REPO_ROOT)

from ai.core.decoder import (build_tables, emission_loglik, hsmm_decode,  # noqa: E402
                             peak_loglik, segments_to_reps, video_scale)
from ai.core.phase_model import predict_phase  # noqa: E402
from ai.evaluation import harness as H  # noqa: E402
from ai.evaluation.protocol import evaluate_dontcare, prf, pull_tol_frames  # noqa: E402

OUT = os.path.join(REPO_ROOT, "ai", "outputs", "phase2")
PIPELINE = "motionbert_lite"
MEND_FPS = 15.0


def flip_backward_sign(v: dict) -> dict:
    """Copy of a prepare_video() output with the backward ('pull') axis's sign negated for both
    arms and the trunk — the ONLY columns FACING_IN_CAMERA's sign choice affects (verified in
    ai/core/features.py::prepare_video: lateral/vertical columns don't depend on `fwd`'s sign)."""
    v2 = dict(v)
    v2["arm"] = {a: v["arm"][a].copy() for a in "LR"}
    for a in "LR":
        v2["arm"][a][:, [2, 5]] *= -1
    v2["trunk"] = v["trunk"].copy()
    v2["trunk"][:, 0] *= -1
    return v2


def predict_with_score(v: dict, model, dec, cfg):
    phi, fused, _design = predict_phase(v, model, cfg, None)
    scale = video_scale(fused, v["fps"], cfg, dec.ref_tempo_s) if dec.tempo_norm else 1.0
    E = emission_loglik(dec, fused, v["fps"], cfg, scale)
    tabs = build_tables(dec, v["fps"], v["T"], scale)
    segs, score = hsmm_decode(E, tabs, dec.p_direct, cfg.dur_weight, peak_loglik(dec, fused), return_score=True)
    reps = segments_to_reps(segs, dec, cfg, v["fps"], scale, fused, phi)
    return reps, float(score)


def predict_auto_sign(v: dict, model, dec, cfg):
    reps0, score0 = predict_with_score(v, model, dec, cfg)
    reps1, score1 = predict_with_score(flip_backward_sign(v), model, dec, cfg)
    if score1 > score0:
        return reps1, "flipped", score0, score1
    return reps0, "as_is", score0, score1


# ----------------------------------------------------------------------------- evaluation over the 3 suites
def eval_internal_sr(system):
    items = H.load_internal_sr()
    rows, choices = [], []
    for name, it in items.items():
        from ai.core.features import prepare_video
        v = prepare_video(name, it["pose"], it["fps"], conf=it["conf"])
        reps, choice, s0, s1 = predict_auto_sign(v, system.model, system.dec, system.cfg)
        pred = [dict(start=r["start"], pull=r["pull"], end=r["end"]) for r in reps]
        tol = pull_tol_frames(it["fps"])
        for rule in ("iou", "pull"):
            m = evaluate_dontcare(pred, it["gtc"], it["gtu"], rule, "v2", tol)
            rows.append(dict(video=name, rule=rule, **{k: m[k] for k in ("tp", "fp", "fn")}))
        choices.append(dict(video=name, choice=choice, score_as_is=s0, score_flipped=s1))
    return pd.DataFrame(rows), pd.DataFrame(choices)


def eval_mendeley_val(system):
    from ai.core.features import prepare_video
    POSES = os.path.join(REPO_ROOT, "ai", "outputs", "pose_bench", "poses", PIPELINE)
    vids = H.mendeley_videos(PIPELINE)
    rows, choices = [], []
    for vid, v0 in vids.items():
        if v0["split"] != "val" or v0["exercise"] != "back":
            continue
        pose = np.load(os.path.join(POSES, vid + ".npy"))
        conf = np.load(os.path.join(POSES, vid + "_conf.npy"))
        v = prepare_video(vid, pose, MEND_FPS, conf=conf)
        reps, choice, s0, s1 = predict_auto_sign(v, system.model, system.dec, system.cfg)
        pred = [dict(start=r["start"], pull=r["pull"], end=r["end"]) for r in reps]
        tol = pull_tol_frames(MEND_FPS)
        m = evaluate_dontcare(pred, v0["gt"], v0["gt_uncertain"], "iou", "v2", tol)
        rows.append(dict(video=vid, view=v0["view"], subject=v0["subject"],
                         tp=m["tp"], fp=m["fp"], fn=m["fn"]))
        choices.append(dict(video=vid, view=v0["view"], choice=choice,
                            score_as_is=s0, score_flipped=s1))
    return pd.DataFrame(rows), pd.DataFrame(choices)


def eval_external(system):
    from ai.core.features import prepare_video
    items = H.load_external()
    rows, choices = [], []
    for name, it in items.items():
        v = prepare_video(name, it["pose"], it["fps"], conf=it["conf"])
        reps, choice, s0, s1 = predict_auto_sign(v, system.model, system.dec, system.cfg)
        pred = [dict(start=r["start"], pull=r["pull"], end=r["end"]) for r in reps]
        tol = pull_tol_frames(it["fps"])
        for rule in ("iou", "pull"):
            m = evaluate_dontcare(pred, it["gtc"], it["gtu"], rule, "v2", tol)
            rows.append(dict(video=name, rule=rule, **{k: m[k] for k in ("tp", "fp", "fn")}))
        choices.append(dict(video=name, choice=choice, score_as_is=s0, score_flipped=s1))
    return pd.DataFrame(rows), pd.DataFrame(choices)


def pooled_f1(df, rule=None):
    g = df[df.rule == rule] if rule else df
    tp, fp, fn = g.tp.sum(), g.fp.sum(), g.fn.sum()
    return prf(tp, fp, fn)[2], int(tp), int(fp), int(fn)


def main():
    os.makedirs(OUT, exist_ok=True)
    system = H.fit_system(PIPELINE)

    print("=== internal_sr (auto-sign) ===")
    sr_rows, sr_choices = eval_internal_sr(system)
    sr_rows.to_csv(os.path.join(OUT, "auto_sign_internal_sr.csv"), index=False)
    sr_choices.to_csv(os.path.join(OUT, "auto_sign_internal_sr_choices.csv"), index=False)
    for rule in ("iou", "pull"):
        f1, tp, fp, fn = pooled_f1(sr_rows, rule)
        print(f"  {rule}: F1={f1:.4f} tp={tp} fp={fp} fn={fn}")
    print(sr_choices.to_string(index=False))

    print("\n=== mendeley_val back (auto-sign) ===")
    mv_rows, mv_choices = eval_mendeley_val(system)
    mv_rows.to_csv(os.path.join(OUT, "auto_sign_mendeley_val.csv"), index=False)
    mv_choices.to_csv(os.path.join(OUT, "auto_sign_mendeley_val_choices.csv"), index=False)
    f1, tp, fp, fn = pooled_f1(mv_rows)
    print(f"  pooled: F1={f1:.4f} tp={tp} fp={fp} fn={fn}")
    for view in ("front", "side", "diagonal"):
        sub = mv_rows[mv_rows.view == view]
        f1v, tpv, fpv, fnv = pooled_f1(sub)
        print(f"  view={view}: F1={f1v:.4f} tp={tpv} fp={fpv} fn={fnv} (n_videos={len(sub)})")
    print(mv_choices.groupby("view").choice.value_counts())

    print("\n=== external (frozen validation check, auto-sign) ===")
    ex_rows, ex_choices = eval_external(system)
    ex_rows.to_csv(os.path.join(OUT, "auto_sign_external.csv"), index=False)
    ex_choices.to_csv(os.path.join(OUT, "auto_sign_external_choices.csv"), index=False)
    for rule in ("iou", "pull"):
        f1, tp, fp, fn = pooled_f1(ex_rows, rule)
        print(f"  {rule}: F1={f1:.4f} tp={tp} fp={fp} fn={fn}")
    print(ex_choices.to_string(index=False))


if __name__ == "__main__":
    main()
