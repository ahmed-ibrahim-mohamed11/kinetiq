#!/usr/bin/env python3
"""Phase 6 rep annotation: mark START / PULL / END for every rep, frame-accurately and blind.

Definitions and rules: docs/PHASE6_ANNOTATION_GUIDE.md. The tool loads no model and shows nothing
but the video and your own marks (pre-registration §3.3). Frames are decoded once, sequentially, in
the same order as the analysis pipeline, so a frame number here is the same frame everywhere.

  python -m ai.scripts.annotate_reps --annotator a1                  # next take you have not finished
  python -m ai.scripts.annotate_reps --annotator a1 --video A01_side_slow_take1
  python -m ai.scripts.annotate_reps --annotator a1 --status          # done / in progress / missing
  python -m ai.scripts.annotate_reps --annotator a1 --finalize        # build annotations/reps.csv (a1 only)
  python -m ai.scripts.annotate_reps --annotator a2 --root <hand-off folder>   # the agreement subset only
  python -m ai.scripts.annotate_reps --annotator a1 --video X --restart       # file changed: archive, start again

The primary annotator (a1) annotates every take that did not FAIL the intake check; any other
annotator gets exactly the subset of the open agreement round (annotation_agreement.py). A take whose
file, frame count, fps or decoder differs from what its marks were made on is refused, never re-bound:
--restart archives the old marks and starts empty.

Keys:
  SPACE play / pause        A / D or LEFT / RIGHT   one frame back / forward
  S / W or DOWN / UP  10 frames back / forward      J / L   one second back / forward
  M  mark the next boundary of the current rep: START -> PULL -> END (END saves the rep)
  U  undo the last mark (or the last saved rep)     X  delete the rep under the cursor
  C  cycle the confidence of the rep under the cursor: high -> medium -> low
  N / P  jump to the next / previous rep            F  mark the take finished (or not finished)
  H  show / hide help                               Q or ESC  save and quit
Every change is saved immediately to annotations/<annotator>/<video_id>.csv (+ .meta.json).
"""
from __future__ import annotations

import argparse
import csv
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

