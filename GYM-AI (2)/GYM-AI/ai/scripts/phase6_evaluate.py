#!/usr/bin/env python3
"""Phase 6 — the ONE pre-registered final evaluation on fresh, unseen athletes (Domain C).

Pre-registration: docs/PHASE6_PREREGISTRATION.md. Three git tags make the run checkable:
  ai-core-candidate-v1   docs/PHASE6_FREEZE_CANDIDATE.json   the system, the rules and this evaluation code
  phase6-tools-v1 (-v2)  docs/PHASE6_TOOLS_FREEZE.json       intake / annotation / agreement tools + guide + spec
  phase6-data-lock-v1    docs/PHASE6_DATA_LOCK.json          every input of the run (written by --lock)
The freeze files are read FROM THEIR TAGS. Each tag's commit hash is emailed to the supervisor when the
tag is made (external timestamp, pre-registration §6); so is the commit of every final run.

  python -m ai.scripts.phase6_evaluate --check-only [--quick]   every check (decodes every take unless
                                                              --quick); runs no model, writes nothing
  python -m ai.scripts.phase6_evaluate --lock                write docs/PHASE6_DATA_LOCK.json (then commit + tag)
  python -m ai.scripts.phase6_evaluate                        THE final run (CPU); commits its own outputs
  python -m ai.scripts.phase6_evaluate --dry-run-dev [--out DIR]   pipeline check on the 6 DEV videos
The data root defaults to ai.evaluation.phase6_dataset.default_root() (outside the repository); a
cloud-synced root is refused. Pose caches and per-video results stay under the data root.
Exit codes: 0 done | 2 data / coverage / tie-in error, or a re-run on different inputs | 3 frozen identity
or data-lock problem | 4 run state (already run, uncommitted outputs, unreadable ledger, another run
active, ledger commit failed) | 5 crash of the evaluation code or integrity abort (the ledger keeps the
entry) | 6 option not allowed.
"""
from __future__ import annotations

import argparse
import csv
import dataclasses
import datetime as dt
import hashlib
import json
import math
import os
import platform
import shutil
import subprocess
import sys
import traceback
from fractions import Fraction
from typing import Dict, List, Optional

import numpy as np

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from ai.evaluation import phase6_dataset as D  # noqa: E402
from ai.evaluation.protocol import evaluate_dontcare, prf, pull_tol_frames  # noqa: E402

FREEZE_TAG = "ai-core-candidate-v1"
TOOLS_TAGS = ("phase6-tools-v1", "phase6-tools-v2")        # v2: the one pilot re-freeze (§6 step 1), if ever made
LOCK_TAGS = ("phase6-data-lock-v1", "phase6-data-lock-v2")  # v2: only after a consent withdrawal (§3.6), if ever
LOCK_TO_RUN_DAYS = 7                                        # the run follows the lock within this (§3.6, consent)
FREEZE_REL = "docs/PHASE6_FREEZE_CANDIDATE.json"
TOOLS_FREEZE_REL = "docs/PHASE6_TOOLS_FREEZE.json"
LOCK_REL = "docs/PHASE6_DATA_LOCK.json"
ADDENDA_REL = "docs/PHASE6_ADDENDA.md"           # dated addenda (never pinned; may never change a rule)
FREEZE_FILE = os.path.join(REPO_ROOT, *FREEZE_REL.split("/"))
TOOLS_FREEZE_FILE = os.path.join(REPO_ROOT, *TOOLS_FREEZE_REL.split("/"))
LOCK_FILE = os.path.join(REPO_ROOT, *LOCK_REL.split("/"))
ADDENDA_FILE = os.path.join(REPO_ROOT, *ADDENDA_REL.split("/"))
EVALUATION_FILES = ("ai/evaluation/protocol.py", "ai/evaluation/phase6_dataset.py", "ai/scripts/phase6_evaluate.py",
                    "ai/__init__.py", "ai/evaluation/__init__.py", "ai/scripts/__init__.py",
                    "docs/PHASE6_PREREGISTRATION.md")
FIXABLE_FILES = ("ai/evaluation/phase6_dataset.py", "ai/scripts/phase6_evaluate.py")   # only these, only via §7
TOOL_FILES = ("ai/scripts/validate_recordings.py", "ai/scripts/annotate_reps.py", "ai/scripts/annotation_agreement.py",
              "docs/PHASE6_ANNOTATION_GUIDE.md", "docs/UNSEEN_ATHLETE_DATASET_SPEC.md")
OUT_REL = "ai/outputs/phase6"
OUT_DEFAULT = os.path.join(REPO_ROOT, *OUT_REL.split("/"))
LEDGER, RUN_LOCK = "RUN_LEDGER.json", ".run.lock"
FINAL, DRY = "FINAL RUN", "DRY RUN ON DEV DATA — NOT PHASE 6 EVIDENCE"
PROTOCOL, IOU_THRESHOLD, PULL_TOL_S = "v2", 0.5, 8 / 15.0
IOU_RULE, PULL_RULE = "iou", "pull"
# ---- pre-registered gate constants (docs/PHASE6_PREREGISTRATION.md §4) -------------------------
G1_POOLED_F1, G1_RECALL = "0.80", "0.85"             # decimal strings: compared as exact fractions (§4.4)
G2_ATHLETE_F1, G2_MAX_FAIL_FRAC = "0.65", "0.10"
G3_VIEW_F1 = "0.70"
G4_TEMPO_F1 = "0.70"
G5_PULL_MAE_S = "0.25"
G6_MIN_TYPES = 3
LOW_N_ATHLETES = 5                    # a gated slice with fewer athletes is flagged low_n (never skipped)
LOW_SHARE_FLAG = 0.10                 # a slice whose `low` share exceeds this is flagged in the report
BOOTSTRAP_B, BOOTSTRAP_SEED = 2000, 20261010
MODE_B_PRESETS = ("V2_quality", "V3_budget_15fps")
SLOW_MIN_S, FAST_MAX_S, PAUSE_MIN_S = 3.0, 1.5, 1.0   # tempo compliance (reported, not gated; §4.5)
KNOWN_LIMITATIONS = {                 # declared before any data (pre-registration §1), shown next to the gate
    "G3_per_view:side": "R-07 side view (LOSO 0.654, locked TEST 0.383): at elevated risk",
    "G4_per_tempo:normal": "R-07: a third of every tempo slice is side view",
    "G4_per_tempo:fast": "R-07: a third of every tempo slice is side view",
    "G4_per_tempo:slow": "R-08 slow tempo + R-07: at elevated risk",
    "G4_per_tempo:paused": "R-08 paused tempo (extension holds) + R-07: at elevated risk",
    "G5_pull_mae": "R-08: squeeze holds move the PULL (annotated at the hold centre): at risk",
    "G6_form_direction": "seated-row thresholds PROVISIONAL (R-09); a sanity check that can pass by chance (§4.3)",
}


def rules() -> dict:
    """Every rule the verdict depends on: the constants AND a battery of canonical cases run through the
    live code (rule_battery). Frozen in the candidate freeze and compared on EVERY run, so no
    evaluation-code fix can move a gate, a threshold, the matching rule or a coverage rule."""
    return dict(gates=dict(G1_POOLED_F1=G1_POOLED_F1, G1_RECALL=G1_RECALL, G2_ATHLETE_F1=G2_ATHLETE_F1,
                           G2_MAX_FAIL_FRAC=G2_MAX_FAIL_FRAC, G3_VIEW_F1=G3_VIEW_F1, G4_TEMPO_F1=G4_TEMPO_F1,
                           G5_PULL_MAE_S=G5_PULL_MAE_S, G6_MIN_TYPES=G6_MIN_TYPES, G6_MIN_REPS=D.G6_MIN_REPS,
                           G6_MIN_ATHLETES=D.G6_MIN_ATHLETES, LOW_N_ATHLETES=LOW_N_ATHLETES),
                protocol=dict(name=PROTOCOL, iou_threshold=IOU_THRESHOLD, pull_tolerance_s=PULL_TOL_S,
                              dont_care="annotation_confidence == low",
                              abstention="any status other than OK, and SYSTEM_ERROR -> zero predictions"),
                bootstrap=dict(b=BOOTSTRAP_B, seed=BOOTSTRAP_SEED, unit="athlete"),
                tempo_compliance=dict(slow_min_s=SLOW_MIN_S, fast_max_s=FAST_MAX_S, pause_min_s=PAUSE_MIN_S),
                mode_b=list(MODE_B_PRESETS),
                data=dict(VIEWS=list(D.VIEWS), TEMPOS=list(D.TEMPOS), REQUIRED_VIEWS=list(D.REQUIRED_VIEWS),
                          MANDATORY_VIEWS=list(D.MANDATORY_VIEWS), MANDATORY_TEMPOS=list(D.MANDATORY_TEMPOS),
                          MIN_ATHLETES=D.MIN_ATHLETES, INDICATOR_FOR_DEVIATION=D.INDICATOR_FOR_DEVIATION,
                          CONFIDENCES=list(D.CONFIDENCES), PRIMARY_ANNOTATOR=D.PRIMARY_ANNOTATOR,
                          MIN_CERTAIN_REPS_PER_TAKE=D.MIN_CERTAIN_REPS_PER_TAKE,
                          LOW_TIER_MAX_SHARE=D.LOW_TIER_MAX_SHARE, LOW_TIER_MAX_SHARE_SLICE=D.LOW_TIER_MAX_SHARE_SLICE,
                          AGREE=[D.AGREE_MIN_IOU_F1, D.AGREE_MAX_PULL_MAE_S, D.AGREE_MIN_VIDEOS, D.AGREE_MIN_FRACTION],
                          SUBSET_SEED=D.SUBSET_SEED, INTAKE_DEVICE=D.INTAKE_DEVICE),
                battery=rule_battery())


def rule_battery() -> dict:
    """Canonical cases run through the live scoring, gate, pairing, coverage and agreement code. The
    outputs are frozen with the rules: any change of behaviour (not only of a constant) is drift."""
    R = lambda s, p, e, c="high": dict(start=s, pull=p, end=e, confidence=c)  # noqa: E731
    P = lambda s, p, e: dict(start=s, pull=p, end=e)  # noqa: E731
    gt = [R(0, 10, 20), R(30, 40, 50), R(60, 70, 80, "low"), R(100, 110, 120), R(130, 140, 150), R(160, 170, 180)]
    pred = [P(0, 10, 10), P(30, 56, 50), P(61, 71, 81), P(100, 110, 121), P(129, 141, 150), P(300, 310, 320),
            P(161, 186, 185)]
    scores = {}
    for fps in (15.0, 25.0, 30.0, 59.94):
        s = score_video(pred, gt, fps)
        scores[str(fps)] = {r: [s[r]["tp"], s[r]["fp"], s[r]["fn"], s[r]["n_ignored"]] for r in (IOU_RULE, PULL_RULE)}
        scores[str(fps)]["strict"] = [s["strict_iou"][k] for k in ("tp", "fp", "fn")]
    man = {f"A0{a}_{v}_normal_take{t}": dict(athlete_id=f"A0{a}", view=v, tempo="normal",
                                              form_type="good" if t == 1 else "deviated",
                                              deviation_type="none" if t == 1 else "reduced_rom")
           for a in (1, 2) for v in ("rear", "side") for t in (1, 2)}
    man["A03_side_slow_take2"] = dict(athlete_id="A03", view="side", tempo="slow", form_type="deviated",
                                      deviation_type="reduced_rom")
    man["A03_rear_normal_take1"] = dict(athlete_id="A03", view="rear", tempo="normal", form_type="good",
                                        deviation_type="none")
    statuses = ("OK", "NO_PERSON", "VIDEO_TOO_SHORT", "INSUFFICIENT_CONFIDENCE", "LOST_TRACK", "AMBIGUOUS_SUBJECT",
                "UNSUPPORTED_EXERCISE", "SYSTEM_ERROR")
    eligible = [f"A{a:02d}_{v}_{t}_take1" for a in range(1, 4) for v in D.VIEWS for t in D.TEMPOS]
    reps = {v: [dict(confidence="high")] * (3 if v.endswith("1") else 2) + [dict(confidence="low")] for v in man}
    return dict(
        pull_tol_frames={str(f): pull_tol_frames(f, PULL_TOL_S) for f in (15.0, 24.0, 25.0, 29.97, 30.0, 59.94, 60.0)},
        scores=scores,
        abstention={st: len(predictions_for_scoring(dict(status=st, reps=[dict(start_frame=1, pull_frame=2,
                                                                                 end_frame=3)]))) for st in statuses},
        f1_at_least=[f1_at_least(900, 300, 150, G1_POOLED_F1), f1_at_least(899, 300, 151, G1_POOLED_F1),
                     f1_at_least(26, 11, 17, G2_ATHLETE_F1), f1_at_least(26, 11, 18, G2_ATHLETE_F1),
                     f1_at_least(7, 3, 3, G3_VIEW_F1), recall_at_least(17, 3, G1_RECALL), recall_at_least(16, 3, G1_RECALL)],
        g2_allowed={n: math.floor(Fraction(G2_MAX_FAIL_FRAC) * n) for n in (9, 10, 19, 20, 30)},
        g6_pairs={dev: D.g6_pairs(man, sorted(man), dev) for dev in ("reduced_rom", "asymmetric_pull")},
        g6_data=D.g6_data_problems(man, reps, sorted(man)),
        quality=D.quality_problems(man, reps, sorted(man)),
        cohort=D.cohort_problems(man, sorted(man)),
        agreement_needed={n: agreement_needed(n) for n in (10, 80, 81, 150, 151)},
        subset=select_agreement_subset(eligible, [], round_seed(1, "battery", {v: "x" for v in eligible})),
        exact_mae=str(exact_pull_mae([score_video(pred, gt, 30.0)])),
        iou_boundary={"0.50": score_video([P(0, 10, 40)], [R(0, 10, 20)], 30.0)[IOU_RULE]["tp"],     # exactly 0.5
                      "0.49": score_video([P(0, 10, 41)], [R(0, 10, 20)], 30.0)[IOU_RULE]["tp"]},    # just below
        low_absorbs=score_video([P(0, 10, 20), P(60, 70, 80)], [R(0, 10, 20), R(60, 70, 80, "low")], 30.0)[IOU_RULE]["fp"],
    )


# ============================================================================ git + frozen identity
def find_git() -> Optional[str]:
    g = shutil.which("git")
    if g:
        return g
    cand = os.path.expandvars(r"%LOCALAPPDATA%\Programs\MinGit\cmd\git.exe")
    return cand if os.path.exists(cand) else None


