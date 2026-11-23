"""ai.augmentation.perturb: correctness of each synthetic transform, in isolation and via the
harness-compatible factories (checked against ai.evaluation.harness's real transform signature).

Also locks two system properties DISCOVERED by running these transforms against the real frozen
decoder (ai/core/config.py L_WRI/R_WRI note, 2026-10-09): (1) wrist confidence/coordinates have
zero effect on any decoder output (wrist is never read by ai.core.features) - a deliberate,
documented regression lock, not an oversight; (2) a SMALL rigid global yaw rotation of the 3D
pose is a decoder no-op, but a LARGE one (confirmed at 180 deg) is not, because
ai.core.features's backward-axis sign comes from a fixed external constant (FACING_IN_CAMERA),
not purely from the rotated pose - see test_small_yaw_rotation_is_a_decoder_noop_but_180_is_not
for the mechanism and why this means the transform is a bounded sanity check, NOT a general
view-augmentation that helps the known side/rear-view weakness (that weakness is in the 2D->3D
LIFTING stage, upstream of where this synthetic transform operates)."""
import numpy as np
import pytest

from ai.augmentation.perturb import (compose, confidence_noise, frame_drop, fps_downsample,
                                     inconsistent_tempo, insert_pauses, joint_dropout_conf,
                                     make_confidence_noise_transform,
                                     make_fps_downsample_transform,
                                     make_frame_drop_transform,
                                     make_inconsistent_tempo_transform,
                                     make_joint_dropout_transform,
                                     make_pause_insertion_transform,
                                     make_positional_jitter_transform,
                                     make_time_warp_transform, make_yaw_rotation_transform,
                                     positional_jitter, time_warp, yaw_rotate)
from ai.core.config import L_WRI, R_WRI
from ai.tests.conftest import needs_sr_caches


def _fake_video(T=90, seed=0):
    rng = np.random.default_rng(seed)
    pose = np.zeros((T, 17, 3), dtype=np.float32)
    # a plausible-ish moving skeleton: pelvis at origin, thorax above, shoulders lateral,
    # elbows oscillating in depth (toy "pull") so bone lengths are meaningful to check.
    pose[:, 8] = [0, -1.0, 0]                       # thorax
    pose[:, 11] = [-0.7, -1.0, 0]                   # L shoulder
    pose[:, 14] = [0.7, -1.0, 0]                    # R shoulder
    t = np.linspace(0, 4 * np.pi, T)
    pose[:, 12] = pose[:, 11] + np.stack([np.zeros(T), np.zeros(T), 0.3 * np.sin(t)], 1)  # L elbow
    pose[:, 15] = pose[:, 14] + np.stack([np.zeros(T), np.zeros(T), 0.3 * np.sin(t)], 1)  # R elbow
    pose += rng.normal(0, 1e-4, pose.shape)         # tiny noise so nothing is exactly degenerate
    conf = np.full((T, 17), 0.9, dtype=np.float32)
    return pose.astype(np.float32), conf


GT = [dict(start=10, pull=25, end=40), dict(start=50, pull=61, end=75)]


# ----------------------------------------------------------------------------- time_warp
@pytest.mark.parametrize("speed", [0.5, 0.7, 1.0, 1.5, 2.0])
def test_time_warp_resamples_and_preserves_endpoints(speed):
    pose, conf = _fake_video()
    p, c = time_warp(pose, conf, speed)
    assert len(p) == len(c)
    assert abs(len(p) - round(len(pose) / speed)) <= 1
    np.testing.assert_allclose(p[0], pose[0], atol=1e-5)
    np.testing.assert_allclose(p[-1], pose[-1], atol=1e-5)
    assert np.isfinite(p).all() and np.isfinite(c).all()


def test_time_warp_rejects_nonpositive_speed():
    pose, conf = _fake_video()
    with pytest.raises(ValueError):
        time_warp(pose, conf, 0.0)


def test_time_warp_transform_rescales_gt_monotonically_and_in_range():
    pose, conf = _fake_video(T=90)
    T = len(pose)
    tf = make_time_warp_transform(0.6)                 # slower -> more frames
    p, c, fps, gtc, gtu = tf("v", pose, conf, 30.0, GT, [])
    assert len(p) > T
    for r, orig in zip(gtc, GT):
        assert r["start"] < r["pull"] < r["end"]
        for k in ("start", "pull", "end"):
            assert 0 <= r[k] < len(p)
        # scaled up roughly by new_T/T
        assert r["end"] > orig["end"]


