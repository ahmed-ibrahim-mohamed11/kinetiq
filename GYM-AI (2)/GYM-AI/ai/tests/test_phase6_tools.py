"""Phase 6 intake + annotation + agreement tooling: ai/scripts/{validate_recordings,annotate_reps,
annotation_agreement}.py, against the frozen conventions (ai/evaluation/phase6_dataset.py) and the
runner's own checks (ai/scripts/phase6_evaluate.py).

No real recordings and no model: framing rules run on synthetic detections (and a stub detector for
the CLI), technical checks on small generated videos, annotation logic on the GUI-free state class,
and the whole agreement round trip (select -> hand-off -> collect -> measure) is checked by the
runner's recomputation.
"""
import csv
import json
import os

import numpy as np
import pytest

from ai.evaluation import phase6_dataset as D
from ai.scripts import annotate_reps as AR
from ai.scripts import annotation_agreement as AG
from ai.scripts import phase6_evaluate as P6
from ai.scripts import validate_recordings as VR

W, H = 1080, 1920


# ----------------------------------------------------------------------------- framing (synthetic detections)
def _det(subject=True, box=(540, 1000, 500, 1200), wrists=((400, 900, 0.9), (680, 900, 0.9)), others=()):
    kp = np.full((8, 17, 3), np.nan, np.float32)
    bx = np.full((8, 5), np.nan, np.float32)
    k = 0
    if subject:
        bx[0] = [*box, 0.9]
        kp[0, :, :] = [box[0], box[1], 0.9]
        kp[0, VR.L_WRI], kp[0, VR.R_WRI] = wrists
        k = 1
    for o in others:
        bx[k] = [*o, 0.8]
        k += 1
    return kp, bx


def test_framing_clean_shot():
    f = VR.framing_metrics([_det() for _ in range(30)], W, H)
    assert f == dict(sampled=30, subject_frac=1.0, body_inside_frac=1.0, hands_inside_frac=1.0,
                     hands_visible_frac=1.0, second_person_frac=0.0)


def test_framing_detects_cut_body_hidden_hands_and_second_person():
    cut = _det(box=(540, 1500, 500, 900))                          # bottom edge at 1950 > 1920
    hidden = _det(wrists=((400, 900, 0.1), (680, 900, 0.1)))
    outside = _det(wrists=((5, 900, 0.9), (680, 900, 0.9)))        # x=5 < 1% margin (10.8 px)
    crowd = _det(others=[(100, 1000, 150, 600)])                    # height 600 >= 25% of 1200
    small = _det(others=[(100, 1000, 50, 200)])                     # far-away person, ignored
    f = VR.framing_metrics([cut, hidden, outside, crowd, small, _det(subject=False)], W, H)
    assert f["subject_frac"] == round(5 / 6, 3)
    assert f["body_inside_frac"] == 0.8 and f["hands_inside_frac"] == 0.8
    assert f["hands_visible_frac"] == 0.8 and f["second_person_frac"] == round(1 / 6, 3)


def test_framing_without_any_detection_has_no_framing_fractions():
    f = VR.framing_metrics([_det(subject=False) for _ in range(10)], W, H)
    assert f["subject_frac"] == 0.0 and f["body_inside_frac"] is None and f["hands_inside_frac"] is None
    j = VR.judge(_rec(framing=f))
    assert j["status"] == "WARN" and any("check the take by eye" in w for w in j["warnings"])


def _rec(**kw):
    r = dict(parsed=D.parse_video_name("A01_side_normal_take1"), folder="A01", ext=".mp4", decodable=True,
             width=W, height=H, fps=30.0, n_frames=600, duration_s=20.0, frame_size=[W, H],
             interval_cv=0.01, mean_luma=120.0,
             framing=dict(sampled=60, subject_frac=1.0, body_inside_frac=1.0, hands_inside_frac=1.0,
                          hands_visible_frac=1.0, second_person_frac=0.0))
    r.update(kw)
    return r


