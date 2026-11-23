"""Evaluation harness: ONE protocol for every BEFORE/AFTER comparison (global rule 1).

Suites (each returns pooled metrics + per-item rows; nothing is written to the frozen artifact
folders):
  mendeley_val     Mendeley BACK, VAL split (5 subjects), 15 fps, protocol v2  -> development set
  internal_sr      6 internal seated-row videos (1 athlete, Domain A), 30 fps   -> dev / CONTAMINATED
                   (used to choose MB-Lite as product primary and to set provisional SR thresholds)
  external         Instagram units (Domain B), 25/30 fps, eval-only             -> stress reference;
                   NEVER used for tuning (owner rule + EXTERNAL_REAL_WORLD_STRESS_TEST.md §8)
  causal           streaming variants of causal_prototype on internal_sr        -> Mode B reference
The Mendeley TEST split is deliberately NOT a suite: it is locked (one run per frozen candidate,
via ai/research/phase27/train_eval.py --unlock-test).

Default system = the frozen product system (MB-Lite poses, Phase 2.7 config_final.json, phase model
+ HSMM refit on Mendeley BACK TRAIN with masked supervision) — byte-identical logic to
phase28/seated_row_transfer.py, external_stress/evaluate_external.py and realtime/causal_prototype.py.
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Callable, Dict, List, Optional

import numpy as np
import pandas as pd

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
for _p in (REPO_ROOT, os.path.join(REPO_ROOT, "ai", "research", "pose_benchmark"),
           os.path.join(REPO_ROOT, "ai", "research", "phase27"),
           os.path.join(REPO_ROOT, "ai", "research", "realtime")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from ai.core.config import Cfg  # noqa: E402
from ai.core.decoder import Decoder, predict_video  # noqa: E402
from ai.core.features import prepare_video  # noqa: E402
from ai.core.phase_model import PhaseModel  # noqa: E402
from ai.evaluation.protocol import evaluate_dontcare, prf, pull_tol_frames  # noqa: E402

OUT_P27 = os.path.join(REPO_ROOT, "ai", "outputs", "phase27")
OUT_P28 = os.path.join(REPO_ROOT, "ai", "outputs", "phase28")
OUT_EXT = os.path.join(REPO_ROOT, "ai", "outputs", "external_stress_test")
SR_GT = os.path.join(REPO_ROOT, "ai", "outputs", "manual_rep_annotations.csv")
EXT_GT = os.path.join(REPO_ROOT, "ai", "data", "external_stress_test", "annotations.csv")
SR_VIDEOS = ["correct_01", "correct_02", "correct_03", "asymmetry_01", "asymmetry_02", "asymmetry_03"]
EXT_UNITS = {   # unit -> (video, lo, hi, fps, segment_id); frozen audit windows
    "ext_02_full": ("ext_02", 0, 800, 30.0, "full"),
    "ext_03_segA": ("ext_03", 237, 330, 25.0, "segA_237_330"),
    "ext_03_segB": ("ext_03", 361, 470, 25.0, "segB_361_470"),
    "ext_08_segB": ("ext_08", 160, 250, 30.0, "segB_160_250"),
    "ext_08_segC": ("ext_08", 310, 435, 30.0, "segC_310_435"),
}


# ----------------------------------------------------------------------------- system
@dataclass
class RepSystem:
    """A rep-segmentation system = config + phase model + decoder (+ optional post-processing).
    Experiments subclass/replace `predict` or `postprocess`; the harness never changes."""
    name: str
    cfg: Cfg
    model: PhaseModel
    dec: Decoder
    postprocess: Optional[Callable[[dict, dict], List[dict]]] = None   # (v, result) -> reps
    meta: dict = field(default_factory=dict)

    def predict(self, v: dict) -> dict:
        res = predict_video(v, self.model, self.dec, self.cfg, None)
        if self.postprocess is not None:
            res["reps"] = self.postprocess(v, res)
        return res


def frozen_cfg(pipeline: str = "motionbert_lite") -> Cfg:
    c = json.load(open(os.path.join(OUT_P27, pipeline, "config_final.json")))["cfg"]
    return Cfg(smooth_hz=float(c["smooth_hz"]),
               detrend_s=None if c["detrend_s"] in (None, "None") else float(c["detrend_s"]),
               dur_weight=float(c["dur_weight"]), dur_sigma_floor=float(c["dur_sigma_floor"]))


@lru_cache(maxsize=4)
def mendeley_videos(pipeline: str = "motionbert_lite") -> dict:
    import train_eval as te
    return te.load_videos(pipeline)


def train_videos(pipeline: str = "motionbert_lite") -> List[dict]:
    return [v for v in mendeley_videos(pipeline).values()
            if v["split"] == "train" and v["exercise"] == "back" and v["gt"]]


def fit_system(pipeline: str = "motionbert_lite", cfg: Optional[Cfg] = None,
               train: Optional[List[dict]] = None, name: str = "frozen") -> RepSystem:
    import train_eval as te
    cfg = cfg or frozen_cfg(pipeline)
    train = train if train is not None else train_videos(pipeline)
    cache: dict = {}
    model = te.fit_model_masked(train, cfg, cache)
    dec = te.fit_decoder_masked(train, te.crossfit_fused_masked(train, cfg, cache), cfg)
    return RepSystem(name=name, cfg=cfg, model=model, dec=dec,
                     meta=dict(pipeline=pipeline, n_train_videos=len(train)))


# ----------------------------------------------------------------------------- scoring
def _score(rows: List[dict]) -> Dict:
    """Pool per-item protocol outputs. MAE is pooled over MATCHED PAIRS (seconds), the canonical
    definition from 2026-10-09 on; legacy docs used per-video means (noted in the manifest)."""
    out = {}
    for rule in ("iou", "pull"):
        rr = [r for r in rows if r["rule"] == rule]
        tp, fp, fn = (int(sum(r[k] for r in rr)) for k in ("tp", "fp", "fn"))
        P, R, F = prf(tp, fp, fn)
        d = dict(tp=tp, fp=fp, fn=fn, n_gt=int(sum(r["n_gt"] for r in rr)),
                 n_pred=int(sum(r["n_pred"] for r in rr)),
                 n_ignored=int(sum(r["n_ignored"] for r in rr)),
                 precision=round(P, 4), recall=round(R, 4), f1=round(F, 4))
        for k in ("start", "pull", "end"):
            errs = [e for r in rr for e in r[f"_{k}_err_s"]]
            d[f"{k}_mae_s"] = round(float(np.mean(errs)), 4) if errs else None
        d["abs_count_error_mean"] = round(float(np.mean(
            [abs((r["tp"] + r["fp"]) - r["n_gt"]) for r in rr])), 4) if rr else None
        out[rule] = d
    return out


def _eval_item(item: str, pred, gtc, gtu, fps, protocol="v2", extra=None) -> List[dict]:
    rows = []
    tol = pull_tol_frames(fps)
    for rule in ("iou", "pull"):
        m = evaluate_dontcare(pred, gtc, gtu, rule, protocol, tol)
        row = dict(item=item, rule=rule, protocol=protocol, fps=fps, pull_tol_f=tol,
                   **{k: v for k, v in m.items() if k != "matched"})
        for k in ("start", "pull", "end"):
            row[f"_{k}_err_s"] = [abs(pred[i][k] - gtc[j][k]) / fps for i, j in m["matched"]]
        row.update(extra or {})
        rows.append(row)
    return rows


# ----------------------------------------------------------------------------- suites
def load_internal_sr():
    gt = pd.read_csv(SR_GT)
    gt["video"] = gt.video.str.replace(".mp4", "", regex=False)
    items = {}
    for name in SR_VIDEOS:
        pose = np.load(os.path.join(OUT_P28, f"pose_motionbert_lite_{name}.npy"))
        conf = np.load(os.path.join(OUT_P28, f"pose_motionbert_lite_{name}_conf.npy"))
        g = [dict(start=int(r.start_frame), pull=int(r.pull_frame), end=int(r.end_frame))
             for r in gt[gt.video == name].itertuples()]
        items[name] = dict(pose=pose, conf=conf, fps=30.0, gtc=g, gtu=[])
    return items


def load_external():
    """Units are scored in UNIT-LOCAL frames (GT shifted by the window start). Matching, IoU and
    the edge rule are translation-invariant, so metrics equal the absolute-frame evaluation in
    evaluate_external.py; local frames keep time-warp/robustness transforms simple."""
    ann = pd.read_csv(EXT_GT)
    items = {}
    for unit, (vid, lo, hi, fps, seg) in EXT_UNITS.items():
        g = ann[(ann.video_id == vid) & (ann.segment_id == seg)]
        mk = lambda df: [dict(start=int(r.start_frame) - lo, pull=int(r.pull_frame) - lo,
                              end=int(r.end_frame) - lo) for r in df.itertuples()]
        items[unit] = dict(pose=np.load(os.path.join(OUT_EXT, f"pose_{vid}.npy"))[lo:hi + 1],
                           conf=np.load(os.path.join(OUT_EXT, f"pose_{vid}_conf.npy"))[lo:hi + 1],
                           fps=fps, gtc=mk(g[g.gt_tier == "certain"]),
                           gtu=mk(g[g.gt_tier == "uncertain"]))
    return items


def run_pose_suite(system: RepSystem, items: dict, transform: Optional[Callable] = None,
                   protocol: str = "v2") -> Dict:
    """items: name -> dict(pose, conf, fps, gtc, gtu). `transform(name, pose, conf, fps, gtc, gtu)`
    -> (pose, conf, fps, gtc, gtu) lets robustness/tempo suites perturb inputs (and map GT frames
    consistently) while the scorer stays identical."""
    rows, preds = [], {}
    for name, it in items.items():
        pose, conf, fps = it["pose"], it["conf"], it["fps"]
        gtc, gtu = it["gtc"], it["gtu"]
        if transform is not None:
            pose, conf, fps, gtc, gtu = transform(name, pose, conf, fps, gtc, gtu)
        v = prepare_video(name, pose, fps, conf=conf)
        res = system.predict(v)
        pred = [dict(start=r["start"], pull=r["pull"], end=r["end"]) for r in res["reps"]]
        rows += _eval_item(name, pred, gtc, gtu, fps, protocol,
                           extra=dict(tempo_s=res.get("tempo_s")))
        preds[name] = pred
    return dict(pooled=_score(rows), rows=[{k: v for k, v in r.items() if not k.startswith("_")}
                                            for r in rows], predictions=preds)


def suite_internal_sr(system: RepSystem, transform=None) -> Dict:
    return run_pose_suite(system, load_internal_sr(), transform)


def suite_external(system: RepSystem, transform=None) -> Dict:
    return run_pose_suite(system, load_external(), transform)


def suite_mendeley(system: RepSystem, split: str = "val", exercise: str = "back",
                   pipeline: str = "motionbert_lite") -> Dict:
    if split == "test":
        raise PermissionError("Mendeley TEST is locked; use train_eval.py --unlock-test for the "
                              "single frozen-candidate run.")
    rows, preds = [], {}
    for v in mendeley_videos(pipeline).values():
        if v["split"] != split or v["exercise"] != exercise:
            continue
        res = system.predict(v)
        pred = [dict(start=r["start"], pull=r["pull"], end=r["end"]) for r in res["reps"]]
        rows += _eval_item(v["name"], pred, v["gt"], v["gt_uncertain"], v["fps"], "v2",
                           extra=dict(subject=v["subject"], view=v["view"], form=v["form"]))
        preds[v["name"]] = pred
    return dict(pooled=_score(rows), rows=[{k: v for k, v in r.items() if not k.startswith("_")}
                                            for r in rows], predictions=preds)


def suite_causal(system: RepSystem, variants=("V2_quality",)) -> Dict:
    """Mode B accuracy on internal_sr via the frozen streaming simulator (timing NOT reported here:
    timing belongs to the Phase 4 benchmark harness, run on an idle machine)."""
    import causal_prototype as cp  # noqa: F401  (ai/research/realtime)
    from ai.core.form_indicators import thresholds_path
    thr = json.load(open(thresholds_path("seated_row")))["thresholds"]
    items = load_internal_sr()
    out = {}
    for vn in variants:
        rows = []
        for name in SR_VIDEOS:
            reps, _tm, lat_s, _T = cp.stream_video(name, cp.VARIANTS[vn], system.cfg, system.model,
                                                   system.dec, thr)
            rows += _eval_item(name, reps, items[name]["gtc"], [], 30.0, "v2",
                               extra=dict(median_event_latency_s=float(np.median(lat_s))
                                          if lat_s else None))
        out[vn] = dict(pooled=_score(rows),
                       rows=[{k: v for k, v in r.items() if not k.startswith("_")} for r in rows])
    return out


def headline(results: Dict) -> Dict[str, float]:
    """The baseline numbers every change is compared against (global rule 1)."""
    h = {}
    if "mendeley_val" in results:
        h["mendeley_val_iou_f1"] = results["mendeley_val"]["pooled"]["iou"]["f1"]
    if "internal_sr" in results:
        h["internal_sr_iou_f1"] = results["internal_sr"]["pooled"]["iou"]["f1"]
        h["internal_sr_pull_f1"] = results["internal_sr"]["pooled"]["pull"]["f1"]
    if "external" in results:
        h["external_iou_f1"] = results["external"]["pooled"]["iou"]["f1"]
        h["external_pull_f1"] = results["external"]["pooled"]["pull"]["f1"]
    if "causal" in results:
        for vn, r in results["causal"].items():
            h[f"causal_{vn}_iou_f1"] = r["pooled"]["iou"]["f1"]
    return h


def run_all(system: Optional[RepSystem] = None, suites=("mendeley_val", "internal_sr", "external"),
            causal_variants=("V2_quality",)) -> Dict:
    system = system or fit_system()
    res = {}
    if "mendeley_val" in suites:
        res["mendeley_val"] = suite_mendeley(system, "val")
    if "internal_sr" in suites:
        res["internal_sr"] = suite_internal_sr(system)
    if "external" in suites:
        res["external"] = suite_external(system)
    if "causal" in suites:
        res["causal"] = suite_causal(system, causal_variants)
    res["headline"] = headline(res)
    res["system"] = dict(name=system.name, cfg=system.cfg.__dict__, meta=system.meta)
    return res
