"""Phase 6 (final unseen-athlete validation) dataset conventions — FROZEN with the pre-registration
(docs/PHASE6_PREREGISTRATION.md, git tag `ai-core-candidate-v1`).

One definition of the file naming, the coverage and data-quality rules and the manifest / annotation
/ intake / agreement formats, shared by the evaluation runner (ai/scripts/phase6_evaluate.py) and the
intake and annotation tools. Tools import these definitions; they never redefine them.

Dataset root (default `default_root()`: %LOCALAPPDATA%\\gym-ai\\phase6 on Windows — OUTSIDE the repository
and outside any cloud-synced folder; the athletes' videos and derived poses never leave this machine):
  videos/<athlete>/<athlete>_<view>_<tempo>_take<N>.<mp4|mov>    e.g. videos/A01/A01_side_paused_take1.mp4
  manifest.csv                  one row per video (MANIFEST_COLUMNS)
  qa/intake_report.json         validate_recordings.py: the CURRENT verdict per file
  qa/intake_history.jsonl       validate_recordings.py: append-only history of every check
  qa/annotation_agreement.json  annotation_agreement.py: agreement rounds (append-only)
  annotations/<annotator>/<video_id>.csv        per-annotator rep marks (REP_COLUMNS)
  annotations/<annotator>/<video_id>.meta.json  sidecar: video sha256, frames, fps, decoder, status
  annotations/reps.csv          FINAL ground truth (FINAL_REP_COLUMNS), built by annotate_reps --finalize
  cache/                        the runner's per-video pose caches (biometric: never in the repository)

Frames are 0-based indices in the order OpenCV decodes the file sequentially — the same order the
product pipeline (ai/core/pipeline.py) reads it — so annotation and prediction frames are the same
frames by construction. Times are frame / the container's average fps (the production default).
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import re
from typing import Dict, List, Optional, Tuple

NAME_RE = re.compile(r"^(?P<athlete>A\d{2,3})_(?P<view>rear|side|oblique)_"
                     r"(?P<tempo>normal|slow|paused|fast)_take(?P<take>[1-9]\d*)$")
VIDEO_EXTS = (".mp4", ".mov")
VIEWS = ("rear", "side", "oblique")                         # exactly these three (spec v1.1)
TEMPOS = ("normal", "slow", "paused", "fast")
MANDATORY_VIEWS = ("rear", "side")                          # every tempo recorded from each of these
MANDATORY_TEMPOS = ("normal", "slow", "paused", "fast")
REQUIRED_VIEWS = ("rear", "side", "oblique")                # every athlete has all three
MIN_ATHLETES = 10
FORM_TYPES = ("good", "deviated")
DEVIATIONS = ("asymmetric_pull", "excessive_trunk_motion", "reduced_rom", "delayed_side_onset",
              "inconsistent_tempo")
# Form-indicator sanity gate: the indicator each deviation type should raise (None = no v1 indicator)
INDICATOR_FOR_DEVIATION = {"asymmetric_pull": "lr_amplitude_ratio",
                           "excessive_trunk_motion": "trunk_motion",
                           "reduced_rom": "rom_proxy",
                           "delayed_side_onset": "lr_lag",
                           "inconsistent_tempo": None}
CONFIDENCES = ("high", "medium", "low")                     # low = don't-care (protocol v2 uncertain)
LIGHTING = ("bright", "dim", "mixed")
PRIMARY_ANNOTATOR = "a1"                                    # pre-registered; second annotator e.g. "a2"
ANNOTATOR_RE = re.compile(r"^a\d{1,2}$")                    # pseudonymous ids only (no names)
# ---- data-quality rules checked from the annotations BEFORE the run (data only, nothing unblinded) ----
MIN_CERTAIN_REPS_PER_TAKE = 3
LOW_TIER_MAX_SHARE = 0.10                                   # of all annotated reps
LOW_TIER_MAX_SHARE_SLICE = 0.20                             # within any view or tempo slice
G6_MIN_REPS, G6_MIN_ATHLETES = 10, 2                        # per side (deviated / paired good), see §4.3

MANIFEST_COLUMNS = ["video_id", "athlete_id", "view", "tempo", "take", "form_type", "deviation_type",
                    "lighting_condition", "clothing_category", "camera_distance_m", "camera_height_m",
                    "camera_angle_deg", "recording_date", "notes"]
REP_COLUMNS = ["rep_id", "start_frame", "pull_frame", "end_frame", "annotation_confidence", "notes"]
FINAL_REP_COLUMNS = ["video_id", "rep_id", "start_frame", "pull_frame", "end_frame",
                     "annotation_confidence", "annotator", "notes"]
FINAL_REPS = "annotations/reps.csv"
MANIFEST = "manifest.csv"
INTAKE_REPORT = "qa/intake_report.json"
INTAKE_HISTORY = "qa/intake_history.jsonl"
INTAKE_STATUSES = ("PASS", "WARN", "FAIL")                  # FAIL = excluded before any model run
INTAKE_DEVICE = "cpu"                                       # pre-registered device of the framing check
# qa/intake_report.json is written ONLY by validate_recordings.py and reflects exactly the files present:
#   tool_sha256, device, framing_checked, versions, created_utc,
#   videos {video_id: {status, reasons, warnings, sha256, n_frames, fps, width, height, duration_s, framing}},
#   cohort {status, problems}
# (--no-detector writes qa/intake_report_technical.json instead, which the evaluation never reads).
# n_frames / fps are what decode_probe() returns (the pipeline's own sequential decode).
# qa/intake_history.jsonl: one JSON object per line, appended for every detector check, never rewritten:
#   {checked_utc, video_id, path, sha256, status, reasons, warnings, scope, tool_sha256}
HISTORY_KEYS = ("video_id", "sha256", "status", "reasons")
# qa/annotation_agreement.json (rounds are appended; a round's result is written once):
#   {primary: "a1", rounds: [{round: 1..n, salt: str (external value, see select_agreement_subset),
#     created_utc, second: "a2", addendum: "A6-NN" | None, eligible: [video_id],
#     primary_snapshot: {video_id: sha256_file(a1's CSV)} for every eligible take, subset: [video_id],
#     result: None | {reference, second, accepted, checks, pooled, tier_agreement, measured_utc}, notes: [str]}]}
ROUND_KEYS = ("round", "salt", "second", "eligible", "primary_snapshot", "subset", "result", "notes")
AGREEMENT_REPORT = "qa/annotation_agreement.json"
SUBSET_SEED = 20261010
AGREE_MIN_IOU_F1, AGREE_MAX_PULL_MAE_S = 0.90, 0.15         # pre-registration §3.4
AGREE_MIN_VIDEOS, AGREE_MIN_FRACTION = 8, 0.10


def _p(root: str, rel: str) -> str:
    return os.path.join(root, *rel.split("/"))


# ============================================================================ locations
def default_root() -> str:
    """Phase 6 data root: $GYM_AI_PHASE6_ROOT, else a per-user local folder outside the repository."""
    env = os.environ.get("GYM_AI_PHASE6_ROOT")
    if env:
        return env
    base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), ".local", "share")
    return os.path.join(base, "gym-ai", "phase6")


def cloud_sync_root(path: str) -> Optional[str]:
    """The cloud-synced folder `path` lies in (OneDrive personal/business), or None."""
    target = os.path.normcase(os.path.realpath(os.path.abspath(path)))
    for var in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        base = os.environ.get(var)
        if base:
            b = os.path.normcase(os.path.realpath(os.path.abspath(base)))
            if target == b or target.startswith(b.rstrip("\\/") + os.sep):
                return base
    return None


def cache_dir(root: str) -> str:
    return _p(root, "cache")


def annotation_csv(root: str, annotator: str, video_id: str) -> str:
    return os.path.join(root, "annotations", annotator, video_id + ".csv")


def annotation_meta(root: str, annotator: str, video_id: str) -> str:
    """Sidecar written by the annotation tool: video_sha256, n_frames, fps, cv2 version, status."""
    return os.path.join(root, "annotations", annotator, video_id + ".meta.json")


# ============================================================================ files
def parse_video_name(stem: str) -> Optional[dict]:
    """'A01_side_paused_take2' -> {athlete, view, tempo, take}; None if the name is not valid."""
    m = NAME_RE.match(stem)
    if not m:
        return None
    d = m.groupdict()
    d["take"] = int(d["take"])
    return d


def find_videos(root: str) -> List[Tuple[str, str]]:
    """All files under <root>/videos with a video extension -> [(stem, path)], sorted by stem then path.
    Names are NOT validated here (the intake report does that); repeated stems are possible and are
    reported by duplicate_stems()."""
    out = []
    base = os.path.join(root, "videos")
    for dirpath, _, files in os.walk(base):
        for f in files:
            stem, ext = os.path.splitext(f)
            if ext.lower() in VIDEO_EXTS:
                out.append((stem, os.path.join(dirpath, f)))
    return sorted(out)


def duplicate_stems(root: str) -> Dict[str, List[str]]:
    """Stems that occur more than once (another folder, .mov + .mp4, ...) -> their paths."""
    seen: Dict[str, List[str]] = {}
    for stem, path in find_videos(root):
        seen.setdefault(stem, []).append(path)
    return {s: ps for s, ps in seen.items() if len(ps) > 1}


def decode_probe(path: str) -> Optional[dict]:
    """Frame count and fps exactly as the product pipeline decodes the file (ai/core/pipeline.py
    extract_pose: cv2.VideoCapture with the default backend, cap.read() until it fails, the container's
    CAP_PROP_FPS). Every frame index in the annotations refers to this sequence. None if undecodable."""
    import cv2
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        return None
    try:
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        n = 0
        while cap.read()[0]:
            n += 1
    finally:
        cap.release()
    return dict(n_frames=n, fps=fps) if n and fps > 0 else None


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


TEXT_EXT = {".py", ".csv", ".json", ".yaml", ".yml", ".md", ".txt", ".ini", ".toml"}


def sha256_pinned(path: str) -> str:
    """Hash used for every pinned file (freeze, tools freeze, intake tool self-hash): text files with
    CRLF -> LF normalisation (identical on Windows autocrlf checkouts and Linux clones), binaries raw."""
    if os.path.splitext(path)[1].lower() not in TEXT_EXT:
        return sha256_file(path)
    with open(path, "rb") as f:
        return hashlib.sha256(f.read().replace(b"\r\n", b"\n")).hexdigest()


def write_json_atomic(path: str, obj, **kw):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=1, **kw)
    os.replace(tmp, path)


def _read_csv(path: str) -> List[dict]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


# ============================================================================ manifest + coverage
def load_manifest(root: str) -> Tuple[Dict[str, dict], List[str]]:
    """manifest.csv -> ({video_id: row}, errors). Every row must match its own file name."""
    path = _p(root, MANIFEST)
    if not os.path.exists(path):
        return {}, [f"{MANIFEST} missing"]
    rows, errors = _read_csv(path), []
    if rows and set(MANIFEST_COLUMNS) - set(rows[0]):
        return {}, [f"{MANIFEST}: missing columns {sorted(set(MANIFEST_COLUMNS) - set(rows[0]))}"]
    out = {}
    for i, r in enumerate(rows, 2):
        vid = (r.get("video_id") or "").strip()
        p = parse_video_name(vid)
        if p is None:
            errors.append(f"{MANIFEST} line {i}: video_id {vid!r} does not follow A01_<rear|side|oblique>_"
                          f"<normal|slow|paused|fast>_takeN")
            continue
        if vid in out:
            errors.append(f"{MANIFEST} line {i}: duplicate video_id {vid}")
        for k in ("athlete", "view", "tempo"):
            col = "athlete_id" if k == "athlete" else k
            if (r.get(col) or "").strip() != p[k]:
                errors.append(f"{MANIFEST} line {i}: {col}={r.get(col)!r} disagrees with the file name ({p[k]})")
        if str(r.get("take", "")).strip() != str(p["take"]):
            errors.append(f"{MANIFEST} line {i}: take={r.get('take')!r} disagrees with the file name")
        ft, dt = (r.get("form_type") or "").strip(), (r.get("deviation_type") or "").strip()
        if ft not in FORM_TYPES:
            errors.append(f"{MANIFEST} line {i}: form_type {ft!r} not in {FORM_TYPES}")
        elif ft == "good" and dt != "none":
            errors.append(f"{MANIFEST} line {i}: good-form take must have deviation_type 'none'")
        elif ft == "deviated" and dt not in DEVIATIONS:
            errors.append(f"{MANIFEST} line {i}: deviated take needs deviation_type in {DEVIATIONS}")
        out[vid] = {k: (v.strip() if isinstance(v, str) else v) for k, v in r.items()}
    return out, errors


def coverage(video_ids: List[str]) -> Dict[str, dict]:
    """Per athlete: which (view, tempo) cells exist and what is missing from the mandatory protocol."""
    by_ath: Dict[str, set] = {}
    for vid in video_ids:
        p = parse_video_name(vid)
        if p:
            by_ath.setdefault(p["athlete"], set()).add((p["view"], p["tempo"]))
    out = {}
    for a, cells in sorted(by_ath.items()):
        views = sorted({v for v, _ in cells})
        missing = [f"{v}/{t}" for v in MANDATORY_VIEWS for t in MANDATORY_TEMPOS if (v, t) not in cells]
        missing += [f"{v}/any tempo" for v in REQUIRED_VIEWS if v not in MANDATORY_VIEWS and v not in views]
        out[a] = dict(views=views, cells=sorted(f"{v}/{t}" for v, t in cells), missing_mandatory=missing,
                      enough_views=all(v in views for v in REQUIRED_VIEWS))
    return out


DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def recording_date_problems(manifest: Dict[str, dict], video_ids: List[str]) -> List[str]:
    """Every evaluated take needs recording_date = YYYY-MM-DD (the freezes must precede it, §6 step 0)."""
    return [f"{v}: manifest recording_date {manifest[v].get('recording_date')!r} is not YYYY-MM-DD"
            for v in video_ids if v in manifest and not DATE_RE.match((manifest[v].get("recording_date") or "").strip())]


def earliest_recording_date(manifest: Dict[str, dict], video_ids: List[str]) -> Optional[str]:
    ds = [(manifest[v].get("recording_date") or "").strip() for v in video_ids if v in manifest]
    ds = [d for d in ds if DATE_RE.match(d)]
    return min(ds) if ds else None


def included_from(manifest: Dict[str, dict], files: Dict[str, str], intake_videos: Dict[str, dict]) -> List[str]:
    """The takes the evaluation scores: in the manifest, with a file, and not FAIL at intake."""
    return sorted(v for v in manifest if v in files and (intake_videos.get(v) or {}).get("status") != "FAIL")


def included_takes(root: str) -> List[str]:
    man, _ = load_manifest(root) if os.path.exists(_p(root, MANIFEST)) else ({}, [])
    p = _p(root, INTAKE_REPORT)
    iv = json.load(open(p, encoding="utf-8-sig")).get("videos", {}) if os.path.exists(p) else {}
    return included_from(man, dict(find_videos(root)), iv)


def cohort_problems(manifest: Dict[str, dict], video_ids: List[str]) -> List[str]:
    """Manifest-level cohort rules (pre-registration §2.2): athletes, mandatory view/tempo cells, form rows.
    The intake cohort check and the final run apply exactly this."""
    cov = coverage(video_ids)
    out = [f"{len(cov)} athletes with usable takes < {MIN_ATHLETES}"] if len(cov) < MIN_ATHLETES else []
    out += [f"{a}: missing mandatory view/tempo cells {c['missing_mandatory']}" for a, c in cov.items()
            if c["missing_mandatory"]]
    return out + form_coverage(manifest, video_ids)


def form_coverage(manifest: Dict[str, dict], video_ids: List[str]) -> List[str]:
    """Pre-registration §2.2 form rows (manifest level): per athlete AND per required view >= 1 good take
    and >= 1 deviated take, BOTH at tempo `normal`; every indicator-mapped deviation type deviated at
    tempo `normal` by >= 2 athletes."""
    problems = []
    athletes = sorted({manifest[v]["athlete_id"] for v in video_ids if v in manifest})
    for a in athletes:
        for view in REQUIRED_VIEWS:
            rows = [manifest[v] for v in video_ids if v in manifest and manifest[v]["athlete_id"] == a
                    and manifest[v]["view"] == view and manifest[v]["tempo"] == "normal"]
            if not any(r["form_type"] == "good" for r in rows):
                problems.append(f"{a}/{view}: no good-form take at normal tempo")
            if not any(r["form_type"] == "deviated" for r in rows):
                problems.append(f"{a}/{view}: no deviated take at normal tempo")
    for dev, ind in INDICATOR_FOR_DEVIATION.items():
        if ind is None:
            continue
        n = len({manifest[v]["athlete_id"] for v in video_ids if v in manifest
                 and manifest[v]["form_type"] == "deviated" and manifest[v]["deviation_type"] == dev
                 and manifest[v]["tempo"] == "normal"})
        if n < G6_MIN_ATHLETES:
            problems.append(f"deviation type {dev}: deviated at normal tempo by {n} athlete(s), needs >= {G6_MIN_ATHLETES}")
    return problems


def certain_count(reps: List[dict]) -> int:
    return sum(r["confidence"] != "low" for r in reps)


def g6_pairs(manifest: Dict[str, dict], video_ids: List[str], dev: str) -> Dict[str, Dict[str, Dict[str, List[str]]]]:
    """G6 paired design: {athlete: {view: {"deviated": [vids], "good": [vids]}}} for deviation type `dev`,
    keeping only (athlete, view) cells that have BOTH a deviated-`dev` take and a good take at normal tempo."""
    cells: Dict[str, Dict[str, Dict[str, List[str]]]] = {}
    for v in video_ids:
        m = manifest.get(v)
        if not m or m["tempo"] != "normal":
            continue
        side = ("deviated" if (m["form_type"] == "deviated" and m["deviation_type"] == dev) else
                "good" if m["form_type"] == "good" else None)
        if side:
            cells.setdefault(m["athlete_id"], {}).setdefault(m["view"], {"deviated": [], "good": []})[side].append(v)
    return {a: {w: c for w, c in vs.items() if c["deviated"] and c["good"]}
            for a, vs in cells.items() if any(c["deviated"] and c["good"] for c in vs.values())}


def g6_type_problem(manifest: Dict[str, dict], reps: Dict[str, List[dict]], video_ids: List[str],
                    dev: str) -> Optional[str]:
    """§4.3 data condition for ONE deviation type (annotations only, no model): in the paired cells,
    >= 10 certain deviated reps from >= 2 athletes, and >= 10 certain good reps. None = condition met."""
    pairs = g6_pairs(manifest, video_ids, dev)
    dev_n = {a: sum(certain_count(reps.get(v, [])) for c in vs.values() for v in c["deviated"]) for a, vs in pairs.items()}
    good_n = sum(certain_count(reps.get(v, [])) for vs in pairs.values() for c in vs.values() for v in c["good"])
    aths = [a for a, n in dev_n.items() if n > 0]
    if len(aths) >= G6_MIN_ATHLETES and sum(dev_n.values()) >= G6_MIN_REPS and good_n >= G6_MIN_REPS:
        return None
    return (f"G6 data condition not met for {dev}: {sum(dev_n.values())} certain deviated reps from "
            f"{len(aths)} athlete(s), {good_n} certain paired good reps (needs >= {G6_MIN_REPS} from "
            f">= {G6_MIN_ATHLETES} athletes, and >= {G6_MIN_REPS})")


def g6_data_problems(manifest: Dict[str, dict], reps: Dict[str, List[dict]], video_ids: List[str]) -> List[str]:
    """g6_type_problem for every indicator-mapped deviation type."""
    return [p for dev, ind in INDICATOR_FOR_DEVIATION.items() if ind is not None
            for p in [g6_type_problem(manifest, reps, video_ids, dev)] if p]


def quality_problems(manifest: Dict[str, dict], reps: Dict[str, List[dict]], video_ids: List[str]) -> List[str]:
    """Data-quality rules from the annotations: >= MIN_CERTAIN_REPS_PER_TAKE certain reps per take, and the
    share of `low` (don't-care) reps <= 10% overall and <= 20% within any view or tempo slice."""
    problems = []
    for v in video_ids:
        n = certain_count(reps.get(v, []))
        if n < MIN_CERTAIN_REPS_PER_TAKE:
            problems.append(f"{v}: {n} certain annotated reps < {MIN_CERTAIN_REPS_PER_TAKE} (re-record the take)")
    allr = [r for v in video_ids for r in reps.get(v, [])]
    if allr:
        share = sum(r["confidence"] == "low" for r in allr) / len(allr)
        if share > LOW_TIER_MAX_SHARE:
            problems.append(f"{share:.1%} of all annotated reps are `low` > {LOW_TIER_MAX_SHARE:.0%}")
    for key in ("view", "tempo"):
        for name in sorted({manifest[v][key] for v in video_ids if v in manifest}):
            rs = [r for v in video_ids if v in manifest and manifest[v][key] == name for r in reps.get(v, [])]
            if rs:
                share = sum(r["confidence"] == "low" for r in rs) / len(rs)
                if share > LOW_TIER_MAX_SHARE_SLICE:
                    problems.append(f"{key} {name}: {share:.1%} of annotated reps are `low` > {LOW_TIER_MAX_SHARE_SLICE:.0%}")
    return problems


# ============================================================================ annotations, intake, agreement
def load_final_reps(root: str) -> Tuple[Dict[str, List[dict]], List[str]]:
    """annotations/reps.csv -> ({video_id: [rep]}, errors). Validates ordering and labels."""
    path = _p(root, FINAL_REPS)
    if not os.path.exists(path):
        return {}, [f"{FINAL_REPS} missing"]
    rows, errors, out = _read_csv(path), [], {}
    if rows and set(FINAL_REP_COLUMNS) - set(rows[0]):
        return {}, [f"{FINAL_REPS}: missing columns {sorted(set(FINAL_REP_COLUMNS) - set(rows[0]))}"]
    for i, r in enumerate(rows, 2):
        try:
            s, p, e = int(r["start_frame"]), int(r["pull_frame"]), int(r["end_frame"])
        except (TypeError, ValueError):
            errors.append(f"{FINAL_REPS} line {i}: non-integer frame")
            continue
        if not (0 <= s < p < e):
            errors.append(f"{FINAL_REPS} line {i}: needs 0 <= start < pull < end, got {s},{p},{e}")
        c = (r.get("annotation_confidence") or "").strip()
        if c not in CONFIDENCES:
            errors.append(f"{FINAL_REPS} line {i}: annotation_confidence {c!r} not in {CONFIDENCES}")
        out.setdefault(r["video_id"].strip(), []).append(dict(start=s, pull=p, end=e, confidence=c,
                                                              annotator=(r.get("annotator") or "").strip()))
    for vid, reps in out.items():
        reps.sort(key=lambda r: r["start"])
        for a, b in zip(reps, reps[1:]):
            if b["start"] < a["end"]:
                errors.append(f"{FINAL_REPS}: overlapping reps in {vid} ({a['start']}-{a['end']} / {b['start']}-{b['end']})")
    return out, errors


def read_annotator_csv(root: str, annotator: str, video_id: str) -> Optional[List[dict]]:
    p = annotation_csv(root, annotator, video_id)
    if not os.path.exists(p):
        return None
    return [dict(start=int(r["start_frame"]), pull=int(r["pull_frame"]), end=int(r["end_frame"]),
                 confidence=(r.get("annotation_confidence") or "").strip()) for r in _read_csv(p)]


def history_rows(root: str) -> Tuple[List[dict], List[str]]:
    """qa/intake_history.jsonl parsed and checked (every line an object with HISTORY_KEYS)."""
    p = _p(root, INTAKE_HISTORY)
    if not os.path.exists(p):
        return [], []
    rows, errors = [], []
    with open(p, encoding="utf-8-sig") as f:
        for i, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                r = json.loads(line)
            except ValueError:
                errors.append(f"{INTAKE_HISTORY} line {i}: not valid JSON")
                continue
            if not isinstance(r, dict) or any(k not in r for k in HISTORY_KEYS):
                errors.append(f"{INTAKE_HISTORY} line {i}: needs {', '.join(HISTORY_KEYS)}")
                continue
            rows.append(r)
    return rows, errors


def intake_history_problems(intake_videos: Dict[str, dict], rows: List[dict]) -> List[str]:
    """Every current intake verdict must be the LAST history entry for that file (same video, sha256,
    status and reasons): a verdict can only come from the intake tool, never from an edit."""
    last: Dict[tuple, dict] = {}
    for r in rows:
        last[(r["video_id"], r["sha256"])] = r
    out = []
    for vid, v in sorted(intake_videos.items()):
        h = last.get((vid, v.get("sha256")))
        if h is None:
            out.append(f"{vid}: its intake verdict has no entry in {INTAKE_HISTORY}")
        elif h.get("status") != v.get("status") or list(h.get("reasons") or []) != list(v.get("reasons") or []):
            out.append(f"{vid}: its intake verdict ({v.get('status')}) differs from the last check recorded in "
                       f"{INTAKE_HISTORY} ({h.get('status')})")
    return out


def validate_agreement_record(d) -> List[str]:
    """Structure of qa/annotation_agreement.json (see the schema comment above)."""
    if not isinstance(d, dict) or not isinstance(d.get("rounds"), list):
        return [f"{AGREEMENT_REPORT}: needs an object with a list of rounds"]
    out = []
    for k, r in enumerate(d["rounds"], 1):
        if not isinstance(r, dict) or any(x not in r for x in ROUND_KEYS):
            out.append(f"{AGREEMENT_REPORT}: round {k} needs {', '.join(ROUND_KEYS)}")
            continue
        ok = (r["round"] == k and isinstance(r["salt"], str) and r["salt"].strip()
              and isinstance(r["eligible"], list) and isinstance(r["subset"], list)
              and isinstance(r["primary_snapshot"], dict) and isinstance(r["notes"], list)
              and all(isinstance(x, str) for x in r["notes"]))
        if not ok:
            out.append(f"{AGREEMENT_REPORT}: round {k} is malformed (round number, salt, lists, snapshot, notes)")
        if not (isinstance(r["second"], str) and ANNOTATOR_RE.match(r["second"]) and r["second"] != PRIMARY_ANNOTATOR):
            out.append(f"{AGREEMENT_REPORT}: round {k}: the second annotator must be a pseudonymous id other than "
                       f"{PRIMARY_ANNOTATOR} (got {r['second']!r})")
        res = r["result"]
        if res is not None and (not isinstance(res, dict) or res.get("second") != r["second"]
                                or res.get("reference") != PRIMARY_ANNOTATOR or "accepted" not in res):
            out.append(f"{AGREEMENT_REPORT}: round {k}: the result must name reference {PRIMARY_ANNOTATOR} and "
                       f"second {r['second']!r}")
    return out


# Sidecar written by the annotation tool next to every annotator CSV (one per annotator and video).
# status: "in_progress" | "complete"; pending_marks: marks of an unfinished rep (must be empty when
# complete); csv_sha256: sha256 of the CSV bytes the tool wrote (a hand edit shows up as a mismatch).
SIDECAR_KEYS = ("video_id", "annotator", "video_sha256", "n_frames", "fps", "cv2", "csv_sha256", "status",
                "pending_marks")


def read_sidecar(root: str, annotator: str, video_id: str) -> Optional[dict]:
    p = annotation_meta(root, annotator, video_id)
    return json.load(open(p, encoding="utf-8-sig")) if os.path.exists(p) else None


def sidecar_problems(root: str, annotator: str, video_id: str, video_sha256: str, n_frames: Optional[int],
                     fps: Optional[float], cv2_version: Optional[str]) -> List[str]:
    """Why this annotator's marks for this video cannot be used as they are (empty list = usable): a
    different file, unfinished, a different decoder or frame count, or a CSV edited outside the tool."""
    mp, cp = annotation_meta(root, annotator, video_id), annotation_csv(root, annotator, video_id)
    if not (os.path.exists(mp) and os.path.exists(cp)):
        return [f"{video_id}: annotator {annotator!r} has no annotation CSV + sidecar"]
    m = json.load(open(mp, encoding="utf-8-sig"))
    out = []
    if m.get("video_sha256") != video_sha256:
        out.append("annotated a different file (sha256 differs)")
    if m.get("status") != "complete" or m.get("pending_marks"):
        out.append("not finished (status not complete, or an open rep)")
    if n_frames is not None and m.get("n_frames") != n_frames:
        out.append(f"the tool decoded {m.get('n_frames')} frames, the intake check {n_frames}")
    if fps and (m.get("fps") is None or abs(float(m["fps"]) - float(fps)) > 0.01):
        out.append(f"annotation fps {m.get('fps')} != intake fps {fps}")
    if cv2_version is not None and m.get("cv2") != cv2_version:
        out.append(f"annotated with OpenCV {m.get('cv2')}, the frozen environment has {cv2_version}")
    if m.get("csv_sha256") != sha256_file(cp):
        out.append("the CSV was changed outside the annotation tool")
    return [f"{video_id}: annotator {annotator!r}: {x}" for x in out]


def load_intake(root: str) -> Tuple[Optional[dict], List[str]]:
    """The whole intake report (videos, cohort, tool hash, device) and its errors. A cohort-level status
    other than PASS (coverage, duplicates, manifest consistency, no manifest yet) is an error."""
    path = _p(root, INTAKE_REPORT)
    if not os.path.exists(path):
        return None, [f"{INTAKE_REPORT} missing (run ai/scripts/validate_recordings.py first)"]
    d = json.load(open(path, encoding="utf-8-sig"))
    errors = [f"{INTAKE_REPORT}: {vid} has status {r.get('status')!r}"
              for vid, r in d.get("videos", {}).items() if r.get("status") not in INTAKE_STATUSES]
    cohort = d.get("cohort") or {}
    if cohort.get("status") != "PASS":
        errors.append(f"{INTAKE_REPORT}: the cohort check is {cohort.get('status')!r} "
                      f"({'; '.join((cohort.get('problems') or [])[:5])})")
    return d, errors


def load_intake_report(root: str) -> Tuple[Dict[str, dict], List[str]]:
    d, errors = load_intake(root)
    return ((d or {}).get("videos", {}), errors)


def intake_failed(root: str) -> set:
    """Takes whose current intake verdict is FAIL (empty if no intake report yet)."""
    path = _p(root, INTAKE_REPORT)
    if not os.path.exists(path):
        return set()
    return {v for v, r in json.load(open(path, encoding="utf-8-sig")).get("videos", {}).items() if r.get("status") == "FAIL"}


def load_agreement(root: str, included: Optional[List[str]] = None) -> Tuple[Optional[dict], List[str]]:
    """qa/annotation_agreement.json. The §3.4 step is done when the LATEST round was measured on its
    pre-selected subset, met the size and coverage rules, and was ACCEPTED, with the pre-registered
    primary annotator as reference."""
    path = _p(root, AGREEMENT_REPORT)
    if not os.path.exists(path):
        return None, [f"{AGREEMENT_REPORT} missing: run the §3.4 agreement step before the evaluation"]
    try:
        d = json.load(open(path, encoding="utf-8-sig"))
    except ValueError:
        return None, [f"{AGREEMENT_REPORT}: not valid JSON"]
    errors = validate_agreement_record(d)
    if errors:
        return d, errors
    rounds = d.get("rounds") or []
    if d.get("primary") != PRIMARY_ANNOTATOR:
        errors.append(f"{AGREEMENT_REPORT}: primary annotator {d.get('primary')!r} != pre-registered {PRIMARY_ANNOTATOR!r}")
    if not rounds or not rounds[-1].get("result"):
        errors.append(f"{AGREEMENT_REPORT}: the latest agreement round has no measurement")
        return d, errors
    errors += [f"{AGREEMENT_REPORT}: round {r['round']} was never measured (every drawn round is measured and reported)"
               for r in rounds[:-1] if not r.get("result")]
    errors += [f"{AGREEMENT_REPORT}: round {r['round']} was accepted, yet a later round exists"
               for r in rounds[:-1] if (r.get("result") or {}).get("accepted")]
    last = rounds[-1]["result"]
    if not last.get("accepted"):
        errors.append(f"{AGREEMENT_REPORT}: round {rounds[-1]['round']} not accepted (§3.4: fresh disjoint round needed)")
    for k in ("subset_size", "views_covered", "tempos_covered"):
        if not (last.get("checks", {}).get(k) or {}).get("ok"):
            errors.append(f"{AGREEMENT_REPORT}: round {rounds[-1]['round']} fails {k}")
    if last.get("reference") != PRIMARY_ANNOTATOR:
        errors.append(f"{AGREEMENT_REPORT}: reference annotator {last.get('reference')!r} != {PRIMARY_ANNOTATOR!r}")
    if included is not None and not set(rounds[-1].get("subset", [])) <= set(included):
        errors.append(f"{AGREEMENT_REPORT}: the agreement subset contains videos that are not evaluated")
    return d, errors


def dataset_fingerprint(root: str, video_ids: List[str], shas: Optional[Dict[str, str]] = None) -> dict:
    """sha256 of every evaluated video plus the manifest, the final annotations, the intake report and
    history and the agreement record; `fingerprint` hashes all of them together (order-independent;
    keys are POSIX paths so the value is the same on every OS). `shas`: already computed video hashes."""
    paths = dict(find_videos(root))
    files = {vid: (shas or {}).get(vid) or sha256_file(paths[vid]) for vid in sorted(video_ids)}
    meta = {name: (sha256_file(_p(root, name)) if os.path.exists(_p(root, name)) else None)
            for name in (MANIFEST, FINAL_REPS, INTAKE_REPORT, INTAKE_HISTORY, AGREEMENT_REPORT)}
    blob = json.dumps(dict(videos=files, meta=meta), sort_keys=True).encode()
    return dict(fingerprint=hashlib.sha256(blob).hexdigest(), videos=files, meta=meta)