def test_judge_pass_and_each_failure():
    assert VR.judge(_rec())["status"] == "PASS"
    cases = {"resolution": dict(width=1280, height=720, frame_size=[1280, 720]), "fps": dict(fps=15.0),
             "duration": dict(duration_s=3.0), "rotation": dict(frame_size=[H, W]),
             "file name": dict(parsed=None), "videos/A01": dict(folder="A02"), "decoded": dict(decodable=False),
             "occurs 2 times": dict(duplicates=["videos/A01/x.mp4", "videos/A02/x.mp4"])}
    for word, kw in cases.items():
        j = VR.judge(_rec(**kw))
        assert j["status"] == "FAIL" and any(word in r for r in j["reasons"]), (word, j)


def test_judge_set_up_rules_fail_and_detector_quality_rules_only_warn():
    fr = dict(sampled=60, subject_frac=1.0, body_inside_frac=1.0, hands_inside_frac=1.0,
              hands_visible_frac=1.0, second_person_frac=0.0)
    for kw in (dict(subject_frac=0.5), dict(hands_visible_frac=0.2)):           # how well the detector sees
        assert VR.judge(_rec(framing=dict(fr, **kw)))["status"] == "WARN", kw
    rear = VR.judge(_rec(framing=dict(fr, hands_visible_frac=0.2), parsed=D.parse_video_name("A01_rear_normal_take1")))
    assert rear["status"] == "WARN"
    assert VR.judge(_rec(framing=dict(fr, hands_visible_frac=0.5),
                         parsed=D.parse_video_name("A01_rear_normal_take1")))["status"] == "PASS"
    for kw in (dict(body_inside_frac=0.80), dict(hands_inside_frac=0.80), dict(second_person_frac=0.2)):
        assert VR.judge(_rec(framing=dict(fr, **kw)))["status"] == "FAIL", kw            # the camera set-up
    assert VR.judge(_rec(framing=dict(fr, body_inside_frac=0.94)))["status"] == "WARN"
    assert VR.judge(_rec(framing=dict(fr, second_person_frac=0.02)))["status"] == "WARN"


# ----------------------------------------------------------------------------- technical probe + CLI
def _video(path, w, h, seconds, fps=30):
    import cv2
    os.makedirs(os.path.dirname(path), exist_ok=True)
    vw = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
    rng = np.random.default_rng(0)
    for _ in range(int(seconds * fps)):
        vw.write(rng.integers(90, 160, (h, w, 3), dtype=np.uint8))
    vw.release()


class _StubDetector:
    def __init__(self, device="cpu"):
        assert device == D.INTAKE_DEVICE

    def __call__(self, frame):
        h, w = frame.shape[:2]
        return _det(box=(w / 2, h / 2, w / 3, h / 2), wrists=((w / 3, h / 2, 0.9), (2 * w / 3, h / 2, 0.9)))


def test_technical_only_check_never_writes_the_verdict_the_runner_reads(tmp_path):
    root = tmp_path / "p6"
    _video(str(root / "videos" / "A01" / "A01_rear_normal_take1.mp4"), 1080, 1920, 5.5)
    _video(str(root / "videos" / "A01" / "A01_side_normal_take1.mp4"), 640, 360, 5.5)
    _video(str(root / "videos" / "A02" / "bad_name.mp4"), 1080, 1920, 5.5)
    assert VR.main(["--root", str(root), "--no-detector"]) == 1
    assert not os.path.exists(root / D.INTAKE_REPORT) and not os.path.exists(root / D.INTAKE_HISTORY)
    rep = json.load(open(root / "qa" / "intake_report_technical.json"))
    v = rep["videos"]
    assert rep["framing_checked"] is False
    assert v["A01_rear_normal_take1"]["status"] == "PASS" and v["A01_rear_normal_take1"]["n_frames"] == 165
    assert v["A01_side_normal_take1"]["status"] == "FAIL" and v["bad_name"]["status"] == "FAIL"


