#!/usr/bin/env python3
"""Phase 6 intake check for the owner's recordings — run after every session, BEFORE annotation and
before any model run (docs/PHASE6_PREREGISTRATION.md §2.3; recording protocol: docs/UNSEEN_ATHLETE_DATASET_SPEC.md).

Checks every file under <root>/videos and the cohort as a whole:
  naming      A01_<rear|side|oblique>_<normal|slow|paused|fast>_take<N>.<mp4|mov> in videos/<athlete>/;
              a name that occurs twice anywhere under videos/ fails every copy
  technical   decodable; shorter side >= 1080 px; 24-60 fps; 5-600 s; container size equals the decoded
              frame size; constant frame rate (else a warning)
  framing     YOLO11n-pose (the product detector) on CPU, on ~3 sampled frames per second. Rules that
              measure the camera set-up FAIL (body box and both wrists inside the frame, nobody else in
              the frame); rules that measure how well the detector sees the athlete only WARN (athlete
              detected, wrists visible), so a detector weakness is measured later as an abstention
              instead of being filtered out here. Check every WARN take by eye.
  cohort      the same coverage and form rules as the final runner (ai/evaluation/phase6_dataset.py),
              manifest <-> files, identical files. Without manifest.csv the cohort is INCOMPLETE.
It never runs the lifter, the rep model or any metric.

  python -m ai.scripts.validate_recordings [--root DIR] [--athlete A01] [--write-manifest-template]
  python -m ai.scripts.validate_recordings --no-detector      # technical checks only, separate report
Writes <root>/qa/intake_report.json + .md (the verdict the final runner reads; the full check only),
qa/intake_report_<athlete>.json + .md (--athlete: a quick per-session check), or
qa/intake_report_technical.json + .md (--no-detector; never read by the runner), and appends every
detector check to qa/intake_history.jsonl. The data root defaults to %LOCALAPPDATA%\\gym-ai\\phase6;
a root inside OneDrive is refused. The full check takes about an hour for 150 files.
Exit 0 = no FAIL and cohort PASS, 1 = a FAIL or the cohort is not PASS, 2 = refused.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import platform
import sys
from typing import Dict, List, Optional

import numpy as np

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from ai.evaluation import phase6_dataset as D  # noqa: E402

# ---- intake thresholds: committed before any recording exists (pre-registration §2.3) ---------------
MIN_SHORT_SIDE_PX = 1080
FPS_RANGE = (24.0, 60.5)
DURATION_RANGE_S = (5.0, 600.0)
LONG_TAKE_WARN_S = 180.0
VFR_WARN_CV = 0.10                  # std/mean of frame intervals above this -> variable frame rate warning
SAMPLE_FPS = 3.0                    # detector runs on ~3 frames per second
PERSON_CONF = 0.50                  # a detection counts as a person at this box confidence
KPT_CONF = 0.30                     # a keypoint counts as visible at this confidence
EDGE_MARGIN_FRAC = 0.01             # "inside the frame" = at least 1% of the frame size from every edge
WARN_SUBJECT_FRAC = 0.90            # athlete detected in < 90% of sampled frames -> WARN (check by eye)
MIN_BODY_INSIDE_FRAC = 0.90         # body box inside the frame in >= 90% of frames with the athlete (FAIL)
WARN_BODY_INSIDE_FRAC = 0.97        # 90-97%: brief edge contact -> warning (move the camera back next time)
MIN_HANDS_INSIDE_FRAC = 0.90        # both wrist positions inside the frame in >= 90% of those frames (FAIL)
WARN_HANDS_VISIBLE_FRAC = 0.70      # both wrists visible in < 70% (side/oblique) -> WARN
REAR_HANDS_VISIBLE_WARN = 0.30      # from behind the body hides the hands: WARN below 30%
SECOND_PERSON_HEIGHT_FRAC = 0.25    # another person counts when their box is >= 25% of the athlete's height
MAX_SECOND_PERSON_FRAC = 0.05       # FAIL when another person is present in > 5% of sampled frames
DARK_WARN_LUMA = 40.0               # mean luma below this -> lighting warning
L_WRI, R_WRI = 9, 10                # COCO wrist indices (YOLO11n-pose output order)
DETECTOR_WEIGHTS = os.path.join(REPO_ROOT, "yolo11n-pose.pt")


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _inside(x, y, W, H, m) -> bool:
    return (m[0] <= x <= W - m[0]) and (m[1] <= y <= H - m[1])


def framing_metrics(dets: List[tuple], W: int, H: int) -> dict:
    """dets: per sampled frame (kpts (K,17,3) [x,y,conf], boxes (K,5) [cx,cy,w,h,conf]), NaN-padded.
    The athlete is the largest confident box. Fractions over frames with the athlete are None when the
    athlete was never detected. Pure function (tested on synthetic detections)."""
    m = (EDGE_MARGIN_FRAC * W, EDGE_MARGIN_FRAC * H)
    n = len(dets)
    subj = body_in = hands_in = hands_vis = extra = 0
    for kp, bx in dets:
        conf = np.nan_to_num(bx[:, 4], nan=0.0)
        idx = [i for i in range(len(bx)) if conf[i] >= PERSON_CONF]
        if not idx:
            continue
        subj += 1
        area = [bx[i, 2] * bx[i, 3] for i in idx]
        s = idx[int(np.argmax(area))]
        cx, cy, w, h = bx[s, :4]
        if _inside(cx - w / 2, cy - h / 2, W, H, m) and _inside(cx + w / 2, cy + h / 2, W, H, m):
            body_in += 1
        wr = kp[s, [L_WRI, R_WRI]]
        if all(np.isfinite(wr[j, :2]).all() and _inside(wr[j, 0], wr[j, 1], W, H, m) for j in (0, 1)):
            hands_in += 1
        if all(np.isfinite(wr[j, 2]) and wr[j, 2] >= KPT_CONF for j in (0, 1)):
            hands_vis += 1
        if any(bx[i, 3] >= SECOND_PERSON_HEIGHT_FRAC * h for i in idx if i != s):
            extra += 1
    frac = lambda k, d: round(k / d, 3) if d else None  # noqa: E731
    return dict(sampled=n, subject_frac=frac(subj, n) or 0.0, body_inside_frac=frac(body_in, subj),
                hands_inside_frac=frac(hands_in, subj), hands_visible_frac=frac(hands_vis, subj),
                second_person_frac=frac(extra, n) or 0.0)


def judge(rec: dict) -> dict:
    """Turn one video's measurements into PASS / WARN / FAIL with plain-language reasons."""
    fail, warn = [], []
    p = rec.get("parsed")
    if p is None:
        fail.append("file name must be A01_<view>_<tempo>_take<N> (views: rear side oblique; "
                    "tempos: normal slow paused fast)")
    elif rec.get("folder") != p["athlete"]:
        fail.append(f"file must be in videos/{p['athlete']}/ (found in videos/{rec.get('folder')}/)")
    if rec.get("ext") not in D.VIDEO_EXTS:
        fail.append(f"extension {rec.get('ext')} not allowed ({', '.join(D.VIDEO_EXTS)})")
    if rec.get("duplicates"):
        fail.append(f"the name occurs {len(rec['duplicates'])} times under videos/ ({'; '.join(rec['duplicates'])}): "
                    "keep one file per name")
    if not rec.get("decodable"):
        fail.append("cannot be decoded")
        return dict(status="FAIL", reasons=fail, warnings=warn)
    W, H = rec["width"], rec["height"]
    if min(W, H) < MIN_SHORT_SIDE_PX:
        fail.append(f"resolution {W}x{H}: the shorter side must be >= {MIN_SHORT_SIDE_PX} px (record at 1080p or higher)")
    if not (FPS_RANGE[0] <= rec["fps"] <= FPS_RANGE[1]):
        fail.append(f"frame rate {rec['fps']:.2f} fps outside {FPS_RANGE[0]:.0f}-60 fps")
    if not (DURATION_RANGE_S[0] <= rec["duration_s"] <= DURATION_RANGE_S[1]):
        fail.append(f"duration {rec['duration_s']:.1f} s outside {DURATION_RANGE_S[0]:.0f}-{DURATION_RANGE_S[1]:.0f} s")
    elif rec["duration_s"] > LONG_TAKE_WARN_S:
        warn.append(f"long take ({rec['duration_s']:.0f} s): one set per file is expected")
    if rec["frame_size"] != [W, H]:
        fail.append(f"container reports {W}x{H} but frames decode as {rec['frame_size'][0]}x{rec['frame_size'][1]} "
                    "(rotation metadata); re-export the video or record in the target orientation")
    if rec.get("interval_cv") is not None and rec["interval_cv"] > VFR_WARN_CV:
        warn.append(f"variable frame rate (interval CV {rec['interval_cv']:.2f}); timing uses the average rate")
    if rec.get("mean_luma") is not None and rec["mean_luma"] < DARK_WARN_LUMA:
        warn.append(f"dark video (mean luma {rec['mean_luma']:.0f}); add light if possible")
    f = rec.get("framing")
    if f:
        view = p["view"] if p else None
        if f["subject_frac"] < WARN_SUBJECT_FRAC:
            warn.append(f"athlete detected in only {f['subject_frac']:.0%} of sampled frames: check the take by eye "
                        "(keep it if the whole athlete and the handle are visible)")
        if f["body_inside_frac"] is not None:
            if f["body_inside_frac"] < MIN_BODY_INSIDE_FRAC:
                fail.append(f"body touches the frame edge in {1 - f['body_inside_frac']:.0%} of frames: move the camera "
                            "back so the whole body stays in frame")
            elif f["body_inside_frac"] < WARN_BODY_INSIDE_FRAC:
                warn.append(f"body briefly touches the frame edge ({1 - f['body_inside_frac']:.0%} of frames); "
                            "leave more margin next time")
        if f["hands_inside_frac"] is not None and f["hands_inside_frac"] < MIN_HANDS_INSIDE_FRAC:
            fail.append(f"hands (handle) outside the frame in {1 - f['hands_inside_frac']:.0%} of frames: widen the shot "
                        "to include full arm extension")
        lim = REAR_HANDS_VISIBLE_WARN if view == "rear" else WARN_HANDS_VISIBLE_FRAC
        if f["hands_visible_frac"] is not None and f["hands_visible_frac"] < lim:
            warn.append(f"hands visible in only {f['hands_visible_frac']:.0%} of frames from the {view} view: check by "
                        "eye that the handle is in the shot")
        if f["second_person_frac"] > MAX_SECOND_PERSON_FRAC:
            fail.append(f"another person is in the frame in {f['second_person_frac']:.0%} of sampled frames "
                        "(also check for mirror reflections of the athlete); delete this file once noted")
        elif f["second_person_frac"] > 0:
            warn.append(f"another person briefly in the frame ({f['second_person_frac']:.0%} of sampled frames)")
    status = "FAIL" if fail else ("WARN" if warn else "PASS")
    return dict(status=status, reasons=fail, warnings=warn)


