"""Phase 6 runner (ai/scripts/phase6_evaluate.py) + dataset conventions (ai/evaluation/phase6_dataset.py).

Every rule of docs/PHASE6_PREREGISTRATION.md that the runner implements is pinned here on synthetic
inputs: naming, data validation and its ties to the evaluated files (intake, sidecars, final reps),
coverage, protocol-v2 scoring, the abstention rule (Mode A, Mode B, SYSTEM_ERROR), gates G1-G6 decided
EXACTLY on the counts, NOT EVALUABLE counted as FAIL, the paired G6 design, the athlete bootstrap, the
agreement rule recomputed from the files, the frozen identity read from the git tags (git simulated), the
data lock, the one-run ledger and the verdict of record, refused options and the report sections. The
slow test runs the whole runner on the 6 DEV videos (dry run — never Phase 6 evidence) and must reproduce
the frozen Mode A and Mode B numbers.
"""
import csv
import json
import os
import types
from dataclasses import dataclass
from fractions import Fraction

import numpy as np
import pytest

from ai.evaluation import phase6_dataset as D
from ai.scripts import phase6_evaluate as P6
from ai.tests.conftest import MBLITE_CKPT, REPO_ROOT


# ----------------------------------------------------------------------------- naming + data files
@pytest.mark.parametrize("stem,ok", [
    ("A01_rear_normal_take1", True), ("A12_side_paused_take3", True), ("A101_oblique_fast_take10", True),
    ("A1_rear_normal_take1", False), ("a01_rear_normal_take1", False), ("A01_back_normal_take1", False),
    ("A01_front_normal_take1", False), ("A01_rear_medium_take1", False), ("A01_rear_normal_take0", False),
    ("A01_rear_normal", False), ("A01_rear_normal_take1_v2", False), ("ua_001_view01_good_set1", False)])
def test_video_naming(stem, ok):
    p = D.parse_video_name(stem)
    assert (p is not None) == ok
    if ok:
        a, v, t, _ = stem.split("_")
        assert (p["athlete"], p["view"], p["tempo"]) == (a, v, t)


def _write(path, cols, rows):
    os.makedirs(os.path.dirname(str(path)), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)


def _mrow(vid, form="good", dev="none", **kw):
    p = D.parse_video_name(vid)
    r = {c: "" for c in D.MANIFEST_COLUMNS}
    r.update(video_id=vid, athlete_id=p["athlete"], view=p["view"], tempo=p["tempo"], take=p["take"],
             form_type=form, deviation_type=dev, lighting_condition="bright", **kw)
    return r


def test_manifest_rows_must_agree_with_file_names(tmp_path):
    rows = [_mrow("A01_rear_normal_take1"), _mrow("A01_side_slow_take1"),
            dict(_mrow("A01_side_paused_take1"), view="rear"),
            _mrow("A01_rear_fast_take1", form="good", dev="reduced_rom"),
            _mrow("A01_side_fast_take1", form="deviated", dev="none"),
            dict(_mrow("A01_rear_normal_take1"), notes="dup")]
    _write(tmp_path / D.MANIFEST, D.MANIFEST_COLUMNS, rows)
    _, err = D.load_manifest(str(tmp_path))
    assert len(err) == 4 and any("disagrees" in e for e in err) and any("duplicate" in e for e in err)


def test_final_reps_validation(tmp_path):
    base = dict(video_id="A01_rear_normal_take1", annotator="a1", notes="")
    rows = [dict(base, rep_id=1, start_frame=10, pull_frame=20, end_frame=30, annotation_confidence="high"),
            dict(base, rep_id=2, start_frame=25, pull_frame=40, end_frame=50, annotation_confidence="medium"),
            dict(base, rep_id=3, start_frame=60, pull_frame=55, end_frame=70, annotation_confidence="low"),
            dict(base, rep_id=4, start_frame=80, pull_frame=90, end_frame=99, annotation_confidence="sure")]
    _write(tmp_path / D.FINAL_REPS, D.FINAL_REP_COLUMNS, rows)
    reps, err = D.load_final_reps(str(tmp_path))
    assert len(err) == 3 and len(reps["A01_rear_normal_take1"]) == 4
    assert reps["A01_rear_normal_take1"][0]["annotator"] == "a1"


def test_coverage_needs_rear_side_all_tempos_and_oblique():
    vids = [f"A01_{v}_{t}_take1" for v in ("rear", "side") for t in D.MANDATORY_TEMPOS] + \
           ["A01_oblique_normal_take1", "A02_rear_normal_take1"]
    cov = D.coverage(vids)
    assert cov["A01"]["missing_mandatory"] == [] and cov["A01"]["enough_views"]
    assert "side/paused" in cov["A02"]["missing_mandatory"] and "oblique/any tempo" in cov["A02"]["missing_mandatory"]


def test_form_coverage_per_view_and_per_deviation_type():
    man = {}
    for a in ("A01", "A02"):
        for v in D.REQUIRED_VIEWS:
            man[f"{a}_{v}_normal_take1"] = _mrow(f"{a}_{v}_normal_take1")
            man[f"{a}_{v}_normal_take2"] = _mrow(f"{a}_{v}_normal_take2", "deviated", "asymmetric_pull")
    probs = D.form_coverage(man, list(man))
    assert not any("A01/" in p for p in probs)                    # every view has good + deviated (normal)
    assert any("excessive_trunk_motion" in p for p in probs)      # types deviated by < 2 athletes
    man["A01_side_normal_take2"]["tempo"] = "slow"                # deviated take not at normal tempo
    assert any("A01/side: no deviated take at normal tempo" in p for p in D.form_coverage(man, list(man)))
    man["A02_rear_normal_take1"]["tempo"] = "paused"              # good take only at another tempo (G6 needs normal)
    assert any("A02/rear: no good-form take at normal tempo" in p for p in D.form_coverage(man, list(man)))


def test_data_quality_rules_from_the_annotations():
    man = {v: _mrow(v) for v in ("A01_rear_normal_take1", "A01_side_normal_take1")}
    reps = {"A01_rear_normal_take1": [dict(confidence="high")] * 9 + [dict(confidence="low")],
            "A01_side_normal_take1": [dict(confidence="high")] * 2 + [dict(confidence="low")] * 3}
    probs = D.quality_problems(man, reps, list(man))
    assert any("A01_side_normal_take1: 2 certain" in p for p in probs)          # < 3 certain reps in a take
    assert any("of all annotated reps are `low`" in p for p in probs)           # 4/15 > 10% overall
    assert any(p.startswith("view side") for p in probs) and not any(p.startswith("view rear") for p in probs)


# ----------------------------------------------------------------------------- scoring
def _rep(s, p, e, c="high"):
    return dict(start=s, pull=p, end=e, confidence=c)


