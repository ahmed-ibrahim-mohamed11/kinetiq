#!/usr/bin/env python3
"""Phase 2 DoD: leave-one-subject-out results for the trained components (ridge phase model +
explicit-duration HSMM, frozen Phase 2.7 config) on the DEVELOPMENT subjects.

Folds = the 15 linked-subject components of Mendeley TRAIN+VAL (`component` column of
ai/data/mendeley_subject_split.csv: duplicate-linked subjects are never split across train and
test). Each fold refits the system on every other development component's back-exercise videos
(same masked supervision, same frozen cfg) and scores the held-out component's back videos with
protocol v2, exactly as harness.suite_mendeley scores VAL. Measurement only: nothing is tuned.
The Mendeley TEST split stays locked: no TEST video is loaded into any fold (asserted).

Caveat (stated in the output): the frozen cfg was selected on VAL in Phase 2.7 and every fold's
training set overlaps the data that selection used, so these numbers are mildly optimistic for
VAL components; they are per-subject stability evidence, not a replacement for the locked TEST.

  python ai/research/phase2/loso_dev.py      -> ai/outputs/phase2/loso_dev_{components,views}.csv + summary
"""
from __future__ import annotations

import json
import os
import sys
import time

import numpy as np
import pandas as pd

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, REPO_ROOT)

from ai.evaluation import harness as H  # noqa: E402

OUT = os.path.join(REPO_ROOT, "ai", "outputs", "phase2")


def main():
    os.makedirs(OUT, exist_ok=True)
    split = pd.read_csv(os.path.join(REPO_ROOT, "ai", "data", "mendeley_subject_split.csv"))
    comp_of = dict(zip(split.subject_id, split.component))
    dev = split[split.split.isin(["train", "val"])]
    components = sorted(dev.component.unique())
    vids = [v for v in H.mendeley_videos().values() if v["exercise"] == "back"]
    assert all(comp_of[v["subject"]] is not None for v in vids)
    dev_vids = [v for v in vids if v["split"] in ("train", "val")]
    assert not any(v["split"] == "test" for v in dev_vids)
    cfg = H.frozen_cfg()
    rows_all, comp_rows = [], []
    t0 = time.time()
    for c in components:
        held = [v for v in dev_vids if comp_of[v["subject"]] == c]
        train = [v for v in dev_vids if comp_of[v["subject"]] != c and v["gt"]]
        assert held and not any(comp_of[v["subject"]] == c for v in train)
        system = H.fit_system(cfg=cfg, train=train, name=f"loso_{c}")
        rows = []
        for v in held:
            res = system.predict(v)
            pred = [dict(start=r["start"], pull=r["pull"], end=r["end"]) for r in res["reps"]]
            rows += H._eval_item(v["name"], pred, v["gt"], v["gt_uncertain"], v["fps"], "v2",
                                 extra=dict(component=c, subject=v["subject"], split=v["split"],
                                            view=v["view"], form=v["form"]))
        s = H._score(rows)
        comp_rows.append(dict(component=c, split=held[0]["split"],
                              subjects=",".join(sorted({v["subject"] for v in held})),
                              n_videos=len(held), n_train_videos=len(train), n_gt=s["iou"]["n_gt"],
                              iou_tp=s["iou"]["tp"], iou_fp=s["iou"]["fp"], iou_fn=s["iou"]["fn"],
                              iou_f1=s["iou"]["f1"], pull_f1=s["pull"]["f1"],
                              pull_mae_s=s["pull"]["pull_mae_s"]))
        rows_all += rows
        print(f"{c} ({held[0]['split']}): {len(held)} videos, IoU F1 {s['iou']['f1']} "
              f"({s['iou']['tp']}/{s['iou']['fp']}/{s['iou']['fn']}), pull F1 {s['pull']['f1']}  "
              f"[{time.time() - t0:.0f} s]", flush=True)
    comp_df = pd.DataFrame(comp_rows)
    comp_df.to_csv(os.path.join(OUT, "loso_dev_components.csv"), index=False)
    pooled = H._score(rows_all)
    by_view = []
    for view in sorted({r["view"] for r in rows_all}):
        sv = H._score([r for r in rows_all if r["view"] == view])
        by_view.append(dict(view=view, n_gt=sv["iou"]["n_gt"], iou_f1=sv["iou"]["f1"],
                            iou_tp_fp_fn=f"{sv['iou']['tp']}/{sv['iou']['fp']}/{sv['iou']['fn']}",
                            pull_f1=sv["pull"]["f1"]))
    pd.DataFrame(by_view).to_csv(os.path.join(OUT, "loso_dev_views.csv"), index=False)
    f1 = comp_df.iou_f1.to_numpy()
    summary = dict(
        folds=len(components), protocol="v2", exercise="back", splits_used=["train", "val"],
        test_split_touched=False,
        pooled=dict(iou=pooled["iou"], pull=pooled["pull"]),
        per_component_iou_f1=dict(median=float(np.median(f1)), min=float(f1.min()), max=float(f1.max()),
                                  n_below_0_80=int((f1 < 0.80).sum()), n_below_0_70=int((f1 < 0.70).sum())),
        caveat="cfg selected on VAL in Phase 2.7; per-subject stability evidence, not a TEST replacement")
    json.dump(summary, open(os.path.join(OUT, "loso_dev_summary.json"), "w"), indent=1)
    print(json.dumps(summary, indent=1))
    print(pd.DataFrame(by_view).to_string(index=False))


if __name__ == "__main__":
    main()