def probe_video(path: str, detector, sample_fps: float = SAMPLE_FPS) -> dict:
    """Decode the whole file sequentially (the order the pipeline and the annotation tool use)."""
    import cv2
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        return dict(decodable=False)
    W, H = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
    step = max(1, int(round(fps / sample_fps))) if fps > 0 else 10
    n, ts, dets, luma, frame_size = 0, [], [], [], None
    while True:
        ok, fr = cap.read()
        if not ok:
            break
        ts.append(cap.get(cv2.CAP_PROP_POS_MSEC))
        if frame_size is None:
            frame_size = [int(fr.shape[1]), int(fr.shape[0])]
        if n % step == 0:
            if detector is not None:
                dets.append(detector(fr))
            luma.append(float(cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY).mean()))
        n += 1
    cap.release()
    if n == 0 or not (fps > 0):
        return dict(decodable=False)
    d = np.diff(np.asarray(ts, dtype=float))
    d = d[d > 0]
    cv_ = float(d.std() / d.mean()) if len(d) > 2 else None
    out = dict(decodable=True, width=W, height=H, fps=round(fps, 3), n_frames=n, duration_s=round(n / fps, 2),
               frame_size=frame_size, interval_cv=None if cv_ is None else round(cv_, 3),
               mean_luma=round(float(np.mean(luma)), 1) if luma else None)
    if detector is not None:
        out["framing"] = framing_metrics(dets, frame_size[0], frame_size[1])
    return out