def test_score_video_protocol_v2_and_low_confidence_dont_care():
    gt = [_rep(0, 15, 30), _rep(40, 55, 70), _rep(80, 95, 110, "low")]
    pred = [dict(start=1, pull=16, end=31), dict(start=81, pull=96, end=111), dict(start=200, pull=210, end=220)]
    s = P6.score_video(pred, gt, 30.0)
    assert (s["iou"]["tp"], s["iou"]["fp"], s["iou"]["fn"]) == (1, 0, 1)
    assert s["iou"]["n_ignored"] == 2 and s["iou"]["n_unc"] == 1
    assert s["iou"]["pull_err_frames"] == [1] and s["iou"]["pull_err_s"] == [pytest.approx(1 / 30)]
    assert s["strict_iou"] == dict(tp=2, fp=0, fn=1)               # `low` counted as certain (secondary)


def test_abstention_rule_mode_a_mode_b_and_system_error():
    reps = [dict(start_frame=1, pull_frame=5, end_frame=9)]
    assert P6.predictions_for_scoring(dict(status="OK", reps=reps)) == [dict(start=1, pull=5, end=9)]
    for st in ("UNSUPPORTED_EXERCISE", "INSUFFICIENT_CONFIDENCE", "LOST_TRACK", "NO_PERSON", "SYSTEM_ERROR"):
        assert P6.predictions_for_scoring(dict(status=st, reps=reps)) == []
    ev = [dict(start=1, pull=5, end=9, status="OK"), dict(start=20, pull=25, end=30, status="UNSUPPORTED_EXERCISE")]
    assert P6.mode_b_predictions(ev) == [dict(start=1, pull=5, end=9)]


# ----------------------------------------------------------------------------- gates
def _video(ath, view, tempo, tp, fp, fn, form="good", dev="none", status="OK", ind=None, pull_err_frames=3,
           fps=30.0, n_low=0):
    matched = [[i, i] for i in range(tp)]
    sc = {"fps": fps, "strict_iou": dict(tp=tp, fp=fp, fn=fn + n_low)}
    for r in ("iou", "pull"):
        sc[r] = dict(tp=tp, fp=fp, fn=fn, n_gt=tp + fn, n_unc=n_low, n_pred=tp + fp, n_ignored=0, matched=matched,
                     pull_err_frames=[pull_err_frames] * tp, pull_err_s=[pull_err_frames / fps] * tp,
                     start_err_s=[0.1] * tp, end_err_s=[0.1] * tp)
    inds = [dict(indicator=k, severity=v) for k, v in (ind or {}).items()]          # as Indicator.to_dict()
    return dict(video_id=f"{ath}_{view}_{tempo}_{form}_{dev}", athlete=ath, view=view, tempo=tempo, form_type=form,
                deviation_type=dev, lighting_condition="bright", status=status, guard_reason="", fps=fps,
                pred_reps=[dict(start=0, pull=1, end=2, indicators=inds) for _ in range(tp + fp)], score=sc)


ALL_OK = {"lr_amplitude_ratio": "ok", "trunk_motion": "ok", "rom_proxy": "ok", "lr_lag": "ok"}


def _cohort(n=10, side_fn=0, paused_fn=0, form=True, views=D.REQUIRED_VIEWS):
    vids = []
    for k in range(1, n + 1):
        a = f"A{k:02d}"
        for view in views:
            for tempo in D.MANDATORY_TEMPOS:
                fn = (side_fn if view == "side" else 0) + (paused_fn if tempo == "paused" else 0)
                vids.append(_video(a, view, tempo, 8, 0, fn, ind=ALL_OK))
        if form:
            for dev, ind in D.INDICATOR_FOR_DEVIATION.items():
                if ind:
                    vids.append(_video(a, "rear", "normal", 6, 0, 0, form="deviated", dev=dev, ind={ind: "attention"}))
    return vids


def test_gates_pass_on_a_clean_cohort():
    ev = P6.apply_gates(_cohort())
    assert ev["verdict"] == "PASS", json.dumps(ev["gates"], indent=1)[:2000]
    assert ev["gates"]["G6_form_direction"]["n_right"] == 4