# ----------------------------------------------------------------------------- joint dropout (confidence, not NaN)
def test_joint_dropout_zeroes_only_named_joints_in_one_window():
    pose, conf = _fake_video(T=100)
    rng = np.random.default_rng(42)
    out = joint_dropout_conf(conf, [12, 15], 0.3, rng)   # L/R elbow
    changed_frames = np.where((out[:, 12] == 0) | (out[:, 15] == 0))[0]
    assert len(changed_frames) == pytest.approx(30, abs=1)
    # contiguous window
    assert changed_frames.max() - changed_frames.min() + 1 == len(changed_frames)
    # untouched joints unaffected
    assert np.array_equal(out[:, 0], conf[:, 0])
    # pose coordinates are NEVER touched by joint dropout (confidence-only, see module docstring)
    pass


def test_joint_dropout_transform_leaves_pose_and_gt_untouched():
    pose, conf = _fake_video()
    tf = make_joint_dropout_transform([13, 16], 0.2, seed=1)
    p, c, fps, gtc, gtu = tf("v", pose, conf, 30.0, GT, [])
    np.testing.assert_array_equal(p, pose)
    assert gtc == GT


# ----------------------------------------------------------------------------- confidence noise
def test_confidence_noise_bounded_and_seeded_reproducible():
    _, conf = _fake_video()
    rng1, rng2 = np.random.default_rng(7), np.random.default_rng(7)
    a = confidence_noise(conf, 0.3, rng1)
    b = confidence_noise(conf, 0.3, rng2)
    np.testing.assert_array_equal(a, b)
    assert (a >= 0).all() and (a <= 1).all()


# ----------------------------------------------------------------------------- frame drop
def test_frame_drop_interpolates_dropped_frames_only():
    pose, conf = _fake_video(T=120)
    rng = np.random.default_rng(3)
    p, c = frame_drop(pose, conf, 0.2, rng)
    n_zero = int((c == 0).all(axis=1).sum())
    assert n_zero == pytest.approx(24, abs=1)
    assert np.isfinite(p).all()
    # kept frames (conf still > 0 everywhere) are untouched
    kept = (c > 0).all(axis=1)
    np.testing.assert_allclose(p[kept], pose[kept])


def test_frame_drop_too_aggressive_raises():
    pose, conf = _fake_video(T=10)
    with pytest.raises(ValueError):
        frame_drop(pose, conf, 0.95, np.random.default_rng(0))


def test_frame_drop_transform_keeps_T_and_gt():
    pose, conf = _fake_video()
    tf = make_frame_drop_transform(0.1, seed=5)
    p, c, fps, gtc, gtu = tf("v", pose, conf, 30.0, GT, [])
    assert len(p) == len(pose)
    assert gtc == GT


# ----------------------------------------------------------------------------- pause insertion (targeted hold)
def test_insert_pauses_lengthens_by_exactly_n_per_rep_and_holds_the_frame():
    pose, conf = _fake_video(T=90)
    pause_s, fps = 0.5, 30.0
    n = round(pause_s * fps)
    p, c, gtc, gtu = insert_pauses(pose, conf, GT, [], fps, pause_s, at="pull")
    assert len(p) == len(pose) + n * len(GT)
    for r, orig in zip(gtc, GT):
        held = p[r["pull"]:r["pull"] + n]
        np.testing.assert_allclose(held, np.repeat(pose[orig["pull"]:orig["pull"] + 1], n, axis=0))
        assert r["start"] < r["pull"] < r["end"]


def test_insert_pauses_shifts_only_frames_after_each_insertion():
    pose, conf = _fake_video(T=90)
    p, c, gtc, gtu = insert_pauses(pose, conf, GT, [], 30.0, 0.3, at="pull")
    n = round(0.3 * 30.0)
    # first rep's start is BEFORE its own pull -> unshifted; its end is AFTER -> shifted by n;
    # second rep is entirely after the first insertion -> shifted by n, then by its own too for 'end'
    assert gtc[0]["start"] == GT[0]["start"]
    assert gtc[0]["pull"] == GT[0]["pull"]
    assert gtc[0]["end"] == GT[0]["end"] + n
    assert gtc[1]["start"] == GT[1]["start"] + n
    assert gtc[1]["pull"] == GT[1]["pull"] + n
    assert gtc[1]["end"] == GT[1]["end"] + 2 * n


def test_insert_pauses_zero_or_empty_is_noop():
    pose, conf = _fake_video()
    p, c, gtc, gtu = insert_pauses(pose, conf, GT, [], 30.0, 0.0)
    np.testing.assert_array_equal(p, pose)
    assert gtc == GT
    p2, c2, gtc2, gtu2 = insert_pauses(pose, conf, [], [], 30.0, 0.5)
    np.testing.assert_array_equal(p2, pose)