def cohort_checks(records: Dict[str, dict], root: str) -> dict:
    """The runner's manifest-level rules (phase6_dataset.coverage / form_coverage), applied while the
    athletes are still available. PASS / FAIL, or INCOMPLETE without a manifest."""
    usable = sorted(v for v, r in records.items() if r["status"] != "FAIL" and r.get("parsed"))
    cov = D.coverage(usable)
    problems: List[str] = []
    by_hash: Dict[str, List[str]] = {}
    for v, r in records.items():
        if r.get("sha256"):
            by_hash.setdefault(r["sha256"], []).append(v)
    problems += [f"identical files: {', '.join(sorted(vids))}" for vids in by_hash.values() if len(vids) > 1]
    if not os.path.exists(os.path.join(root, D.MANIFEST)):
        return dict(coverage=cov, problems=problems + ["manifest.csv not found (use --write-manifest-template, "
                                                       "then fill it in)"], status="INCOMPLETE")
    man, errs = D.load_manifest(root)
    problems += errs
    problems += [f"{v}: video file has no manifest row" for v in sorted(records) if v not in man]
    problems += [f"{v}: manifest row has no video file (remove the row of a deleted take)"
                 for v in sorted(man) if v not in records]
    included = [v for v in usable if v in man]
    problems += D.cohort_problems(man, included)        # exactly the final run's cohort rules
    problems += D.recording_date_problems(man, included)
    return dict(coverage=cov, problems=problems, status="FAIL" if problems else "PASS")