BOUNDARIES = ("start", "pull", "end")
TOOL_VERSION = "2.0"


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ============================================================================ annotation state (pure)
class Annotation:
    """One annotator's marks for one video. GUI-free, so every rule here is unit-tested."""

    def __init__(self, video_id: str, n_frames: int, fps: float):
        self.video_id, self.n_frames, self.fps = video_id, int(n_frames), float(fps)
        self.reps: List[dict] = []          # {start, pull, end, confidence, notes}
        self.pending: List[int] = []        # marks of the rep in progress (0..2 frames)
        self.finished = False
        self._undo: List[tuple] = []

    # ---- marking
    def next_boundary(self) -> str:
        return BOUNDARIES[len(self.pending)]

    def mark(self, frame: int) -> str:
        """Add the next boundary at `frame`. Returns a message for the status line."""
        if not 0 <= frame < self.n_frames:
            return "frame outside the video"
        if self.pending and frame <= self.pending[-1]:
            return f"{self.next_boundary().upper()} must come after {BOUNDARIES[len(self.pending) - 1].upper()}"
        if not self.pending and self.rep_at(frame) is not None:
            return "START falls inside an existing rep"
        self.pending.append(int(frame))
        self._undo.append(("mark", int(frame)))
        if len(self.pending) < 3:
            return f"{BOUNDARIES[len(self.pending) - 1].upper()} at {frame}"
        s, p, e = self.pending
        clash = [r for r in self.reps if not (e < r["start"] or s > r["end"])]
        if clash:
            self.pending.pop()
            self._undo.pop()
            return f"rep {s}-{e} would overlap the rep {clash[0]['start']}-{clash[0]['end']}"
        rep = dict(start=s, pull=p, end=e, confidence="high", notes="")
        self.reps.append(rep)
        self.reps.sort(key=lambda r: r["start"])
        self.pending = []
        for i in reversed([i for i, op in enumerate(self._undo) if op[0] == "mark"][-3:]):
            del self._undo[i]                     # this rep's three marks become one undo step
        self._undo.append(("rep", rep))
        return f"rep saved: {s} / {p} / {e}"

    def undo(self) -> str:
        if not self._undo:
            return "nothing to undo"
        op = self._undo.pop()
        if op[0] == "mark":
            if self.pending:
                self.pending.pop()
                return "last mark removed"
            return "nothing to undo"
        if op[0] == "rep":
            r = op[1]
            if r in self.reps:
                self.reps.remove(r)
            self.pending = [r["start"], r["pull"]]
            self._undo += [("mark", r["start"]), ("mark", r["pull"])]
            return "last rep reopened (END removed)"
        if op[0] == "delete":
            self.reps.append(op[1])
            self.reps.sort(key=lambda r: r["start"])
            return "deleted rep restored"
        if op[0] == "conf":
            op[1]["confidence"] = op[2]
            return "confidence restored"
        return "nothing to undo"

    # ---- editing
    def rep_at(self, frame: int) -> Optional[dict]:
        for r in self.reps:
            if r["start"] <= frame <= r["end"]:
                return r
        return None

    def delete_at(self, frame: int) -> str:
        r = self.rep_at(frame)
        if r is None:
            return "no rep under the cursor"
        self.reps.remove(r)
        self._undo.append(("delete", r))
        return f"rep {r['start']}-{r['end']} deleted"

    def cycle_confidence(self, frame: int) -> str:
        r = self.rep_at(frame)
        if r is None:
            return "no rep under the cursor"
        old = r["confidence"]
        r["confidence"] = D.CONFIDENCES[(D.CONFIDENCES.index(old) + 1) % len(D.CONFIDENCES)]
        self._undo.append(("conf", r, old))
        return f"confidence {old} -> {r['confidence']}"

    def neighbour(self, frame: int, step: int) -> Optional[int]:
        starts = [r["start"] for r in self.reps]
        cand = [s for s in starts if (s > frame if step > 0 else s < frame)]
        return (min(cand) if step > 0 else max(cand)) if cand else None

    # ---- persistence
    def to_rows(self) -> List[dict]:
        return [dict(rep_id=i, start_frame=r["start"], pull_frame=r["pull"], end_frame=r["end"],
                     annotation_confidence=r["confidence"], notes=r.get("notes", ""))
                for i, r in enumerate(sorted(self.reps, key=lambda r: r["start"]), 1)]

    def save(self, csv_path: str, meta: dict):
        """CSV first, then the sidecar with the CSV's sha256 (so a hand edit shows up as a mismatch).
        `meta` carries the identity fields fixed when the take was first opened; they are never rewritten."""
        os.makedirs(os.path.dirname(csv_path), exist_ok=True)
        tmp = csv_path + ".tmp"
        with open(tmp, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=D.REP_COLUMNS)
            w.writeheader()
            w.writerows(self.to_rows())
        os.replace(tmp, csv_path)
        meta = dict(meta, status="complete" if self.finished else "in_progress", n_reps=len(self.reps),
                    pending_marks=list(self.pending), csv_sha256=D.sha256_file(csv_path), tool_version=TOOL_VERSION,
                    updated_utc=_now())
        D.write_json_atomic(csv_path[:-4] + ".meta.json", meta)

    @classmethod
    def load(cls, csv_path: str, video_id: str, n_frames: int, fps: float) -> "Annotation":
        a = cls(video_id, n_frames, fps)
        if os.path.exists(csv_path):
            with open(csv_path, newline="", encoding="utf-8") as f:
                for r in csv.DictReader(f):
                    a.reps.append(dict(start=int(r["start_frame"]), pull=int(r["pull_frame"]), end=int(r["end_frame"]),
                                       confidence=r["annotation_confidence"], notes=r.get("notes", "")))
            mp = csv_path[:-4] + ".meta.json"
            if os.path.exists(mp):
                m = json.load(open(mp, encoding="utf-8"))
                a.finished = m.get("status") == "complete"
                a.pending = [int(x) for x in m.get("pending_marks", [])]
                a._undo = [("mark", f) for f in a.pending]     # a restored open rep can still be undone
        a.reps.sort(key=lambda r: r["start"])
        return a


# ============================================================================ files
def annotation_path(root: str, annotator: str, video_id: str) -> str:
    return D.annotation_csv(root, annotator, video_id)


def read_meta(root: str, annotator: str, video_id: str) -> Optional[dict]:
    return D.read_sidecar(root, annotator, video_id)


def open_round_subset(root: str) -> Optional[List[str]]:
    """The subset of the open agreement round (drawn, not yet measured), or None."""
    p = os.path.join(root, *D.AGREEMENT_REPORT.split("/"))
    if not os.path.exists(p):
        return None
    rounds = json.load(open(p, encoding="utf-8")).get("rounds") or []
    return list(rounds[-1]["subset"]) if rounds and not rounds[-1].get("result") else None


