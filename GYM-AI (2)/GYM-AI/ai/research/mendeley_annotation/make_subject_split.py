#!/usr/bin/env python3
"""
Phase 2.6: build and FREEZE the subject-wise TRAIN/VAL/TEST split.

Rules (owner spec):
  * split unit = connected component of subjects over the duplicate-file graph
    (any two subjects appearing in one MD5-duplicate group are linked, regardless of exercise,
    because file identity/subject labels are unreliable inside a group);
  * no subject (or linked group) crosses splits; all views/forms of a subject stay together;
  * deterministic greedy balance: components sorted by (reps desc, name) are assigned to the
    split whose rep deficit vs target (train 60% / val 20% / test 20%) is largest, with
    val and test each required to end up with >= 4 subjects.
Once committed, ai/data/mendeley_subject_split.csv is FROZEN for Phase 2.7+.
"""
from __future__ import annotations

import json
import os
import re
import sys

import pandas as pd

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
ANN = os.path.join(REPO_ROOT, "ai", "data", "mendeley_rep_annotations_expanded.csv")
DUPS = os.path.join(REPO_ROOT, "ai", "outputs", "mendeley_duplicate_groups.json")
OUT = os.path.join(REPO_ROOT, "ai", "data", "mendeley_subject_split.csv")
TARGETS = {"train": 0.60, "val": 0.20, "test": 0.20}
MIN_SUBJ = {"val": 4, "test": 4}


def components(subjects: set[str]) -> dict[str, str]:
    parent = {s: s for s in subjects}
    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)
    for group in json.load(open(DUPS)):
        subs = {m.group(0) for v in group if (m := re.match(r"subject_\d+", v))}
        subs = [s for s in subs if s in subjects]
        for a in subs[1:]:
            union(subs[0], a)
    return {s: find(s) for s in subjects}


def main():
    ann = pd.read_csv(ANN)
    subjects = set(ann.subject_id.unique())
    comp = components(subjects)
    reps_per_subj = ann.groupby("subject_id").size()
    comp_members: dict[str, list[str]] = {}
    for s, c in comp.items():
        comp_members.setdefault(c, []).append(s)
    comp_rows = sorted(
        ((c, sorted(ms), int(reps_per_subj.reindex(ms).fillna(0).sum())) for c, ms in comp_members.items()),
        key=lambda x: (-x[2], x[0]))
    total = sum(r for _, _, r in comp_rows)
    assigned, counts, nsub = {}, {k: 0 for k in TARGETS}, {k: 0 for k in TARGETS}
    for c, members, reps in comp_rows:
        deficits = {k: TARGETS[k] * total - counts[k] for k in TARGETS}
        for k in ("val", "test"):     # make sure small splits reach their subject minimum
            remaining = len(subjects) - sum(nsub.values())
            if nsub[k] + remaining <= MIN_SUBJ[k]:
                deficits[k] = float("inf")
        k = max(deficits, key=lambda x: (deficits[x], x))
        assigned[c] = k
        counts[k] += reps
        nsub[k] += len(members)
    rows = [dict(subject_id=s, split=assigned[comp[s]], component=comp[s],
                 linked=",".join(sorted(set(comp_members[comp[s]]) - {s})) or "-",
                 n_reps=int(reps_per_subj.get(s, 0)))
            for s in sorted(subjects)]
    df = pd.DataFrame(rows)
    df.to_csv(OUT, index=False)
    print(df.to_string(index=False))
    print("\nreps per split:", {k: int(v) for k, v in counts.items()},
          " subjects:", {k: int(df[df.split == k].subject_id.nunique()) for k in TARGETS})
    print("FROZEN ->", OUT)


if __name__ == "__main__":
    main()