def write_manifest_template(root: str, records: Dict[str, dict]) -> str:
    """Rows for the video files that are not in manifest.csv yet: append them to manifest.csv and fill in
    form_type, deviation_type and the recording details during the session."""
    man, _ = D.load_manifest(root) if os.path.exists(os.path.join(root, D.MANIFEST)) else ({}, [])
    path = os.path.join(root, "qa", "manifest_template.csv")
    rows = []
    for v, r in sorted(records.items()):
        p = r.get("parsed")
        if not p or v in man:
            continue
        row = {c: "" for c in D.MANIFEST_COLUMNS}
        row.update(video_id=v, athlete_id=p["athlete"], view=p["view"], tempo=p["tempo"], take=p["take"],
                   form_type="good", deviation_type="none")
        rows.append(row)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=D.MANIFEST_COLUMNS)
        w.writeheader()
        w.writerows(rows)
    return path


def versions() -> dict:
    from importlib.metadata import version
    out = {"python": platform.python_version()}
    for p in ("ultralytics", "torch", "numpy", "opencv-python", "opencv-contrib-python"):
        try:
            out[p] = version(p)
        except Exception:                          # noqa: BLE001
            out[p] = None
    try:
        import cv2
        out["cv2"] = cv2.__version__
    except Exception:                              # noqa: BLE001
        out["cv2"] = None
    return out


