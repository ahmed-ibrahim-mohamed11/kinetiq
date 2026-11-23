"""Rep-matching protocol v1/v2 as a PURE function (explicit pull tolerance, no module state).

Logic is verbatim from ai/research/phase27/train_eval.py (`_iou`, `_match`, `evaluate_dontcare`);
the only change is that the pull tolerance is an argument instead of the module global
`train_eval.PULL_TOL_F`, which callers used to monkey-patch per fps. Equivalence with the
frozen implementation is locked by ai/tests/test_evaluation_protocol.py.

Protocol v2 (primary since 2026-10-05): one-to-one greedy matching to CERTAIN reps; unmatched
predictions whose pull lies outside the annotated span are ignored (edge don't-care); remaining
unmatched predictions may each absorb one UNCERTAIN rep (pull within tol OR IoU >= 0.25).
"""
from __future__ import annotations

from typing import Dict, List, Sequence

import numpy as np


def iou(a, b) -> float:
    inter = max(0, min(a[1], b[1]) - max(a[0], b[0]))
    union = max(a[1], b[1]) - min(a[0], b[0])
    return inter / union if union > 0 else 0.0


def match(pred_idx, gt: Sequence[dict], pred: Sequence[dict], rule: str, pull_tol_f: int):
    if rule == "iou":
        c = sorted(((iou((pred[i]["start"], pred[i]["end"]), (g["start"], g["end"])), i, j)
                    for i in pred_idx for j, g in enumerate(gt)), reverse=True)
        ok = lambda s: s >= 0.5
    elif rule == "pull":
        c = sorted(((-abs(pred[i]["pull"] - g["pull"]), i, j)
                    for i in pred_idx for j, g in enumerate(gt)), reverse=True)
        ok = lambda s: -s <= pull_tol_f
    else:
        raise ValueError(f"unknown matching rule {rule!r} (expected 'iou' or 'pull')")
    up, ug, pairs = set(), set(), []
    for s, i, j in c:
        if not ok(s) or i in up or j in ug:
            continue
        up.add(i), ug.add(j), pairs.append((i, j))
    return pairs


def evaluate_dontcare(pred: List[dict], gt_certain: List[dict], gt_uncertain: List[dict],
                      rule: str, protocol: str, pull_tol_f: int) -> Dict:
    if protocol not in ("v1", "v2"):
        raise ValueError(f"unknown protocol {protocol!r}")
    pairs = match(range(len(pred)), gt_certain, pred, rule, pull_tol_f)
    matched_p = {i for i, _ in pairs}
    rest = [i for i in range(len(pred)) if i not in matched_p]
    if protocol == "v1":
        ignored = {i for i, _ in match(rest, gt_uncertain, pred, rule, pull_tol_f)}
    else:
        ignored = set()
        allb = gt_certain + gt_uncertain
        if allb:
            first = min(g["start"] for g in allb)
            last = max(g["end"] for g in allb)
            ignored |= {i for i in rest if pred[i]["pull"] < first or pred[i]["pull"] > last}
        rest2 = [i for i in rest if i not in ignored]
        used = set()
        cands = sorted((max(iou((pred[i]["start"], pred[i]["end"]), (g["start"], g["end"])),
                            1.0 - abs(pred[i]["pull"] - g["pull"]) / (10 * pull_tol_f)), i, j)
                       for i in rest2 for j, g in enumerate(gt_uncertain))
        for s, i, j in sorted(cands, reverse=True):
            if i in ignored or j in used:
                continue
            g = gt_uncertain[j]
            ov = iou((pred[i]["start"], pred[i]["end"]), (g["start"], g["end"]))
            if ov >= 0.25 or abs(pred[i]["pull"] - g["pull"]) <= pull_tol_f:
                ignored.add(i)
                used.add(j)
    fp_idx = [i for i in rest if i not in ignored]
    tp, fn = len(pairs), len(gt_certain) - len(pairs)
    m = dict(n_gt=len(gt_certain), n_unc=len(gt_uncertain), n_pred=len(pred),
             tp=tp, fp=len(fp_idx), fn=fn, n_ignored=len(ignored),
             matched=[(i, j) for i, j in pairs])
    for k in ("start", "pull", "end"):
        errs = [abs(pred[i][k] - gt_certain[j][k]) for i, j in pairs]
        m[f"{k}_mae_frames"] = float(np.mean(errs)) if errs else np.nan
    return m


def prf(tp: int, fp: int, fn: int):
    P = tp / max(tp + fp, 1)
    R = tp / max(tp + fn, 1)
    return P, R, 2 * P * R / max(P + R, 1e-9)


def pull_tol_frames(fps: float, tol_s: float = 8 / 15.0) -> int:
    """Seconds-defined pull tolerance (0.533 s) -> frames at this video's fps (8 @15, 16 @30, 13 @25)."""
    return int(round(tol_s * fps))
