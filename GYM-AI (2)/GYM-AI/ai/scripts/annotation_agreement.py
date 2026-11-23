#!/usr/bin/env python3
"""Phase 6 annotator agreement in rounds (pre-registration §3.4). The selection and measurement rules
live in the frozen evaluation code (ai/scripts/phase6_evaluate.py: round_seed, select_agreement_subset,
measure_agreement), which the final run uses to recompute every round; this tool only applies them and
keeps the append-only record qa/annotation_agreement.json (schema: ai/evaluation/phase6_dataset.py).

  1. python -m ai.scripts.annotation_agreement --select-subset --salt <value> [--second a2] [--addendum A6-NN]
       after a1 has finished EVERY take and run `annotate_reps --finalize`: checks the data-quality rules,
       then draws round r's subset (deterministic, stratified by view x tempo, disjoint from earlier
       rounds) from an EXTERNAL salt — a value the supervisor sends once a1 reports the annotation
       complete — and a snapshot of a1's files; round >= 2 needs the addendum id of the guide
       clarification (pre-registration §3.4). `--pilot` skips the cohort-level rules (pilot root only).
  2. python -m ai.scripts.annotation_agreement --handoff <folder>
       copies ONLY the subset videos, the manifest and this record to a separate local folder (never
       a1's marks); a2 annotates there: annotate_reps --annotator a2 --root <folder>
  3. python -m ai.scripts.annotation_agreement --collect <folder>
       copies a2's marks for exactly the subset back into the data root
  4. python -m ai.scripts.annotation_agreement --measure
       scores a2 against a1 once; the result is written once and never overwritten
  python -m ai.scripts.annotation_agreement --note "text"    adds a note to the latest round (no names)
  python -m ai.scripts.annotation_agreement --status
Writes qa/annotation_agreement.json and qa/annotation_agreement_round<r>.md (the disagreements, for the
guide addendum if the round fails). Exit 0 = done / accepted, 1 = round not accepted, 2 = refused.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import sys
from typing import Dict, List, Optional

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from ai.evaluation import phase6_dataset as D  # noqa: E402
from ai.scripts import phase6_evaluate as P6  # noqa: E402

FLAG_BOUNDARY_S = 0.20          # matched reps whose boundaries differ by more are listed for the addendum
ADDENDA_FILE = os.path.join(REPO_ROOT, "docs", "PHASE6_ADDENDA.md")


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _path(root: str) -> str:
    return os.path.join(root, *D.AGREEMENT_REPORT.split("/"))


def load_record(root: str) -> dict:
    p = _path(root)
    return json.load(open(p, encoding="utf-8-sig")) if os.path.exists(p) else dict(primary=D.PRIMARY_ANNOTATOR, rounds=[])


def save_record(root: str, rec: dict):
    D.write_json_atomic(_path(root), rec)


def addendum_exists(addendum_id: str, path: Optional[str] = None) -> bool:
    path = path or ADDENDA_FILE
    text = open(path, encoding="utf-8").read() if os.path.exists(path) else ""
    return any(h == addendum_id or h.startswith(addendum_id + " ")
               for h in P6.addenda_headings(text))


def intake_videos(root: str) -> Dict[str, dict]:
    intake, _ = D.load_intake(root)
    return (intake or {}).get("videos", {})


def readiness_problems(root: str, eligible: List[str], pilot: bool) -> List[str]:
    """Before a subset is drawn: a1 finished every take, reps.csv is the current build of a1's files, and
    the data-quality rules hold (a failing take is re-recorded BEFORE the draw, so the round stays valid)."""
    out = []
    for v in eligible:
        m = D.read_sidecar(root, D.PRIMARY_ANNOTATOR, v)
        if not m or m.get("status") != "complete" or m.get("pending_marks"):
            out.append(f"{v}: {D.PRIMARY_ANNOTATOR} has not finished it")
    reps, errs = D.load_final_reps(root)
    out += errs
    key = lambda r: (r["start"], r["pull"], r["end"], r["confidence"])  # noqa: E731
    for v in eligible:
        own = D.read_annotator_csv(root, D.PRIMARY_ANNOTATOR, v)
        if own is None or sorted(map(key, own)) != sorted(map(key, reps.get(v, []))):
            out.append(f"{v}: {D.FINAL_REPS} is not the current build of {D.PRIMARY_ANNOTATOR}'s file (run --finalize)")
    man, _ = D.load_manifest(root)
    out += D.quality_problems(man, reps, eligible)
    if not pilot:
        out += D.cohort_problems(man, eligible) + D.g6_data_problems(man, reps, eligible)
    return out


def select(root: str, second: str, salt: str, addendum: Optional[str], pilot: bool) -> int:
    rec = load_record(root)
    rounds = rec["rounds"]
    if rounds and not rounds[-1].get("result"):
        print(f"REFUSED: round {rounds[-1]['round']} is open (subset drawn, not measured); measure it first.",
              file=sys.stderr)
        return 2
    if rounds and rounds[-1]["result"].get("accepted"):
        print(f"REFUSED: round {rounds[-1]['round']} was accepted; no further round is drawn.", file=sys.stderr)
        return 2
    if rounds and not (addendum and addendum_exists(addendum)):
        print("REFUSED: a new round after a failed one needs --addendum A6-NN naming the dated guide clarification "
              "in docs/PHASE6_ADDENDA.md, and a1's re-review of every take (pre-registration §3.4).", file=sys.stderr)
        return 2
    if not (salt or "").strip():
        print("REFUSED: --salt <value> is required: the external value the supervisor sent for this round.",
              file=sys.stderr)
        return 2
    intake, errs = D.load_intake(root)
    if intake is None or (errs and not pilot):
        print("REFUSED: the intake check must be complete and its cohort check PASS first:\n  " + "\n  ".join(errs),
              file=sys.stderr)
        return 2
    eligible = D.included_takes(root)
    probs = readiness_problems(root, eligible, pilot)
    if probs:
        print("REFUSED: not ready for a round (fix, re-record or finalize first):\n  " + "\n  ".join(probs[:30]),
              file=sys.stderr)
        return 2
    r = len(rounds) + 1
    previous = [v for x in rounds for v in x["subset"]]
    snapshot = {v: D.sha256_file(D.annotation_csv(root, D.PRIMARY_ANNOTATOR, v)) for v in eligible}
    subset = P6.select_agreement_subset(eligible, previous, P6.round_seed(r, salt.strip(), snapshot))
    if not subset:
        print("REFUSED: no takes left for a new round (every take was used in an earlier round). Phase 6 stops "
              "before any model run (pre-registration §3.4).", file=sys.stderr)
        return 2
    rounds.append(dict(round=r, salt=salt.strip(), created_utc=_now(), second=second, addendum=addendum,
                       pilot=pilot, eligible=eligible, primary_snapshot=snapshot, subset=subset, result=None,
                       notes=[]))
    rec["primary"] = D.PRIMARY_ANNOTATOR
    save_record(root, rec)
    print(f"round {r}: {len(subset)} of {len(eligible)} takes for {second} (salt {salt.strip()!r}). "
          f"Next: --handoff <folder>.")
    return 0


def handoff(root: str, dest: str) -> int:
    """Copy only what the second annotator needs into a separate local folder."""
    rec = load_record(root)
    if not rec["rounds"] or rec["rounds"][-1].get("result"):
        print("REFUSED: no open round.", file=sys.stderr)
        return 2
    cloud = D.cloud_sync_root(dest)
    if cloud or os.path.abspath(dest) == os.path.abspath(root):
        print(f"REFUSED: the hand-off folder must be a separate local folder outside {cloud or 'the data root'}.",
              file=sys.stderr)
        return 2
    if os.path.exists(os.path.join(dest, "annotations", D.PRIMARY_ANNOTATOR)):
        print("REFUSED: the hand-off folder contains the primary annotator's marks.", file=sys.stderr)
        return 2
    files = dict(D.find_videos(root))
    for v in rec["rounds"][-1]["subset"]:
        d = os.path.join(dest, "videos", D.parse_video_name(v)["athlete"])
        os.makedirs(d, exist_ok=True)
        shutil.copy2(files[v], os.path.join(d, os.path.basename(files[v])))
    for rel in (D.MANIFEST, D.AGREEMENT_REPORT):
        os.makedirs(os.path.dirname(os.path.join(dest, *rel.split("/"))) or dest, exist_ok=True)
        shutil.copy2(os.path.join(root, *rel.split("/")), os.path.join(dest, *rel.split("/")))
    print(f"hand-off ready in {dest}: {len(rec['rounds'][-1]['subset'])} videos. The second annotator runs "
          f"annotate_reps --annotator {rec['rounds'][-1]['second']} --root \"{dest}\"; then --collect.")
    return 0


def collect(root: str, src: str) -> int:
    rec = load_record(root)
    if not rec["rounds"] or rec["rounds"][-1].get("result"):
        print("REFUSED: no open round.", file=sys.stderr)
        return 2
    rnd = rec["rounds"][-1]
    who = rnd["second"]
    missing = [v for v in rnd["subset"] if not (os.path.exists(D.annotation_csv(src, who, v))
                                                 and os.path.exists(D.annotation_meta(src, who, v)))]
    if missing:
        print(f"REFUSED: {who} has not annotated {len(missing)} subset take(s) in {src}, e.g. {missing[:5]}.",
              file=sys.stderr)
        return 2
    for v in rnd["subset"]:
        for s, d in ((D.annotation_csv(src, who, v), D.annotation_csv(root, who, v)),
                     (D.annotation_meta(src, who, v), D.annotation_meta(root, who, v))):
            if os.path.exists(d) and D.sha256_file(d) != D.sha256_file(s):
                print(f"REFUSED: {d} exists and differs; resolve it by hand (never overwritten).", file=sys.stderr)
                return 2
            os.makedirs(os.path.dirname(d), exist_ok=True)
            shutil.copy2(s, d)
    print(f"collected {who}'s marks for {len(rnd['subset'])} takes. Next: --measure.")
    return 0


def disagreements(root: str, subset: List[str], second: str) -> List[str]:
    """Plain-language list of where the two annotators differ (for the guide addendum)."""
    out = []
    for v in subset:
        ra = D.read_annotator_csv(root, D.PRIMARY_ANNOTATOR, v) or []
        rb = D.read_annotator_csv(root, second, v) or []
        fps = float((D.read_sidecar(root, D.PRIMARY_ANNOTATOR, v) or {}).get("fps") or 30.0)
        s = P6.score_video([dict(start=r["start"], pull=r["pull"], end=r["end"]) for r in rb], ra, fps)
        gtc = [r for r in ra if r["confidence"] != "low"]
        mi, mj = {i for i, _ in s["iou"]["matched"]}, {j for _, j in s["iou"]["matched"]}
        out += [f"{v}: only {D.PRIMARY_ANNOTATOR} has a rep at {r['start']}-{r['end']}" for j, r in enumerate(gtc)
                if j not in mj]
        out += [f"{v}: only {second} has a rep at {r['start']}-{r['end']}" for i, r in enumerate(rb) if i not in mi]
        for i, j in s["iou"]["matched"]:
            d = {k: rb[i][k] - gtc[j][k] for k in ("start", "pull", "end")}
            if any(abs(x) / fps > FLAG_BOUNDARY_S for x in d.values()):
                out.append(f"{v}: rep {gtc[j]['start']}-{gtc[j]['end']}: {second} - {D.PRIMARY_ANNOTATOR} = {d} frames")
            if gtc[j]["confidence"] != rb[i]["confidence"]:
                out.append(f"{v}: rep {gtc[j]['start']}-{gtc[j]['end']}: tier {gtc[j]['confidence']} vs "
                           f"{rb[i]['confidence']}")
    return out


def measure(root: str) -> int:
    rec = load_record(root)
    if not rec["rounds"] or rec["rounds"][-1].get("result"):
        print("REFUSED: no open round (every result is written once).", file=sys.stderr)
        return 2
    rnd = rec["rounds"][-1]
    res = P6.measure_agreement(root, rnd["subset"], len(rnd["eligible"]), rnd["second"], intake_videos(root))
    if not res["complete"]:
        print("REFUSED: the subset is not complete and intact for both annotators:\n  " + "\n  ".join(res["problems"]),
              file=sys.stderr)
        return 2
    rnd["result"] = dict({k: v for k, v in res.items() if k != "complete"}, measured_utc=_now())
    save_record(root, rec)
    c = res["checks"]
    L = [f"# Agreement round {rnd['round']}: {rnd['second']} vs {D.PRIMARY_ANNOTATOR} (reference)", "",
         f"**{'ACCEPTED' if res['accepted'] else 'NOT ACCEPTED'}** (pre-registration §3.4)", "",
         "| check | ok | value |", "|---|---|---|"]
    L += [f"| {k} | {'yes' if x['ok'] else 'NO'} | {json.dumps({a: b for a, b in x.items() if a != 'ok'})} |"
          for k, x in c.items()]
    lc = res["primary_low_marked_certain"]
    L += ["", f"Tier agreement over ALL of {D.PRIMARY_ANNOTATOR}'s reps ({D.PRIMARY_ANNOTATOR}->{rnd['second']}): "
          f"{json.dumps(res['tier_agreement'])}; {D.PRIMARY_ANNOTATOR}'s `low` reps that {rnd['second']} marked certain: "
          f"{lc['n']} of {lc['of']}", "", "## Where the annotators differ", ""]
    L += [f"- {x}" for x in disagreements(root, rnd["subset"], rnd["second"])] or ["- nowhere"]
    if not res["accepted"]:
        L += ["", "Not accepted: write a dated addendum with clarifying examples (docs/PHASE6_ADDENDA.md; never a "
              f"new definition or tier), have {D.PRIMARY_ANNOTATOR} re-review EVERY take, run --finalize, then draw a "
              "new round with --select-subset --salt <new value> --addendum A6-NN."]
    with open(os.path.join(root, "qa", f"annotation_agreement_round{rnd['round']}.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    print("\n".join(L[:4 + len(c) + 2]))
    return 0 if res["accepted"] else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=None, help="data root (default: %%LOCALAPPDATA%%\\gym-ai\\phase6)")
    ap.add_argument("--select-subset", action="store_true")
    ap.add_argument("--salt", default=None, help="the external value for this round (from the supervisor)")
    ap.add_argument("--second", default="a2", help="the second annotator's pseudonymous id (default a2)")
    ap.add_argument("--addendum", default=None, help="round >= 2: the addendum id (A6-NN) of the guide clarification")
    ap.add_argument("--pilot", action="store_true", help="pilot root only: skip the cohort-level rules")
    ap.add_argument("--handoff", default=None, metavar="FOLDER")
    ap.add_argument("--collect", default=None, metavar="FOLDER")
    ap.add_argument("--measure", action="store_true")
    ap.add_argument("--note", default=None, help="a note for the latest round (what happened; never a name)")
    ap.add_argument("--status", action="store_true")
    a = ap.parse_args(argv)
    root = a.root or D.default_root()
    cloud = D.cloud_sync_root(root)
    if cloud:
        print(f"REFUSED: {root} is inside the cloud-synced folder {cloud} (pre-registration §2.1).", file=sys.stderr)
        return 2
    if not D.ANNOTATOR_RE.match(a.second) or a.second == D.PRIMARY_ANNOTATOR:
        print(f"--second must be a pseudonymous id other than {D.PRIMARY_ANNOTATOR} (a2, a3, ...)", file=sys.stderr)
        return 2
    if a.select_subset:
        return select(root, a.second, a.salt or "", a.addendum, a.pilot)
    if a.handoff:
        return handoff(root, a.handoff)
    if a.collect:
        return collect(root, a.collect)
    if a.measure:
        return measure(root)
    rec = load_record(root)
    if a.note:
        if not rec["rounds"]:
            print("REFUSED: no round yet.", file=sys.stderr)
            return 2
        rec["rounds"][-1]["notes"].append(f"{_now()}: {a.note}")
        save_record(root, rec)
        print(f"note added to round {rec['rounds'][-1]['round']}")
        return 0
    for r in rec["rounds"]:
        res = r.get("result")
        print(f"round {r['round']}: {len(r['subset'])} takes, second {r['second']}, salt {r.get('salt')!r}, "
              + ("open" if not res else f"{'ACCEPTED' if res['accepted'] else 'not accepted'} "
                 f"(IoU F1 {res['checks']['iou_f1']['value']}, pull MAE {res['checks']['pull_mae_s']['value']} s)"))
    if not rec["rounds"]:
        print("no agreement round yet")
    return 0


if __name__ == "__main__":
    sys.exit(main())