def test_detector_check_writes_the_verdict_and_appends_the_history(tmp_path, monkeypatch):
    import ai.core.pose as pose
    monkeypatch.setattr(pose, "YoloTopK", _StubDetector)
    root = tmp_path / "p6"
    _video(str(root / "videos" / "A01" / "A01_rear_normal_take1.mp4"), 1080, 1920, 5.5)
    _video(str(root / "videos" / "A01" / "A01_side_normal_take1.mp4"), 640, 360, 5.5)
    assert VR.main(["--root", str(root), "--write-manifest-template"]) == 1
    rep = json.load(open(root / D.INTAKE_REPORT))
    assert rep["framing_checked"] is True and rep["device"] == "cpu" and rep["tool_sha256"]
    assert rep["videos"]["A01_rear_normal_take1"]["framing"]["subject_frac"] == 1.0
    assert rep["cohort"]["status"] == "INCOMPLETE"                     # no manifest yet
    _, errs = D.load_intake(str(root))                                   # the runner refuses it
    assert any("cohort check is 'INCOMPLETE'" in e for e in errs)
    hist = [json.loads(x) for x in open(root / D.INTAKE_HISTORY)]
    assert [h["video_id"] for h in hist] == ["A01_rear_normal_take1", "A01_side_normal_take1"]
    tpl = list(csv.DictReader(open(root / "qa" / "manifest_template.csv")))
    assert [r["video_id"] for r in tpl] == ["A01_rear_normal_take1", "A01_side_normal_take1"]
    assert VR.main(["--root", str(root), "--athlete", "A01"]) == 1        # quick check: own file, history grows
    assert os.path.exists(root / "qa" / "intake_report_A01.json") and len(open(root / D.INTAKE_HISTORY).readlines()) == 4


def test_cohort_check_uses_the_runners_rules(tmp_path):
    """The intake cohort check must refuse what the runner refuses: here every good take is at a
    non-normal tempo (G6 needs good AND deviated at tempo normal, per athlete and view)."""
    root = tmp_path / "p6"
    os.makedirs(root)
    vids = [f"A01_{v}_{t}_take1" for v in ("rear", "side") for t in D.MANDATORY_TEMPOS] + \
           ["A01_oblique_normal_take1", "A01_oblique_slow_take1"]
    rows = []
    for v in vids:
        p = D.parse_video_name(v)
        r = {c: "" for c in D.MANIFEST_COLUMNS}
        dev = p["tempo"] == "normal"
        r.update(video_id=v, athlete_id="A01", view=p["view"], tempo=p["tempo"], take=1,
                 form_type="deviated" if dev else "good", deviation_type="asymmetric_pull" if dev else "none")
        rows.append(r)
    with open(root / D.MANIFEST, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=D.MANIFEST_COLUMNS)
        w.writeheader()
        w.writerows(rows)
    records = {v: dict(status="PASS", parsed=D.parse_video_name(v), sha256=v) for v in vids}
    c = VR.cohort_checks(records, str(root))
    man, _ = D.load_manifest(str(root))
    runner = D.form_coverage(man, vids)
    assert c["status"] == "FAIL" and runner and set(runner) <= set(c["problems"])
    assert any("no good-form take at normal tempo" in p for p in c["problems"])


def test_tools_refuse_a_cloud_synced_root(tmp_path, monkeypatch):
    monkeypatch.setenv("OneDrive", str(tmp_path))
    root = str(tmp_path / "phase6")
    assert VR.main(["--root", root]) == 2
    assert AR.main(["--root", root, "--annotator", "a1", "--status"]) == 2
    assert AG.main(["--root", root, "--status"]) == 2


# ----------------------------------------------------------------------------- annotation state
def test_marking_saves_reps_and_rejects_bad_order_or_overlap():
    a = AR.Annotation("A01_rear_normal_take1", 300, 30.0)
    assert a.next_boundary() == "start"
    a.mark(10), a.mark(25)
    assert "must come after" in a.mark(20)                       # END before PULL
    assert a.mark(40).startswith("rep saved") and a.reps[0]["confidence"] == "high"
    assert "inside an existing rep" in a.mark(30)
    for f in (100, 110, 120):
        a.mark(f)
    a.mark(50), a.mark(60)
    assert "overlap the rep 100-120" in a.mark(130)              # would swallow the rep 100-120
    assert len(a.reps) == 2 and a.pending == [50, 60]
    assert a.mark(70).startswith("rep saved") and len(a.reps) == 3