def test_pause_insertion_transform_matches_harness_signature_and_preserves_ordering():
    pose, conf = _fake_video(T=90)
    tf = make_pause_insertion_transform(0.4, at="pull")
    p, c, fps, gtc, gtu = tf("v", pose, conf, 30.0, GT, [])
    assert fps == 30.0
    assert len(p) > len(pose)
    for r in gtc:
        assert r["start"] < r["pull"] < r["end"]
        assert 0 <= r["end"] < len(p)


# ----------------------------------------------------------------------------- inconsistent tempo
def test_inconsistent_tempo_endpoints_and_ordering():
    pose, conf = _fake_video(T=200)
    p, c, src = inconsistent_tempo(pose, conf, fps=30.0, speed_lo=0.7, speed_hi=1.3, cycle_s=2.0)
    assert len(p) == len(c) == len(src)
    assert src[0] == 0.0
    assert src[-1] == pytest.approx(len(pose) - 1, abs=1e-6)
    assert np.all(np.diff(src) > 0)                 # strictly increasing: never goes backward
    assert np.all((src >= 0) & (src <= len(pose) - 1))


def test_inconsistent_tempo_transform_keeps_gt_ordered_and_in_range():
    pose, conf = _fake_video(T=200)
    tf = make_inconsistent_tempo_transform(0.6, 1.4, cycle_s=1.5)
    p, c, fps, gtc, gtu = tf("v", pose, conf, 30.0, GT, [])
    assert fps == 30.0
    for r in gtc:
        assert r["start"] <= r["pull"] <= r["end"]
        assert 0 <= r["end"] < len(p)


# ----------------------------------------------------------------------------- fps downsample
def test_fps_downsample_takes_every_nth_and_scales_fps():
    pose, conf = _fake_video(T=90)
    p, c, fps = fps_downsample(pose, conf, 30.0, 2)
    np.testing.assert_array_equal(p, pose[::2])
    assert fps == 15.0


def test_fps_downsample_rejects_bad_step():
    pose, conf = _fake_video()
    with pytest.raises(ValueError):
        fps_downsample(pose, conf, 30.0, 0)


def test_fps_downsample_transform_rescales_gt_in_range():
    pose, conf = _fake_video(T=90)
    tf = make_fps_downsample_transform(3)
    p, c, fps, gtc, gtu = tf("v", pose, conf, 30.0, GT, [])
    assert fps == 10.0
    for r in gtc:
        for k in ("start", "pull", "end"):
            assert 0 <= r[k] < len(p)
        assert r["start"] < r["pull"] < r["end"]


# ----------------------------------------------------------------------------- positional jitter (resolution proxy)
def test_positional_jitter_zero_mean_scaled_by_torso_and_zero_sigma_is_noop():
    pose, conf = _fake_video()
    out0 = positional_jitter(pose, 0.0, np.random.default_rng(0))
    np.testing.assert_allclose(out0, pose)
    out = positional_jitter(pose, 0.05, np.random.default_rng(0))
    assert not np.allclose(out, pose)
    assert np.abs(out - pose).mean() < 0.2    # sane magnitude relative to ~1.0 torso length


# ----------------------------------------------------------------------------- yaw rotation (viewpoint)
def test_yaw_rotate_preserves_bone_lengths_and_vertical_axis():
    pose, _ = _fake_video()
    rotated = yaw_rotate(pose, 37.0)
    bone = pose[:, 15] - pose[:, 14]                # R shoulder -> R elbow
    bone_r = rotated[:, 15] - rotated[:, 14]
    np.testing.assert_allclose(np.linalg.norm(bone, axis=-1), np.linalg.norm(bone_r, axis=-1), atol=1e-5)
    # vertical (axis 1) component of every joint is untouched by a yaw rotation
    np.testing.assert_allclose(rotated[..., 1], pose[..., 1], atol=1e-5)


def test_yaw_rotate_0_is_identity_and_360_is_identity():
    pose, _ = _fake_video()
    np.testing.assert_allclose(yaw_rotate(pose, 0.0), pose, atol=1e-5)
    np.testing.assert_allclose(yaw_rotate(pose, 360.0), pose, atol=1e-4)


def test_yaw_rotate_composes():
    pose, _ = _fake_video()
    a = yaw_rotate(yaw_rotate(pose, 20.0), 30.0)
    b = yaw_rotate(pose, 50.0)
    np.testing.assert_allclose(a, b, atol=1e-4)


