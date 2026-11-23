"""Phase 3 P3.5/P3.6: ai.core.guards. Unit tests on synthetic garbage (must abstain) + an
integration check that NO real video this project has ever abstains (false-abstention check,
"measure false abstentions vs prevented errors") and that safe_predict_video's reps are
byte-identical to predict_video's whenever it returns OK (additive wrapper, zero behaviour
change to the existing, already-tested decode path)."""
import numpy as np
import pytest

from ai.core.decoder import predict_video
from ai.core.features import prepare_video
from ai.core.guards import (check_exercise_plausibility, check_pre_decode, safe_predict_video)
from ai.tests.conftest import needs_ext_caches, needs_mendeley_caches, needs_sr_caches


def _toy_video(T=90, frac_valid=1.0, seed=0):
    rng = np.random.default_rng(seed)
    P = np.zeros((T, 17, 3))
    P[:, 8, 1] = 0.5
    P[:, 11, :] = [-0.2, 0.45, 0.0]
    P[:, 14, :] = [0.2, 0.45, 0.0]
    t = np.arange(T)
    z = 0.3 * np.sin(2 * np.pi * t / 30.0)
    P[:, 12, 2] = -z
    P[:, 15, 2] = -z
    P += rng.normal(0, 1e-4, P.shape)
    conf = np.full((T, 17), 1.0)
    n_valid = int(round(frac_valid * T))
    conf[n_valid:] = 0.0
    return prepare_video("toy", P, 30.0, conf=conf)


# ----------------------------------------------------------------------------- pre-decode guards
def test_no_person_when_fully_invalid():
    v = _toy_video(frac_valid=0.0)
    g = check_pre_decode(v)
    assert g.status == "NO_PERSON"


def test_video_too_short():
    v = _toy_video(T=5)
    g = check_pre_decode(v)
    assert g.status == "VIDEO_TOO_SHORT"


def test_insufficient_confidence_below_threshold():
    v = _toy_video(frac_valid=0.3)
    g = check_pre_decode(v)
    assert g.status == "INSUFFICIENT_CONFIDENCE"


def test_ok_above_threshold():
    v = _toy_video(frac_valid=0.9)
    g = check_pre_decode(v)
    assert g.ok


def test_lost_track_flagged():
    v = _toy_video(frac_valid=1.0)
    g = check_pre_decode(v, tracker_log=dict(lost=60, disagree=0))  # 60/90 = 0.67 > 0.5
    assert g.status == "LOST_TRACK"


def test_ambiguous_subject_flagged():
    v = _toy_video(frac_valid=1.0)
    g = check_pre_decode(v, tracker_log=dict(lost=0, disagree=40))  # 40/90 = 0.44 > 0.3
    assert g.status == "AMBIGUOUS_SUBJECT"


def test_tracker_log_within_bounds_is_ok():
    v = _toy_video(frac_valid=1.0)
    g = check_pre_decode(v, tracker_log=dict(lost=5, disagree=3))   # well under both thresholds
    assert g.ok


# ----------------------------------------------------------------------------- exercise plausibility
def test_empty_reps_is_ok_not_unsupported():
    assert check_exercise_plausibility([]).ok


def test_mostly_implausible_reps_flagged_unsupported():
    reps = [dict(peak_z=5.0), dict(peak_z=-6.0), dict(peak_z=0.1)]
    g = check_exercise_plausibility(reps, peak_z_thr=4.0, frac_implausible_thr=0.5)
    assert g.status == "UNSUPPORTED_EXERCISE"


def test_mostly_plausible_reps_is_ok():
    reps = [dict(peak_z=0.1), dict(peak_z=-0.2), dict(peak_z=5.0)]
    g = check_exercise_plausibility(reps, peak_z_thr=4.0, frac_implausible_thr=0.5)
    assert g.ok


def test_messages_do_not_claim_injury_or_diagnosis():
    for g in (check_pre_decode(_toy_video(frac_valid=0.0)),
             check_exercise_plausibility([dict(peak_z=10.0)])):
        text = g.reason.lower()
        for banned in ("injury", "incorrect", "wrong", "dangerous", "diagnos"):
            assert banned not in text


# ----------------------------------------------------------------------------- real-data integration
@pytest.fixture(scope="module")
def _real_system():
    """The committed artifact (decode byte-identical to the refit, P5.1); no Mendeley caches needed."""
    from ai.core.model_artifact import load_artifact
    return load_artifact()


@needs_sr_caches
@needs_ext_caches
@needs_mendeley_caches
def test_no_real_video_ever_abstains(_real_system):
    """False-abstention check: run safe_predict_video on EVERY real video across all three
    suites this project has (internal_sr, mendeley_val, external). None may abstain — the
    thresholds were chosen conservatively above every documented worst real case (42% lost
    frames, 16% NaN window). A failure here means a threshold is too aggressive."""
    from ai.evaluation import harness as H
    system = _real_system
    offenders = []
    for name, it in H.load_internal_sr().items():
        v = prepare_video(name, it["pose"], it["fps"], conf=it["conf"])
        r = safe_predict_video(v, system.model, system.dec, system.cfg)
        if r["status"] != "OK":
            offenders.append((name, r["status"]))
    for name, it in H.load_external().items():
        v = prepare_video(name, it["pose"], it["fps"], conf=it["conf"])
        r = safe_predict_video(v, system.model, system.dec, system.cfg)
        if r["status"] != "OK":
            offenders.append((name, r["status"]))
    for v in H.mendeley_videos().values():
        if v["split"] != "val" or v["exercise"] != "back":
            continue
        r = safe_predict_video(v, system.model, system.dec, system.cfg)
        if r["status"] != "OK":
            offenders.append((v["name"], r["status"]))
    assert offenders == [], f"real videos incorrectly abstained: {offenders}"


@needs_sr_caches
def test_safe_predict_matches_predict_video_exactly_when_ok(_real_system):
    from ai.evaluation import harness as H
    system = _real_system
    for name, it in H.load_internal_sr().items():
        v = prepare_video(name, it["pose"], it["fps"], conf=it["conf"])
        safe = safe_predict_video(v, system.model, system.dec, system.cfg)
        assert safe["status"] == "OK"
        ref = predict_video(v, system.model, system.dec, system.cfg, None)
        assert safe["reps"] == ref["reps"]


def test_synthetic_garbage_video_abstains_end_to_end(_real_system):
    system = _real_system
    v = _toy_video(T=3)   # far too short for any trained exercise
    r = safe_predict_video(v, system.model, system.dec, system.cfg)
    assert r["status"] == "VIDEO_TOO_SHORT"
    assert r["reps"] == []