def video_list(root: str, annotator: str = D.PRIMARY_ANNOTATOR) -> List[tuple]:
    """The takes this annotator works on: manifest takes with a file that did not FAIL the intake check;
    for any annotator other than the primary, only the open agreement round's subset."""
    man, _ = D.load_manifest(root) if os.path.exists(os.path.join(root, D.MANIFEST)) else ({}, [])
    failed = D.intake_failed(root)
    files = [(v, p) for v, p in D.find_videos(root) if v in man and v not in failed and D.parse_video_name(v)]
    if annotator != D.PRIMARY_ANNOTATOR:
        sub = set(open_round_subset(root) or [])
        files = [(v, p) for v, p in files if v in sub]
    return files


def status(root: str, annotator: str) -> dict:
    out = dict(complete=[], in_progress=[], missing=[])
    for v, _ in video_list(root, annotator):
        m = read_meta(root, annotator, v)
        out["missing" if m is None else "complete" if m.get("status") == "complete" and not m.get("pending_marks")
            else "in_progress"].append(v)
    return out


def identity_problems(old: Optional[dict], cur: dict) -> List[str]:
    """Why existing marks cannot be used on the file as it is now (empty = same file, decoder, frames)."""
    if not old:
        return []
    return [f"{k}: marks made on {old.get(k)!r}, now {cur[k]!r}" for k in ("video_sha256", "n_frames", "cv2")
            if old.get(k) != cur[k]] + \
           ([f"fps: marks made at {old.get('fps')}, now {cur['fps']}"]
            if old.get("fps") is None or abs(float(old["fps"]) - float(cur["fps"])) > 0.01 else [])


def restart(root: str, annotator: str, video_id: str) -> Optional[str]:
    """Archive the existing marks and sidecar (never deleted) so the take can be annotated afresh."""
    csv_path, meta_path = annotation_path(root, annotator, video_id), D.annotation_meta(root, annotator, video_id)
    if not (os.path.exists(csv_path) or os.path.exists(meta_path)):
        return None
    arch = os.path.join(os.path.dirname(csv_path), "archive")
    os.makedirs(arch, exist_ok=True)
    stamp = _now().replace(":", "")
    for p in (csv_path, meta_path):
        if os.path.exists(p):
            shutil.move(p, os.path.join(arch, f"{stamp}_{os.path.basename(p)}"))
    return arch


def finalize(root: str, annotator: str) -> int:
    """annotations/reps.csv from the PRIMARY annotator's complete files, each checked against its video
    and the intake record. Refuses (1) on any problem; nothing is written then."""
    if annotator != D.PRIMARY_ANNOTATOR:
        print(f"REFUSED: the final ground truth is the pre-registered primary annotator's ({D.PRIMARY_ANNOTATOR}).",
              file=sys.stderr)
        return 2
    import cv2
    intake, _ = D.load_intake(root)
    ivids = (intake or {}).get("videos", {})
    rows, problems = [], []
    vids = video_list(root, annotator)
    if not vids:
        problems.append("no takes to finalize (manifest, videos and intake report needed)")
    for v, path in vids:
        sha = D.sha256_file(path)
        iv = ivids.get(v, {})
        if iv.get("sha256") != sha:
            problems.append(f"{v}: the intake check did not see this file (run validate_recordings again)")
        problems += D.sidecar_problems(root, annotator, v, sha, iv.get("n_frames"), iv.get("fps"), cv2.__version__)
        reps = D.read_annotator_csv(root, annotator, v) or []
        if not reps:
            problems.append(f"{v}: no reps annotated")
        for k, r in enumerate(reps, 1):
            rows.append(dict(video_id=v, rep_id=k, start_frame=r["start"], pull_frame=r["pull"], end_frame=r["end"],
                             annotation_confidence=r["confidence"], annotator=annotator, notes=""))
    if problems:
        print("NOT FINALIZED:\n  " + "\n  ".join(problems), file=sys.stderr)
        return 1
    out = os.path.join(root, *D.FINAL_REPS.split("/"))
    tmp = out + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=D.FINAL_REP_COLUMNS)
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, out)
    _, errs = D.load_final_reps(root)
    if errs:
        print("written but INVALID:\n  " + "\n  ".join(errs), file=sys.stderr)
        return 1
    print(f"wrote {out}: {len(rows)} reps from {len({r['video_id'] for r in rows})} takes ({annotator})")
    return 0