def test_yaw_rotation_transform_leaves_conf_and_gt_untouched():
    pose, conf = _fake_video()
    tf = make_yaw_rotation_transform(45.0)
    p, c, fps, gtc, gtu = tf("v", pose, conf, 30.0, GT, [])
    np.testing.assert_array_equal(c, conf)
    assert gtc == GT
    assert not np.allclose(p, pose)


# ----------------------------------------------------------------------------- compose
def test_compose_chains_in_order_and_matches_harness_signature():
    pose, conf = _fake_video(T=90)
    tf = compose(make_time_warp_transform(0.7), make_confidence_noise_transform(0.1, seed=9),
                make_yaw_rotation_transform(15.0))
    p, c, fps, gtc, gtu = tf("v", pose, conf, 30.0, GT, [])
    assert len(p) == len(c)
    assert fps == 30.0
    assert (c >= 0).all() and (c <= 1).all()
    for r in gtc:
        assert r["start"] < r["pull"] < r["end"]


# ----------------------------------------------------------------------------- real-decoder integration
# These two need the git-ignored seated-row pose caches (they skip, with the rebuild command, on a
# fresh clone) and the committed artifact. Not "slow"-marked: they run every pytest invocation to
# guard the two documented system properties above (config.py's L_WRI/R_WRI note).
@pytest.fixture(scope="module")
def _real_system_and_items():
    """The committed artifact (decode byte-identical to the refit, P5.1) + the seated-row caches."""
    from ai.core.model_artifact import load_artifact
    from ai.evaluation import harness as H
    return load_artifact(), H.load_internal_sr()


@needs_sr_caches
def test_wrist_joint_is_not_used_by_the_decoder(_real_system_and_items):
    """Locks the property documented in ai/core/config.py: dropping ALL confidence for both
    wrists over the whole video must not change a single predicted rep, because wrist is never
    read by ai.core.features. If this ever fails, ai.core started using the wrist - update the
    config.py note deliberately, don't just relax this test."""
    from ai.evaluation import harness as H
    system, items = _real_system_and_items
    tf = make_joint_dropout_transform([L_WRI, R_WRI], 1.0)   # the whole video
    base = H.run_pose_suite(system, items)["pooled"]["iou"]
    dropped = H.run_pose_suite(system, items, transform=tf)["pooled"]["iou"]
    assert dropped == base


@needs_sr_caches
def test_small_yaw_rotation_is_a_decoder_noop_but_180_is_not(_real_system_and_items):
    """Locks the property found while building this module, corrected after this test caught my
    first (wrong) assumption that ANY yaw rotation is a no-op:

    ai.core.features.prepare_video derives `lat` (lateral) and `up` (vertical) FROM the same pose
    it analyses, so those two output coordinates ARE rotation-equivariant for any yaw. But `fwd`
    (the BACKWARD/pull axis - the one `pull`/`rom_proxy` actually read) gets its SIGN from a
    one-bit comparison against the FIXED external constant `FACING_IN_CAMERA = [0,0,1]`
    (config.py: "athlete faces away from a rear camera"), not from the pose alone. A small
    rotation doesn't cross that sign threshold, so it's a no-op; a rotation that crosses it
    (confirmed here at 180 deg, not at 30) flips which direction counts as "backward" relative to
    the rotated body, changing decoded reps (observed: n_pred 47 -> 32, precision 0.744 -> 0.967).
    This is the intended behaviour of a heuristic built for a FIXED assumed camera setup, not a
    bug - but it means yaw rotation is a faithful sanity/robustness probe only for rotations well
    inside that threshold, and is NOT a general-purpose "view augmentation" (see perturb.py's
    module docstring: the real side/rear-view weakness lives further upstream, in 2D->3D
    lifting, which this transform never touches)."""
    from ai.evaluation import harness as H
    system, items = _real_system_and_items
    base = H.run_pose_suite(system, items)["pooled"]["iou"]
    small = H.run_pose_suite(system, items, transform=make_yaw_rotation_transform(30.0))["pooled"]["iou"]
    assert small == base, "a small (30 deg) yaw rotation should still be a decoder no-op"
    flipped = H.run_pose_suite(system, items, transform=make_yaw_rotation_transform(180.0))["pooled"]["iou"]
    assert flipped != base, (
        "180 deg no longer flips the FACING_IN_CAMERA sign threshold - re-check the mechanism "
        "this test documents before relying on the corrected perturb.py docstring")