def _git(*args) -> Optional[subprocess.CompletedProcess]:
    g = find_git()
    return subprocess.run([g, *args], cwd=REPO_ROOT, capture_output=True) if g else None


def git_show_bytes(ref: str, rel: str) -> Optional[bytes]:
    p = _git("show", f"{ref}:{rel}")
    return p.stdout if p is not None and p.returncode == 0 else None


def git_show(ref: str, rel: str) -> Optional[str]:
    """Content of `rel` at git ref `ref`, or None (no git, no tag, no file)."""
    b = git_show_bytes(ref, rel)
    return None if b is None else b.decode("utf-8")


def git_tree_files(ref: str) -> Optional[set]:
    p = _git("ls-tree", "-r", "--name-only", "-z", ref)
    return set(p.stdout.decode("utf-8").split("\0")) - {""} if p is not None and p.returncode == 0 else None


def git_is_ancestor(ref: str) -> Optional[bool]:
    """True if `ref` is an ancestor of HEAD (HEAD descends from the tag); None if unknown."""
    p = _git("merge-base", "--is-ancestor", ref, "HEAD")
    return None if p is None else {0: True, 1: False}.get(p.returncode)


def tag_info(tag: str) -> Optional[dict]:
    p = _git("for-each-ref", f"refs/tags/{tag}", "--format=%(*objectname)|%(objectname)|%(creatordate:iso-strict)")
    if p is None or p.returncode != 0 or not p.stdout.strip():
        return None
    peeled, obj, date = p.stdout.decode("utf-8").strip().split("|")
    return dict(tag=tag, commit=peeled or obj, date=date)


def _latest_tag(cands) -> str:
    for t in reversed(cands):
        if tag_info(t):
            return t
    return cands[0]


def tools_tag() -> str:
    """The tools freeze in force: the pilot re-freeze (v2) if it was ever made, else v1."""
    return _latest_tag(TOOLS_TAGS)


def lock_tag() -> str:
    """The data lock in force: the withdrawal re-lock (v2) if it was ever made, else v1."""
    return _latest_tag(LOCK_TAGS)


def lock_withdrawn() -> List[str]:
    """Athletes withdrawn by consent after the first lock, as recorded in the lock in force (§3.6)."""
    lock = git_show(lock_tag(), LOCK_REL)
    return list((json.loads(lock) if lock else {}).get("withdrawn") or [])


def _athlete(video_id: str) -> Optional[str]:
    return (D.parse_video_name(video_id) or {}).get("athlete")


def git_commit_paths(paths: List[str], message: str) -> Optional[str]:
    """Commit exactly these paths (nothing else that may be staged); returns the new commit or None."""
    a = _git("add", "-f", "--", *paths)
    if a is None or a.returncode != 0:
        return None
    c = _git("commit", "-m", message, "--", *paths)
    if c is None or c.returncode != 0:
        return None
    h = _git("rev-parse", "HEAD")
    return h.stdout.decode().strip() if h is not None and h.returncode == 0 else None


def system_files() -> List[str]:
    """Repo-relative paths whose content defines the evaluated SYSTEM (model, product code, lifter)."""
    from ai.core.model_artifact import DEFAULT_ARTIFACT
    files = ["yolo11n-pose.pt", "ai/models/motionbert/FT_MB_lite_MB_ft_h36m_global_lite/best_epoch.bin",
             "motionbert/configs/pose3d/MB_ft_h36m_global_lite.yaml", "ai/models/form_thresholds_v1_seated_row.json",
             "requirements/core.txt", os.path.relpath(DEFAULT_ARTIFACT, REPO_ROOT).replace(os.sep, "/")]
    for sub in ("ai/core", "motionbert/lib"):
        for dirpath, _, fs in os.walk(os.path.join(REPO_ROOT, sub)):
            files += [os.path.relpath(os.path.join(dirpath, f), REPO_ROOT).replace(os.sep, "/")
                      for f in fs if f.endswith(".py")]
    return sorted(set(files))


def file_hashes(rels) -> Dict[str, Optional[str]]:
    out = {}
    for rel in rels:
        p = os.path.join(REPO_ROOT, *rel.split("/"))
        out[rel] = D.sha256_pinned(p) if os.path.exists(p) else None
    return out


def key_packages() -> Dict[str, Optional[str]]:
    """FULL version strings (build tags such as +cu130 kept), the Python version, and the OpenCV build
    actually loaded (both opencv distributions + the hashes of the cv2 binary and its FFmpeg plugin, which
    decide the frame order every annotation refers to)."""
    from importlib.metadata import version
    out: Dict[str, Optional[str]] = {"python": platform.python_version()}
    for p in ("torch", "torchvision", "ultralytics", "numpy", "scipy", "opencv-python", "opencv-contrib-python",
              "pandas", "pydantic", "PyYAML"):
        try:
            out[p] = version(p)
        except Exception:                          # noqa: BLE001 - absent package recorded as None
            out[p] = None
    try:
        import cv2
        out["cv2"] = cv2.__version__
        cdir = os.path.dirname(cv2.__file__)
        for f in sorted(os.listdir(cdir)):
            if f.lower().startswith(("cv2", "opencv_videoio_ffmpeg")) and f.lower().endswith((".pyd", ".so", ".dll")):
                out[f"binary:{f}"] = D.sha256_file(os.path.join(cdir, f))
    except Exception:                              # noqa: BLE001
        out["cv2"] = None
    return out