# ============================================================================ GUI
def load_frames(path: str, max_side: int):
    import cv2
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        raise SystemExit(f"cannot decode {path}")
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
    frames = []
    while True:
        ok, fr = cap.read()
        if not ok:
            break
        h, w = fr.shape[:2]
        s = min(1.0, max_side / max(h, w))
        if s < 1.0:
            fr = cv2.resize(fr, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA)
        frames.append(cv2.imencode(".jpg", fr, [cv2.IMWRITE_JPEG_QUALITY, 90])[1])
        if len(frames) % 200 == 0:
            print(f"  decoded {len(frames)} frames", flush=True)
    cap.release()
    return frames, fps


HELP = ["SPACE play/pause   A/D or arrows: -/+1 frame   S/W: -/+10   J/L: -/+1 s",
        "M mark next (START>PULL>END)   U undo   X delete rep   C confidence",
        "N/P next/prev rep   F finished   H help   Q/ESC save+quit"]
COLORS = dict(high=(80, 200, 80), medium=(0, 200, 230), low=(60, 60, 230))


def prepare(root: str, annotator: str, video_id: str, path: str, n_frames: int, fps: float) -> Dict:
    """Identity of the take as decoded now; refuses (SystemExit) if existing marks were made on a
    different file, frame count, fps or decoder. Returns the sidecar identity fields to keep."""
    import cv2
    cur = dict(video_sha256=D.sha256_file(path), n_frames=int(n_frames), fps=round(float(fps), 3),
               cv2=cv2.__version__)
    old = read_meta(root, annotator, video_id)
    probs = identity_problems(old, cur)
    if probs:
        raise SystemExit(f"REFUSED: {video_id}: these marks were made on a different file or decoder "
                         f"({'; '.join(probs)}). Run again with --restart to archive them and start afresh.")
    keep = {k: (old or {}).get(k) for k in ("created_utc",)}
    return dict(video_id=video_id, annotator=annotator, **cur, created_utc=keep["created_utc"] or _now())


