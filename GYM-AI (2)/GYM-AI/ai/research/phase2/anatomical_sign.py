#!/usr/bin/env python3
"""P2.7 — last pre-freeze improvement attempt (owner, 2026-10-10): anatomical backward-axis sign.
Gates pre-registered in docs/EXPERIMENT_REGISTRY.md ("P2.7 plan") BEFORE any candidate number was
computed. One bounded attempt: if the gates fail, the weakness is documented and nothing is tuned.

Weakness: R-07 side view (LOSO side IoU F1 0.654 vs front 0.967 / diagonal 0.974; locked TEST side
0.383). Secondary endpoint: R-08 paused tempo (P2.2a probes).

Mechanism (label-free diagnostic, no ground truth used): ai/core/features.prepare_video takes the
sign of its backward axis from the CAMERA — fwd = n * sign(median(n . Z)), n = cross(lat, up) —
i.e. it assumes the athlete faces away from the camera. In every development video the lifted
skeleton has its knees (and in 117/120 videos its hands) on the same side of n, so n is the
athlete's anatomical BACKWARD direction in every view. The camera rule instead gives front and
diagonal videos one convention (71/73), the rear product view the other (6/6), and splits side
videos 10/26, so the training data mixes two conventions.

Candidate (one line): fwd = cross(up, lat), the anatomical forward direction of a non-mirrored
skeleton. No camera assumption; every feature becomes invariant to camera rotation. Everything
else stays frozen (cfg, splits, masked supervision, decoder, guards, thresholds); the system is
refit as usual. Rear-view features are byte-identical under both rules (the camera rule already
picks -n there), so form indicators (ranges/lags, sign-invariant) are unaffected.

  python ai/research/phase2/anatomical_sign.py --arm camera       # BEFORE: must reproduce frozen numbers
  python ai/research/phase2/anatomical_sign.py --arm anatomical   # AFTER
  python ai/research/phase2/anatomical_sign.py --compare          # applies the pre-registered gates
  python ai/research/phase2/anatomical_sign.py --panoptic         # informational real-3D-GT check
Outputs: ai/outputs/phase2/p27_{camera,anatomical}.json, p27_decision.json, p27_panoptic.csv.
No Mendeley TEST pose or confidence file is ever opened: the annotation and split CSVs are read
whole and the TEST rows are dropped before any pose is loaded or any label is used (an audited
re-run by the adversarial review opened 218 pose files = 109 dev videos x 2, and 0 TEST files).
Added after the pre-registration (badf440), no decision role: --explain, --cues, and a fix of the
manifest lookup in compare()'s validity check.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

import numpy as np
import pandas as pd

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, REPO_ROOT)

from ai.augmentation.perturb import (make_pause_insertion_transform,  # noqa: E402
                                     make_time_warp_transform)
from ai.core import features as F  # noqa: E402
from ai.core.config import (FACING_IN_CAMERA, L_ELB, L_SHO, PELVIS, R_ELB, R_SHO,  # noqa: E402
                            THORAX)
from ai.core.model_artifact import fingerprint  # noqa: E402
from ai.evaluation import harness as H  # noqa: E402  (puts phase27/realtime/pose_benchmark on sys.path)

import causal_prototype as CP  # noqa: E402
import train_eval as TE  # noqa: E402

OUT = os.path.join(REPO_ROOT, "ai", "outputs", "phase2")
MANIFEST = os.path.join(REPO_ROOT, "docs", "AI_CORE_BASELINE_MANIFEST.json")
CAUSAL = ("V1_low_latency", "V2_quality", "V3_budget_15fps")
PROBES = {  # P2.2a probes (paused_tempo.py), same transforms and parameters
    "end_hold_0.5s": lambda: make_pause_insertion_transform(0.5, at="end"),
    "end_hold_1.0s": lambda: make_pause_insertion_transform(1.0, at="end"),
    "pull_hold_1.0s": lambda: make_pause_insertion_transform(1.0, at="pull"),
    "timewarp_0.7x": lambda: make_time_warp_transform(0.7),
}
CAMERA_PREPARE = F.prepare_video


# ----------------------------------------------------------------------------- the candidate rule
def prepare_video_anatomical(name, pose, fps, conf=None, conf_thr=0.3):
    """ai.core.features.prepare_video with ONE change: fwd = cross(up, lat) instead of the
    camera-derived sign. Every other line is verbatim."""
    P = np.asarray(pose, dtype=float)
    if P.ndim != 3 or P.shape[1:] != (17, 3):
        raise ValueError(f"{name}: expected (T,17,3) H36M pose, got {P.shape}")
    if not (fps > 0) or not np.isfinite(fps):
        raise ValueError(f"{name}: fps must be a positive, finite number, got {fps!r}")
    T = len(P)
    finite = np.isfinite(P).all(axis=(1, 2))
    P = F._interp_rows(P.reshape(T, -1), finite).reshape(T, 17, 3)
    pelvis = P[:, PELVIS]
    up_raw = P[:, THORAX] - pelvis
    torso_len = float(np.median(np.linalg.norm(up_raw, axis=1)))
    up = F._unit(up_raw)
    lat = P[:, R_SHO] - P[:, L_SHO]
    lat = F._unit(lat - (lat * up).sum(1, keepdims=True) * up)
    fwd = np.cross(up, lat)                                   # <-- the only change

    def coords(v):
        d = (v - pelvis) / torso_len
        return np.stack([(d * lat).sum(1), (d * up).sum(1), -(d * fwd).sum(1)], axis=1)

    sho_c = coords(0.5 * (P[:, L_SHO] + P[:, R_SHO]))
    trunk = sho_c[:, 2:3]
    arm, valid = {}, {}
    for a, (sho, elb, mirror) in {"L": (L_SHO, L_ELB, -1.0), "R": (R_SHO, R_ELB, 1.0)}.items():
        e, s = coords(P[:, elb]), coords(P[:, sho])
        f = np.hstack([e, e - s])
        f[:, [0, 3]] *= mirror
        arm[a] = f
        ok = finite.copy()
        if conf is not None:
            ok &= conf[:, [sho, elb, PELVIS, THORAX]].min(axis=1) >= conf_thr
        valid[a] = ok
    return dict(name=name, fps=float(fps), T=T, arm=arm, trunk=trunk, valid=valid, torso_len=torso_len)


def camera_sign(pose) -> float:
    """The sign the frozen camera rule picks for this video (+1: fwd = n, -1: fwd = -n)."""
    P = np.asarray(pose, dtype=float)
    T = len(P)
    P = F._interp_rows(P.reshape(T, -1), np.isfinite(P).all(axis=(1, 2))).reshape(T, 17, 3)
    up = F._unit(P[:, THORAX] - P[:, PELVIS])
    lat = P[:, R_SHO] - P[:, L_SHO]
    lat = F._unit(lat - (lat * up).sum(1, keepdims=True) * up)
    return float(np.sign(np.median(np.cross(lat, up) @ FACING_IN_CAMERA)) or 1.0)


def check_only_sign_differs(name, pose, fps, conf):
    """Self-check: the candidate equals the frozen output with the backward columns negated exactly
    when the camera rule picked +n, and is byte-identical otherwise."""
    a = prepare_video_anatomical(name, pose, fps, conf=conf)
    c = CAMERA_PREPARE(name, pose, fps, conf=conf)
    s = -camera_sign(pose)                     # factor applied to the backward columns
    for k in "LR":
        assert np.array_equal(a["arm"][k][:, [0, 1, 3, 4]], c["arm"][k][:, [0, 1, 3, 4]]), name
        assert np.array_equal(a["arm"][k][:, [2, 5]], s * c["arm"][k][:, [2, 5]]), name
        assert np.array_equal(a["valid"][k], c["valid"][k]), name
    assert np.array_equal(a["trunk"], s * c["trunk"]) and a["torso_len"] == c["torso_len"], name
    return s


def use_rule(arm: str):
    """Route every prepare_video the evaluation uses (harness suites, causal streamer) to the arm."""
    prep = CAMERA_PREPARE if arm == "camera" else prepare_video_anatomical
    H.prepare_video = prep
    CP.prepare_video = prep
    TE.prepare_video = prep
    return prep


# ----------------------------------------------------------------------------- data (dev only)
def load_dev(prep):
    """train_eval.load_videos restricted to Mendeley BACK TRAIN+VAL before any POSE file is read (the
    annotation and split CSVs are read whole; TEST rows are dropped first)."""
    ann = pd.read_csv(TE.ANN).fillna({"annotation_notes": ""})
    split = pd.read_csv(TE.SPLIT)[["subject_id", "split", "component"]]
    ann = ann.merge(split, on="subject_id")
    ann = ann[(ann.exercise == "back") & ann.split.isin(["train", "val"])]
    assert len(ann) and not (ann.split == "test").any()
    vids, raw = {}, {}
    poses = os.path.join(TE.POSES, "motionbert_lite")
    for vid, g in ann.groupby("video_id"):
        pose = np.load(os.path.join(poses, vid + ".npy"))
        conf = np.load(os.path.join(poses, vid + "_conf.npy"))
        v = prep(vid, pose, TE.FPS, conf=conf)
        g = g.sort_values("rep_id")
        certain = g[g.annotation_quality.isin(["verified", "auto"])]
        unc = g[g.annotation_quality == "uncertain"]
        v["gt"] = [dict(start=int(r.start_frame), pull=int(r.pull_frame), end=int(r.end_frame))
                   for r in certain.itertuples()]
        v["gt_uncertain"] = [dict(start=int(r.start_frame), pull=int(r.pull_frame), end=int(r.end_frame))
                             for r in unc.itertuples()]
        v["gt_tier"] = list(certain.annotation_quality)
        v["y"] = F.phase_target(v["T"], v["gt"])
        mask = np.ones(v["T"], dtype=bool)
        for r in v["gt_uncertain"]:
            mask[max(0, r["start"] - TE.MASK_PAD):min(v["T"], r["end"] + TE.MASK_PAD + 1)] = False
        v["mask"] = mask
        v["subject"], v["split"], v["component"] = g.subject_id.iloc[0], g.split.iloc[0], g.component.iloc[0]
        v["view"], v["form"], v["exercise"] = g.camera_view.iloc[0], g.form_label.iloc[0], g.exercise.iloc[0]
        vids[vid] = v
        raw[vid] = (pose, conf)
    return vids, raw


def _brief(s):
    return {r: {k: s[r][k] for k in ("tp", "fp", "fn", "n_gt", "f1", "precision", "recall",
                                     "start_mae_s", "pull_mae_s", "end_mae_s")} for r in ("iou", "pull")}


# ----------------------------------------------------------------------------- one arm
def run_arm(arm: str) -> dict:
    t0 = time.time()
    prep = use_rule(arm)
    vids, raw = load_dev(prep)
    cfg = H.frozen_cfg()
    out = dict(arm=arm, n_dev_videos=len(vids), test_split_touched=False)

    # label-free sign table + exactness self-check of the candidate rule
    signs = []
    for name, (pose, conf) in raw.items():
        s = check_only_sign_differs(name, pose, TE.FPS, conf)
        signs.append(dict(item=name, set="mendeley_dev", view=vids[name]["view"], camera_sign=camera_sign(pose),
                          backward_flipped_by_candidate=bool(s < 0)))
    sr, ext = H.load_internal_sr(), H.load_external()
    for st, items in (("internal_sr", sr), ("external", ext)):
        for name, it in items.items():
            s = check_only_sign_differs(name, it["pose"], it["fps"], it["conf"])
            signs.append(dict(item=name, set=st, view="rear" if st == "internal_sr" else "?",
                              camera_sign=camera_sign(it["pose"]), backward_flipped_by_candidate=bool(s < 0)))
    out["sign_table"] = signs

    # LOSO over the 15 development components (as loso_dev.py)
    comps = sorted({v["component"] for v in vids.values()})
    rows_all, comp_rows = [], []
    for c in comps:
        held = [v for v in vids.values() if v["component"] == c]
        train = [v for v in vids.values() if v["component"] != c and v["gt"]]
        system = H.fit_system(cfg=cfg, train=train, name=f"loso_{c}")
        rows = []
        for v in held:
            res = system.predict(v)
            pred = [dict(start=r["start"], pull=r["pull"], end=r["end"]) for r in res["reps"]]
            rows += H._eval_item(v["name"], pred, v["gt"], v["gt_uncertain"], v["fps"], "v2",
                                 extra=dict(component=c, view=v["view"]))
        s = H._score(rows)
        comp_rows.append(dict(component=c, split=held[0]["split"], n_videos=len(held),
                              iou=[s["iou"]["tp"], s["iou"]["fp"], s["iou"]["fn"]], iou_f1=s["iou"]["f1"],
                              pull_f1=s["pull"]["f1"]))
        rows_all += rows
        print(f"[{arm}] LOSO {c}: IoU F1 {s['iou']['f1']} ({s['iou']['tp']}/{s['iou']['fp']}/{s['iou']['fn']})"
              f"  [{time.time() - t0:.0f} s]", flush=True)
    out["loso"] = dict(pooled=_brief(H._score(rows_all)), components=comp_rows,
                       views={vw: _brief(H._score([r for r in rows_all if r["view"] == vw]))
                              for vw in sorted({r["view"] for r in rows_all})})

    # regression suites: system fit on TRAIN only, as the frozen product system
    system = H.fit_system(cfg=cfg, train=[v for v in vids.values() if v["split"] == "train" and v["gt"]],
                          name=f"p27_{arm}")
    out["fingerprint"] = fingerprint(system.cfg, system.model, system.dec)
    rows = []
    for v in (v for v in vids.values() if v["split"] == "val"):
        res = system.predict(v)
        pred = [dict(start=r["start"], pull=r["pull"], end=r["end"]) for r in res["reps"]]
        rows += H._eval_item(v["name"], pred, v["gt"], v["gt_uncertain"], v["fps"], "v2",
                             extra=dict(view=v["view"]))
    suites = dict(mendeley_val=_brief(H._score(rows)),
                  mendeley_val_by_view={vw: _brief(H._score([r for r in rows if r["view"] == vw]))
                                        for vw in sorted({r["view"] for r in rows})},
                  internal_sr=_brief(H.run_pose_suite(system, sr)["pooled"]),
                  external=_brief(H.run_pose_suite(system, ext)["pooled"]))
    print(f"[{arm}] suites done [{time.time() - t0:.0f} s]; causal next", flush=True)
    suites["causal"] = {vn: _brief(r["pooled"]) for vn, r in H.suite_causal(system, CAUSAL).items()}
    out["suites"] = suites

    # R-08 secondary endpoint: the P2.2a probes on internal_sr + mendeley_val (raw poses, same scorer)
    mv = {n: dict(pose=raw[n][0], conf=raw[n][1], fps=TE.FPS, gtc=vids[n]["gt"], gtu=vids[n]["gt_uncertain"])
          for n in vids if vids[n]["split"] == "val"}
    out["probes"] = {p: {"internal_sr": _brief(H.run_pose_suite(system, sr, mk())["pooled"]),
                         "mendeley_val": _brief(H.run_pose_suite(system, mv, mk())["pooled"])}
                     for p, mk in PROBES.items()}
    out["runtime_s"] = round(time.time() - t0, 1)
    return out


# ----------------------------------------------------------------------------- pre-registered gates
NOISE_F1 = 0.03


def _not_worse(b, a, rule="iou"):
    """Regression guard (pre-registered): F1 drop <= 0.03, or the change is at most one rep
    (TP down by <= 1 and FP up by <= 1), which global rule 4 treats as noise."""
    d = a[rule]["f1"] - b[rule]["f1"]
    one_rep = (a[rule]["tp"] - b[rule]["tp"] >= -1) and (a[rule]["fp"] - b[rule]["fp"] <= 1)
    return bool(d >= -NOISE_F1 or one_rep), round(d, 4)


def compare() -> dict:
    b = json.load(open(os.path.join(OUT, "p27_camera.json")))
    a = json.load(open(os.path.join(OUT, "p27_anatomical.json")))
    man = json.load(open(MANIFEST))
    hm = {k: v["value"] for k, v in man["headline_metrics"].items()}
    same = lambda x, y: abs(float(x) - float(y)) < 5e-5
    # validity (not a gate): the BEFORE arm must reproduce the frozen system and the frozen
    # P2.6 LOSO / headline numbers, otherwise the comparison itself is INVALID
    validity = dict(
        fingerprint=b["fingerprint"] == man["product_system"]["fitted_system_sha256"],
        loso_pooled=same(b["loso"]["pooled"]["iou"]["f1"], 0.8779),
        loso_side=same(b["loso"]["views"]["side"]["iou"]["f1"], 0.6537),
        mendeley_val=same(b["suites"]["mendeley_val"]["iou"]["f1"], hm["mendeley_val_iou_f1"]),
        internal_sr=same(b["suites"]["internal_sr"]["iou"]["f1"], hm["internal_sr_iou_f1"])
        and same(b["suites"]["internal_sr"]["pull"]["f1"], hm["internal_sr_pull_f1"]),
        external=same(b["suites"]["external"]["iou"]["f1"], hm["external_iou_f1"])
        and same(b["suites"]["external"]["pull"]["f1"], hm["external_pull_f1"]),
        causal=all(same(b["suites"]["causal"][vn]["iou"]["f1"], hm[f"causal_{vn}_iou_f1"]) for vn in CAUSAL))
    g = {}
    side_b, side_a = b["loso"]["views"]["side"]["iou"]["f1"], a["loso"]["views"]["side"]["iou"]["f1"]
    g["G1_loso_side_iou_f1_ge_0.70"] = dict(ok=bool(side_a >= 0.70), before=side_b, after=side_a)
    g["G2_no_view_drops_more_than_0.03"] = {
        vw: dict(ok=bool(a["loso"]["views"][vw]["iou"]["f1"] >= b["loso"]["views"][vw]["iou"]["f1"] - NOISE_F1),
                 before=b["loso"]["views"][vw]["iou"]["f1"], after=a["loso"]["views"][vw]["iou"]["f1"])
        for vw in b["loso"]["views"]}
    g["G3_loso_pooled_not_worse"] = {
        rule: dict(ok=bool(a["loso"]["pooled"][rule]["f1"] >= b["loso"]["pooled"][rule]["f1"] - NOISE_F1),
                   before=b["loso"]["pooled"][rule]["f1"], after=a["loso"]["pooled"][rule]["f1"])
        for rule in ("iou", "pull")}
    reg = {}
    for s in ("mendeley_val", "internal_sr", "external"):
        for rule in ("iou", "pull"):
            ok, d = _not_worse(b["suites"][s], a["suites"][s], rule)
            reg[f"{s}_{rule}"] = dict(ok=ok, delta_f1=d, before=b["suites"][s][rule]["f1"],
                                      after=a["suites"][s][rule]["f1"])
    for vn in CAUSAL:
        ok, d = _not_worse(b["suites"]["causal"][vn], a["suites"]["causal"][vn], "iou")
        reg[f"causal_{vn}_iou"] = dict(ok=ok, delta_f1=d, before=b["suites"]["causal"][vn]["iou"]["f1"],
                                       after=a["suites"]["causal"][vn]["iou"]["f1"])
    g["G4_regression_suites"] = reg
    pr = {}
    for p in PROBES:
        for s in ("internal_sr", "mendeley_val"):
            ok, d = _not_worse(b["probes"][p][s], a["probes"][p][s], "iou")
            pr[f"{p}_{s}"] = dict(ok=ok, delta_f1=d, before=b["probes"][p][s]["iou"]["f1"],
                                  after=a["probes"][p][s]["iou"]["f1"])
    g["G5_r08_probes_not_worse"] = pr

    def all_ok(x):
        return x["ok"] if "ok" in x else all(all_ok(y) for y in x.values())
    gates = {k: all_ok(v) for k, v in g.items()}
    # R-08 secondary claim (reported only): both END-hold probes improve beyond noise on BOTH suites
    r08 = {f"{p}_{s}": round(a["probes"][p][s]["iou"]["f1"] - b["probes"][p][s]["iou"]["f1"], 4)
           for p in ("end_hold_0.5s", "end_hold_1.0s") for s in ("internal_sr", "mendeley_val")}
    if not all(validity.values()):
        decision = "INVALID (the BEFORE arm does not reproduce the frozen numbers)"
    else:
        decision = "ACCEPT" if all(gates.values()) else "REJECT"
    res = dict(validity=validity, gates=gates, detail=g, r08_end_hold_delta_iou_f1=r08,
               r08_improved_beyond_noise=bool(all(d > NOISE_F1 for d in r08.values())), decision=decision)
    json.dump(res, open(os.path.join(OUT, "p27_decision.json"), "w"), indent=1)
    return res


# ----------------------------------------------------------------------------- informational: Panoptic
def panoptic():
    sys.path.insert(0, os.path.join(REPO_ROOT, "ai", "research", "phase2"))
    import panoptic_mpjpe as PM
    lifter = PM.MotionBertLiteLifter()
    rows = []
    for view, cam in PM.CAMS.items():
        kt, bt, wh = PM.extract_2d(os.path.join(PM.DATA, "hd_videos", f"hd_00_{cam:02d}.mp4"))
        coco, _ = PM.track_subject(kt, bt, wh)
        pose3d, conf = lifter.lift(coco, wh)
        T = min(PM.N_FRAMES, len(pose3d))
        gt = np.stack([PM.gt_to_h36m_full(PM.load_gt(f)) for f in range(T)])
        for rule, prep in (("camera", CAMERA_PREPARE), ("anatomical", prepare_video_anatomical)):
            def sig(p, c):   # mean L/R elbow-vs-pelvis backward component (as PM.backward_signal)
                v = prep("x", p, 29.97, conf=c)
                return np.mean([v["arm"]["L"][:, 2], v["arm"]["R"][:, 2]], axis=0)
            r = float(np.corrcoef(sig(pose3d[:T], conf[:T]), sig(gt, None))[0, 1])
            rows.append(dict(view=view, cam=cam, rule=rule, backward_signal_corr=round(r, 4)))
            print(f"{view:5s} {rule:10s} backward-signal correlation vs GT = {r:+.3f}", flush=True)
    pd.DataFrame(rows).to_csv(os.path.join(OUT, "p27_panoptic.csv"), index=False)


# ----------------------------------------------------------------------------- label-free cue table
def cues():
    """Reproduces the label-free evidence quoted in the P2.7 plan (first computed with a scratch
    diagnostic before the pre-registration): per video, which side of n = cross(lat, up) the knees
    (vs the hips) and the hands (vs the shoulders) are on, the camera rule's sign and median |n_z|.
    No ground truth and no model are involved. No Mendeley TEST pose file is opened."""
    from ai.core.config import L_WRI, R_WRI, L_HIP, R_HIP
    L_KNEE, R_KNEE = 5, 2

    def per_video(pose):
        P = np.asarray(pose, dtype=float)
        P = P[np.isfinite(P).all(axis=(1, 2))]
        up_raw = P[:, THORAX] - P[:, PELVIS]
        tl = float(np.median(np.linalg.norm(up_raw, axis=1)))
        up = F._unit(up_raw)
        lat = P[:, R_SHO] - P[:, L_SHO]
        lat = F._unit(lat - (lat * up).sum(1, keepdims=True) * up)
        n = np.cross(lat, up)
        side = lambda a, b: float(np.median((((a - b) / tl) * n).sum(1)))
        return dict(camera_rule_picks_plus_n=bool(np.sign(np.median(n @ FACING_IN_CAMERA)) > 0),
                    median_abs_nz=round(float(np.median(np.abs(n[:, 2]))), 3),
                    knee_cue=round(side(0.5 * (P[:, L_KNEE] + P[:, R_KNEE]), 0.5 * (P[:, L_HIP] + P[:, R_HIP])), 4),
                    hand_cue=round(side(0.5 * (P[:, L_WRI] + P[:, R_WRI]), 0.5 * (P[:, L_SHO] + P[:, R_SHO])), 4))
    rows = []
    vids, raw = load_dev(CAMERA_PREPARE)
    for name, (pose, _) in raw.items():
        rows.append(dict(set="mendeley_dev", view=vids[name]["view"], item=name, **per_video(pose)))
    for st, items in (("internal_sr", H.load_internal_sr()), ("external", H.load_external())):
        for name, it in items.items():
            rows.append(dict(set=st, view="rear" if st == "internal_sr" else "?", item=name, **per_video(it["pose"])))
    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(OUT, "p27_cues.csv"), index=False)
    g = df.groupby(["set", "view"]).agg(videos=("item", "size"),
                                        camera_plus_n=("camera_rule_picks_plus_n", "sum"),
                                        knees_on_minus_n=("knee_cue", lambda s: int((s < 0).sum())),
                                        hands_on_minus_n=("hand_cue", lambda s: int((s < 0).sum())),
                                        median_abs_nz=("median_abs_nz", "median"))
    print(g.to_string())
    print(f"knees on the -n side: {int((df.knee_cue < 0).sum())}/{len(df)}; hands: {int((df.hand_cue < 0).sum())}/{len(df)}")


# ----------------------------------------------------------------------------- post-hoc explanation
def explain():
    """Explanation only (the decision is the pre-registered gates'): how much each fitted system
    weights the backward columns, and how strong its fused phase signal is per domain/view."""
    from ai.core.phase_model import predict_phase
    cols = ["elbow-pelvis lateral", "elbow-pelvis vertical", "elbow-pelvis BACKWARD", "elbow-shoulder lateral",
            "elbow-shoulder vertical", "elbow-shoulder BACKWARD", "trunk BACKWARD"]
    out = {}
    for arm in ("camera", "anatomical"):
        prep = use_rule(arm)
        vids, _ = load_dev(prep)
        cfg = H.frozen_cfg()
        system = H.fit_system(cfg=cfg, train=[v for v in vids.values() if v["split"] == "train" and v["gt"]])
        w = np.asarray(system.model.w, dtype=float)
        o = dict(ridge_weights=dict(zip(cols, np.round(w, 4).tolist())),
                 backward_weight_share=round(float(np.abs(w[[2, 5, 6]]).sum() / np.abs(w).sum()), 3),
                 internal_sr={}, mendeley_val_median_phase_range={})
        for name, it in H.load_internal_sr().items():
            v = prep(name, it["pose"], it["fps"], conf=it["conf"])
            fused = predict_phase(v, system.model, cfg, None)[1]
            o["internal_sr"][name] = dict(phase_p5_p95=[round(float(np.percentile(fused, q)), 3) for q in (5, 95)],
                                          n_pred=len(system.predict(v)["reps"]), n_gt=len(it["gtc"]))
        for view in ("front", "side", "diagonal"):
            r = [float(np.percentile(f, 95) - np.percentile(f, 5)) for f in
                 (predict_phase(v, system.model, cfg, None)[1] for v in vids.values()
                  if v["split"] == "val" and v["view"] == view)]
            o["mendeley_val_median_phase_range"][view] = round(float(np.median(r)), 3)
        out[arm] = o
    json.dump(out, open(os.path.join(OUT, "p27_explanation.json"), "w"), indent=1)
    print(json.dumps({a: dict(backward_weight_share=o["backward_weight_share"],
                              mendeley_val_median_phase_range=o["mendeley_val_median_phase_range"])
                      for a, o in out.items()}, indent=1))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", choices=["camera", "anatomical"])
    ap.add_argument("--compare", action="store_true")
    ap.add_argument("--panoptic", action="store_true")
    ap.add_argument("--explain", action="store_true", help="post-hoc explanation (no decision role)")
    ap.add_argument("--cues", action="store_true", help="label-free cue table quoted in the P2.7 plan")
    args = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    if args.arm:
        res = run_arm(args.arm)
        json.dump(res, open(os.path.join(OUT, f"p27_{args.arm}.json"), "w"), indent=1)
        print(json.dumps(dict(fingerprint=res["fingerprint"][:12], loso_views={k: v["iou"]["f1"] for k, v in
                                                                               res["loso"]["views"].items()},
                              loso_pooled=res["loso"]["pooled"]["iou"]["f1"]), indent=1))
    if args.compare:
        res = compare()
        print(json.dumps(dict(validity=res["validity"], gates=res["gates"], decision=res["decision"],
                              r08=res["r08_end_hold_delta_iou_f1"]), indent=1))
    if args.panoptic:
        panoptic()
    if args.explain:
        explain()
    if args.cues:
        cues()


if __name__ == "__main__":
    main()