def test_g1_fails_on_recall_alone():
    vids = [dict(v, score=dict(v["score"], **{r: dict(v["score"][r], fn=v["score"][r]["tp"] // 5 + 1)
                                              for r in ("iou", "pull")})) for v in _cohort()]
    g = P6.apply_gates(vids)["gates"]["G1_pooled"]
    assert g["status"] == "FAIL" and g["f1"] >= 0.80 and g["recall"] < 0.85


def test_gates_are_decided_exactly_on_the_counts():
    # F1 = 1800/2250 = 0.80 exactly, but floating-point prf() gives 0.7999999999999999: must PASS G1
    v = _video("A01", "rear", "normal", 900, 300, 150)
    assert P6.pool([v["score"]])["iou"]["f1_raw"] < 0.80
    assert P6.f1_at_least(900, 300, 150, P6.G1_POOLED_F1) and P6.recall_at_least(900, 150, P6.G1_RECALL)
    assert P6.apply_gates([v])["gates"]["G1_pooled"]["status"] == "PASS"
    # athlete F1 = 52/80 = 0.65 exactly (float 0.6499999999999999): the athlete passes G2
    assert P6.f1_at_least(26, 11, 17, P6.G2_ATHLETE_F1) and not P6.f1_at_least(26, 11, 18, P6.G2_ATHLETE_F1)
    # recall 861/1013 = 0.849951 is displayed as 0.85 but must FAIL "recall >= 0.85"
    w = _video("A01", "rear", "normal", 861, 0, 152)
    assert P6.pool([w["score"]])["iou"]["recall"] == 0.85
    assert P6.apply_gates([w])["gates"]["G1_pooled"]["status"] == "FAIL"
    # pull MAE exactly 0.25 s (8 frames at 32 fps) passes G5, 9 frames fails
    assert P6.exact_pull_mae([_video("A01", "rear", "normal", 8, 0, 0, pull_err_frames=8, fps=32.0)["score"]]
                             ) == Fraction(1, 4)
    assert P6.apply_gates([_video("A01", "rear", "normal", 8, 0, 0, pull_err_frames=8, fps=32.0)]
                          )["gates"]["G5_pull_mae"]["status"] == "PASS"
    assert P6.apply_gates([_video("A01", "rear", "normal", 8, 0, 0, pull_err_frames=9, fps=32.0)]
                          )["gates"]["G5_pull_mae"]["status"] == "FAIL"


def test_g5_not_evaluable_without_matches_counts_as_fail():
    ev = P6.apply_gates([_video("A01", "rear", "normal", 0, 0, 5)])
    assert ev["gates"]["G5_pull_mae"]["status"] == "NOT EVALUABLE" and ev["verdict"].startswith("FAIL")


def test_side_view_failure_fails_the_per_view_gate_and_the_verdict():
    ev = P6.apply_gates(_cohort(side_fn=8))
    assert ev["gates"]["G3_per_view"]["side"]["status"] == "FAIL"
    assert ev["gates"]["G3_per_view"]["rear"]["status"] == "PASS" and ev["verdict"] == "FAIL"


def test_every_view_present_is_gated_and_flagged_low_n():
    vids = [v for v in _cohort() if not (v["view"] == "oblique" and v["athlete"] not in ("A01", "A02"))]
    g = P6.apply_gates(vids)["gates"]["G3_per_view"]["oblique"]
    assert g["status"] == "PASS" and g["low_n"] is True


def test_missing_required_slice_is_not_evaluable_and_counts_as_fail():
    ev = P6.apply_gates(_cohort(views=("rear", "side")))
    assert ev["gates"]["G3_per_view"]["oblique"]["status"] == "NOT EVALUABLE"
    assert ev["verdict"] == "FAIL (1 gate(s) NOT EVALUABLE, counted as FAIL)"


def test_paused_tempo_failure_fails_the_per_tempo_gate():
    ev = P6.apply_gates(_cohort(paused_fn=9))
    assert ev["gates"]["G4_per_tempo"]["paused"]["status"] == "FAIL" and ev["verdict"] == "FAIL"


def test_per_athlete_gate_allows_one_failure_in_ten():
    vids = _cohort()
    for v in vids:
        if v["athlete"] == "A03":
            v["score"] = dict(v["score"], **{r: dict(v["score"][r], tp=2, fn=6, matched=[[0, 0], [1, 1]],
                                                     pull_err_frames=[3] * 2, pull_err_s=[0.1] * 2,
                                                     start_err_s=[0.1] * 2, end_err_s=[0.1] * 2) for r in ("iou", "pull")})
    ev = P6.apply_gates(vids)
    assert ev["gates"]["G2_per_athlete"]["failing"] == ["A03"] and ev["gates"]["G2_per_athlete"]["status"] == "PASS"
    for v in vids:
        if v["athlete"] == "A04":
            v["score"] = dict(v["score"], **{r: dict(v["score"][r], tp=2, fn=6) for r in ("iou", "pull")})
    assert P6.apply_gates(vids)["gates"]["G2_per_athlete"]["status"] == "FAIL"


def test_low_share_is_reported_per_slice():
    vids = _cohort()
    for v in vids:
        if v["view"] == "side":
            v["score"] = dict(v["score"], **{r: dict(v["score"][r], n_unc=2) for r in ("iou", "pull")})
    g = P6.apply_gates(vids)["gates"]["G3_per_view"]["side"]
    assert g["n_low"] == 80 and g["low_share"] == pytest.approx(0.2)


# ----------------------------------------------------------------------------- G6 (paired form-indicator direction)
TYPES = ("asymmetric_pull", "excessive_trunk_motion", "reduced_rom", "delayed_side_onset")


def _g6(types_ok=TYPES, missing=(), one_athlete_matched=(), tempo="normal", dev_view="rear"):
    """A01..A03: a good take (indicators ok) and, per type, a deviated take, 6 reps each. Paired when
    both are at tempo normal in the same view."""
    vids = [_video(f"A{k:02d}", "rear", "normal", 6, 0, 0, ind=ALL_OK) for k in (1, 2, 3)]
    for dev, ind in D.INDICATOR_FOR_DEVIATION.items():
        if ind is None or dev in missing:
            continue
        sev = "attention" if dev in types_ok else "ok"
        for k in (1, 2, 3):
            status = "UNSUPPORTED_EXERCISE" if (dev in one_athlete_matched and k > 1) else "OK"
            v = _video(f"A{k:02d}", dev_view, tempo, 6, 0, 0, form="deviated", dev=dev, ind={ind: sev}, status=status)
            if status != "OK":                    # abstained: the annotated reps are there, nothing matched
                v["score"] = dict(v["score"], **{r: dict(v["score"][r], tp=0, fn=6, matched=[]) for r in ("iou", "pull")})
                v["pred_reps"] = []
            vids.append(v)
    return vids


def test_g6_pass_and_fail():
    g = P6.form_direction(_g6())
    assert g["status"] == "PASS" and g["types"]["asymmetric_pull"]["athletes_right"] == ["A01", "A02", "A03"]
    assert g["types"]["asymmetric_pull"]["difference"] == 1.0 and g["types"]["asymmetric_pull"]["difference_ci95"]
    g = P6.form_direction(_g6(types_ok=("asymmetric_pull", "reduced_rom")))
    assert g["status"] == "FAIL" and g["n_right"] == 2 and g["types"]["inconsistent_tempo"]["status"] == "NO INDICATOR"


def test_g6_uses_only_pairs_at_normal_tempo_in_the_same_view():
    g = P6.form_direction(_g6(tempo="slow"))                       # deviated takes not at normal tempo
    assert g["status"] == "FAIL" and g["n_not_evaluable"] == 4
    g = P6.form_direction(_g6(dev_view="side"))                    # good takes only in rear: no pairs
    assert g["status"] == "FAIL" and g["types"]["reduced_rom"]["athletes_paired"] == 0


def test_g6_single_athlete_evidence_is_not_a_right_direction():
    """A02/A03 abstain on the deviated takes: the annotations make the type evaluable, but matched reps
    come from one athlete only -> DIRECTION NOT SHOWN, which counts against the gate."""
    g = P6.form_direction(_g6(one_athlete_matched=("asymmetric_pull", "excessive_trunk_motion", "reduced_rom")))
    t = g["types"]["asymmetric_pull"]
    assert t["status"] == "DIRECTION NOT SHOWN" and t["athletes_with_matched_deviated_reps"] == 1
    assert g["status"] == "FAIL"


def test_g6_needs_the_direction_in_two_individual_athletes():
    vids = _g6()
    for v in vids:                     # only A01's deviated asymmetric_pull reps raise the indicator
        if v["deviation_type"] == "asymmetric_pull" and v["athlete"] != "A01":
            v["pred_reps"] = [dict(r, indicators=[dict(indicator="lr_amplitude_ratio", severity="ok")])
                              for r in v["pred_reps"]]
    t = P6.form_direction(vids)["types"]["asymmetric_pull"]
    assert t["rate_deviated"] > t["rate_good"] and t["athletes_right"] == ["A01"]
    assert t["status"] == "DIRECTION NOT SHOWN"


def test_g6_not_evaluable_types_count_as_not_right():
    g = P6.form_direction(_g6(missing=("delayed_side_onset",)))
    assert (g["n_right"], g["n_not_evaluable"], g["status"]) == (3, 1, "PASS")
    g = P6.form_direction(_g6(types_ok=("asymmetric_pull", "excessive_trunk_motion"), missing=("delayed_side_onset",)))
    assert (g["n_right"], g["n_not_evaluable"], g["status"]) == (2, 1, "FAIL")


def test_athlete_bootstrap_is_deterministic():
    vids = _cohort(side_fn=3)
    a, b = P6.bootstrap_ci(vids, "f1"), P6.bootstrap_ci(vids, "f1")
    point = round(P6.pool([v["score"] for v in vids])["iou"]["f1_raw"], 4)   # the CI is reported to 4 decimals
    assert a == b and a[0] <= point <= a[1]
    vids[0]["score"] = dict(vids[0]["score"], iou=dict(vids[0]["score"]["iou"], tp=1, fn=20))   # athletes now differ
    c = P6.bootstrap_ci(vids, "f1")
    assert c[0] < c[1] and P6.bootstrap_ci(vids[:1], "f1") is None
    assert P6.bootstrap_ci(vids, "pull_mae_s") == [0.1, 0.1]


# ----------------------------------------------------------------------------- tempo compliance + verdict of record
def test_tempo_compliance_is_reported_from_the_annotations():
    def take(vid, ath, tempo, durs, rest):
        reps, t = [], 0
        for d in durs:
            reps.append(dict(start=t, pull=t + 1, end=t + int(d * 30), confidence="high"))
            t += int(d * 30) + int(rest * 30)
        return dict(video_id=vid, athlete=ath, tempo=tempo, fps=30.0), reps
    takes = [take("n", "A01", "normal", [2.0] * 5, 0.2), take("s", "A01", "slow", [3.5] * 5, 0.2),
             take("f", "A01", "fast", [2.0] * 5, 0.2), take("p", "A01", "paused", [3.2] * 5, 1.5),
             take("q", "A01", "paused", [3.2] * 5, 0.5)]
    tab = P6.tempo_compliance([v for v, _ in takes], {v["video_id"]: r for v, r in takes})
    assert tab["slow"] == dict(n=1, compliant=1, not_compliant=[])
    assert tab["fast"]["compliant"] == 0 and tab["paused"]["n"] == 2 and tab["paused"]["compliant"] == 1
    assert "normal" not in tab


def test_verdict_of_record():
    e = [dict(run_id="1", status="crashed"), dict(run_id="2", status="completed", verdict="FAIL"),
         dict(run_id="3", status="completed", verdict="PASS", rerun_reason="again")]
    assert P6.verdict_of_record(e)["run_id"] == "2"               # a plain re-run never replaces it
    e.append(dict(run_id="4", status="completed", verdict="PASS", evaluation_fix="fps bug"))
    assert P6.verdict_of_record(e)["run_id"] == "4"


# ----------------------------------------------------------------------------- annotation agreement (§3.4)
def test_agreement_subset_size_is_exact():
    assert P6.agreement_needed(10) == 8 and P6.agreement_needed(150) == 15 and P6.agreement_needed(151) == 16


def test_agreement_subset_is_deterministic_stratified_and_disjoint():
    eligible = [f"A{a:02d}_{v}_{t}_take1" for a in range(1, 14) for v in D.VIEWS for t in D.TEMPOS]   # 156
    s1 = P6.select_agreement_subset(eligible, [], 1)
    assert s1 == P6.select_agreement_subset(eligible, [], 1) and len(s1) == 16
    cells = {(D.parse_video_name(v)["view"], D.parse_video_name(v)["tempo"]) for v in s1}
    assert len(cells) == 12                                        # every view x tempo cell
    s2 = P6.select_agreement_subset(eligible, s1, 2)
    assert not set(s1) & set(s2) and len(s2) == 16 and s2 != s1


def _ann(root, who, vid, reps, fps=30.0, sha="s"):
    p = D.annotation_csv(str(root), who, vid)
    _write(p, D.REP_COLUMNS, [dict(rep_id=k + 1, start_frame=r[0], pull_frame=r[1], end_frame=r[2],
                                   annotation_confidence=r[3] if len(r) > 3 else "high", notes="")
                              for k, r in enumerate(reps)])
    json.dump(dict(video_id=vid, annotator=who, video_sha256=sha, n_frames=1000, fps=fps, cv2=P6._cv2_version(),
                   csv_sha256=D.sha256_file(p), status="complete", pending_marks=[]),
              open(D.annotation_meta(str(root), who, vid), "w"))


def test_agreement_is_recomputed_from_the_files_by_the_runner(tmp_path):
    included = [f"A01_{v}_{t}_take1" for v in D.VIEWS for t in D.TEMPOS]                # 12 videos
    base = [(10 + 60 * k, 30 + 60 * k, 50 + 60 * k) for k in range(6)]
    for vid in included:
        _ann(tmp_path, "a1", vid, base)
        _ann(tmp_path, "a2", vid, [(s + 1, p + 1, e + 1) for s, p, e in base])
    subset = P6.select_agreement_subset(included, [], 1)
    res = P6.measure_agreement(str(tmp_path), subset, len(included), "a2")
    assert res["complete"] and res["accepted"] and res["tier_agreement"] == {"high->high": 72}
    rnd = dict(round=1, seed=D.SUBSET_SEED, eligible=included, subset=subset, notes=[],
               result={k: v for k, v in res.items() if k != "per_video"})
    agreement = dict(primary="a1", rounds=[rnd])
    json.dump(agreement, open(os.path.join(tmp_path, "agreement.json"), "w"))
    assert P6.verify_agreement(str(tmp_path), agreement, included) == []
    os.makedirs(tmp_path / "qa", exist_ok=True)
    json.dump(agreement, open(tmp_path / D.AGREEMENT_REPORT, "w"))
    assert D.load_agreement(str(tmp_path), included)[1] == []
    bad = json.loads(json.dumps(agreement))
    bad["rounds"][0]["subset"] = subset[:-1]                                             # hand-picked subset
    assert any("not the one the pre-registered rule" in e for e in P6.verify_agreement(str(tmp_path), bad, included))
    assert any("different set of videos" in e for e in P6.verify_agreement(str(tmp_path), agreement, included[:-1]))
    for vid in included:                                       # a2's marks no longer agree: recomputation differs
        _ann(tmp_path, "a2", vid, [(s + 25, p + 25, e + 25) for s, p, e in base])
    assert any("not reproduced" in e for e in P6.verify_agreement(str(tmp_path), agreement, included))


# ----------------------------------------------------------------------------- frozen identity (git simulated)
class _Proc:
    def __init__(self, out=b"", rc=0):
        self.stdout, self.returncode = out, rc


@pytest.fixture
def frozen(tmp_path, monkeypatch):
    real, memo = P6.file_hashes, {}

    def fh(rels):
        rels = list(rels)
        memo.update(real([r for r in rels if r not in memo]))
        return {r: memo[r] for r in rels}
    monkeypatch.setattr(P6, "file_hashes", fh)
    pk = P6.key_packages()
    monkeypatch.setattr(P6, "key_packages", lambda: dict(pk))
    freeze = dict(tag=P6.FREEZE_TAG, rules=json.loads(json.dumps(P6.rules())),
                  system=dict(files=fh(P6.system_files()), packages=dict(pk)),
                  evaluation=dict(files=fh(P6.EVALUATION_FILES)))
    tools = dict(tag=P6.TOOLS_TAG, files=fh(P6.TOOL_FILES))
    store = {(P6.FREEZE_TAG, P6.FREEZE_REL): freeze, (P6.TOOLS_TAG, P6.TOOLS_FREEZE_REL): tools}
    for attr, obj, name in (("FREEZE_FILE", freeze, "freeze.json"), ("TOOLS_FREEZE_FILE", tools, "tools.json")):
        json.dump(obj, open(tmp_path / name, "w"))
        monkeypatch.setattr(P6, attr, str(tmp_path / name))
    monkeypatch.setattr(P6, "git_show", lambda ref, rel: None if (ref, rel) not in store else
                        store[(ref, rel)] if isinstance(store[(ref, rel)], str) else json.dumps(store[(ref, rel)]))
    monkeypatch.setattr(P6, "git_tree_files", lambda ref: None)
    monkeypatch.setattr(P6, "git_is_ancestor", lambda ref: True)
    monkeypatch.setattr(P6, "tag_info", lambda tag: None)
    return store


def test_identity_ok_when_everything_matches_the_tags(frozen):
    r = P6.check_identity()
    assert r["problems"] == [] and r["evaluation_changes"] == {}


def test_identity_flags_system_package_rules_and_ancestry_drift(frozen, monkeypatch):
    f = frozen[(P6.FREEZE_TAG, P6.FREEZE_REL)]
    f["system"]["files"]["ai/core/features.py"] = "0" * 64
    f["system"]["packages"]["torch"] = "0.0+cpu"
    f["rules"]["gates"]["G1_POOLED_F1"] = "0.75"
    probs = P6.check_identity()["problems"]
    assert any("working tree differs" in p for p in probs)        # the working-tree copy is no longer the tagged one
    assert any("ai/core/features.py" in p for p in probs) and any("package torch" in p for p in probs)
    assert any("rule constants" in p for p in probs)
    monkeypatch.setattr(P6, "git_is_ancestor", lambda ref: False)
    assert any("does not descend" in p for p in P6.check_identity()["problems"])
    monkeypatch.setattr(P6, "git_show", lambda ref, rel: None)
    probs = P6.check_identity()["problems"]
    assert any(P6.FREEZE_TAG in p for p in probs) and any(P6.TOOLS_TAG in p for p in probs)


def test_identity_checks_the_content_at_the_tag(frozen, monkeypatch):
    monkeypatch.setattr(P6, "git_tree_files", lambda ref: {"ai/evaluation/protocol.py"})
    monkeypatch.setattr(P6, "git_show_bytes", lambda ref, rel: b"something else")
    assert any("content at tag" in p for p in P6.check_identity()["problems"])


def test_identity_evaluation_code_change_needs_a_committed_documented_fix(frozen, monkeypatch):
    f = frozen[(P6.FREEZE_TAG, P6.FREEZE_REL)]
    f["evaluation"]["files"]["ai/scripts/phase6_evaluate.py"] = "1" * 64
    json.dump(f, open(P6.FREEZE_FILE, "w"))
    assert any("evaluation code differs" in p for p in P6.check_identity()["problems"])
    monkeypatch.setattr(P6, "_git", lambda *a: _Proc(b"" if a[0] == "status" else b"diff --git a/x b/x\n"))
    assert any("no committed addendum" in p for p in P6.check_identity(evaluation_fix="A6-01")["problems"])
    frozen[("HEAD", P6.ADDENDA_REL)] = "# Addenda\n\n## A6-01 — 2026-11-02 — fps rounding bug in scoring\n"
    r = P6.check_identity(evaluation_fix="A6-01")
    assert r["problems"] == [] and "EVALUATION-CODE FIX" in r["notes"][0] and r["evaluation_diff"]["sha256"]
    monkeypatch.setattr(P6, "_git", lambda *a: _Proc(b" M ai/scripts/phase6_evaluate.py" if a[0] == "status" else b""))
    assert any("commit the changed" in p for p in P6.check_identity(evaluation_fix="A6-01")["problems"])


def test_identity_flags_a_changed_tool(frozen):
    frozen[(P6.TOOLS_TAG, P6.TOOLS_FREEZE_REL)]["files"]["ai/scripts/validate_recordings.py"] = "2" * 64
    probs = P6.check_identity()["problems"]
    assert any("frozen tool" in p for p in probs) and any(P6.TOOLS_FREEZE_REL in p for p in probs)


def test_freeze_refuses_untracked_or_modified_pinned_text_files(monkeypatch):
    from ai.scripts import freeze_candidate as FC
    tracked, dirty = {"dirty.py", "clean.py"}, {"dirty.py"}
    monkeypatch.setattr(FC.P6, "find_git", lambda: "git")
    monkeypatch.setattr(FC, "_git", lambda *a: _Proc(rc=0 if a[-1] in tracked else 1) if a[0] == "ls-files"
                        else _Proc(b" M x" if a[-1] in dirty else b""))
    probs = FC.git_problems(["new.py", "dirty.py", "clean.py", "weights.bin"])
    assert probs == ["new.py: not tracked by git (commit it before freezing)",
                     "dirty.py: uncommitted changes (commit them before freezing)"]


def _ident(problems=()):
    return dict(problems=list(problems), notes=[], evaluation_changes={}, evaluation_diff=None, tools=None,
                tags={t: None for t in (P6.FREEZE_TAG, P6.TOOLS_TAG, P6.LOCK_TAG)}, freeze_files={})


def test_final_run_refuses_drift_and_disallowed_options(tmp_path, monkeypatch):
    monkeypatch.setattr(P6, "OUT_DEFAULT", str(tmp_path / "out"))
    monkeypatch.setattr(P6, "check_identity", lambda fix=None: _ident(["drift"]))
    assert P6.main(["--root", str(tmp_path)]) == 3
    assert P6.main(["--root", str(tmp_path), "--device", "cuda"]) == 6
    assert P6.main(["--root", str(tmp_path), "--no-mode-b"]) == 6
    assert P6.main(["--root", str(tmp_path), "--out", str(tmp_path / "elsewhere")]) == 6
    assert P6.main(["--dry-run-dev", "--lock"]) == 6


def test_a_cloud_synced_data_root_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("OneDrive", str(tmp_path / "od"))
    monkeypatch.setattr(P6, "check_identity", lambda fix=None: pytest.fail("must refuse before any check"))
    assert P6.main(["--root", str(tmp_path / "od" / "phase6")]) == 2
    assert D.cloud_sync_root(str(tmp_path / "od" / "phase6")) and not D.cloud_sync_root(str(tmp_path / "local"))


# ----------------------------------------------------------------------------- dataset checks against the files
def _dataset(tmp_path, n_frames=100, fps=30.0):
    """A tiny but consistent Phase 6 layout (one athlete) in the final formats."""
    root = tmp_path / "p6"
    vids = ["A01_rear_normal_take1", "A01_rear_normal_take2"]
    man, reps, intake = [], [], {}
    for i, v in enumerate(vids):
        p = root / "videos" / "A01" / f"{v}.mp4"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(f"video {v}".encode())
        sha = D.sha256_file(str(p))
        man.append(_mrow(v, *(("deviated", "asymmetric_pull") if i else ("good", "none"))))
        rows = [(10 + 20 * k, 15 + 20 * k, 20 + 20 * k) for k in range(3)]
        _ann(root, "a1", v, rows, fps=fps, sha=sha)
        meta = json.load(open(D.annotation_meta(str(root), "a1", v)))
        json.dump(dict(meta, n_frames=n_frames), open(D.annotation_meta(str(root), "a1", v), "w"))
        reps += [dict(video_id=v, rep_id=k + 1, start_frame=s, pull_frame=pp, end_frame=e, annotation_confidence="high",
                      annotator="a1", notes="") for k, (s, pp, e) in enumerate(rows)]
        intake[v] = dict(status="PASS", reasons=[], warnings=[], sha256=sha, n_frames=n_frames, fps=fps,
                         framing=dict(body_inside=1.0))
    _write(root / D.MANIFEST, D.MANIFEST_COLUMNS, man)
    _write(root / D.FINAL_REPS, D.FINAL_REP_COLUMNS, reps)
    os.makedirs(root / "qa", exist_ok=True)
    json.dump(dict(tool_sha256="t", device="cpu", framing_checked=True, videos=intake, cohort=dict(status="PASS")),
              open(root / D.INTAKE_REPORT, "w"))
    json.dump(dict(primary="a1", rounds=[]), open(root / D.AGREEMENT_REPORT, "w"))
    return root, vids


def _intake(root, **kw):
    rep = json.load(open(root / D.INTAKE_REPORT))
    rep.update(kw)
    json.dump(rep, open(root / D.INTAKE_REPORT, "w"))
    return rep


def test_load_dataset_consistent_layout(tmp_path):
    root, vids = _dataset(tmp_path)
    d = P6.load_dataset(str(root))
    assert d["file_errors"] == [] and d["included"] == vids
    assert d["agreement_errors"] and d["coverage_problems"] and d["errors"]        # 1 athlete, no agreement round
    dry = P6.load_dataset(str(root), dry_run=True)
    assert dry["errors"] == [] and dry["relaxed"]


def test_load_dataset_ties_inputs_to_the_evaluated_files(tmp_path):
    root, vids = _dataset(tmp_path)
    (root / "videos" / "A01" / f"{vids[0]}.mp4").write_bytes(b"re-exported")          # changed after intake
    errs = P6.load_dataset(str(root))["file_errors"]
    assert any("not the one the intake check saw" in e for e in errs)
    assert any("annotated a different file" in e for e in errs)
    root, vids = _dataset(tmp_path / "b", n_frames=25)                               # END 60 beyond 25 frames
    assert any("beyond the video" in e for e in P6.load_dataset(str(root))["file_errors"])
    root, vids = _dataset(tmp_path / "c")
    with open(D.annotation_csv(str(root), "a1", vids[0]), "a", encoding="utf-8") as f:   # hand edit
        f.write("4,90,95,99,high,\n")
    errs = P6.load_dataset(str(root))["file_errors"]
    assert any("changed outside the annotation tool" in e for e in errs)
    assert any("differs from annotator 'a1'" in e for e in errs)
    root, vids = _dataset(tmp_path / "d", fps=25.0)
    _intake(root, videos={v: dict(r, fps=30.0) for v, r in json.load(open(root / D.INTAKE_REPORT))["videos"].items()})
    assert any("annotation fps" in e for e in P6.load_dataset(str(root))["file_errors"])


def test_load_dataset_requires_the_primary_annotator_and_finished_sidecars(tmp_path):
    root, vids = _dataset(tmp_path)
    m = json.load(open(D.annotation_meta(str(root), "a1", vids[0])))
    json.dump(dict(m, status="in_progress", pending_marks=[{"start": 5}]), open(D.annotation_meta(str(root), "a1", vids[0]), "w"))
    rows = list(csv.DictReader(open(root / D.FINAL_REPS, encoding="utf-8")))
    _write(root / D.FINAL_REPS, D.FINAL_REP_COLUMNS, [dict(r, annotator="a2") if r["video_id"] == vids[1] else r
                                                      for r in rows])
    errs = P6.load_dataset(str(root))["file_errors"]
    assert any("not finished" in e for e in errs) and any("pre-registered primary" in e for e in errs)


def test_load_dataset_intake_rules(tmp_path):
    root, vids = _dataset(tmp_path)
    _intake(root, framing_checked=False, device="cuda")
    errs = P6.load_dataset(str(root))["file_errors"]
    assert any("without the framing check" in e for e in errs) and any("pre-registered 'cpu'" in e for e in errs)
    root, vids = _dataset(tmp_path / "b")
    rep = json.load(open(root / D.INTAKE_REPORT))
    rep["videos"][vids[0]].pop("framing")
    rep["videos"]["A01_rear_normal_take9"] = dict(status="PASS", sha256="x")              # stale verdict
    json.dump(rep, open(root / D.INTAKE_REPORT, "w"))
    (root / "videos" / "A01" / "sub").mkdir()
    (root / "videos" / "A01" / "sub" / f"{vids[1]}.mov").write_bytes(b"copy")             # duplicate stem
    errs = P6.load_dataset(str(root))["file_errors"]
    assert any("no framing measurements" in e for e in errs)
    assert any("no longer exists" in e for e in errs) and any("occurs 2 times" in e for e in errs)


def test_intake_fail_is_the_only_exclusion_and_a_cohort_fail_blocks(tmp_path):
    root, vids = _dataset(tmp_path)
    rep = json.load(open(root / D.INTAKE_REPORT))
    rep["videos"][vids[1]].update(status="FAIL", reasons=["resolution 1280x720"])
    rep["videos"][vids[0]].update(status="WARN", warnings=["dim"])
    json.dump(rep, open(root / D.INTAKE_REPORT, "w"))
    d = P6.load_dataset(str(root))
    assert d["included"] == [vids[0]] and d["excluded"] == {vids[1]: ["resolution 1280x720"]}
    assert any("reps for the excluded take" in e for e in d["file_errors"])
    _intake(root, cohort=dict(status="FAIL", problems=["A01: missing side/paused"]))
    assert any("cohort check is 'FAIL'" in e for e in P6.load_dataset(str(root))["file_errors"])


def test_dataset_fingerprint_covers_the_agreement_record_and_history(tmp_path):
    root, vids = _dataset(tmp_path)
    a = D.dataset_fingerprint(str(root), vids)["fingerprint"]
    json.dump(dict(primary="a1", rounds=[], note="x"), open(root / D.AGREEMENT_REPORT, "w"))
    b = D.dataset_fingerprint(str(root), vids)["fingerprint"]
    with open(root / D.INTAKE_HISTORY, "a", encoding="utf-8") as f:
        f.write(json.dumps(dict(video_id=vids[0], status="PASS")) + "\n")
    assert len({a, b, D.dataset_fingerprint(str(root), vids)["fingerprint"]}) == 3


# ----------------------------------------------------------------------------- running a video
@dataclass
class _Comp:
    detector: object


def test_a_system_exception_becomes_system_error_and_mode_b_never_blocks(tmp_path, monkeypatch):
    import ai.core.pipeline as pl
    monkeypatch.setattr(pl, "extract_pose", lambda path, comp: (_ for _ in ()).throw(RuntimeError("boom")))
    r = P6.run_video("x.mp4", "A01_rear_normal_take1", _Comp(None), str(tmp_path), True, "k", 10, 30.0)
    assert r["result"] is None and "boom" in r["system_error"] and set(r["mode_b_errors"]) == set(P6.MODE_B_PRESETS)


def test_a_frame_count_mismatch_aborts_the_run(tmp_path, monkeypatch):
    import ai.core.pipeline as pl

    def fake_extract(path, comp):
        for _ in range(2):
            comp.detector(None)
        return types.SimpleNamespace(pose=np.zeros((2, 17, 2)), conf=np.zeros((2, 17)), tracker_log=[],
                                     container_fps=30.0, width=1920, height=1080, source_sha256="s", n_frames=2)
    monkeypatch.setattr(pl, "extract_pose", fake_extract)
    det = _Comp(lambda frame: (np.zeros((8, 17, 3)), np.zeros((8, 5))))
    with pytest.raises(P6.EvaluationIntegrityError):
        P6.run_video("x.mp4", "A01_rear_normal_take1", det, str(tmp_path), False, "k", 3, 30.0)


# ----------------------------------------------------------------------------- data lock, ledger, one run per cohort
def test_data_lock_check(tmp_path, monkeypatch):
    lock = dict(fingerprint="f" * 64, videos={"A01_rear_normal_take1": "a"}, meta={"manifest.csv": "m"})
    monkeypatch.setattr(P6, "LOCK_FILE", str(tmp_path / "lock.json"))
    json.dump(lock, open(tmp_path / "lock.json", "w"))
    monkeypatch.setattr(P6, "git_is_ancestor", lambda ref: True)
    monkeypatch.setattr(P6, "git_show", lambda ref, rel: None)
    assert "not found" in P6.check_lock(dict(fingerprint="f" * 64, videos={}, meta={}))[0]
    monkeypatch.setattr(P6, "git_show", lambda ref, rel: json.dumps(lock) if ref == P6.LOCK_TAG else None)
    assert P6.check_lock(dict(lock)) == []
    p = P6.check_lock(dict(fingerprint="0" * 64, videos={"A01_rear_normal_take1": "b"}, meta={"manifest.csv": "m"}))
    assert len(p) == 1 and "A01_rear_normal_take1" in p[0]


def _patch_ready(monkeypatch, out, fp="x" * 64):
    monkeypatch.setattr(P6, "OUT_DEFAULT", str(out))
    monkeypatch.setattr(P6, "check_identity", lambda fix=None: _ident())
    monkeypatch.setattr(P6, "load_dataset", lambda root, dry_run=False: dict(
        errors=[], included=[], shas={}, intake=None, reps={}, manifest={}, files={}, excluded={}, coverage={},
        coverage_problems=[], relaxed=[], agreement=None))
    monkeypatch.setattr(P6.D, "dataset_fingerprint", lambda root, ids, shas=None: dict(
        fingerprint=fp, videos={"A01_rear_normal_take1": "new"}, meta={}))
    monkeypatch.setattr(P6, "check_lock", lambda f: [])
    monkeypatch.setattr(P6, "outputs_committed", lambda: True)
    monkeypatch.setattr(P6, "ledger_matches_git", lambda out_dir: True)
    monkeypatch.setattr(P6, "system_id", lambda: "sys")


def test_the_lock_is_written_once(tmp_path, monkeypatch):
    _patch_ready(monkeypatch, tmp_path / "out")
    monkeypatch.setattr(P6, "LOCK_FILE", str(tmp_path / "lock.json"))
    monkeypatch.setattr(P6, "tag_info", lambda tag: None)
    assert P6.main(["--root", str(tmp_path), "--lock"]) == 0
    assert json.load(open(tmp_path / "lock.json"))["fingerprint"] == "x" * 64
    assert P6.main(["--root", str(tmp_path), "--lock"]) == 4


def test_final_run_needs_the_data_lock_and_committed_outputs(tmp_path, monkeypatch):
    _patch_ready(monkeypatch, tmp_path / "out")
    monkeypatch.setattr(P6, "check_lock", lambda f: ["no lock"])
    assert P6.main(["--root", str(tmp_path)]) == 3
    monkeypatch.setattr(P6, "check_lock", lambda f: [])
    monkeypatch.setattr(P6, "outputs_committed", lambda: False)
    assert P6.main(["--root", str(tmp_path)]) == 4


@pytest.mark.parametrize("status", ["completed", "crashed", "started"])
def test_any_earlier_final_run_blocks_a_new_one_without_a_documented_reason(tmp_path, monkeypatch, status):
    out = tmp_path / "out"
    out.mkdir()
    P6.write_ledger(str(out), [dict(run_id="20261101T000000Z", kind=P6.FINAL, status=status,
                                    dataset_fingerprint="x" * 64)])
    _patch_ready(monkeypatch, out)
    assert P6.main(["--root", str(tmp_path)]) == 4


def test_a_rerun_must_use_identical_inputs(tmp_path, monkeypatch):
    out = tmp_path / "out"
    out.mkdir()
    P6.write_ledger(str(out), [dict(run_id="20261101T000000Z", kind=P6.FINAL, status="crashed",
                                    dataset_fingerprint="y" * 64,
                                    inputs=dict(videos={"A01_rear_normal_take1": "old"}, meta={}))])
    _patch_ready(monkeypatch, out)
    assert P6.main(["--root", str(tmp_path), "--rerun-reason", "relabelled"]) == 2


def test_a_rerun_on_identical_inputs_is_logged(tmp_path, monkeypatch):
    out = tmp_path / "out"
    out.mkdir()
    P6.write_ledger(str(out), [dict(run_id="20261101T000000Z", kind=P6.FINAL, status="crashed",
                                    dataset_fingerprint="x" * 64,
                                    inputs=dict(videos={"A01_rear_normal_take1": "new"}, meta={}))])
    _patch_ready(monkeypatch, out)
    monkeypatch.setattr(P6, "evaluate", lambda videos, reps=None: (_ for _ in ()).throw(RuntimeError("stop")))
    import ai.core.pipeline as pl
    monkeypatch.setattr(pl, "load_components", lambda device="cpu": None)
    assert P6.main(["--root", str(tmp_path), "--rerun-reason", "laptop slept during run 1"]) == 5
    e = P6.read_ledger(str(out))[-1]
    assert e["rerun_reason"] == "laptop slept during run 1" and e["status"] == "crashed"
    assert e["input_changes"]["changed_videos"] == [] and not os.path.exists(out / P6.RUN_LOCK)


def test_an_active_run_lock_blocks_a_second_run(tmp_path, monkeypatch):
    out = tmp_path / "out"
    out.mkdir()
    (out / P6.RUN_LOCK).write_text("123")
    _patch_ready(monkeypatch, out)
    assert P6.main(["--root", str(tmp_path)]) == 4


def test_dataset_problems_refuse_before_anything_runs(tmp_path, monkeypatch):
    monkeypatch.setattr(P6, "OUT_DEFAULT", str(tmp_path / "o"))
    monkeypatch.setattr(P6, "check_identity", lambda fix=None: _ident())
    assert P6.main(["--root", str(tmp_path / "empty")]) == 2
    assert P6.main(["--root", str(tmp_path / "empty"), "--check-only"]) == 2
    assert not os.path.exists(tmp_path / "o")


# ----------------------------------------------------------------------------- report
def test_report_contains_every_pre_registered_section():
    vids = _cohort(side_fn=8)
    vids[0]["status"], vids[0]["guard_reason"] = "UNSUPPORTED_EXERCISE", "peak height implausible"
    ev = P6.evaluate(vids)
    rep = dict(kind=P6.FINAL, run_id="r1", commit="abc", verdict_of_record=dict(run_id="r1", verdict=ev["verdict"]),
               identity=dict(problems=[], notes=[], evaluation_changes={}, evaluation_diff=None,
                             tags={P6.FREEZE_TAG: dict(commit="c" * 40, date="2026-10-10")}, freeze_files={}),
               dataset=dict(fingerprint="f" * 64, excluded={"A09_side_fast_take1": ["fps 15"]}, relaxed=[],
                            agreement=dict(primary="a1", rounds=[dict(round=1, subset=["x"] * 12, notes=[], result=dict(
                                accepted=True, checks=dict(iou_f1=dict(value=0.95), pull_mae_s=dict(value=0.05)),
                                tier_agreement={"high->high": 60}))]),
                            intake_history_summary=dict(checked_files=130, takes_failed_then_passed=2, fail_reasons={},
                                                        failed_files=[dict(video_id="A07_side_slow_take1", sha256="ab",
                                                                           reasons=["second person"], now="deleted")])),
               addenda=dict(headings=["A6-01 — 2026-11-02 — example"], sha256="s", sha256_at_lock="s"),
               evaluation=ev, videos=vids, ledger=[dict(run_id="r1", status="completed", verdict=ev["verdict"])])
    md = P6.render_markdown(rep)
    for needle in ("Known limitations", "R-07", "R-08", "false abstention", "conditional", "Rep-count error",
                   "Annotation agreement", "Exclusions", "A09_side_fast_take1", "Run history", "wide",
                   "Verdict of record", "Tempo compliance", "multiplicity", "NOT EVALUABLE counts as FAIL",
                   "Mode B" if ev["secondary"]["mode_b"] else "Gates", P6.FREEZE_TAG, "A07_side_slow_take1",
                   "deleted", "Addenda", "A6-01"):
        assert needle in md, needle


def test_intake_history_lists_every_failed_file_and_its_fate(tmp_path):
    rows = [dict(video_id="A01_rear_normal_take1", sha256="old", status="FAIL", reasons=["fps 15: below 24"]),
            dict(video_id="A01_rear_normal_take1", sha256="new", status="PASS", reasons=[]),
            dict(video_id="A01_side_slow_take1", sha256="gone", status="FAIL", reasons=["second person in 9% of frames"]),
            dict(video_id="A01_side_fast_take2", sha256="kept", status="FAIL", reasons=["resolution 1280x720"])]
    os.makedirs(tmp_path / "qa")
    with open(tmp_path / D.INTAKE_HISTORY, "w", encoding="utf-8") as f:
        f.writelines(json.dumps(r) + "\n" for r in rows)
    s = P6.intake_history_summary(str(tmp_path), ["A01_rear_normal_take1"],
                                  {"A01_rear_normal_take1": "new", "A01_side_fast_take2": "kept"})
    assert s["checked_files"] == 4 and s["takes_failed_then_passed"] == 1
    assert {x["video_id"]: x["now"] for x in s["failed_files"]} == {
        "A01_rear_normal_take1": "replaced; the new file passed", "A01_side_slow_take1": "deleted",
        "A01_side_fast_take2": "present, excluded"}


# ----------------------------------------------------------------------------- end to end (dev dry run)
_VIDS = [os.path.join(REPO_ROOT, "ai", "data", "seated_row", "correct" if n.startswith("c") else "asymmetry", n + ".mp4")
         for n in ("correct_01", "correct_02", "correct_03", "asymmetry_01", "asymmetry_02", "asymmetry_03")]
_YOLO = os.path.join(REPO_ROOT, "yolo11n-pose.pt")


@pytest.mark.slow
@pytest.mark.skipif(not (all(map(os.path.exists, _VIDS)) and os.path.exists(MBLITE_CKPT) and os.path.exists(_YOLO)),
                    reason="raw dev videos or checkpoints not present (git-ignored)")
def test_dry_run_on_dev_videos_reproduces_the_frozen_numbers(tmp_path):
    """The runner end to end on the 6 internal DEV videos laid out as a Phase 6 dataset in the final
    formats: Mode A must equal the frozen result (IoU F1 0.8406 = 29/10/1, pull F1 0.8529 = 29/9/1) and the
    Mode B replay the frozen live numbers (V2 0.806 = 27/10/3, V3 0.8125 = 26/8/4)."""
    out = tmp_path / "dry"
    assert P6.main(["--dry-run-dev", "--out", str(out)]) == 0
    rep = json.load(open(out / "phase6_results.json", encoding="utf-8"))
    p = rep["evaluation"]["pooled"]
    assert (p["iou"]["tp"], p["iou"]["fp"], p["iou"]["fn"], p["iou"]["f1"]) == (29, 10, 1, 0.8406)
    assert (p["pull"]["tp"], p["pull"]["fp"], p["pull"]["fn"], p["pull"]["f1"]) == (29, 9, 1, 0.8529)
    mb = rep["evaluation"]["secondary"]["mode_b"]
    assert (mb["V2_quality"]["iou"]["tp"], mb["V2_quality"]["iou"]["fp"], mb["V2_quality"]["iou"]["fn"],
            mb["V2_quality"]["iou"]["f1"]) == (27, 10, 3, 0.806)
    assert (mb["V3_budget_15fps"]["iou"]["tp"], mb["V3_budget_15fps"]["iou"]["fp"], mb["V3_budget_15fps"]["iou"]["fn"],
            mb["V3_budget_15fps"]["iou"]["f1"]) == (26, 8, 4, 0.8125)
    assert rep["kind"] == P6.DRY and rep["evaluation"]["verdict"].startswith("FAIL") and rep["dataset"]["relaxed"]
    assert (out / "PHASE6_RESULTS.md").exists() and len(rep["dataset"]["per_video_results_sha256"]) == 6