def run_gui(root: str, annotator: str, video_id: str, path: str, max_side: int) -> None:
    import cv2
    import numpy as np
    print(f"loading {video_id} ...", flush=True)
    frames, fps = load_frames(path, max_side)
    T = len(frames)
    meta = prepare(root, annotator, video_id, path, T, fps)
    csv_path = annotation_path(root, annotator, video_id)
    ann = Annotation.load(csv_path, video_id, T, fps)
    ann.save(csv_path, meta)
    cur, playing, show_help, msg = 0, False, True, "ready"
    win = f"GYM-AI annotate - {video_id} ({annotator})"
    cv2.namedWindow(win, cv2.WINDOW_NORMAL)
    keys = {ord("a"): -1, 2424832: -1, ord("d"): 1, 2555904: 1, ord("s"): -10, 2621440: -10,
            ord("w"): 10, 2490368: 10, ord("j"): -int(round(fps)), ord("l"): int(round(fps))}
    while True:
        img = cv2.imdecode(frames[cur], cv2.IMREAD_COLOR)
        h, w = img.shape[:2]
        bar = np.zeros((70, w, 3), np.uint8)
        for r in ann.reps:
            x0, x1 = int(r["start"] / T * w), max(int(r["end"] / T * w), int(r["start"] / T * w) + 1)
            cv2.rectangle(bar, (x0, 20), (x1, 50), COLORS[r["confidence"]], -1)
            xp = int(r["pull"] / T * w)
            cv2.line(bar, (xp, 14), (xp, 56), (255, 255, 255), 1)
        for p in ann.pending:
            cv2.line(bar, (int(p / T * w), 10), (int(p / T * w), 60), (0, 255, 255), 2)
        xc = int(cur / T * w)
        cv2.line(bar, (xc, 0), (xc, 69), (255, 0, 255), 2)
        top = np.zeros((48 + (22 * len(HELP) if show_help else 0), w, 3), np.uint8)
        r_here = ann.rep_at(cur)
        line1 = (f"frame {cur}/{T - 1}  t={cur / fps:6.2f}s  {'PLAY' if playing else 'PAUSE'}  next: "
                 f"{ann.next_boundary().upper()}  reps: {len(ann.reps)}  {'FINISHED' if ann.finished else ''}")
        line2 = (f"in rep {r_here['start']}-{r_here['end']} ({r_here['confidence']})   " if r_here else "") + msg
        cv2.putText(top, line1, (8, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1, cv2.LINE_AA)
        cv2.putText(top, line2, (8, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1, cv2.LINE_AA)
        if show_help:
            for i, t in enumerate(HELP):
                cv2.putText(top, t, (8, 62 + 22 * i), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (200, 200, 200), 1, cv2.LINE_AA)
        cv2.imshow(win, np.vstack([top, img, bar]))
        k = cv2.waitKeyEx(max(1, int(1000 / fps)) if playing else 0)
        if k == -1:
            if playing:
                cur = min(T - 1, cur + 1)
                playing = cur < T - 1
            continue
        kl = k & 0xFF if k < 256 else k
        if ord("A") <= kl <= ord("Z"):
            kl += 32                               # Shift / Caps Lock: same command
        changed = False
        if kl in (ord("q"), 27):
            break
        elif kl == ord(" "):
            playing = not playing
        elif kl in keys:
            playing, cur = False, int(min(T - 1, max(0, cur + keys[kl])))
        elif kl == ord("m"):
            msg, changed = ann.mark(cur), True
        elif kl == ord("u"):
            msg, changed = ann.undo(), True
        elif kl == ord("x"):
            msg, changed = ann.delete_at(cur), True
        elif kl == ord("c"):
            msg, changed = ann.cycle_confidence(cur), True
        elif kl in (ord("n"), ord("p")):
            nb = ann.neighbour(cur, 1 if kl == ord("n") else -1)
            if nb is not None:
                cur, playing = nb, False
        elif kl == ord("f"):
            if ann.pending:
                msg = "finish or undo the open rep first"
            else:
                ann.finished, changed = not ann.finished, True
                msg = "marked FINISHED" if ann.finished else "marked not finished"
        elif kl == ord("h"):
            show_help = not show_help
        if changed:
            ann.save(csv_path, meta)
    ann.save(csv_path, meta)
    cv2.destroyAllWindows()
    print(f"saved {csv_path}: {len(ann.reps)} reps, {'complete' if ann.finished else 'in progress'}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=None, help="data root (default: %%LOCALAPPDATA%%\\gym-ai\\phase6)")
    ap.add_argument("--annotator", required=True, help="pseudonymous id: a1 (primary), a2, ... — never a name")
    ap.add_argument("--video", default=None)
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--finalize", action="store_true", help="build annotations/reps.csv (primary annotator only)")
    ap.add_argument("--restart", action="store_true", help="with --video: archive this take's marks and start again")
    ap.add_argument("--max-side", type=int, default=1100, help="display size of the longer side (px)")
    a = ap.parse_args(argv)
    root = a.root or D.default_root()
    cloud = D.cloud_sync_root(root)
    if cloud:
        print(f"REFUSED: {root} is inside the cloud-synced folder {cloud} (pre-registration §2.1).", file=sys.stderr)
        return 2
    if not D.ANNOTATOR_RE.match(a.annotator):
        print("annotator id must be a1, a2, ... (pseudonymous; never a name)", file=sys.stderr)
        return 2
    if a.finalize:
        return finalize(root, a.annotator)
    if a.annotator != D.PRIMARY_ANNOTATOR and open_round_subset(root) is None:
        print(f"{a.annotator}: no open agreement round in this root; the second annotator works only on a round's "
              "subset (annotation_agreement.py --select-subset, then --handoff).", file=sys.stderr)
        return 2
    st = status(root, a.annotator)
    if a.status:
        for k in ("complete", "in_progress", "missing"):
            print(f"{k}: {len(st[k])}" + (f"  ({', '.join(st[k][:12])}{' ...' if len(st[k]) > 12 else ''})" if st[k] else ""))
        return 0
    vids = dict(video_list(root, a.annotator))
    if a.restart:
        if not a.video:
            print("--restart needs --video", file=sys.stderr)
            return 2
        arch = restart(root, a.annotator, a.video)
        print(f"archived the old marks of {a.video} in {arch}" if arch else f"{a.video}: nothing to archive")
    vid = a.video or next(iter(st["in_progress"] + st["missing"]), None)
    if vid is None:
        print("every take is annotated and finished" + ("; run --finalize" if a.annotator == D.PRIMARY_ANNOTATOR else ""))
        return 0
    if vid not in vids:
        print(f"{vid} is not in your queue (unknown, intake FAIL, not in the manifest, or not in the agreement "
              "subset)", file=sys.stderr)
        return 2
    try:
        run_gui(root, a.annotator, vid, vids[vid], a.max_side)
    except SystemExit as e:
        if isinstance(e.code, str):
            print(e.code, file=sys.stderr)
            return 2
        raise
    return 0


if __name__ == "__main__":
    sys.exit(main())