def test_undo_delete_confidence_and_navigation():
    a = AR.Annotation("v", 500, 30.0)
    for f in (10, 20, 30, 100, 120, 140):
        a.mark(f)
    assert len(a.reps) == 2
    a.undo()                                                     # reopens the second rep
    assert len(a.reps) == 1 and a.pending == [100, 120]
    a.undo(), a.undo()
    assert a.pending == [] and len(a.reps) == 1
    for f in (100, 120, 140):
        a.mark(f)
    assert a.cycle_confidence(125) == "confidence high -> medium" and a.reps[1]["confidence"] == "medium"
    a.undo()
    assert a.reps[1]["confidence"] == "high"
    assert a.delete_at(15).startswith("rep 10-30") and len(a.reps) == 1
    a.undo()
    assert len(a.reps) == 2
    assert a.neighbour(0, 1) == 10 and a.neighbour(50, 1) == 100 and a.neighbour(50, -1) == 10
    assert a.delete_at(70) == "no rep under the cursor"


def test_annotation_round_trip_and_a_restored_open_rep_can_be_undone(tmp_path):
    a = AR.Annotation("A01_rear_normal_take1", 300, 29.9)
    for f in (10, 20, 30):
        a.mark(f)
    a.cycle_confidence(15), a.cycle_confidence(15)               # -> low
    a.mark(50), a.mark(60)
    p = str(tmp_path / "ann" / "A01_rear_normal_take1.csv")
    a.save(p, dict(annotator="a1", video_sha256="x"))
    meta = json.load(open(p[:-4] + ".meta.json"))
    assert meta["csv_sha256"] == D.sha256_file(p) and meta["pending_marks"] == [50, 60]
    b = AR.Annotation.load(p, "A01_rear_normal_take1", 300, 29.9)
    assert b.reps == a.reps and b.pending == [50, 60] and not b.finished
    assert b.undo() == "last mark removed" and b.undo() == "last mark removed" and b.pending == []
    rows = list(csv.DictReader(open(p)))
    assert list(rows[0]) == D.REP_COLUMNS and rows[0]["annotation_confidence"] == "low"


def test_marks_are_never_rebound_to_a_changed_file(tmp_path):
    cur = dict(video_sha256="new", n_frames=30, fps=30.0, cv2="5.0.0")
    assert AR.identity_problems(None, cur) == [] and AR.identity_problems(dict(cur), cur) == []
    for k, v in (("video_sha256", "old"), ("n_frames", 29), ("cv2", "4.9.0"), ("fps", 25.0)):
        assert AR.identity_problems(dict(cur, **{k: v}), cur), k
    root = tmp_path / "p6"
    vid = "A01_rear_normal_take1"
    path = str(root / "videos" / "A01" / f"{vid}.mp4")
    _video(path, 160, 120, 1.0)
    meta = AR.prepare(str(root), "a1", vid, path, 30, 30.0)
    a = AR.Annotation(vid, 30, 30.0)
    for f in (2, 8, 14):
        a.mark(f)
    a.save(AR.annotation_path(str(root), "a1", vid), meta)
    _video(path, 160, 120, 0.8)                                  # re-exported shorter: 24 frames
    with pytest.raises(SystemExit, match="REFUSED"):
        AR.prepare(str(root), "a1", vid, path, 24, 30.0)
    assert json.load(open(D.annotation_meta(str(root), "a1", vid)))["video_sha256"] == meta["video_sha256"]
    arch = AR.restart(str(root), "a1", vid)                      # explicit restart: archived, not deleted
    assert len(os.listdir(arch)) == 2 and not os.path.exists(AR.annotation_path(str(root), "a1", vid))
    assert AR.prepare(str(root), "a1", vid, path, 24, 30.0)["n_frames"] == 24


