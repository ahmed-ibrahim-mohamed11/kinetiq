#!/usr/bin/env python3
"""Write the Phase 6 freeze files and the messages for their annotated git tags.

  python -m ai.scripts.freeze_candidate            -> docs/PHASE6_FREEZE_CANDIDATE.json  (tag ai-core-candidate-v1)
  python -m ai.scripts.freeze_candidate --tools    -> docs/PHASE6_TOOLS_FREEZE.json      (tag phase6-tools-v1)
  python -m ai.scripts.freeze_candidate --tools --pilot-refreeze --addendum A6-NN
                                                   -> the ONE re-freeze of the tools after the pilot (tag
                                                      phase6-tools-v2), allowed only before the first recording

The candidate freeze has three parts:
  system      every file that defines the evaluated system (ai/core, the rep-segmentation artifact,
              both product checkpoints, the lifter config and vendored lifter code, the seated-row
              form thresholds, the pinned core requirements) + the key package versions and binaries;
  evaluation  the evaluation code and the pre-registration (phase6_evaluate.EVALUATION_FILES);
  rules       every gate, protocol, bootstrap, coverage and data-quality constant plus a battery of
              canonical cases run through the live code (phase6_evaluate.rules()).
The tools freeze pins the intake, annotation and agreement tools, the annotation guide and the recording
spec; it is cut only on a tree whose candidate part is intact (no evaluation drift, HEAD descends from the
candidate tag). `python -m ai.scripts.phase6_evaluate` reads both files FROM THEIR TAGS and refuses to run on
any drift (pre-registration §1, §6, §7).

Order: commit every pinned file first (this script refuses a pinned text file that git does not track or
that has uncommitted changes, so the tag reproduces exactly what is hashed); run this script; commit the
freeze file; create the annotated tag with the printed message; then email the supervisor the tag's commit
hash and the sha256 lines of the message (the external timestamp of pre-registration §6, step 0).
This script refuses to rewrite a freeze whose tag exists. Git tags are local and can be deleted, so the
emailed hash — not the tag — is the proof that the freeze preceded the recordings.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
from typing import Dict, List, Optional

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from ai.evaluation import phase6_dataset as D  # noqa: E402
from ai.scripts import phase6_evaluate as P6  # noqa: E402


def _git(*args) -> Optional[subprocess.CompletedProcess]:
    g = P6.find_git()
    return subprocess.run([g, *args], cwd=REPO_ROOT, capture_output=True) if g else None


def _tag_exists(tag: str) -> bool:
    p = _git("rev-parse", "-q", "--verify", f"refs/tags/{tag}")
    return p is not None and p.returncode == 0


def git_problems(rels: List[str]) -> List[str]:
    """Pinned text files must be tracked and committed (the tag must reproduce them); binaries that git
    ignores (the two checkpoints) are pinned by hash only."""
    if P6.find_git() is None:
        return ["git not found"]
    out = []
    for rel in rels:
        text = os.path.splitext(rel)[1].lower() in D.TEXT_EXT
        tracked = _git("ls-files", "--error-unmatch", "--", rel).returncode == 0
        if not tracked:
            if text:
                out.append(f"{rel}: not tracked by git (commit it before freezing)")
            continue
        st = _git("status", "--porcelain", "--", rel)
        if st.stdout.strip():
            out.append(f"{rel}: uncommitted changes (commit them before freezing)")
    return out


def _guard(path: str, tag: str, force: bool) -> bool:
    if _tag_exists(tag):
        print(f"REFUSED: tag {tag} already exists; the freeze it points to is not rewritten.", file=sys.stderr)
        return False
    if os.path.exists(path) and not force:
        print(f"REFUSED: {os.path.relpath(path, REPO_ROOT)} exists (use --force only before tagging).", file=sys.stderr)
        return False
    return True


def _write(path: str, d: dict):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(d, f, indent=1)
        f.write("\n")


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def freeze_candidate(force: bool) -> int:
    if not _guard(P6.FREEZE_FILE, P6.FREEZE_TAG, force):
        return 1
    from ai.core.model_artifact import DEFAULT_ARTIFACT, load_artifact
    system = load_artifact(DEFAULT_ARTIFACT)            # verifies the artifact's own fingerprint
    sysf = P6.file_hashes(P6.system_files())
    evalf = P6.file_hashes(P6.EVALUATION_FILES)
    missing = [f for f, h in {**sysf, **evalf}.items() if h is None]
    if missing:
        print(f"ERROR: pinned files missing: {missing}", file=sys.stderr)
        return 1
    gp = git_problems(sorted({**sysf, **evalf}))
    if gp:
        print("REFUSED:\n  " + "\n  ".join(gp), file=sys.stderr)
        return 1
    cfg = system.cfg
    art = os.path.relpath(DEFAULT_ARTIFACT, REPO_ROOT).replace(os.sep, "/")
    freeze = dict(
        tag=P6.FREEZE_TAG, created_utc=_now(),
        statement=("Phase 6 evaluates exactly this system with exactly these rules. No model, threshold or "
                   "configuration change is allowed before the Phase 6 evaluation (docs/PHASE6_PREREGISTRATION.md)."),
        artifact=dict(path=art, version=system.version, fingerprint=system.fingerprint),
        cfg={k: getattr(cfg, k) for k in cfg.__dataclass_fields__},
        mode_a=dict(device="cpu", detector="yolo11n-pose top-8 @640 + deterministic tracker",
                    lifter="MotionBERT-Lite (flip-TTA)", fps="container average fps",
                    backward_axis_sign="camera-derived (P2.7 anatomical sign REJECTED)"),
        rules=json.loads(json.dumps(P6.rules())),
        system=dict(files=sysf, packages=P6.key_packages()),
        evaluation=dict(files=evalf))
    _write(P6.FREEZE_FILE, freeze)
    msg = "\n".join([
        f"{P6.FREEZE_TAG}: frozen AI-core candidate for Phase 6 (final unseen-athlete validation)",
        "",
        "Phase 6 evaluates exactly this system with exactly these rules; no model/threshold/config change",
        "before it. Pre-registration: docs/PHASE6_PREREGISTRATION.md.",
        "",
        f"{P6.FREEZE_REL} sha256 {D.sha256_pinned(P6.FREEZE_FILE)}",
        f"docs/PHASE6_PREREGISTRATION.md sha256 {evalf['docs/PHASE6_PREREGISTRATION.md']}",
        f"rep-segmentation artifact {art} v{system.version}",
        f"  fingerprint {system.fingerprint}",
        f"  sha256      {sysf[art]}",
        f"YOLO11n-pose yolo11n-pose.pt sha256 {sysf['yolo11n-pose.pt']}",
        "MotionBERT-Lite best_epoch.bin sha256 "
        f"{sysf['ai/models/motionbert/FT_MB_lite_MB_ft_h36m_global_lite/best_epoch.bin']}",
        f"lifter config sha256 {sysf['motionbert/configs/pose3d/MB_ft_h36m_global_lite.yaml']}",
        f"seated-row form thresholds sha256 {sysf['ai/models/form_thresholds_v1_seated_row.json']}",
        "evaluation code sha256: " + ", ".join(f"{os.path.basename(k)} {v[:16]}" for k, v in evalf.items()),
        f"gates {json.dumps(freeze['rules']['gates'], sort_keys=True)}",
        f"cfg {json.dumps(freeze['cfg'], sort_keys=True)}",
        f"python {freeze['system']['packages']['python']}, torch {freeze['system']['packages']['torch']}, "
        f"ultralytics {freeze['system']['packages']['ultralytics']}, cv2 {freeze['system']['packages']['cv2']}",
        f"{len(sysf)} pinned system files, {len(evalf)} evaluation files (see the JSON).",
    ])
    return _finish(msg)


def freeze_tools(force: bool, pilot_refreeze: bool, addendum: Optional[str]) -> int:
    tag = P6.TOOLS_TAGS[1] if pilot_refreeze else P6.TOOLS_TAGS[0]
    if pilot_refreeze:
        probs = [m for ok, m in ((_tag_exists(P6.TOOLS_TAGS[0]), f"{P6.TOOLS_TAGS[0]} must exist first"),
                                 (not _tag_exists(tag), f"{tag} already exists: one pilot re-freeze only"),
                                 (P6.addendum_present(addendum or ""),
                                  "needs --addendum A6-NN, a committed addendum documenting the pilot defect")) if not ok]
        if probs:
            print("REFUSED:\n  " + "\n  ".join(probs), file=sys.stderr)
            return 1
    elif not _guard(P6.TOOLS_FREEZE_FILE, tag, force):
        return 1
    ident = P6.check_identity(tools_required=False)
    if ident["problems"]:
        print("REFUSED: the candidate part of the tree is not the frozen one:\n  " + "\n  ".join(ident["problems"]),
              file=sys.stderr)
        return 1
    files: Dict[str, Optional[str]] = P6.file_hashes(P6.TOOL_FILES)
    if any(h is None for h in files.values()):
        print(f"ERROR: tool files missing: {[f for f, h in files.items() if h is None]}", file=sys.stderr)
        return 1
    gp = git_problems(sorted(files))
    if gp:
        print("REFUSED:\n  " + "\n  ".join(gp), file=sys.stderr)
        return 1
    _write(P6.TOOLS_FREEZE_FILE, dict(tag=tag, created_utc=_now(),
                                      statement="Phase 6 intake, annotation and agreement tools, annotation guide and "
                                                "recording spec, frozen before any recording exists "
                                                "(pre-registration §2, §3).",
                                      builds_on=P6.FREEZE_TAG, pilot_refreeze_addendum=addendum if pilot_refreeze else None,
                                      files=files))
    msg = "\n".join([f"{tag}: Phase 6 intake / annotation / agreement tools, guide and spec, frozen before any "
                     "recording" + (f" (pilot re-freeze, addendum {addendum})" if pilot_refreeze else ""), "",
                     f"{P6.TOOLS_FREEZE_REL} sha256 {D.sha256_pinned(P6.TOOLS_FREEZE_FILE)}"]
                    + [f"{k} sha256 {v}" for k, v in files.items()])
    return _finish(msg)


_TAG_MSG_OUT = None


def _finish(msg: str) -> int:
    if _TAG_MSG_OUT:
        with open(_TAG_MSG_OUT, "w", encoding="utf-8", newline="\n") as f:
            f.write(msg + "\n")
    print(msg)
    return 0


def main(argv=None) -> int:
    global _TAG_MSG_OUT
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tools", action="store_true", help="write the tools freeze instead of the candidate freeze")
    ap.add_argument("--pilot-refreeze", action="store_true", help="with --tools: the one re-freeze after the pilot")
    ap.add_argument("--addendum", default=None, help="with --pilot-refreeze: the committed addendum id")
    ap.add_argument("--force", action="store_true", help="overwrite an existing, not yet tagged freeze file")
    ap.add_argument("--tag-message-out", default=None)
    a = ap.parse_args(argv)
    _TAG_MSG_OUT = a.tag_message_out
    if a.pilot_refreeze and not a.tools:
        ap.error("--pilot-refreeze goes with --tools")
    return freeze_tools(a.force, a.pilot_refreeze, a.addendum) if a.tools else freeze_candidate(a.force)


if __name__ == "__main__":
    sys.exit(main())
