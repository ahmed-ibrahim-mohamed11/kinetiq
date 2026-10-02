#!/usr/bin/env python3
"""
Single evaluator for seated-row repetition segmentation (OFFLINE_EVAL).

Compares predicted reps (START / PULL / END frame per rep, per video) with the manual ground truth
and reports detection (TP / FP / FN, precision / recall / F1) and timing (START / PULL / END mean
absolute error in frames) per video and pooled over all videos.

Matching rules (one-to-one, greedy, deterministic):
  pull  |pred.pull - gt.pull| <= tol frames               (project default, tol = 15)
  iou   temporal IoU of [start, end] >= thr               (rule used inside rep_segmentation_v1/v2, thr = 0.5;
                                                           the implementation is copied from v2 so numbers agree)
Several rules can be requested at once (--match pull iou); each is stored under its own key in the JSON.

Guarantees
  * the GT 'label' column (correct / asymmetry) is never read;
  * nothing assumes a fixed rep count;
  * frames are compared exactly as given — the GT CSV and the legacy 2D pipeline are both 1-based
    (see docs/AI_EVALUATION.md "frame convention");
  * videos present in GT but missing from the predictions count as all-FN (never silently dropped);
    videos present in the predictions but absent from GT are an error unless --ignore_extra_videos.

For regression tests compare the "results" object only; "created_utc" / "git_commit" change per run.

Usage
  python ai/research/eval_rep_segmentation.py --gt ai/outputs/manual_rep_annotations.csv \
      --pred <predictions.csv> --name <run_name> --out ai/outputs/baselines/<run_name>.json [--match pull iou]

predictions.csv columns: video, start_frame|start, pull_frame|pull, end_frame|end  ('rep' optional, used for ordering).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from typing import Dict, List, Tuple

import pandas as pd

KEYS = ("start", "pull", "end")
Rep = Dict[str, int]
Pairs = List[Tuple[int, int]]


# ----------------------------------------------------------------------------- IO
def _stem(name: str) -> str:
    return re.sub(r"\.[A-Za-z0-9]+$", "", str(name))


def _md5(path: str) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        h.update(f.read())
    return h.hexdigest()


def load_reps(path: str, kind: str) -> Dict[str, List[Rep]]:
    """kind='gt' requires start < pull < end; kind='pred' requires start <= pull <= end."""
    df = pd.read_csv(path)
    cols = {c.lower(): c for c in df.columns}

    def pick(*names):
        for n in names:
            if n in cols:
                return cols[n]
        raise ValueError(f"{path}: expected one of {names}; columns are {list(df.columns)}")

    v, s, p, e = pick("video"), pick("start_frame", "start"), pick("pull_frame", "pull"), pick("end_frame", "end")
    df = df[[v, s, p, e] + ([cols["rep"]] if "rep" in cols else [])].copy()
    df["_video"] = df[v].map(_stem)
    for c in (s, p, e):
        if df[c].isna().any():
            raise ValueError(f"{path}: NaN in column {c}")
    out: Dict[str, List[Rep]] = {}
    for name, g in df.groupby("_video", sort=True):
        g = g.sort_values(cols["rep"] if "rep" in cols else s)
        reps = [dict(start=int(a), pull=int(b), end=int(c)) for a, b, c in g[[s, p, e]].to_numpy()]
        for r in reps:
            ok = r["start"] < r["pull"] < r["end"] if kind == "gt" else r["start"] <= r["pull"] <= r["end"]
            if not ok:
                raise ValueError(f"{kind} {name}: rep not ordered start/pull/end: {r}")
        out[name] = reps
    return out


# ----------------------------------------------------------------------------- matching
def _iou(a, b) -> float:
    inter = max(0, min(a[1], b[1]) - max(a[0], b[0]))
    union = max(a[1], b[1]) - min(a[0], b[0])
    return inter / union if union > 0 else 0.0


def match_iou(pred: List[Rep], gt: List[Rep], thr: float) -> Pairs:
    """Verbatim copy of rep_segmentation_v2_hsmm.match_reps: greedy one-to-one on IoU of [start, end]."""
    c = sorted(((_iou((p["start"], p["end"]), (g["start"], g["end"])), i, j)
                for i, p in enumerate(pred) for j, g in enumerate(gt)), reverse=True)
    up, ug, pairs = set(), set(), []
    for iou, i, j in c:
        if iou < thr or i in up or j in ug:
            continue
        up.add(i), ug.add(j), pairs.append((i, j))
    return pairs


def match_pull(pred: List[Rep], gt: List[Rep], tol: int) -> Pairs:
    """Greedy one-to-one on |pull difference| (closest pairs first), accepted only if <= tol frames."""
    c = sorted((abs(p["pull"] - g["pull"]), i, j) for i, p in enumerate(pred) for j, g in enumerate(gt))
    up, ug, pairs = set(), set(), []
    for d, i, j in c:
        if d > tol or i in up or j in ug:
            continue
        up.add(i), ug.add(j), pairs.append((i, j))
    return pairs


# ----------------------------------------------------------------------------- metrics
def _prf(tp: int, fp: int, fn: int):
    P = tp / (tp + fp) if tp + fp else 0.0
    R = tp / (tp + fn) if tp + fn else 0.0
    F = 2 * P * R / (P + R) if P + R else 0.0
    return P, R, F


def _mae(err: List[int]):
    return sum(abs(x) for x in err) / len(err) if err else None


def evaluate_video(pred: List[Rep], gt: List[Rep], pairs: Pairs) -> dict:
    tp, fp, fn = len(pairs), len(pred) - len(pairs), len(gt) - len(pairs)
    P, R, F = _prf(tp, fp, fn)
    err = {k: [pred[i][k] - gt[j][k] for i, j in pairs] for k in KEYS}          # signed, pred - gt
    dur = [gt[j]["end"] - gt[j]["start"] for _, j in pairs]
    pull_frac = [abs(e) / d for e, d in zip(err["pull"], dur)]
    return dict(n_gt=len(gt), n_pred=len(pred), tp=tp, fp=fp, fn=fn,
                precision=P, recall=R, f1=F,
                mae_frames={k: _mae(err[k]) for k in KEYS},
                pull_err_frac=(sum(pull_frac) / len(pull_frac) if pull_frac else None),
                signed_err_frames=err,
                matched_pairs=[[i, j] for i, j in pairs])


def pool(per_video: Dict[str, dict]) -> dict:
    tp = sum(m["tp"] for m in per_video.values())
    fp = sum(m["fp"] for m in per_video.values())
    fn = sum(m["fn"] for m in per_video.values())
    P, R, F = _prf(tp, fp, fn)
    err = {k: [x for m in per_video.values() for x in m["signed_err_frames"][k]] for k in KEYS}
    return dict(n_videos=len(per_video),
                n_gt=sum(m["n_gt"] for m in per_video.values()),
                n_pred=sum(m["n_pred"] for m in per_video.values()),
                tp=tp, fp=fp, fn=fn, precision=P, recall=R, f1=F,
                mae_frames={k: _mae(err[k]) for k in KEYS},
                videos_with_exact_count=sum(m["n_pred"] == m["n_gt"] for m in per_video.values()))


def run_rule(rule: str, pred: Dict[str, List[Rep]], gt: Dict[str, List[Rep]], tol: int, iou_thr: float) -> dict:
    per_video = {}
    for name in sorted(gt):
        p, g = pred.get(name, []), gt[name]
        pairs = match_pull(p, g, tol) if rule == "pull" else match_iou(p, g, iou_thr)
        per_video[name] = evaluate_video(p, g, pairs)
    params = dict(tol_frames=tol) if rule == "pull" else dict(iou_thr=iou_thr)
    return dict(rule=rule, params=params, pooled=pool(per_video), per_video=per_video)


# ----------------------------------------------------------------------------- reporting
def _f(x, w=8, d=2):
    return f"{'-':>{w}}" if x is None else f"{x:>{w}.{d}f}"


def print_tables(res: dict) -> None:
    desc = (f"one-to-one PULL within +/-{res['params']['tol_frames']} frames" if res["rule"] == "pull"
            else f"one-to-one temporal IoU >= {res['params']['iou_thr']} on [start, end]")
    print(f"\nDETECTION  (match = {desc})")
    print(f"{'Video':<14}{'GT':>5}{'Pred':>6}{'TP':>4}{'FP':>4}{'FN':>4}{'Prec':>8}{'Rec':>8}{'F1':>8}"
          f"{'StartMAE':>10}{'PullMAE':>9}{'EndMAE':>8}")
    for name, m in res["per_video"].items():
        print(f"{name:<14}{m['n_gt']:>5}{m['n_pred']:>6}{m['tp']:>4}{m['fp']:>4}{m['fn']:>4}"
              f"{_f(m['precision'])}{_f(m['recall'])}{_f(m['f1'])}"
              f"{_f(m['mae_frames']['start'], 10, 1)}{_f(m['mae_frames']['pull'], 9, 1)}{_f(m['mae_frames']['end'], 8, 1)}")
    m = res["pooled"]
    print(f"{'ALL (pooled)':<14}{m['n_gt']:>5}{m['n_pred']:>6}{m['tp']:>4}{m['fp']:>4}{m['fn']:>4}"
          f"{_f(m['precision'])}{_f(m['recall'])}{_f(m['f1'])}"
          f"{_f(m['mae_frames']['start'], 10, 1)}{_f(m['mae_frames']['pull'], 9, 1)}{_f(m['mae_frames']['end'], 8, 1)}")
    print(f"(videos where predicted count == GT count: {m['videos_with_exact_count']}/{m['n_videos']} - informational only)")


def git_commit() -> str | None:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                              check=True, cwd=os.path.dirname(os.path.abspath(__file__))).stdout.strip()
    except Exception:
        return None


# ----------------------------------------------------------------------------- main
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--gt", required=True, help="manual_rep_annotations.csv (label column is ignored)")
    ap.add_argument("--pred", required=True, help="predictions CSV: video, start[_frame], pull[_frame], end[_frame]")
    ap.add_argument("--name", required=True, help="run name stored in the JSON")
    ap.add_argument("--out", default=None, help="JSON output path (default: print only)")
    ap.add_argument("--match", nargs="+", choices=["pull", "iou"], default=["pull"])
    ap.add_argument("--tol", type=int, default=15, help="pull rule: max |pull difference| in frames")
    ap.add_argument("--iou", type=float, default=0.5, help="iou rule: min temporal IoU on [start, end]")
    ap.add_argument("--ignore_extra_videos", action="store_true",
                    help="allow predictions for videos that have no GT (they are dropped with a warning)")
    args = ap.parse_args(argv)

    gt, pred = load_reps(args.gt, "gt"), load_reps(args.pred, "pred")
    extra = sorted(set(pred) - set(gt))
    if extra:
        msg = f"predictions contain videos without GT: {extra}"
        if not args.ignore_extra_videos:
            raise SystemExit("ERROR: " + msg + " (use --ignore_extra_videos to drop them)")
        print("WARNING: " + msg + " — dropped", file=sys.stderr)
    for name in sorted(set(gt) - set(pred)):
        print(f"WARNING: no predictions for {name}: counted as {len(gt[name])} FN", file=sys.stderr)

    results = {}
    for rule in args.match:
        res = run_rule(rule, pred, gt, args.tol, args.iou)
        key = f"pull_tol{args.tol}" if rule == "pull" else f"iou_{args.iou:.2f}"
        results[key] = res
        print_tables(res)

    report = dict(
        name=args.name,
        created_utc=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        git_commit=git_commit(),
        evaluator="ai/research/eval_rep_segmentation.py",
        frame_convention="frames compared as given; GT CSV and legacy 2D predictions are 1-based",
        inputs=dict(gt=dict(path=args.gt, md5=_md5(args.gt), n_videos=len(gt), n_reps=sum(map(len, gt.values()))),
                    pred=dict(path=args.pred, md5=_md5(args.pred), n_videos=len(pred), n_reps=sum(map(len, pred.values())))),
        results=results,
    )
    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=1)
        print(f"\nSaved {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