# ----------------------------------------------------------------------------- a small annotated cohort
def _cohort_root(tmp_path, athletes=("A01",), fail=()):
    """Tiny takes (160x120, 1 s) for every view x tempo, a manifest, an intake report (cohort PASS, as the
    tools need it) and complete a1 annotations made through the tool's own save path."""
    import cv2
    root = tmp_path / "p6"
    vids = [f"{a}_{v}_{t}_take1" for a in athletes for v in D.VIEWS for t in D.TEMPOS]
    rows, intake = [], {}
    for v in vids:
        p = D.parse_video_name(v)
        path = str(root / "videos" / p["athlete"] / f"{v}.mp4")
        _video(path, 160, 120, 1.0)
        r = {c: "" for c in D.MANIFEST_COLUMNS}
        r.update(video_id=v, athlete_id=p["athlete"], view=p["view"], tempo=p["tempo"], take=1, form_type="good",
                 deviation_type="none")
        rows.append(r)
        intake[v] = dict(status="FAIL" if v in fail else "PASS", reasons=["x"] if v in fail else [], warnings=[],
                         sha256=D.sha256_file(path), n_frames=30, fps=30.0, framing=dict(subject_frac=1.0))
        if v in fail:
            continue
        meta = dict(video_id=v, annotator="a1", video_sha256=D.sha256_file(path), n_frames=30, fps=30.0,
                    cv2=cv2.__version__, created_utc="t")
        a = AR.Annotation(v, 30, 30.0)
        for f in (2, 8, 14, 16, 22, 28):
            a.mark(f)
        a.finished = True
        a.save(AR.annotation_path(str(root), "a1", v), meta)
    with open(root / D.MANIFEST, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=D.MANIFEST_COLUMNS)
        w.writeheader()
        w.writerows(rows)
    D.write_json_atomic(str(root / D.INTAKE_REPORT), dict(tool_sha256="t", device="cpu", framing_checked=True,
                                                         videos=intake, cohort=dict(status="PASS", problems=[])))
    return root, sorted(v for v in vids if v not in fail)


def test_queue_skips_intake_fail_takes_and_finalize_is_primary_only(tmp_path):
    root, vids = _cohort_root(tmp_path, fail=("A01_side_fast_take1",))
    assert [v for v, _ in AR.video_list(str(root))] == vids          # the FAIL take is never queued
    assert AR.finalize(str(root), "a2") == 2
    assert AR.finalize(str(root), "a1") == 0
    reps, errs = D.load_final_reps(str(root))
    assert not errs and sorted(reps) == vids and all(r["annotator"] == "a1" for x in reps.values() for r in x)
    with open(AR.annotation_path(str(root), "a1", vids[0]), "a", encoding="utf-8") as f:
        f.write("3,24,26,29,high,\n")                                # edited outside the tool
    assert AR.finalize(str(root), "a1") == 1
    assert AR.status(str(root), "a2") == dict(complete=[], in_progress=[], missing=[])   # no open round: nothing


def _a2_marks(root, vids, bad):
    """a2's marks, made where a2 works (a hand-off folder or the root). a1 has (2,8,14) and (16,22,28);
    a2 agrees closely, or (bad) puts the first rep at 9-15 (IoU 0.38 with a1's: no match)."""
    import cv2
    files = dict(D.find_videos(str(root)))
    for v in vids:
        a = AR.Annotation(v, 30, 30.0)
        for f in ((9, 12, 15) if bad else (3, 9, 15)) + (16, 22, 28):
            a.mark(f)
        a.finished = True
        a.save(AR.annotation_path(str(root), "a2", v), dict(video_id=v, annotator="a2",
                                                            video_sha256=D.sha256_file(files[v]), n_frames=30,
                                                            fps=30.0, cv2=cv2.__version__, created_utc="t"))