def render_md(rep: dict) -> str:
    L = ["# Phase 6 intake report", "", f"Created {rep['created_utc']} · {len(rep['videos'])} files · "
         f"framing checked: **{rep['framing_checked']}** · cohort check: **{rep['cohort']['status']}** · "
         f"overall: **{rep['overall']}**", "",
         "Detector-based framing check only — no rep model, no metrics (pre-registration §2.3). "
         "**Check every WARN take by eye.** FAIL takes are re-recorded and not annotated.", "",
         "| video | status | resolution | fps | frames | duration s | reasons / warnings |", "|---|---|---|---|---|---|---|"]
    for v, r in sorted(rep["videos"].items()):
        res = f"{r.get('width', '?')}x{r.get('height', '?')}"
        L.append(f"| {v} | {r['status']} | {res} | {r.get('fps', '—')} | {r.get('n_frames', '—')} | "
                 f"{r.get('duration_s', '—')} | {'; '.join(r['reasons'] + r['warnings'])} |")
    L += ["", f"## Cohort: {rep['cohort']['status']}", ""]
    L += [f"- {p}" for p in rep["cohort"]["problems"]] or ["- no cohort problems"]
    L += ["", "| athlete | views | missing mandatory view/tempo |", "|---|---|---|"]
    for a, c in rep["cohort"]["coverage"].items():
        L.append(f"| {a} | {', '.join(c['views'])} | {', '.join(c['missing_mandatory']) or '—'} |")
    return "\n".join(L) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=None, help="data root (default: %%LOCALAPPDATA%%\\gym-ai\\phase6)")
    ap.add_argument("--athlete", default=None, help="check one athlete's folder only (quick check after a session)")
    ap.add_argument("--no-detector", action="store_true",
                    help="technical checks only; writes qa/intake_report_technical.* (never read by the runner)")
    ap.add_argument("--write-manifest-template", action="store_true")
    a = ap.parse_args(argv)
    root = a.root or D.default_root()
    cloud = D.cloud_sync_root(root)
    if cloud:
        print(f"REFUSED: {root} is inside the cloud-synced folder {cloud}. Keep the athletes' videos on this "
              "machine only (pre-registration §2.1).", file=sys.stderr)
        return 2
    all_files = D.find_videos(root)
    files = [(s, p) for s, p in all_files if not a.athlete or os.path.basename(os.path.dirname(p)) == a.athlete]
    if not files:
        print(f"no video files under {os.path.join(root, 'videos')}", file=sys.stderr)
        return 2
    dups = D.duplicate_stems(root)
    detector = None
    if not a.no_detector:
        from ai.core.pose import YoloTopK
        detector = YoloTopK(device=D.INTAKE_DEVICE)
    records: Dict[str, dict] = {}
    for k, (stem, path) in enumerate(files, 1):
        if stem in records:
            continue                                       # a repeated name: one record, FAIL (below)
        print(f"[{k}/{len(files)}] {stem}", flush=True)
        rec = probe_video(path, detector)
        rec.update(parsed=D.parse_video_name(stem), folder=os.path.basename(os.path.dirname(path)),
                   ext=os.path.splitext(path)[1].lower(), sha256=D.sha256_file(path),
                   path=os.path.relpath(path, root).replace(os.sep, "/"),
                   duplicates=[os.path.relpath(x, root).replace(os.sep, "/") for x in dups.get(stem, [])])
        rec.update(judge(rec))
        records[stem] = rec
    if a.athlete:
        cohort = dict(coverage=D.coverage([v for v, r in records.items() if r["status"] != "FAIL"]),
                      problems=["single-athlete check: cohort rules not applied"], status="SKIPPED")
    else:
        cohort = cohort_checks(records, root)
    overall = "PASS" if cohort["status"] in ("PASS", "SKIPPED") and \
        not any(r["status"] == "FAIL" for r in records.values()) else "FAIL"
    tool_sha = D.sha256_pinned(os.path.abspath(__file__))
    rep = dict(tool="ai/scripts/validate_recordings.py", tool_sha256=tool_sha, created_utc=_now(),
               framing_checked=detector is not None, device=D.INTAKE_DEVICE if detector is not None else None,
               detector_sha256=(D.sha256_file(DETECTOR_WEIGHTS) if detector is not None
                                and os.path.exists(DETECTOR_WEIGHTS) else None),
               versions=versions(),
               thresholds={k: v for k, v in globals().items() if k.isupper() and isinstance(v, (int, float, tuple))},
               overall=overall, cohort=cohort,
               videos={v: {k: r[k] for k in r if k not in ("parsed", "duplicates")} for v, r in records.items()})
    name = ("intake_report_technical" if detector is None else
            f"intake_report_{a.athlete}" if a.athlete else "intake_report")
    D.write_json_atomic(os.path.join(root, "qa", name + ".json"), rep)
    with open(os.path.join(root, "qa", name + ".md"), "w", encoding="utf-8") as f:
        f.write(render_md(rep))
    if detector is not None:                               # every detector check goes into the history
        with open(os.path.join(root, *D.INTAKE_HISTORY.split("/")), "a", encoding="utf-8") as f:
            for v, r in sorted(records.items()):
                f.write(json.dumps(dict(checked_utc=rep["created_utc"], video_id=v, path=r["path"], sha256=r["sha256"],
                                        status=r["status"], reasons=r["reasons"], warnings=r["warnings"],
                                        scope=a.athlete or "cohort", tool_sha256=tool_sha)) + "\n")
    if a.write_manifest_template:
        print("manifest template (append these rows to manifest.csv):", write_manifest_template(root, records))
    n_fail = sum(r["status"] == "FAIL" for r in records.values())
    n_warn = sum(r["status"] == "WARN" for r in records.values())
    print(f"{len(records)} files: {len(records) - n_fail - n_warn} PASS, {n_warn} WARN (check by eye), {n_fail} FAIL "
          f"(re-record); cohort {cohort['status']} -> overall {overall}. Report: "
          f"{os.path.join(root, 'qa', name + '.md')}")
    return 0 if overall == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