def system_id() -> str:
    """One hash of the system files + environment; keys the pose cache so a cache is never reused across systems."""
    blob = json.dumps(dict(files=file_hashes(system_files()), packages=key_packages()), sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()


def _lf_sha(b: bytes, rel: str) -> str:
    if os.path.splitext(rel)[1].lower() in D.TEXT_EXT:
        b = b.replace(b"\r\n", b"\n")
    return hashlib.sha256(b).hexdigest()


def _tag_content_problems(tag: str, files: Dict[str, Optional[str]]) -> List[str]:
    """Every pinned file that git tracks must have, AT THE TAG, the frozen content (the tag reproduces it)."""
    tree = git_tree_files(tag)
    if tree is None:
        return []
    out = []
    for rel, want in files.items():
        if rel in tree:
            b = git_show_bytes(tag, rel)
            if b is None or _lf_sha(b, rel) != want:
                out.append(f"{rel}: the content at tag {tag} differs from its frozen hash")
    return out


def addenda_headings(text: Optional[str]) -> List[str]:
    return [line[3:].strip() for line in (text or "").splitlines() if line.startswith("## ")]


def addendum_present(addendum_id: str, ref: str = "HEAD") -> bool:
    """A dated addendum headed '## <id> ...' exists in the addenda committed at `ref` (§7)."""
    return bool(addendum_id) and any(h == addendum_id or h.startswith(addendum_id + " ")
                                     for h in addenda_headings(git_show(ref, ADDENDA_REL)))


def _diff_pathspecs() -> List[str]:
    """Everything an evaluation fix could touch (new files included), minus the separately frozen tools."""
    return ["ai/evaluation", "ai/scripts", "docs/PHASE6_PREREGISTRATION.md"] + \
        [f":(exclude){t}" for t in TOOL_FILES if t.startswith("ai/")]


def check_identity(evaluation_fix: Optional[str] = None, tools_required: bool = True) -> dict:
    """Compare the working tree with the frozen candidate READ FROM THE TAGS. Returns problems (fatal for
    the final run), notes, the evaluation-code changes (with the git diff) and the tools freeze."""
    problems, notes, eval_changes, eval_diff, tools = [], [], {}, None, None
    ttag = tools_tag()
    tagged = git_show(FREEZE_TAG, FREEZE_REL)
    if tagged is None:
        problems.append(f"git tag {FREEZE_TAG} with {FREEZE_REL} not found (or git unavailable)")
    else:
        freeze = json.loads(tagged)
        wt = json.load(open(FREEZE_FILE, encoding="utf-8")) if os.path.exists(FREEZE_FILE) else None
        if wt != freeze:
            problems.append(f"{FREEZE_REL} in the working tree differs from the copy at tag {FREEZE_TAG}")
        if freeze.get("tag") != FREEZE_TAG:
            problems.append(f"the tagged freeze names tag {freeze.get('tag')!r}, expected {FREEZE_TAG!r}")
        if set(freeze["evaluation"]["files"]) != set(EVALUATION_FILES):
            problems.append("the tagged freeze does not pin exactly the pre-registered evaluation files")
        if freeze.get("rules") != json.loads(json.dumps(rules())):
            problems.append("the running rules (constants or the behaviour of the rule battery) differ from the "
                            "tagged freeze; no fix may change a rule (§7)")
        cur = file_hashes(sorted(set(freeze["system"]["files"]) | set(system_files())))
        for rel, want in freeze["system"]["files"].items():
            if cur.get(rel) != want:
                problems.append(f"{rel}: {'missing' if cur.get(rel) is None else 'differs from the frozen system'}")
        for rel in sorted(set(system_files()) - set(freeze["system"]["files"])):
            problems.append(f"{rel}: new file in a frozen system location")
        pk = key_packages()
        for name in sorted(set(freeze["system"]["packages"]) | set(pk)):
            if pk.get(name) != freeze["system"]["packages"].get(name):
                problems.append(f"package {name}: {pk.get(name)} != frozen {freeze['system']['packages'].get(name)}")
        problems += _tag_content_problems(FREEZE_TAG, {**freeze["system"]["files"], **freeze["evaluation"]["files"]})
        cur_e = file_hashes(freeze["evaluation"]["files"])
        eval_changes = {rel: dict(frozen=want, now=cur_e[rel]) for rel, want in freeze["evaluation"]["files"].items()
                        if cur_e[rel] != want}
        problems += [f"{rel}: differs from the frozen version and may never change (§7)" for rel in eval_changes
                     if rel not in FIXABLE_FILES]
        fixable = [rel for rel in eval_changes if rel in FIXABLE_FILES]
        if evaluation_fix:
            if not addendum_present(evaluation_fix):
                problems.append(f"--evaluation-fix {evaluation_fix!r}: no committed addendum headed "
                                f"'## {evaluation_fix}' in {ADDENDA_REL} (document the bug, the diff and the test first)")
            if not fixable:
                problems.append("--evaluation-fix given, but no evaluation file differs from the frozen version")
            p = _git("status", "--porcelain", "--", *fixable) if fixable else None
            if fixable and (p is None or p.returncode != 0 or p.stdout.strip()):
                problems.append("--evaluation-fix: commit the changed evaluation files first")
            d = _git("diff", FREEZE_TAG, "HEAD", "--", *_diff_pathspecs())
            st = _git("diff", "--stat", FREEZE_TAG, "HEAD", "--", *_diff_pathspecs())
            eval_diff = dict(stat=st.stdout.decode("utf-8", "replace") if st is not None else None,
                             sha256=hashlib.sha256(d.stdout).hexdigest() if d is not None else None,
                             diff=d.stdout.decode("utf-8", "replace") if d is not None else None)
            notes.append(f"EVALUATION-CODE FIX ({evaluation_fix}): {', '.join(fixable) or 'nothing'} differ from {FREEZE_TAG}")
        elif fixable:
            problems += [f"{rel}: evaluation code differs from the frozen version (only allowed with "
                         f"--evaluation-fix, pre-registration §7)" for rel in fixable]
        if git_is_ancestor(FREEZE_TAG) is not True:
            problems.append(f"HEAD does not descend from tag {FREEZE_TAG}")
    tagged_tools = git_show(ttag, TOOLS_FREEZE_REL) if tools_required else None
    if tools_required and tagged_tools is None:
        problems.append(f"git tag {ttag} with {TOOLS_FREEZE_REL} not found: intake/annotation tools not frozen")
    elif tagged_tools is not None:
        tools = json.loads(tagged_tools)
        wt = json.load(open(TOOLS_FREEZE_FILE, encoding="utf-8")) if os.path.exists(TOOLS_FREEZE_FILE) else None
        if wt != tools:
            problems.append(f"{TOOLS_FREEZE_REL} in the working tree differs from the copy at tag {ttag}")
        if tools.get("tag") != ttag or tools.get("builds_on") != FREEZE_TAG or set(tools.get("files", {})) != set(TOOL_FILES):
            problems.append(f"the tools freeze at {ttag} does not name {ttag}, build on {FREEZE_TAG} and pin exactly "
                            "the pre-registered tool files")
        cur_t = file_hashes(tools.get("files", {}))
        problems += [f"{rel}: differs from the frozen tool (tag {ttag})"
                     for rel, want in tools.get("files", {}).items() if cur_t[rel] != want]
        problems += _tag_content_problems(ttag, tools.get("files", {}))
        if git_is_ancestor(ttag) is not True:
            problems.append(f"HEAD does not descend from tag {ttag}")
    return dict(problems=problems, notes=notes, evaluation_changes=eval_changes, evaluation_diff=eval_diff,
                tools=tools, tools_tag=ttag, tags={t: tag_info(t) for t in (FREEZE_TAG, ttag, lock_tag())},
                freeze_files={rel: (D.sha256_pinned(os.path.join(REPO_ROOT, *rel.split("/")))
                                    if os.path.exists(os.path.join(REPO_ROOT, *rel.split("/"))) else None)
                              for rel in (FREEZE_REL, TOOLS_FREEZE_REL, LOCK_REL)})


def withdrawal_problems(lock: dict) -> List[str]:
    """A withdrawal re-lock (§3.6): tag v2 must equal tag v1 minus the withdrawn athletes' takes, and the
    withdrawal must be documented in a committed addendum."""
    out = []
    if lock_tag() != LOCK_TAGS[1]:
        out.append(f"a lock with withdrawn athletes must be tag {LOCK_TAGS[1]}")
    v1 = git_show(LOCK_TAGS[0], LOCK_REL)
    if v1 is None:
        return out + [f"{LOCK_TAGS[0]} not found: a withdrawal lock supersedes the first lock"]
    wd = set(lock.get("withdrawn") or [])
    keep = {v: s for v, s in json.loads(v1).get("videos", {}).items() if _athlete(v) not in wd}
    if lock.get("videos") != keep:
        out.append("the withdrawal lock does not equal the first lock minus the withdrawn athletes' takes")
    if not addendum_present(lock.get("addendum") or ""):
        out.append("the withdrawal is not documented in a committed addendum (§3.6)")
    return out


def check_lock(fp: dict, ident: Optional[dict] = None) -> List[str]:
    """The data lock (pre-registration §6 step 7) must exist at its tag, equal the working tree, be an
    ancestor of HEAD, name the tag commits in force, and match the inputs being evaluated byte for byte."""
    ltag = lock_tag()
    tagged = git_show(ltag, LOCK_REL)
    if tagged is None:
        return [f"git tag {ltag} with {LOCK_REL} not found: run --lock, commit, tag and timestamp it first"]
    lock, problems = json.loads(tagged), []
    wt = json.load(open(LOCK_FILE, encoding="utf-8")) if os.path.exists(LOCK_FILE) else None
    if wt != lock:
        problems.append(f"{LOCK_REL} in the working tree differs from the copy at tag {ltag}")
    if git_is_ancestor(ltag) is not True:
        problems.append(f"HEAD does not descend from tag {ltag}")
    if lock.get("withdrawn"):
        problems += withdrawal_problems(lock)
    if ident is not None:
        for t, c in (lock.get("tags") or {}).items():
            now = (ident.get("tags") or {}).get(t) or tag_info(t)
            if (now or {}).get("commit") != c:
                problems.append(f"the lock was made with tag {t} at {c}; it now points to {(now or {}).get('commit')}")
    if lock.get("fingerprint") != fp["fingerprint"]:
        lv, lm = lock.get("videos", {}), lock.get("meta", {})
        changed = sorted(v for v in set(lv) | set(fp["videos"]) if lv.get(v) != fp["videos"].get(v))
        meta = sorted(k for k in set(lm) | set(fp["meta"]) if lm.get(k) != fp["meta"].get(k))
        problems.append(f"the inputs differ from the data lock (videos {changed[:5]}{' ...' if len(changed) > 5 else ''}, "
                        f"files {meta})")
    return problems


# ============================================================================ scoring (pure functions)
def score_video(pred: List[dict], gt: List[dict], fps: float) -> Dict[str, dict]:
    """Protocol v2 for one video. gt: [{start,pull,end,confidence}], `low` = don't-care. Also scores the
    non-gating STRICT variant in which `low` reps count as certain (pre-registration §4.2)."""
    gtc = [dict(start=r["start"], pull=r["pull"], end=r["end"]) for r in gt if r["confidence"] != "low"]
    gtu = [dict(start=r["start"], pull=r["pull"], end=r["end"]) for r in gt if r["confidence"] == "low"]
    gta = [dict(start=r["start"], pull=r["pull"], end=r["end"]) for r in gt]
    tol = pull_tol_frames(fps, PULL_TOL_S)
    out: Dict[str, dict] = {"fps": fps}
    for rule in (IOU_RULE, PULL_RULE):
        m = evaluate_dontcare(pred, gtc, gtu, rule, PROTOCOL, tol)
        out[rule] = dict(tp=m["tp"], fp=m["fp"], fn=m["fn"], n_gt=m["n_gt"], n_unc=m["n_unc"], n_pred=m["n_pred"],
                         n_ignored=m["n_ignored"], matched=[list(p) for p in m["matched"]],
                         pull_err_frames=[abs(pred[i]["pull"] - gtc[j]["pull"]) for i, j in m["matched"]],
                         pull_err_s=[abs(pred[i]["pull"] - gtc[j]["pull"]) / fps for i, j in m["matched"]],
                         start_err_s=[abs(pred[i]["start"] - gtc[j]["start"]) / fps for i, j in m["matched"]],
                         end_err_s=[abs(pred[i]["end"] - gtc[j]["end"]) / fps for i, j in m["matched"]])
    s = evaluate_dontcare(pred, gta, [], IOU_RULE, PROTOCOL, tol)
    out["strict_iou"] = dict(tp=s["tp"], fp=s["fp"], fn=s["fn"])
    return out


def _exact_f1(tp: int, fp: int, fn: int) -> Optional[Fraction]:
    d = 2 * tp + fp + fn
    return Fraction(2 * tp, d) if d else None


def f1_at_least(tp: int, fp: int, fn: int, thr: str) -> bool:
    """F1 >= thr decided EXACTLY on the integer counts (no floating-point rounding at the threshold)."""
    f = _exact_f1(tp, fp, fn)
    return f is not None and f >= Fraction(thr)


def recall_at_least(tp: int, fn: int, thr: str) -> bool:
    return (tp + fn) > 0 and Fraction(tp, tp + fn) >= Fraction(thr)


def exact_pull_mae(scores: List[Dict[str, dict]], rule: str = IOU_RULE) -> Optional[Fraction]:
    """Mean |Δ pull| in seconds over matched pairs, exact: integer frame errors / each video's fps."""
    errs = [Fraction(e) / Fraction(s["fps"]) for s in scores for e in s[rule]["pull_err_frames"]]
    return sum(errs, Fraction(0)) / len(errs) if errs else None


def pool(scores: List[Dict[str, dict]]) -> dict:
    """Micro-pooled metrics over per-video scores (MAE over matched pairs). Rounded values are for
    display; the gates are decided on the counts and on exact_pull_mae (§4.4)."""
    out = {}
    for rule in (IOU_RULE, PULL_RULE):
        tp, fp, fn = (sum(s[rule][k] for s in scores) for k in ("tp", "fp", "fn"))
        P, R, F = prf(tp, fp, fn)
        n_gt, n_low = sum(s[rule]["n_gt"] for s in scores), sum(s[rule].get("n_unc", 0) for s in scores)
        d = dict(tp=tp, fp=fp, fn=fn, n_gt=n_gt, n_low=n_low,
                 low_share=round(n_low / (n_gt + n_low), 4) if n_gt + n_low else None,
                 precision=round(P, 4), recall=round(R, 4), f1=round(F, 4), recall_raw=R, f1_raw=F)
        for k in ("pull", "start", "end"):
            e = [x for s in scores for x in s[rule][f"{k}_err_s"]]
            d[f"{k}_mae_s"] = round(float(np.mean(e)), 4) if e else None
            d[f"{k}_mae_s_raw"] = float(np.mean(e)) if e else None
        ce = [(s[rule]["tp"] + s[rule]["fp"]) - s[rule]["n_gt"] for s in scores]   # ignored predictions excluded
        d["count_error_mean_abs"] = round(float(np.mean(np.abs(ce))), 4) if ce else None
        d["count_error_mean_signed"] = round(float(np.mean(ce)), 4) if ce else None
        out[rule] = d
    st = [s["strict_iou"] for s in scores if "strict_iou" in s]
    if st:
        tp, fp, fn = (sum(x[k] for x in st) for k in ("tp", "fp", "fn"))
        out["strict_iou_f1"] = round(prf(tp, fp, fn)[2], 4)
    return out


def predictions_for_scoring(result: dict) -> List[dict]:
    """Abstention rule (pre-registered): any status other than OK (SYSTEM_ERROR included) means the system
    gave no answer -> zero predictions (every annotated rep of the video counts as a miss)."""
    if result["status"] != "OK":
        return []
    return [dict(start=r["start_frame"], pull=r["pull_frame"], end=r["end_frame"]) for r in result["reps"]]


def mode_b_predictions(events: List[dict]) -> List[dict]:
    """Same abstention rule for Mode B: events emitted by a decode whose status was not OK are dropped."""
    return [dict(start=e["start"], pull=e["pull"], end=e["end"]) for e in events if e.get("status") == "OK"]


def group_metrics(videos: List[dict], key) -> Dict[str, dict]:
    groups: Dict[str, List[dict]] = {}
    for v in videos:
        groups.setdefault(key(v), []).append(v)
    return {g: dict(n_videos=len(vs), n_athletes=len({v["athlete"] for v in vs}),
                    athletes=sorted({v["athlete"] for v in vs}), **pool([v["score"] for v in vs]))
            for g, vs in sorted(groups.items())}


def _percentile_ci(vals) -> Optional[list]:
    if not len(vals):
        return None
    lo, hi = np.percentile(vals, [2.5, 97.5])
    return [round(float(lo), 4), round(float(hi), 4)]


def bootstrap_ci(videos: List[dict], stat: str = "f1", b: int = BOOTSTRAP_B, seed: int = BOOTSTRAP_SEED):
    """Athlete-cluster percentile bootstrap (athletes resampled with replacement, `b` resamples, fixed
    seed) of the pooled IoU-rule F1, recall or pull MAE (stat "f1" | "recall" | "pull_mae_s")."""
    by: Dict[str, List[float]] = {}
    for v in videos:
        s = v["score"][IOU_RULE]
        a = by.setdefault(v["athlete"], [0.0] * 5)
        for k, x in enumerate((s["tp"], s["fp"], s["fn"], sum(s["pull_err_s"]), len(s["pull_err_s"]))):
            a[k] += x
    if len(by) < 2:
        return None
    m = np.array([by[k] for k in sorted(by)])
    idx = np.random.default_rng(seed).integers(0, len(m), size=(b, len(m)))
    tp, fp, fn, es, en = m[idx].sum(axis=1).T
    with np.errstate(divide="ignore", invalid="ignore"):
        x = {"f1": 2 * tp / (2 * tp + fp + fn), "recall": tp / (tp + fn), "pull_mae_s": es / en}[stat]
    return _percentile_ci(x[np.isfinite(x)].tolist())


def gate(status: bool, **detail) -> dict:
    return dict(status="PASS" if status else "FAIL", **detail)


def not_evaluable(reason: str, **detail) -> dict:
    return dict(status="NOT EVALUABLE", reason=reason, **detail)


def _cell_stat(cells: List[tuple]) -> Optional[Fraction]:
    """Weighted mean, over paired (athlete, view) cells with matched reps on both sides, of the
    within-cell rate difference (deviated - good), weight min(n_deviated, n_good). Exact."""
    num, den = Fraction(0), 0
    for _, _, dk, dn, gk, gn in cells:
        if dn and gn:
            w = min(dn, gn)
            num += w * (Fraction(dk, dn) - Fraction(gk, gn))
            den += w
    return num / den if den else None


def form_direction(videos: List[dict]) -> dict:
    """G6 exactly as pre-registered (§4.3), PAIRED design. For each deviation type with a v1 indicator,
    each athlete's deviated takes are compared with the SAME athlete's good takes in the SAME view, all at
    tempo `normal` (phase6_dataset.g6_pairs), WITHIN each such (athlete, view) cell. The type is NOT
    EVALUABLE when the annotations cannot test it (phase6_dataset.g6_type_problem). It shows the RIGHT
    DIRECTION when, over IoU-matched predicted reps, (a) the weighted mean within-cell difference of the
    matching indicator's `attention` rate (deviated - good, weight min of the two counts) is > 0;
    (b) there are >= 10 matched deviated reps from >= 2 athletes and >= 10 matched good reps; and (c) the
    same statistic is > 0 for >= 2 athletes individually. Otherwise DIRECTION NOT SHOWN. G6 PASS iff >= 3
    types show the right direction; anything else is FAIL."""
    pm = {v["video_id"]: dict(athlete_id=v["athlete"], view=v["view"], tempo=v["tempo"], form_type=v["form_type"],
                              deviation_type=v["deviation_type"]) for v in videos}
    byid = {v["video_id"]: v for v in videos}
    ids = sorted(byid)
    certain = {vid: [dict(confidence="high")] * byid[vid]["score"][IOU_RULE]["n_gt"] for vid in ids}

    def tally(vids, ind):
        n = k = 0
        for vid in vids:
            v = byid[vid]
            for i, _ in v["score"][IOU_RULE]["matched"]:
                sev = {x["indicator"]: x["severity"] for x in v["pred_reps"][i]["indicators"]}.get(ind)
                if sev is not None:
                    n, k = n + 1, k + (sev == "attention")
        return n, k

    types = {}
    for dev, ind in D.INDICATOR_FOR_DEVIATION.items():
        if ind is None:
            types[dev] = dict(indicator=None, status="NO INDICATOR")
            continue
        pairs = D.g6_pairs(pm, ids, dev)
        data_problem = D.g6_type_problem(pm, certain, ids, dev)
        cells = []
        for a, views in sorted(pairs.items()):
            for w, c in sorted(views.items()):
                dn, dk = tally(c["deviated"], ind)
                gn, gk = tally(c["good"], ind)
                cells.append((a, w, dk, dn, gk, gn))
        nd, ng = sum(c[3] for c in cells), sum(c[5] for c in cells)
        m_aths = len({c[0] for c in cells if c[3] > 0})
        pooled = _cell_stat(cells)
        per_ath = {a: _cell_stat([c for c in cells if c[0] == a]) for a in sorted(pairs)}
        right_aths = sorted(a for a, x in per_ath.items() if x is not None and x > 0)
        shown = (nd >= D.G6_MIN_REPS and m_aths >= D.G6_MIN_ATHLETES and ng >= D.G6_MIN_REPS
                 and pooled is not None and pooled > 0 and len(right_aths) >= D.G6_MIN_ATHLETES)
        aths = sorted(pairs)
        ci = None
        if len(aths) >= 2:
            rng = np.random.default_rng(BOOTSTRAP_SEED)
            vals = []
            for _ in range(BOOTSTRAP_B):
                pick = [aths[i] for i in rng.integers(0, len(aths), len(aths))]
                x = _cell_stat([c for a in pick for c in cells if c[0] == a])
                if x is not None:
                    vals.append(float(x))
            ci = _percentile_ci(vals)
        types[dev] = dict(indicator=ind, rate_deviated=round(sum(c[2] for c in cells) / nd, 4) if nd else None,
                          rate_good=round(sum(c[4] for c in cells) / ng, 4) if ng else None,
                          difference=None if pooled is None else round(float(pooled), 4),
                          difference_ci95=ci, matched_reps_deviated=nd, matched_reps_good=ng,
                          athletes_paired=len(pairs), athletes_with_matched_deviated_reps=m_aths,
                          athletes_right=right_aths,
                          per_athlete={a: None if x is None else round(float(x), 4) for a, x in per_ath.items()},
                          cells=[dict(athlete=a, view=w, deviated=f"{dk}/{dn}", good=f"{gk}/{gn}")
                                 for a, w, dk, dn, gk, gn in cells],
                          status=("NOT EVALUABLE" if data_problem else "RIGHT DIRECTION" if shown
                                  else "DIRECTION NOT SHOWN"),
                          reason=data_problem)
    with_ind = [t for t in types.values() if t["indicator"] is not None]
    n_ok = sum(t["status"] == "RIGHT DIRECTION" for t in with_ind)
    n_ne = sum(t["status"] == "NOT EVALUABLE" for t in with_ind)
    res = gate(n_ok >= G6_MIN_TYPES, n_right=n_ok, n_not_evaluable=n_ne, threshold=f">= {G6_MIN_TYPES} types")
    res["types"] = types
    return res


def apply_gates(videos: List[dict]) -> dict:
    pooled = pool([v["score"] for v in videos])
    athletes = group_metrics(videos, lambda v: v["athlete"])
    views = group_metrics(videos, lambda v: v["view"])
    tempos = group_metrics(videos, lambda v: v["tempo"])
    g = {}
    iou = pooled[IOU_RULE]
    g["G1_pooled"] = gate(f1_at_least(iou["tp"], iou["fp"], iou["fn"], G1_POOLED_F1)
                          and recall_at_least(iou["tp"], iou["fn"], G1_RECALL),
                          f1=iou["f1"], recall=iou["recall"], thresholds=[G1_POOLED_F1, G1_RECALL])
    n_ath = len(athletes)
    allowed = math.floor(Fraction(G2_MAX_FAIL_FRAC) * n_ath)
    failing = sorted(a for a, m in athletes.items()
                     if not f1_at_least(m[IOU_RULE]["tp"], m[IOU_RULE]["fp"], m[IOU_RULE]["fn"], G2_ATHLETE_F1))
    g["G2_per_athlete"] = gate(len(failing) <= allowed, n_athletes=n_ath, failing=failing,
                               allowed_failures=allowed, threshold=G2_ATHLETE_F1)

    def slice_gate(table, required, thr):
        """EVERY slice present is gated (spec §5: "for every view"), flagged low_n under LOW_N_ATHLETES
        athletes; a required slice with no data is NOT EVALUABLE (which counts as FAIL)."""
        out = {}
        for name in sorted(set(table) | set(required)):
            m = table.get(name)
            if m is None:
                out[name] = not_evaluable("no data for a required slice", threshold=thr)
                continue
            i = m[IOU_RULE]
            out[name] = gate(f1_at_least(i["tp"], i["fp"], i["fn"], thr), f1=i["f1"], n_gt=i["n_gt"],
                             n_low=i["n_low"], low_share=i["low_share"], n_athletes=m["n_athletes"], threshold=thr,
                             low_n=m["n_athletes"] < LOW_N_ATHLETES)
        return out
    g["G3_per_view"] = slice_gate(views, D.REQUIRED_VIEWS, G3_VIEW_F1)
    g["G4_per_tempo"] = slice_gate(tempos, D.MANDATORY_TEMPOS, G4_TEMPO_F1)
    mae = exact_pull_mae([v["score"] for v in videos])
    g["G5_pull_mae"] = (gate(mae <= Fraction(G5_PULL_MAE_S), pull_mae_s=iou["pull_mae_s"], threshold=G5_PULL_MAE_S)
                        if mae is not None else not_evaluable("no matched reps", threshold=G5_PULL_MAE_S))
    g["G6_form_direction"] = form_direction(videos)
    all_st = [g[k]["status"] for k in ("G1_pooled", "G2_per_athlete", "G5_pull_mae", "G6_form_direction")]
    all_st += [s["status"] for k in ("G3_per_view", "G4_per_tempo") for s in g[k].values()]
    n_ne = all_st.count("NOT EVALUABLE")
    verdict = ("PASS" if all(s == "PASS" for s in all_st) else
               "FAIL" + (f" ({n_ne} gate(s) NOT EVALUABLE, counted as FAIL)" if n_ne else ""))
    return dict(verdict=verdict, gates=g, pooled=pooled, per_athlete=athletes, per_view=views, per_tempo=tempos)


def tempo_compliance(videos: List[dict], reps: Dict[str, List[dict]]) -> Dict[str, dict]:
    """§4.5, reported, never gated, labels kept as recorded (intention to treat): does each take match its
    tempo label? slow: median rep (END-START) >= 3.0 s; fast: <= 1.5 s; paused: median rest between reps
    >= 1 s AND median rep >= the athlete's good-form normal median + 1 s (both holds). Certain reps only."""
    def medians(vid, fps):
        rs = sorted((r for r in reps.get(vid, []) if r["confidence"] != "low"), key=lambda r: r["start"])
        d = [(r["end"] - r["start"]) / fps for r in rs]
        rest = [(b["start"] - a["end"]) / fps for a, b in zip(rs, rs[1:])]
        return (float(np.median(d)) if d else None), (float(np.median(rest)) if rest else None)
    info, normal = {}, {}
    for v in videos:
        d, r = medians(v["video_id"], v["fps"])
        info[v["video_id"]] = (v, d, r)
        if v["tempo"] == "normal" and v["form_type"] == "good" and d is not None:
            normal.setdefault(v["athlete"], []).append(d)
    table: Dict[str, dict] = {}
    for vid, (v, d, r) in sorted(info.items()):
        t = v["tempo"]
        if t == "normal" or d is None:
            continue
        base = float(np.median(normal[v["athlete"]])) if normal.get(v["athlete"]) else None
        ok = (d >= SLOW_MIN_S if t == "slow" else d <= FAST_MAX_S if t == "fast" else
              r is not None and r >= PAUSE_MIN_S and base is not None and d >= base + PAUSE_MIN_S)
        e = table.setdefault(t, dict(n=0, compliant=0, not_compliant=[]))
        e["n"] += 1
        e["compliant"] += bool(ok)
        if not ok:
            e["not_compliant"].append(dict(video_id=vid, median_rep_s=round(d, 2),
                                           median_rest_s=None if r is None else round(r, 2)))
    return table


def verdict_of_record(entries: List[dict]) -> Optional[dict]:
    """§7: the first completed run, replaced only by a later completed --evaluation-fix run (same data)."""
    rec = None
    for e in entries:
        if e.get("status") == "completed" and (rec is None or e.get("evaluation_fix")):
            rec = e
    return rec


# ============================================================================ annotation agreement (§3.4)
# The rule lives here, frozen with the candidate: the agreement tool (ai/scripts/annotation_agreement.py)
# calls these functions to select and measure, and the final run recomputes every round from the files.
def agreement_needed(n_videos: int) -> int:
    """Subset size: >= AGREE_MIN_VIDEOS and >= AGREE_MIN_FRACTION of the evaluated videos (exact)."""
    return max(D.AGREE_MIN_VIDEOS, math.ceil(Fraction(str(D.AGREE_MIN_FRACTION)) * n_videos))


def round_seed(round_no: int, salt: str, primary_snapshot: Dict[str, str]) -> int:
    """The subset seed of a round: fixed by the round number, an EXTERNAL value (`salt`, e.g. a number the
    supervisor sends once a1 reports the annotation complete) and the snapshot of a1's files. Nobody can
    compute the subset before a1's files are final and the external value is known."""
    blob = json.dumps(dict(base=D.SUBSET_SEED, round=round_no, salt=salt, snapshot=primary_snapshot), sort_keys=True)
    return int(hashlib.sha256(blob.encode()).hexdigest()[:15], 16)


def select_agreement_subset(eligible: List[str], previous: List[str], seed: int) -> List[str]:
    """Deterministic, stratified subset (nobody picks it): the eligible videos not used in an earlier
    round, grouped by view x tempo cell and shuffled per cell with `seed` (round_seed); one video from every
    cell first (coverage), then one per cell in turn until agreement_needed(len(eligible)) are chosen."""
    rng = np.random.default_rng(seed)
    cells: Dict[tuple, List[str]] = {}
    for v in sorted(set(eligible) - set(previous)):
        p = D.parse_video_name(v)
        cells.setdefault((p["view"], p["tempo"]), []).append(v)
    for k in sorted(cells):
        cells[k] = [str(x) for x in rng.permutation(cells[k])]
    need = agreement_needed(len(set(eligible)))
    out = [cells[k].pop(0) for k in sorted(cells) if cells[k]]
    while len(out) < need and any(cells.values()):
        for k in sorted(cells):
            if cells[k] and len(out) < need:
                out.append(cells[k].pop(0))
    return sorted(out)


def _cv2_version() -> Optional[str]:
    try:
        import cv2
        return cv2.__version__
    except Exception:                              # noqa: BLE001
        return None


def measure_agreement(root: str, subset: List[str], n_videos: int, second: str, intake_videos: Dict[str, dict],
                      reference: str = D.PRIMARY_ANNOTATOR) -> dict:
    """The second annotator scored against the reference (the primary annotator) with protocol v2, the
    reference's `low` reps being don't-care. Both annotators' files must be intact and made on the
    intake-checked file with the frozen decoder. Accepted iff pooled IoU F1 >= 0.90 and pull MAE <= 0.15 s
    (exact), the subset has agreement_needed(n_videos) videos and covers every view and tempo. Tier
    agreement covers ALL of the reference's reps, `low` included."""
    problems, per_video = [], {}
    if not (isinstance(second, str) and D.ANNOTATOR_RE.match(second)) or second == reference:
        problems.append(f"second annotator {second!r}: must be a pseudonymous id other than {reference}")
    elif os.path.realpath(os.path.join(root, "annotations", second)) == \
            os.path.realpath(os.path.join(root, "annotations", reference)):
        problems.append(f"annotations/{second} is the same folder as annotations/{reference}")
    if problems:
        return dict(complete=False, problems=problems)
    cv2v = _cv2_version()
    for vid in subset:
        iv = intake_videos.get(vid) or {}
        bad = []
        for who in (reference, second):
            m = D.read_sidecar(root, who, vid)
            if m is not None and m.get("annotator") != who:
                bad.append(f"{vid}: the sidecar in annotations/{who} names annotator {m.get('annotator')!r}")
            bad += D.sidecar_problems(root, who, vid, iv.get("sha256"), iv.get("n_frames"), iv.get("fps"), cv2v)
        if bad:
            problems += bad
            continue
        ra, rb = D.read_annotator_csv(root, reference, vid), D.read_annotator_csv(root, second, vid)
        fps = float(iv["fps"])
        pred = [dict(start=r["start"], pull=r["pull"], end=r["end"]) for r in rb]
        allr = evaluate_dontcare(pred, [dict(start=r["start"], pull=r["pull"], end=r["end"]) for r in ra], [],
                                 IOU_RULE, PROTOCOL, pull_tol_frames(fps, PULL_TOL_S))
        per_video[vid] = dict(score=score_video(pred, ra, fps), ra=ra, rb=rb,
                              all_pairs={j: i for i, j in allr["matched"]})
    if problems:
        return dict(complete=False, problems=problems)
    scores = [x["score"] for x in per_video.values()]
    iou = pool(scores)[IOU_RULE] if scores else None
    mae = exact_pull_mae(scores) if scores else None
    views = {D.parse_video_name(v)["view"] for v in subset}
    tempos = {D.parse_video_name(v)["tempo"] for v in subset}
    need = agreement_needed(n_videos)
    checks = dict(
        subset_size=dict(ok=len(subset) >= need, value=len(subset), needed=need),
        views_covered=dict(ok=set(D.VIEWS) <= views, missing=sorted(set(D.VIEWS) - views)),
        tempos_covered=dict(ok=set(D.TEMPOS) <= tempos, missing=sorted(set(D.TEMPOS) - tempos)),
        iou_f1=dict(ok=bool(iou) and f1_at_least(iou["tp"], iou["fp"], iou["fn"], str(D.AGREE_MIN_IOU_F1)),
                    value=iou and iou["f1"], threshold=D.AGREE_MIN_IOU_F1),
        pull_mae_s=dict(ok=mae is not None and mae <= Fraction(str(D.AGREE_MAX_PULL_MAE_S)),
                        value=iou and iou["pull_mae_s"], threshold=D.AGREE_MAX_PULL_MAE_S))
    tiers: Dict[str, int] = {}
    for x in per_video.values():
        for j, r in enumerate(x["ra"]):
            i = x["all_pairs"].get(j)
            k = f"{r['confidence']}->{x['rb'][i]['confidence'] if i is not None else 'none'}"
            tiers[k] = tiers.get(k, 0) + 1
    n_low = sum(v for k, v in tiers.items() if k.startswith("low->"))
    low_certain = sum(v for k, v in tiers.items() if k in ("low->high", "low->medium"))
    return dict(complete=True, reference=reference, second=second, accepted=all(c["ok"] for c in checks.values()),
                checks=checks, pooled=iou, pull_mae_exact=None if mae is None else str(mae),
                tier_agreement=dict(sorted(tiers.items())),
                primary_low_marked_certain=dict(n=low_certain, of=n_low),
                per_video={v: dict(tp=x["score"][IOU_RULE]["tp"], fp=x["score"][IOU_RULE]["fp"],
                                   fn=x["score"][IOU_RULE]["fn"], n_reference=len(x["ra"]), n_second=len(x["rb"]))
                           for v, x in per_video.items()})


def verify_agreement(root: str, agreement: dict, included: List[str], intake_videos: Dict[str, dict],
                     withdrawn: Optional[List[str]] = None) -> List[str]:
    """The final run does not trust qa/annotation_agreement.json: every round's subset must be the one the
    rule gives for its recorded salt and a1 snapshot (which must cover exactly its eligible takes); rounds
    after the first must name a committed addendum; the accepted round must have been drawn from exactly
    the evaluated takes (plus the takes of athletes withdrawn by consent after the lock, none of which may
    be in its subset), a1 must not have changed any audited take after the draw, and its result must be
    reproduced from the annotation files."""
    errors, prev = [], []
    wd = set(withdrawn or [])
    rounds = agreement.get("rounds") or []
    for k, r in enumerate(rounds, 1):
        snap = r.get("primary_snapshot") or {}
        if set(snap) != set(r.get("eligible") or []):
            errors.append(f"{D.AGREEMENT_REPORT}: round {k}'s snapshot of {D.PRIMARY_ANNOTATOR}'s files does not cover "
                          "exactly its eligible takes")
        seed = round_seed(k, r.get("salt") or "", snap)
        if sorted(r.get("subset") or []) != select_agreement_subset(r.get("eligible") or [], prev, seed):
            errors.append(f"{D.AGREEMENT_REPORT}: round {k}'s subset is not the one the pre-registered rule selects")
        if k >= 2 and not addendum_present(r.get("addendum") or ""):
            errors.append(f"{D.AGREEMENT_REPORT}: round {k} names no committed addendum (a new round follows a "
                          "dated guide clarification, §3.4)")
        prev += r.get("subset") or []
    if rounds and rounds[-1].get("result"):
        last = rounds[-1]
        if {v for v in last.get("eligible") or [] if _athlete(v) not in wd} != set(included):
            errors.append(f"{D.AGREEMENT_REPORT}: the latest round was drawn from a different set of videos than "
                          "the ones evaluated (a new round is needed)")
        if any(_athlete(v) in wd for v in last.get("subset") or []):
            errors.append(f"{D.AGREEMENT_REPORT}: the accepted round audited a withdrawn athlete's take; a new "
                          "round is needed")
        changed = [v for v in last.get("subset") or []
                   if (D.sha256_file(D.annotation_csv(root, D.PRIMARY_ANNOTATOR, v))
                       if os.path.exists(D.annotation_csv(root, D.PRIMARY_ANNOTATOR, v)) else None)
                   != (last.get("primary_snapshot") or {}).get(v)]
        if changed:
            errors.append(f"{D.AGREEMENT_REPORT}: {D.PRIMARY_ANNOTATOR} changed {len(changed)} audited take(s) after the "
                          f"round was drawn ({changed[:3]}); a new round is needed")
        res = measure_agreement(root, last["subset"], len(included), last.get("second"), intake_videos)
        rec = last["result"]
        if not res["complete"]:
            errors += res["problems"]
        elif not res["accepted"] or res["accepted"] != bool(rec.get("accepted")) or \
                [res["pooled"][x] for x in ("tp", "fp", "fn", "pull_mae_s")] != \
                [(rec.get("pooled") or {}).get(x) for x in ("tp", "fp", "fn", "pull_mae_s")]:
            errors.append(f"{D.AGREEMENT_REPORT}: the recorded result is not reproduced from the annotation files "
                          f"(recomputed: accepted={res['accepted']})")
    return errors


# ============================================================================ running the product
class EvaluationIntegrityError(RuntimeError):
    """Inputs no longer match the data lock / the annotations (file bytes, decoded frame count or fps)."""


class RecordingDetector:
    """Wraps the product detector and keeps its per-frame output for the Mode B replay."""

    def __init__(self, det):
        self.det, self.kts, self.bts = det, [], []

    def __call__(self, frame):
        k, b = self.det(frame)
        self.kts.append(k)
        self.bts.append(b)
        return k, b


class CachedDetector:
    """Mode B replay: LiveStreamer passes whatever is pushed straight to the detector, so the native
    frame index is pushed and the recorded Mode A detection for that frame is returned."""

    def __init__(self, kts, bts):
        self.kts, self.bts = kts, bts

    def __call__(self, i):
        return self.kts[i], self.bts[i]


def _load_cache(cpath: str, key: str) -> Optional[dict]:
    try:
        with np.load(cpath) as z:
            if str(z["cache_key"]) != key:
                return None
            return {k: z[k] for k in z.files}
    except Exception:                              # noqa: BLE001 - torn or unreadable cache = cache miss
        try:
            os.remove(cpath)
        except OSError:
            pass
        return None


def run_video(path: str, video_id: str, comp, cache_dir: str, mode_b: bool, cache_key: str,
              expected_frames: Optional[int], expected_fps: Optional[float], locked_sha: str) -> dict:
    """Mode A exactly as the product runs it. Only an exception raised by the frozen system itself
    (pose extraction or analysis) becomes SYSTEM_ERROR (zero predictions, pre-registration §5); the
    runner's own cache I/O never does. A Mode B exception only affects that preset on this video. The file
    is re-hashed before and after it is read: any difference from the data lock aborts the run."""
    from ai.core.pipeline import PoseTrack, analyze_pose, extract_pose

    def same_file(when: str):
        if D.sha256_file(path) != locked_sha:
            raise EvaluationIntegrityError(f"{video_id}: the file differs from the locked one ({when})")
    out = dict(result=None, system_error=None, mode_b={}, mode_b_errors={}, cache_error=None)
    os.makedirs(cache_dir, exist_ok=True)
    cpath = os.path.join(cache_dir, video_id + ".npz")
    same_file("before reading")
    z = _load_cache(cpath, cache_key) if os.path.exists(cpath) else None
    if z is not None:
        track = PoseTrack(pose=z["pose"], conf=z["conf"], tracker_log=json.loads(str(z["tracker_log"])),
                          container_fps=float(z["fps"]), width=int(z["wh"][0]), height=int(z["wh"][1]),
                          source_sha256=str(z["source_sha256"]) or None, timings_ms={})
        kts, bts = z["kts"], z["bts"]
    else:
        rec = RecordingDetector(comp.detector)
        try:
            track = extract_pose(path, dataclasses.replace(comp, detector=rec))
        except Exception:                          # noqa: BLE001 - a crash of the frozen SYSTEM on this video
            out["system_error"] = traceback.format_exc()[-3000:]
            if mode_b:
                out["mode_b_errors"] = {p: "not run: Mode A system error on this video" for p in MODE_B_PRESETS}
            return out
        same_file("after reading")
        kts, bts = np.stack(rec.kts), np.stack(rec.bts)
        try:                                       # the runner's own I/O: best effort, never a system error
            tmp = cpath + ".tmp.npz"
            np.savez_compressed(tmp, pose=track.pose, conf=track.conf, kts=kts, bts=bts, fps=track.container_fps,
                                wh=np.array([track.width, track.height]), source_sha256=track.source_sha256 or "",
                                cache_key=cache_key,
                                tracker_log=json.dumps(track.tracker_log,
                                                       default=lambda o: o.item() if hasattr(o, "item") else str(o)))
            os.replace(tmp, cpath)
        except Exception as e:                     # noqa: BLE001
            out["cache_error"] = f"{type(e).__name__}: {e}"
    if expected_frames is not None and track.n_frames != expected_frames:
        raise EvaluationIntegrityError(f"{video_id}: decoded {track.n_frames} frames, the intake check and the "
                                       f"annotation tool saw {expected_frames}: the annotations would not line up")
    if expected_fps and abs(track.container_fps - float(expected_fps)) > 0.01:
        raise EvaluationIntegrityError(f"{video_id}: container fps {track.container_fps}, intake {expected_fps}")
    try:
        out["result"] = json.loads(analyze_pose(track, comp, name=video_id).model_dump_json())
    except Exception:                              # noqa: BLE001 - a crash of the frozen SYSTEM on this video
        out["system_error"] = traceback.format_exc()[-3000:]
        if mode_b:
            out["mode_b_errors"] = {p: "not run: Mode A system error on this video" for p in MODE_B_PRESETS}
        return out
    if mode_b:
        from ai.core.live import LiveConfig, LiveStreamer
        for preset in MODE_B_PRESETS:
            try:
                ls = LiveStreamer(LiveConfig.preset(preset), CachedDetector(kts, bts), comp.lifter, comp.system,
                                  (float(track.width), float(track.height)), track.container_fps,
                                  thresholds=comp.thresholds, name=video_id)
                for i in range(len(kts)):
                    ls.push_frame(i)
                ls.finish()
                out["mode_b"][preset] = [dict(start=e.start, pull=e.pull, end=e.end, status=e.status) for e in ls.events]
            except Exception:                      # noqa: BLE001 - Mode B never blocks Mode A
                out["mode_b_errors"][preset] = traceback.format_exc()[-1500:]
    return out


# ============================================================================ dataset
def load_dataset(root: str, dry_run: bool = False, decode: bool = False,
                 withdrawn: Optional[List[str]] = None) -> dict:
    """Everything the run needs, checked against the files actually evaluated (pre-registration §2, §3.5).
    `decode` (used by --check-only and --lock) also decodes every included take exactly as the pipeline
    does and compares frames and fps with the intake record. `withdrawn`: athletes withdrawn by consent
    after the first lock (§3.6). The DEV dry run relaxes exactly these, and nothing else: the cohort /
    data-quality / G6 data rules (one athlete), the agreement record, the intake framing measurements,
    device and history, the recording dates, and one frame of END slack (the legacy 6-video ground truth is
    1-based; documented). The final run relaxes nothing."""
    errors: List[str] = []
    manifest, e1 = D.load_manifest(root)
    reps, e2 = D.load_final_reps(root)
    intake, e3 = D.load_intake(root)
    hist, e5 = D.history_rows(root)
    errors += e1 + e2 + e3 + e5
    ivids = (intake or {}).get("videos", {})
    if intake is not None and not dry_run:
        if intake.get("framing_checked") is not True:
            errors.append(f"{D.INTAKE_REPORT}: written without the framing check (detector); run the intake tool again")
        if intake.get("device") != D.INTAKE_DEVICE:
            errors.append(f"{D.INTAKE_REPORT}: framing check ran on {intake.get('device')!r}, pre-registered "
                          f"{D.INTAKE_DEVICE!r}")
        errors += D.intake_history_problems(ivids, hist)
    for stem, paths in D.duplicate_stems(root).items():
        errors.append(f"{stem}: the name occurs {len(paths)} times under videos/ ({paths})")
    files: Dict[str, str] = {}
    for stem, path in D.find_videos(root):
        files.setdefault(stem, path)
    for vid in sorted(set(manifest) - set(files)):
        errors.append(f"{vid}: listed in the manifest, no video file (remove the row of a deleted take)")
    for vid in sorted(set(files) - set(manifest)):
        errors.append(f"{vid}: video file not in the manifest")
    for vid in sorted(set(files) - set(ivids)):
        errors.append(f"{vid}: no intake verdict (run the intake check again)")
    for vid in sorted(set(ivids) - set(files)):
        errors.append(f"{vid}: intake verdict for a file that no longer exists (run the intake check again)")
    excluded = {vid: ivids[vid].get("reasons", []) for vid in sorted(files) if ivids.get(vid, {}).get("status") == "FAIL"}
    shas = {vid: D.sha256_file(files[vid]) for vid in sorted(files)}
    by_sha: Dict[str, List[str]] = {}
    for vid, s in shas.items():
        by_sha.setdefault(s, []).append(vid)
    errors += [f"identical files under different names: {', '.join(v)}" for v in by_sha.values() if len(v) > 1]
    for vid in sorted(files):
        if vid in ivids and ivids[vid].get("sha256") != shas[vid]:
            errors.append(f"{vid}: the file is not the one the intake check saw (sha256 differs or missing)")
    included = D.included_from(manifest, files, ivids)
    if not dry_run:
        errors += D.recording_date_problems(manifest, included)
    cv2v = _cv2_version()
    for vid in included:
        iv, rs = ivids.get(vid, {}), reps.get(vid) or []
        if not dry_run and not iv.get("framing"):
            errors.append(f"{vid}: the intake verdict has no framing measurements")
        n, fps = iv.get("n_frames"), iv.get("fps")
        if not n or not fps:
            errors.append(f"{vid}: the intake report has no frame count / fps")
        elif decode:
            pr = D.decode_probe(files[vid])
            if pr is None or pr["n_frames"] != n or abs(pr["fps"] - float(fps)) > 0.01:
                errors.append(f"{vid}: the pipeline decodes {pr} but the intake recorded {n} frames at {fps} fps")
        if not rs:
            errors.append(f"{vid}: no annotated reps in {D.FINAL_REPS}")
        elif n and any(r["end"] >= n + (1 if dry_run else 0) for r in rs):
            errors.append(f"{vid}: an annotated END is beyond the video's {n} frames")
        for who in sorted({r["annotator"] for r in rs}):
            if not who:
                errors.append(f"{vid}: {D.FINAL_REPS} rows without an annotator id")
                continue
            if who != D.PRIMARY_ANNOTATOR:
                errors.append(f"{vid}: final reps from annotator {who!r}; the pre-registered primary is "
                              f"{D.PRIMARY_ANNOTATOR!r}")
            errors += D.sidecar_problems(root, who, vid, shas[vid], n, fps, cv2v)
            own = D.read_annotator_csv(root, who, vid)
            key = lambda r: (r["start"], r["pull"], r["end"], r["confidence"])  # noqa: E731
            if own is not None and sorted(map(key, own)) != sorted(map(key, [r for r in rs if r["annotator"] == who])):
                errors.append(f"{vid}: {D.FINAL_REPS} differs from annotator {who!r}'s own file (run --finalize again)")
    for vid in reps:
        if vid not in manifest:
            errors.append(f"{D.FINAL_REPS}: reps for unknown video {vid}")
        elif vid in excluded:
            errors.append(f"{D.FINAL_REPS}: reps for the excluded take {vid}")
    agreement, e4 = D.load_agreement(root, included)
    if agreement is not None and not dry_run and not D.validate_agreement_record(agreement):
        e4 = e4 + verify_agreement(root, agreement, included, ivids, withdrawn)
    problems = D.cohort_problems(manifest, included)
    problems += D.quality_problems(manifest, reps, included)
    problems += D.g6_data_problems(manifest, reps, included)
    adir = os.path.join(root, "annotations", D.PRIMARY_ANNOTATOR)
    orphans = sorted(f[:-4] for f in os.listdir(adir) if f.endswith(".csv") and f[:-4] not in files) \
        if os.path.isdir(adir) else []
    primary_csv = {v: D.sha256_file(D.annotation_csv(root, D.PRIMARY_ANNOTATOR, v)) for v in included
                   if os.path.exists(D.annotation_csv(root, D.PRIMARY_ANNOTATOR, v))}
    return dict(manifest=manifest, reps=reps, intake=intake, agreement=agreement, files=files, excluded=excluded,
                included=included, shas={v: shas[v] for v in included}, all_shas=shas, primary_csv_sha256=primary_csv,
                orphan_annotations=orphans, withdrawn=sorted(withdrawn or []), coverage=D.coverage(included),
                coverage_problems=problems, file_errors=errors, agreement_errors=e4,
                relaxed=(e4 + problems) if dry_run else [], errors=errors + ([] if dry_run else e4 + problems))


def recording_order_problems(manifest: Dict[str, dict], included: List[str], tags: Dict[str, Optional[dict]]) -> List[str]:
    """The freezes must precede every recording (§6 step 0): each tag's creation date <= the earliest
    recording_date in the manifest (the emailed hash is the proof; this is the consistency check)."""
    first = D.earliest_recording_date(manifest, included)
    if not first:
        return []
    return [f"tag {t} was created on {info['date'][:10]}, after the first recording ({first}); every freeze must "
            "precede every recording (§6 step 0)" for t, info in tags.items() if info and info["date"][:10] > first]


# ============================================================================ ledger + outputs
def read_ledger(out_dir: str) -> List[dict]:
    p = os.path.join(out_dir, LEDGER)
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else []


def write_ledger(out_dir: str, entries: List[dict]):
    D.write_json_atomic(os.path.join(out_dir, LEDGER), entries)


def input_changes(prev: dict, fp: dict) -> dict:
    """What a re-run changes relative to an earlier run's inputs (recorded and reported)."""
    pv, nv = (prev.get("inputs") or {}).get("videos", {}), fp["videos"]
    pm, nm = (prev.get("inputs") or {}).get("meta", {}), fp["meta"]
    return dict(compared_with=prev.get("run_id"), added=sorted(set(nv) - set(pv)), removed=sorted(set(pv) - set(nv)),
                changed_videos=sorted(v for v in set(nv) & set(pv) if nv[v] != pv[v]),
                changed_meta=sorted(k for k in set(nm) | set(pm) if nm.get(k) != pm.get(k)))


def outputs_committed() -> Optional[bool]:
    """True when ai/outputs/phase6 has no uncommitted change (every earlier run is published)."""
    p = _git("status", "--porcelain", "--", OUT_REL)
    return None if p is None or p.returncode != 0 else not p.stdout.strip()


def ledger_matches_git(out_dir: str) -> bool:
    """A committed ledger must not be missing or different in the working tree."""
    committed = git_show("HEAD", f"{OUT_REL}/{LEDGER}")
    if committed is None:
        return True
    p = os.path.join(out_dir, LEDGER)
    return os.path.exists(p) and json.load(open(p, encoding="utf-8")) == json.loads(committed)


def _write_csv(path, cols, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)


def build_dev_dry_run(root: str):
    """The 6 internal DEV videos (one athlete, contaminated) laid out as a Phase 6 dataset in the final
    formats — a check of this runner end to end, NEVER Phase 6 evidence. Expected Mode A: IoU F1 0.8406,
    pull F1 0.8529; Mode B V2 0.806 (27/10/3), V3 0.8125 (26/8/4)."""
    import cv2
    src = [("correct_01", "correct", "good", "none"), ("correct_02", "correct", "good", "none"),
           ("correct_03", "correct", "good", "none"), ("asymmetry_01", "asymmetry", "deviated", "asymmetric_pull"),
           ("asymmetry_02", "asymmetry", "deviated", "asymmetric_pull"),
           ("asymmetry_03", "asymmetry", "deviated", "asymmetric_pull")]
    for sub in (os.path.join("videos", "A90"), "annotations", "qa"):
        os.makedirs(os.path.join(root, sub), exist_ok=True)
    gt = list(csv.DictReader(open(os.path.join(REPO_ROOT, "ai", "outputs", "manual_rep_annotations.csv"))))
    man, reps, intake = [], [], {}
    a1 = D.PRIMARY_ANNOTATOR
    for i, (name, label, form, dev) in enumerate(src, 1):
        vid = f"A90_rear_normal_take{i}"
        dst = os.path.join(root, "videos", "A90", vid + ".mp4")
        if not os.path.exists(dst):
            shutil.copyfile(os.path.join(REPO_ROOT, "ai", "data", "seated_row", label, name + ".mp4"), dst)
        pr = D.decode_probe(dst)
        sha = D.sha256_file(dst)
        man.append(dict(video_id=vid, athlete_id="A90", view="rear", tempo="normal", take=i, form_type=form,
                        deviation_type=dev, lighting_condition="mixed", clothing_category="dev",
                        camera_distance_m="", camera_height_m="", camera_angle_deg="", recording_date="",
                        notes=f"DRY RUN: internal DEV video {name}"))
        mine = [r for r in gt if r["video"] == name + ".mp4"]
        for r in mine:
            reps.append(dict(video_id=vid, rep_id=r["rep"], start_frame=r["start_frame"], pull_frame=r["pull_frame"],
                             end_frame=r["end_frame"], annotation_confidence="high", annotator=a1, notes=""))
        intake[vid] = dict(status="PASS", reasons=[], warnings=["dry run: not intake-checked"], sha256=sha,
                           n_frames=pr["n_frames"], fps=round(pr["fps"], 3), framing=None)
        own = D.annotation_csv(root, a1, vid)
        os.makedirs(os.path.dirname(own), exist_ok=True)
        _write_csv(own, D.REP_COLUMNS, [dict(rep_id=r["rep"], start_frame=r["start_frame"], pull_frame=r["pull_frame"],
                                             end_frame=r["end_frame"], annotation_confidence="high", notes="")
                                        for r in mine])
        D.write_json_atomic(D.annotation_meta(root, a1, vid),
                            dict(video_id=vid, annotator=a1, video_sha256=sha, n_frames=pr["n_frames"],
                                 fps=round(pr["fps"], 3), cv2=cv2.__version__, csv_sha256=D.sha256_file(own),
                                 status="complete", pending_marks=[]))
    _write_csv(os.path.join(root, *D.MANIFEST.split("/")), D.MANIFEST_COLUMNS, man)
    _write_csv(os.path.join(root, *D.FINAL_REPS.split("/")), D.FINAL_REP_COLUMNS, reps)
    D.write_json_atomic(os.path.join(root, *D.INTAKE_REPORT.split("/")),
                        dict(tool="dry run", tool_sha256=None, device=D.INTAKE_DEVICE, framing_checked=False,
                             videos=intake, cohort=dict(status="PASS", problems=["dry run: cohort rules not applied"])))
    D.write_json_atomic(os.path.join(root, *D.AGREEMENT_REPORT.split("/")),
                        dict(primary=a1, note="dry run: development ground truth, no agreement round", rounds=[]))


# ============================================================================ report
def _fmt(x):
    return "—" if x is None else (f"{x:.4f}" if isinstance(x, float) else str(x))


def render_markdown(rep: dict) -> str:
    ev, ident = rep["evaluation"], rep["identity"]
    rec = rep.get("verdict_of_record") or {}
    L = [f"# PHASE6_RESULTS — {rep['kind']}", "", f"**Verdict of this run: {ev['verdict']}**", "",
         "Verdict of record (pre-registration §7): " + (f"run {rec['run_id']} — **{rec.get('verdict')}**" if rec else "—"),
         "", f"Run {rep['run_id']} · commit `{(rep['commit'] or '?')[:10]}` · dataset fingerprint "
         f"`{rep['dataset']['fingerprint'][:16]}` · {len(rep['videos'])} videos from {len(ev['per_athlete'])} athletes", "",
         "**Frozen identity**: " + ("OK — system, rules, evaluation code and tools equal the tagged freeze"
                                   if not ident["problems"] else "DRIFT — " + "; ".join(ident["problems"][:6])), ""]
    for t, info in (ident.get("tags") or {}).items():
        L.append(f"- tag `{t}`: " + (f"commit `{info['commit']}` · {info['date']}" if info else "not present"))
    for rel, h in (ident.get("freeze_files") or {}).items():
        L.append(f"- `{rel}` sha256 `{h or '—'}`")
    L += [f"- NOTE: {n}" for n in ident["notes"]]
    if rep["dataset"].get("relaxed"):
        L += ["", f"Dry-run relaxations ({len(rep['dataset']['relaxed'])} rules not applied): "
              + "; ".join(rep["dataset"]["relaxed"][:4]) + (" …" if len(rep["dataset"]["relaxed"]) > 4 else "")]
    L += ["", "## Known limitations declared before any data (pre-registration §1)", ""]
    L += [f"- {k}: {v}" for k, v in KNOWN_LIMITATIONS.items()]
    L += ["- The results hold for takes that passed the intake check (product-detector framing, ≥ 1080p, 24–60 fps, "
          "one person). Multi-person scenes and lower-quality recordings are out of scope (§1).",
          "", "## Gates (pre-registered; decided exactly on the counts; NOT EVALUABLE counts as FAIL)", "",
          "Every gate must pass, so no multiplicity correction applies to a PASS; the intervals below are "
          "per slice and uncorrected.", "",
          "| gate | status | value | threshold | detail |", "|---|---|---|---|---|"]
    comp = ev.get("tempo_compliance", {})
    for k, v in ev["gates"].items():
        if k in ("G3_per_view", "G4_per_tempo"):
            for s, x in v.items():
                flag = KNOWN_LIMITATIONS.get(f"{k}:{s}", "")
                low = " · LOW SHARE > 10%" if (x.get("low_share") or 0) > LOW_SHARE_FLAG else ""
                tc = (f" · tempo-compliant takes {comp[s]['compliant']}/{comp[s]['n']}"
                      if k == "G4_per_tempo" and s in comp else "")
                L.append(f"| {k} · {s} | {x['status']} | F1 {_fmt(x.get('f1'))} | {x.get('threshold')} | athletes "
                         f"{x.get('n_athletes', '—')}{' · low_n' if x.get('low_n') else ''} · `low` reps "
                         f"{x.get('n_low', '—')} ({_fmt(x.get('low_share'))}){low}{tc} {x.get('reason', '')}"
                         f"{(' · ' + flag) if flag else ''} |")
        elif k == "G6_form_direction":
            L.append(f"| {k} | {v['status']} | {v.get('n_right')} right-direction types | {v.get('threshold')} | "
                     f"{KNOWN_LIMITATIONS[k]} |")
            for t, x in v["types"].items():
                if x.get("indicator") is None:
                    L.append(f"| · {t} | NO INDICATOR | | | |")
                    continue
                dirs = ", ".join(f"{a} {'+' if d is not None and d > 0 else '−' if d is not None else '·'}"
                                 for a, d in x.get("per_athlete", {}).items())
                L.append(f"| · {t} | {x['status']} | {x['indicator']}: within-cell difference {_fmt(x.get('difference'))} "
                         f"(95% CI {x.get('difference_ci95') or '—'}); raw rates {_fmt(x.get('rate_deviated'))} vs "
                         f"{_fmt(x.get('rate_good'))} | | matched reps {x['matched_reps_deviated']} / {x['matched_reps_good']} "
                         f"· athletes (direction): {dirs or '—'} {('· ' + x['reason']) if x.get('reason') else ''} |")
        else:
            flag = KNOWN_LIMITATIONS.get(k, "")
            detail = {a: b for a, b in v.items() if a not in ("status", "threshold", "thresholds")}
            L.append(f"| {k} | {v['status']} | {json.dumps(detail)} | {v.get('threshold', v.get('thresholds'))} | {flag} |")
    p = ev["pooled"]
    L += ["", "## Primary metrics (Mode A, protocol v2, abstentions counted as misses)", "",
          f"With {len(ev['per_athlete'])} athletes the 95% intervals (athlete bootstrap, 2,000 resamples; pooled, "
          "per view and per tempo) are wide; read them before reading small differences. Strict IoU F1 counts `low` "
          "reps as certain (not a gate).", "",
          "| slice | videos | athletes | IoU F1 | precision | recall | TP/FP/FN | `low` reps (share) | strict IoU F1 | "
          "pull F1 | pull MAE s | 95% CI IoU F1 |", "|---|---|---|---|---|---|---|---|---|---|---|---|"]

    def row(name, m, ci=None):
        i, q = m[IOU_RULE], m[PULL_RULE]
        flag = " ⚑" if (i.get("low_share") or 0) > LOW_SHARE_FLAG else ""
        return (f"| {name} | {m.get('n_videos', '—')} | {m.get('n_athletes', '—')} | {_fmt(i['f1'])} | "
                f"{_fmt(i['precision'])} | {_fmt(i['recall'])} | {i['tp']}/{i['fp']}/{i['fn']} | {i['n_low']} "
                f"({_fmt(i.get('low_share'))}){flag} | {_fmt(m.get('strict_iou_f1'))} | {_fmt(q['f1'])} | "
                f"{_fmt(i['pull_mae_s'])} | {ci or '—'} |")
    L.append(row("**pooled**", dict(n_videos=len(rep["videos"]), n_athletes=len(ev["per_athlete"]), **p),
                 ev["ci"].get("pooled_iou_f1")))
    for title, key in (("view", "per_view"), ("tempo", "per_tempo"), ("view × tempo", "per_view_tempo"),
                       ("athlete", "per_athlete"), ("form", "per_form"), ("lighting", "per_lighting")):
        for name, m in ev[key].items():
            L.append(row(f"{title}: {name}", m, ev["ci"].get(f"{key}:{name}")))
    L += ["", f"⚑ = more than {LOW_SHARE_FLAG:.0%} of the slice's annotated reps are `low` (don't-care).",
          f"Pooled recall 95% CI {ev['ci'].get('pooled_recall') or '—'}; pooled pull MAE 95% CI "
          f"{ev['ci'].get('pooled_pull_mae_s') or '—'} s.",
          f"Rep-count error per video (IoU rule): mean absolute {_fmt(p[IOU_RULE]['count_error_mean_abs'])}, "
          f"mean signed {_fmt(p[IOU_RULE]['count_error_mean_signed'])} reps. START / END MAE "
          f"{_fmt(p[IOU_RULE]['start_mae_s'])} / {_fmt(p[IOU_RULE]['end_mae_s'])} s.", ""]
    L += ["## Tempo compliance (reported, not gated; takes keep their recorded label)", "",
          "| tempo | takes checked | compliant | not compliant (median rep s / median rest s) |", "|---|---|---|---|"]
    for t, e in sorted(comp.items()):
        nc = ", ".join(f"{x['video_id']} ({x['median_rep_s']} / {x['median_rest_s']})" for x in e["not_compliant"])
        L.append(f"| {t} | {e['n']} | {e['compliant']} | {nc or '—'} |")
    ab = ev["abstentions"]
    L += ["", "## Abstentions and system errors (counted as misses everywhere above)", "",
          f"{ab['n']} of {len(rep['videos'])} videos; by status {json.dumps(ab['by_status'])}. Every included video "
          f"is a QA-passed seated-row set, so each one is a **false abstention** ({ab['n']}).", ""]
    if ab["videos"]:
        L += ["| video | status | reason |", "|---|---|---|"]
        L += [f"| {a['video_id']} | {a['status']} | {a['reason'][:200]} |" for a in ab["videos"]]
        L.append("")
    ans = ev["secondary"]["answered_only"]
    L += ["Secondary, **conditional** (not a gate): on the videos the system answered (status OK) only: " +
          (f"IoU F1 {_fmt(ans[IOU_RULE]['f1'])}, recall {_fmt(ans[IOU_RULE]['recall'])}." if ans
           else "no video was answered."), ""]
    if ev["secondary"]["mode_b"]:
        L += ["## Mode B (live presets, secondary — no gate; a video without a Mode B answer counts as misses)", "",
              "| preset | IoU F1 | pull F1 | TP/FP/FN | events dropped (non-OK decode) | videos with an error |",
              "|---|---|---|---|---|---|"]
        for pr in MODE_B_PRESETS:
            m = ev["secondary"]["mode_b"].get(pr)
            errs = ev["secondary"]["mode_b_errors"].get(pr, [])
            if m:
                L.append(f"| {pr} | {_fmt(m[IOU_RULE]['f1'])} | {_fmt(m[PULL_RULE]['f1'])} | "
                         f"{m[IOU_RULE]['tp']}/{m[IOU_RULE]['fp']}/{m[IOU_RULE]['fn']} | "
                         f"{ev['secondary']['mode_b_abstained_events'].get(pr, 0)} | {', '.join(errs) or '—'} |")
        L.append("")
    ag = rep["dataset"].get("agreement") or {}
    final_a1 = rep["dataset"].get("primary_csv_sha256") or {}
    L += ["## Annotation agreement (pre-registration §3.4; every round, failed ones included)", ""]
    for r in ag.get("rounds", []):
        res = r.get("result") or {}
        c = res.get("checks", {})
        snap = r.get("primary_snapshot") or {}
        changed = sum(1 for v, s in snap.items() if final_a1.get(v) != s)
        lowc = res.get("primary_low_marked_certain") or {}
        L.append(f"- round {r['round']} (second annotator `{r.get('second')}`, external salt `{r.get('salt')}`"
                 f"{', addendum ' + r['addendum'] if r.get('addendum') else ''}): subset {len(r.get('subset', []))} "
                 f"videos · accepted {res.get('accepted')} · IoU F1 {_fmt((c.get('iou_f1') or {}).get('value'))} · pull "
                 f"MAE {_fmt((c.get('pull_mae_s') or {}).get('value'))} s · tiers ({D.PRIMARY_ANNOTATOR}->second) "
                 f"{json.dumps(res.get('tier_agreement'))} · {D.PRIMARY_ANNOTATOR}'s `low` reps marked certain by the "
                 f"second: {lowc.get('n', '—')} of {lowc.get('of', '—')} · {D.PRIMARY_ANNOTATOR} files changed since this "
                 f"round was drawn: {changed} of {len(snap)} · notes: {'; '.join(r.get('notes', [])) or '—'}")
    if not ag.get("rounds"):
        L.append(f"- {ag.get('note', 'no agreement record')}")
    exc = rep["dataset"]["excluded"]
    L += ["", "## Exclusions, removed takes and intake history (all decided before the data lock)", "",
          f"{len(exc)} take(s) present and excluded (intake FAIL)." + ("" if exc else " None.")]
    if exc:
        L += ["", "| excluded take | intake reasons |", "|---|---|"] + [f"| {v} | {'; '.join(r)} |" for v, r in exc.items()]
    hist = rep["dataset"].get("intake_history_summary") or {}
    L += ["", f"Intake history: {hist.get('checked_files', 0)} file checks; FAIL reasons "
          f"{json.dumps(hist.get('fail_reasons', {}))}. Athletes checked at intake but not in the cohort: "
          f"{', '.join(hist.get('dropped_athletes', [])) or 'none'}; withdrawn by consent after the first lock: "
          f"{', '.join(rep['dataset'].get('withdrawn', [])) or 'none'}. {D.PRIMARY_ANNOTATOR} annotation files "
          f"without a current video: {', '.join(rep['dataset'].get('orphan_annotations', [])) or 'none'}.", ""]
    if hist.get("removed_files"):
        L += ["Every file that was intake-checked and is no longer evaluated:", "",
              "| file | sha256 | last intake status | reasons / warnings | now |", "|---|---|---|---|---|"]
        L += [f"| {x['video_id']} | `{x['sha256']}` | {x['last_status']} | "
              f"{'; '.join(x['reasons'] + x['warnings'])[:200]} | {x['now']} |" for x in hist["removed_files"]]
        L.append("")
    ad = rep.get("addenda") or {}
    L += [f"## Addenda ({ADDENDA_REL}; dated, may never change a rule)", "",
          f"sha256 now `{ad.get('sha256') or '—'}`, at the data lock `{ad.get('sha256_at_lock') or '—'}`."]
    L += [f"- {h}" for h in ad.get("headings", [])] or ["- none"]
    L += ["", "## Per video", "", "| video | status | certain reps (`low`) | predicted | IoU TP/FP/FN | pull TP/FP/FN |",
          "|---|---|---|---|---|---|"]
    for v in rep["videos"]:
        s = v["score"]
        L.append(f"| {v['video_id']} | {v['status']} | {s[IOU_RULE]['n_gt']} ({s[IOU_RULE].get('n_unc', 0)}) | "
                 f"{len(v['pred_reps'])} | {s[IOU_RULE]['tp']}/{s[IOU_RULE]['fp']}/{s[IOU_RULE]['fn']} | "
                 f"{s[PULL_RULE]['tp']}/{s[PULL_RULE]['fp']}/{s[PULL_RULE]['fn']} |")
    L += ["", "## Run history (this cohort)", "",
          "| run | status | verdict | re-run reason | evaluation fix | input changes |", "|---|---|---|---|---|---|"]
    for e in rep["ledger"]:
        L.append(f"| {e['run_id']} | {e['status']} | {e.get('verdict', '—')} | {e.get('rerun_reason') or '—'} | "
                 f"{e.get('evaluation_fix') or '—'} | {json.dumps(e.get('input_changes')) if e.get('input_changes') else '—'} |")
    if ident.get("evaluation_diff"):
        L += ["", "## Appendix: evaluation-code fix (git diff against the frozen tag)", "",
              f"sha256 of the full diff: `{ident['evaluation_diff'].get('sha256')}`", "", "```",
              (ident["evaluation_diff"].get("stat") or "").strip(), "```"]
    L += ["", "The verdict of record is the first completed run; only an `--evaluation-fix` re-run on byte-identical "
          "data replaces it, and every run is published (pre-registration §7). A FAIL is published as it is.", ""]
    return "\n".join(L)


# ============================================================================ evaluate
def evaluate(videos: List[dict], reps: Optional[Dict[str, List[dict]]] = None) -> dict:
    ev = apply_gates(videos)
    ev["per_view_tempo"] = group_metrics(videos, lambda v: f"{v['view']}/{v['tempo']}")
    ev["per_form"] = group_metrics(videos, lambda v: v["form_type"] if v["form_type"] == "good"
                                   else f"deviated:{v['deviation_type']}")
    ev["per_lighting"] = group_metrics(videos, lambda v: v.get("lighting_condition") or "unknown")
    ev["tempo_compliance"] = tempo_compliance(videos, reps or {})
    ci = {"pooled_iou_f1": bootstrap_ci(videos, "f1"), "pooled_recall": bootstrap_ci(videos, "recall"),
          "pooled_pull_mae_s": bootstrap_ci(videos, "pull_mae_s")}
    for key, attr in (("per_view", "view"), ("per_tempo", "tempo")):
        for name in ev[key]:
            ci[f"{key}:{name}"] = bootstrap_ci([v for v in videos if v[attr] == name], "f1")
    ev["ci"] = ci
    ab = [v for v in videos if v["status"] != "OK"]
    by_status: Dict[str, int] = {}
    for v in ab:
        by_status[v["status"]] = by_status.get(v["status"], 0) + 1
    ev["abstentions"] = dict(n=len(ab), n_false=len(ab), by_status=by_status,
                             videos=[dict(video_id=v["video_id"], status=v["status"], reason=v["guard_reason"])
                                     for v in ab])
    answered = [v for v in videos if v["status"] == "OK"]
    ev["secondary"] = dict(answered_only=pool([v["score"] for v in answered]) if answered else None,
                           mode_b={}, mode_b_abstained_events={}, mode_b_errors={})
    for preset in MODE_B_PRESETS:
        sc = [v["mode_b_score"][preset] for v in videos if preset in v.get("mode_b_score", {})]
        if sc:
            ev["secondary"]["mode_b"][preset] = pool(sc)
            ev["secondary"]["mode_b_abstained_events"][preset] = sum(v.get("mode_b_abstained", {}).get(preset, 0)
                                                                    for v in videos)
        errs = [v["video_id"] for v in videos if preset in v.get("mode_b_errors", {})]
        if errs:
            ev["secondary"]["mode_b_errors"][preset] = errs
    return ev


def intake_history_summary(root: str, included: List[str], all_shas: Optional[Dict[str, str]] = None,
                           withdrawn: Optional[List[str]] = None) -> dict:
    """From the append-only intake history: every file that was ever intake-checked and is no longer
    evaluated (failed, replaced, withdrawn by consent, or removed after passing), with its last status and
    what holds its view / tempo cell now, and every athlete checked at intake but absent from the cohort."""
    rows, _ = D.history_rows(root)
    wd = set(withdrawn or [])
    reasons: Dict[str, int] = {}
    last: Dict[tuple, dict] = {}
    for r in rows:
        if r.get("status") == "FAIL":
            for x in r.get("reasons", []):
                key = x.split(":")[0].split("(")[0].strip()[:60]
                reasons[key] = reasons.get(key, 0) + 1
        last[(r["video_id"], r["sha256"])] = r
    current = set((all_shas or {}).items())
    out = []
    for (vid, sha), r in sorted(last.items(), key=lambda kv: (str(kv[0][0]), str(kv[0][1]))):
        if (vid, sha) in current:
            continue
        p = D.parse_video_name(vid) if isinstance(vid, str) else None
        now = [v for v in included if p and D.parse_video_name(v) and
               (D.parse_video_name(v)["athlete"], D.parse_video_name(v)["view"], D.parse_video_name(v)["tempo"])
               == (p["athlete"], p["view"], p["tempo"])]
        out.append(dict(video_id=vid, sha256=(sha or "")[:12], last_status=r.get("status"),
                        reasons=list(r.get("reasons") or []), warnings=list(r.get("warnings") or []),
                        now=("withdrawn by consent after the first lock (§3.6)" if p and p["athlete"] in wd else
                             ("its view/tempo cell now holds " + ", ".join(now)) if now
                             else "removed; nothing evaluated in its view/tempo cell")))
    seen_aths = {D.parse_video_name(r["video_id"])["athlete"] for r in rows
                 if isinstance(r["video_id"], str) and D.parse_video_name(r["video_id"])}
    cohort_aths = {D.parse_video_name(v)["athlete"] for v in included}
    return dict(checked_files=len(rows), fail_reasons=reasons, removed_files=out,
                removed_after_passing=sum(x["last_status"] != "FAIL" for x in out),
                dropped_athletes=sorted(seen_aths - cohort_aths), withdrawn=sorted(wd))


# ============================================================================ main
def _addenda_info() -> dict:
    """The addenda as committed at HEAD (headings + sha256), and the sha256 recorded in the data lock."""
    text = git_show("HEAD", ADDENDA_REL)
    lock = git_show(lock_tag(), LOCK_REL)
    return dict(headings=addenda_headings(text),
                sha256=hashlib.sha256(text.replace("\r\n", "\n").encode()).hexdigest() if text is not None else None,
                sha256_at_lock=json.loads(lock).get("addenda_sha256") if lock else None)


def _print_identity(ident: dict):
    for t, info in ident["tags"].items():
        print(f"tag {t}: " + (f"commit {info['commit']} ({info['date']})" if info else "not present"))
    for rel, h in ident["freeze_files"].items():
        print(f"{rel}: sha256 {h or '—'}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=None, help="data root (default: phase6_dataset.default_root())")
    ap.add_argument("--out", default=None, help="dry runs only; the final run always writes ai/outputs/phase6")
    ap.add_argument("--device", default="cpu", choices=["cpu", "cuda"], help="cuda only for dry runs")
    ap.add_argument("--no-mode-b", action="store_true", help="dry runs only: skip the secondary Mode B readout")
    ap.add_argument("--dry-run-dev", action="store_true", help="end-to-end check on the 6 DEV videos (never Phase 6)")
    ap.add_argument("--check-only", action="store_true", help="run every check; run no model, write nothing")
    ap.add_argument("--quick", action="store_true", help="with --check-only: skip decoding every take")
    ap.add_argument("--lock", action="store_true", help="write docs/PHASE6_DATA_LOCK.json (then commit, tag, timestamp)")
    ap.add_argument("--rerun-reason", default=None, help="why a further final run is needed (logged; §7)")
    ap.add_argument("--evaluation-fix", default=None, metavar="A6-NN",
                    help="the addendum id documenting an evaluation-code fix (committed; logged with its diff; §7)")
    ap.add_argument("--withdraw", nargs="+", default=None, metavar="A06",
                    help="with --lock: re-lock after these athletes withdrew by consent (§3.6); needs --addendum")
    ap.add_argument("--addendum", default=None, metavar="A6-NN", help="with --withdraw: the committed addendum")
    a = ap.parse_args(argv)
    final = not a.dry_run_dev
    kind = FINAL if final else DRY
    if not final and (a.lock or a.root or a.evaluation_fix or a.withdraw):
        print("REFUSED: --lock, --root, --evaluation-fix and --withdraw are not used with --dry-run-dev.",
              file=sys.stderr)
        return 6
    if (a.withdraw or a.addendum) and not a.lock:
        print("REFUSED: --withdraw / --addendum only go with --lock.", file=sys.stderr)
        return 6
    if a.quick and not a.check_only:
        print("REFUSED: --quick only goes with --check-only.", file=sys.stderr)
        return 6
    if final and not (a.check_only or a.lock):
        refused = [f for f, bad in (("--device cuda", a.device != "cpu"), ("--no-mode-b", a.no_mode_b),
                                    ("--out", a.out is not None)) if bad]
        if refused:
            print(f"REFUSED: {', '.join(refused)} not allowed for the final run (pre-registration §6, §9).",
                  file=sys.stderr)
            return 6
    if final:
        root, out = a.root or D.default_root(), OUT_DEFAULT
        cloud = D.cloud_sync_root(root)
        if cloud:
            print(f"REFUSED: the data root {root} is inside the cloud-synced folder {cloud}; athletes' videos and "
                  "poses stay on this machine (pre-registration §2.1).", file=sys.stderr)
            return 2
    else:
        out = a.out or os.path.join(os.path.dirname(D.default_root()), "phase6_dry_run_dev")
        os.makedirs(out, exist_ok=True)
        root = os.path.join(out, "dataset")
        build_dev_dry_run(root)

    ident = check_identity(a.evaluation_fix)
    withdrawn = list(a.withdraw or []) or (lock_withdrawn() if final else [])
    data = load_dataset(root, dry_run=not final, decode=final and (a.lock or (a.check_only and not a.quick)),
                        withdrawn=withdrawn)
    if final and ident["tools"]:
        want = ident["tools"]["files"].get("ai/scripts/validate_recordings.py")
        if not want or (data["intake"] or {}).get("tool_sha256") != want:
            data["errors"].append("the intake report was not written by the frozen intake tool "
                                  f"(tag {ident['tools_tag']}); run ai/scripts/validate_recordings.py again")
    if final:
        data["errors"] += recording_order_problems(data["manifest"], data["included"],
                                                   {t: ident["tags"].get(t) for t in (FREEZE_TAG, ident["tools_tag"])})
    fp = D.dataset_fingerprint(root, data["included"], shas=data["shas"])
    if a.check_only:
        lock_tagged = tag_info(lock_tag()) is not None
        lock_p = check_lock(fp, ident) if final and lock_tagged else []
        _print_identity(ident)
        for title, items in (("IDENTITY", ident["problems"]), ("DATA", data["errors"]),
                             ("DATA LOCK", lock_p if lock_tagged or not final else ["not locked yet (§6 step 7)"])):
            print(f"{title}: {'OK' if not items else ''}")
            for x in items:
                print(f"  - {x}")
        return 3 if ident["problems"] or lock_p else 2 if data["errors"] else 0
    if ident["problems"] and final:
        print("REFUSED: the working tree is not the frozen candidate:\n  " + "\n  ".join(ident["problems"]),
              file=sys.stderr)
        return 3
    if data["errors"]:
        print("REFUSED: dataset problems (nothing was run):\n  " + "\n  ".join(data["errors"]), file=sys.stderr)
        return 2
    if a.lock:
        if a.evaluation_fix:
            print("REFUSED: --lock takes no --evaluation-fix.", file=sys.stderr)
            return 6
        extra, new_tag = {}, LOCK_TAGS[0]
        if a.withdraw:
            v1 = json.loads(git_show(LOCK_TAGS[0], LOCK_REL) or "{}")
            keep = {v: s for v, s in v1.get("videos", {}).items() if _athlete(v) not in set(a.withdraw)}
            probs = [m for ok, m in (
                (tag_info(LOCK_TAGS[0]) is not None and v1, f"no first lock ({LOCK_TAGS[0]}) to withdraw from"),
                (tag_info(LOCK_TAGS[1]) is None, f"{LOCK_TAGS[1]} already exists: one withdrawal re-lock only"),
                (not [e for e in read_ledger(out) if e["kind"] == FINAL],
                 "a final run has already started: deletion follows the run, no re-lock (§3.6)"),
                (addendum_present(a.addendum or ""), "needs --addendum A6-NN, a committed addendum naming the withdrawal"),
                (fp["videos"] == keep, "the current inputs are not the first lock minus the withdrawn athletes' takes"),
            ) if not ok]
            if probs:
                print("REFUSED:\n  " + "\n  ".join(probs), file=sys.stderr)
                return 4
            extra, new_tag = dict(withdrawn=sorted(a.withdraw), supersedes=(tag_info(LOCK_TAGS[0]) or {}).get("commit"),
                                  addendum=a.addendum), LOCK_TAGS[1]
        elif os.path.exists(LOCK_FILE) or tag_info(LOCK_TAGS[0]):
            print(f"REFUSED: {LOCK_REL} or tag {LOCK_TAGS[0]} already exists; the data lock is written once "
                  "(a consent withdrawal: --lock --withdraw A06 --addendum A6-NN).", file=sys.stderr)
            return 4
        D.write_json_atomic(LOCK_FILE, dict(
            created_utc=dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            statement="Every input of the Phase 6 run, fixed before the model sees any Phase 6 video.",
            fingerprint=fp["fingerprint"], videos=fp["videos"], meta=fp["meta"], n_videos=len(fp["videos"]),
            excluded=sorted(data["excluded"]), primary_annotator=D.PRIMARY_ANNOTATOR,
            addenda_sha256=_addenda_info()["sha256"], evaluation_files=file_hashes(EVALUATION_FILES),
            tags={t: (ident["tags"].get(t) or {}).get("commit") for t in (FREEZE_TAG, ident["tools_tag"])}, **extra))
        print(f"wrote {LOCK_REL} (fingerprint {fp['fingerprint'][:16]}). Next: commit it, tag {new_tag}, and email "
              f"the tag's commit hash to the supervisor (pre-registration §6 step 7); run within {LOCK_TO_RUN_DAYS} days.")
        return 0
    os.makedirs(out, exist_ok=True)
    lock_path = os.path.join(out, RUN_LOCK)
    if os.path.exists(lock_path):
        print(f"REFUSED: {lock_path} exists: another run is active, or a run died; check, then delete it.",
              file=sys.stderr)
        return 4
    if final:
        _print_identity(ident)
        lock_p = check_lock(fp, ident)
        if lock_p:
            print("REFUSED: data lock problems:\n  " + "\n  ".join(lock_p), file=sys.stderr)
            return 3
        if outputs_committed() is False:
            print(f"REFUSED: {OUT_REL} has uncommitted changes; commit every earlier run first (§6).", file=sys.stderr)
            return 4
        if not ledger_matches_git(out):
            print(f"REFUSED: the committed {LEDGER} is missing or different in the working tree.", file=sys.stderr)
            return 4
    try:
        ledger = read_ledger(out)
    except (OSError, ValueError):
        print(f"REFUSED: {LEDGER} is unreadable; restore it from git before any run.", file=sys.stderr)
        return 4
    prior = [e for e in ledger if e["kind"] == kind]
    if final:
        if prior and not a.rerun_reason:
            print("REFUSED: this cohort already has a final run in the ledger ("
                  + ", ".join(f"{e['run_id']} {e['status']}" for e in prior) + "). Any further run needs "
                  "--rerun-reason (pre-registration §7).", file=sys.stderr)
            return 4
        if prior and prior[0].get("dataset_fingerprint") != fp["fingerprint"]:
            print("REFUSED: a re-run must use byte-identical inputs to the first run (§7):\n  "
                  + json.dumps(input_changes(prior[0], fp)), file=sys.stderr)
            return 2
        if a.evaluation_fix and not prior:
            lock = json.loads(git_show(lock_tag(), LOCK_REL) or "{}")
            if not (addendum_present(a.evaluation_fix, lock_tag())
                    and lock.get("evaluation_files") == file_hashes(EVALUATION_FILES)):
                print("REFUSED: on the first run, an evaluation fix is allowed only if its addendum and the fixed "
                      "code were already in place at the data lock (§7).", file=sys.stderr)
                return 3
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
    except FileExistsError:
        print(f"REFUSED: {lock_path} exists: another run is active.", file=sys.stderr)
        return 4
    try:
        return _run(a, kind, final, root, out, ident, data, fp, ledger, prior)
    finally:
        try:
            os.remove(lock_path)
        except OSError:
            pass


def _run(a, kind, final, root, out, ident, data, fp, ledger, prior) -> int:
    from ai.core.pipeline import _git_commit
    run_id = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = os.path.join(out, "runs", run_id)
    entry = dict(run_id=run_id, kind=kind, status="started", commit=_git_commit(),
                 dataset_fingerprint=fp["fingerprint"], inputs=dict(videos=fp["videos"], meta=fp["meta"]),
                 identity_problems=ident["problems"], identity_notes=ident["notes"],
                 evaluation_fix=a.evaluation_fix, evaluation_diff_sha256=(ident.get("evaluation_diff") or {}).get("sha256"),
                 evaluation_diff_stat=(ident.get("evaluation_diff") or {}).get("stat"),
                 rerun_reason=a.rerun_reason, n_videos=len(data["included"]), tags=ident.get("tags"),
                 freeze_files=ident.get("freeze_files"),
                 input_changes=input_changes(prior[0], fp) if prior and final else None)
    ledger.append(entry)
    write_ledger(out, ledger)
    ledger_path = os.path.join(out, LEDGER)
    if final:                                   # a started run can never disappear without rewriting history
        if git_commit_paths([ledger_path], f"Phase 6 final run {run_id}: started") is None:
            entry.update(status="aborted", error="the ledger could not be committed before the run")
            write_ledger(out, ledger)
            print("REFUSED: could not commit the run ledger (git); nothing was run.", file=sys.stderr)
            return 4
    try:
        from ai.core.pipeline import load_components
        comp = load_components(device=a.device)
        key = system_id()
        cache = D.cache_dir(root)
        videos, results = [], {}
        for k, vid in enumerate(data["included"], 1):
            print(f"[{k}/{len(data['included'])}] {vid}", flush=True)
            m = data["manifest"][vid]
            iv = data["intake"]["videos"][vid]
            r = run_video(data["files"][vid], vid, comp, cache, not a.no_mode_b, f"{data['shas'][vid]}|{key}",
                          iv.get("n_frames"), iv.get("fps"), data["shas"][vid])
            gt = data["reps"][vid]
            if r["system_error"] is not None:
                res = dict(status="SYSTEM_ERROR", reps=[], guard=dict(reason="exception in the frozen system: "
                                                                      + r["system_error"].strip().splitlines()[-1]),
                           video=dict(fps=float(iv["fps"]), n_frames=iv["n_frames"]), traceback=r["system_error"])
            else:
                res = r["result"]
            fps = float(res["video"]["fps"])
            pred = predictions_for_scoring(res)
            mb_score, mb_drop = {}, {}
            if not a.no_mode_b:
                for preset in MODE_B_PRESETS:
                    evs = r["mode_b"].get(preset, [])
                    mb_score[preset] = score_video(mode_b_predictions(evs), gt, fps)
                    mb_drop[preset] = sum(e["status"] != "OK" for e in evs)
            videos.append(dict(video_id=vid, athlete=m["athlete_id"], view=m["view"], tempo=m["tempo"],
                               form_type=m["form_type"], deviation_type=m["deviation_type"],
                               lighting_condition=m.get("lighting_condition"), status=res["status"],
                               guard_reason=res["guard"]["reason"], fps=fps, n_frames=res["video"]["n_frames"],
                               pred_reps=[dict(start=x["start_frame"], pull=x["pull_frame"], end=x["end_frame"],
                                               indicators=x["indicators"]) for x in res["reps"]] if pred else [],
                               score=score_video(pred, gt, fps), mode_b_score=mb_score, mode_b_abstained=mb_drop,
                               mode_b_errors=r["mode_b_errors"], cache_error=r["cache_error"]))
            results[vid] = res                  # kept in memory: nothing per video is readable mid-run
        end_fp = D.dataset_fingerprint(root, data["included"])
        if end_fp["fingerprint"] != fp["fingerprint"]:
            raise EvaluationIntegrityError("the inputs changed during the run: " + json.dumps(input_changes(
                dict(inputs=dict(videos=fp["videos"], meta=fp["meta"])), end_fp)))
        ev = evaluate(videos, data["reps"])
        entry.update(status="scored", verdict=ev["verdict"])
        write_ledger(out, ledger)
        res_dir = os.path.join(root, "results", run_id)       # full per-video results stay with the data
        result_shas = {}
        for vid, res in results.items():
            rp = os.path.join(res_dir, vid + ".json")
            D.write_json_atomic(rp, res)
            result_shas[vid] = D.sha256_file(rp)
        mine = [e for e in ledger if e["kind"] == kind]
        rec = verdict_of_record(mine + [dict(entry, status="completed")])
        report = dict(kind=kind, run_id=run_id, commit=entry["commit"],
                      verdict_of_record=dict(run_id=rec["run_id"], verdict=rec.get("verdict")) if rec else None,
                      identity=dict(problems=ident["problems"], notes=ident["notes"], tags=ident.get("tags"),
                                    freeze_files=ident.get("freeze_files"),
                                    evaluation_changes=ident["evaluation_changes"],
                                    evaluation_diff=ident.get("evaluation_diff")),
                      dataset=dict(fingerprint=fp["fingerprint"], included=data["included"], excluded=data["excluded"],
                                   coverage=data["coverage"], coverage_problems=data["coverage_problems"],
                                   relaxed=data["relaxed"], agreement=data["agreement"], inputs=entry["inputs"],
                                   primary_csv_sha256=data.get("primary_csv_sha256", {}),
                                   orphan_annotations=data.get("orphan_annotations", []),
                                   withdrawn=data.get("withdrawn", []),
                                   intake_history_summary=intake_history_summary(root, data["included"],
                                                                                 data.get("all_shas"),
                                                                                 data.get("withdrawn")),
                                   per_video_results_sha256=result_shas),
                      addenda=_addenda_info(), evaluation=ev, videos=videos,
                      ledger=[dict(e, status="completed") if e is entry else dict(e) for e in mine])
        os.makedirs(run_dir, exist_ok=True)
        D.write_json_atomic(os.path.join(run_dir, "phase6_results.json"), report, default=str)
        with open(os.path.join(run_dir, "PHASE6_RESULTS.md"), "w", encoding="utf-8") as f:
            f.write(render_markdown(report))
        for name in ("phase6_results.json", "PHASE6_RESULTS.md"):   # top level = copies of the latest run
            shutil.copyfile(os.path.join(run_dir, name), os.path.join(out, name))
    except Exception:                                 # noqa: BLE001 - recorded in the ledger, then reported
        entry.update(status="crashed", error=traceback.format_exc()[-3000:])
        write_ledger(out, ledger)
        if final:
            git_commit_paths([ledger_path], f"Phase 6 final run {run_id}: crashed")
        traceback.print_exc()
        return 5
    entry.update(status="completed")
    entry["outputs"] = {f: D.sha256_file(os.path.join(run_dir, f)) for f in ("phase6_results.json", "PHASE6_RESULTS.md")}
    write_ledger(out, ledger)
    p = ev["pooled"]
    print(f"{kind}: verdict {ev['verdict']} | pooled IoU F1 {p[IOU_RULE]['f1']} (recall {p[IOU_RULE]['recall']}) | "
          f"pull F1 {p[PULL_RULE]['f1']} | report {os.path.join(out, 'PHASE6_RESULTS.md')}")
    if final:
        c = git_commit_paths([ledger_path, run_dir] + [os.path.join(out, n) for n in ("phase6_results.json",
                                                                                     "PHASE6_RESULTS.md")],
                             f"Phase 6 final run {run_id}: {ev['verdict']}")
        print(f"Committed as {c}. Email this commit hash and the verdict line to the supervisor (§6 step 9)." if c else
              f"COMMIT FAILED: commit {OUT_REL} by hand now, whatever the verdict (§6 step 9).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