def test_agreement_round_trip_is_reproduced_by_the_runner(tmp_path, monkeypatch):
    root, vids = _cohort_root(tmp_path, athletes=("A01", "A02"))     # 24 takes: two disjoint rounds possible
    R = str(root)
    assert AG.main(["--root", R, "--measure"]) == 2                   # no round yet
    assert AG.main(["--root", R, "--select-subset"]) == 0
    rec = json.load(open(root / D.AGREEMENT_REPORT))
    r1 = rec["rounds"][0]
    assert r1["subset"] == P6.select_agreement_subset(vids, [], 1) and len(r1["subset"]) == 12
    assert r1["eligible"] == sorted(vids) and set(r1["primary_snapshot"]) == set(vids)
    assert AG.main(["--root", R, "--select-subset"]) == 2             # a round is open
    assert [v for v, _ in AR.video_list(R, "a2")] == r1["subset"]       # a2's queue = the subset
    hand = tmp_path / "handoff"
    assert AG.main(["--root", R, "--handoff", str(hand)]) == 0
    assert not os.path.exists(hand / "annotations") and len(D.find_videos(str(hand))) == 12
    assert AG.main(["--root", R, "--collect", str(hand)]) == 2         # a2 has not annotated yet
    _a2_marks(hand, r1["subset"], bad=True)                             # a2 disagrees on rep 1 of every take
    assert AG.main(["--root", R, "--collect", str(hand)]) == 0
    assert AG.main(["--root", R, "--measure"]) == 1                     # NOT accepted, written once
    assert AG.main(["--root", R, "--measure"]) == 2
    assert os.path.exists(root / "qa" / "annotation_agreement_round1.md")
    assert AG.main(["--root", R, "--select-subset"]) == 2               # needs an addendum after a failed round
    add = tmp_path / "addenda.md"
    add.write_text("# Addenda\n\n## A6-01 — 2026-11-01 — PULL examples for squeeze holds\n", encoding="utf-8")
    monkeypatch.setattr(AG, "ADDENDA_FILE", str(add))
    assert AG.main(["--root", R, "--select-subset", "--addendum", "A6-02"]) == 2
    assert AG.main(["--root", R, "--select-subset", "--addendum", "A6-01"]) == 0
    rec = json.load(open(root / D.AGREEMENT_REPORT))
    r2 = rec["rounds"][1]
    assert not set(r2["subset"]) & set(r1["subset"]) and len(r2["subset"]) == 12
    _a2_marks(root, r2["subset"], bad=False)                            # round 2: close agreement
    assert AG.main(["--root", R, "--measure"]) == 0
    assert AG.main(["--root", R, "--note", "round 2 after the A6-01 clarification"]) == 0
    rec = json.load(open(root / D.AGREEMENT_REPORT))
    assert rec["rounds"][1]["result"]["accepted"] and rec["rounds"][0]["result"]["accepted"] is False
    assert P6.verify_agreement(R, rec, vids) == []                      # the final run reproduces both rounds
    assert D.load_agreement(R, vids)[1] == []
    assert AG.main(["--root", R, "--select-subset", "--addendum", "A6-01"]) == 2   # accepted: no more rounds


def test_a_cohort_used_up_by_failed_rounds_stops_before_the_model(tmp_path, monkeypatch):
    root, vids = _cohort_root(tmp_path)                                 # 12 takes: one round uses all of them
    R = str(root)
    assert AG.main(["--root", R, "--select-subset"]) == 0
    _a2_marks(root, json.load(open(root / D.AGREEMENT_REPORT))["rounds"][0]["subset"], bad=True)
    assert AG.main(["--root", R, "--measure"]) == 1
    add = tmp_path / "addenda.md"
    add.write_text("## A6-01 — 2026-11-01 — x\n", encoding="utf-8")
    monkeypatch.setattr(AG, "ADDENDA_FILE", str(add))
    assert AG.main(["--root", R, "--select-subset", "--addendum", "A6-01"]) == 2   # no takes left


def test_selection_waits_for_the_primary_annotation(tmp_path):
    root, vids = _cohort_root(tmp_path)
    m = D.read_sidecar(str(root), "a1", vids[3])
    D.write_json_atomic(D.annotation_meta(str(root), "a1", vids[3]), dict(m, status="in_progress"))
    assert AG.main(["--root", str(root), "--select-subset"]) == 2
