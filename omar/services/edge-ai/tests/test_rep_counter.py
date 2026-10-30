import math

from edge_ai.reps.profiles import RepProfile
from edge_ai.reps.state_machine import RepCounter, RepState

LEG_PRESS = RepProfile.from_catalog(
    {
        "version": 1,
        "signal": "knee_angle_mean",
        "top_threshold": 160,
        "bottom_threshold": 95,
        "min_rep_seconds": 1.0,
        "max_rep_seconds": 8,
        "start_state": "top",
    }
)


def synthetic(
    counter: RepCounter,
    reps: int,
    period: float,
    fs: float = 10.0,
    noise: float = 0.0,
    conf: float = 0.9,
    lo: float = 85,
    hi: float = 168,
) -> int:
    """Feed a cosine between hi (extended) and lo (flexed); returns reps counted."""
    import random

    rng = random.Random(1)  # noqa: S311
    t = 0.0
    counted = 0
    # start with 1 s of rest at the top so the machine enters START
    while t < 1.0:
        counter.update(hi, conf, t)
        t += 1 / fs
    total = reps * period
    start = t
    while t - start < total + 0.5:
        phase = (t - start) / period
        mid, amp = (hi + lo) / 2, (hi - lo) / 2
        v = mid + amp * math.cos(2 * math.pi * phase) + rng.uniform(-noise, noise)
        if counter.update(v, conf, t):
            counted += 1
        t += 1 / fs
    return counted


def test_counts_clean_reps_exactly():
    c = RepCounter(profile=LEG_PRESS)
    assert synthetic(c, reps=10, period=2.5) == 10
    assert c.rejected_cycles == 0


def test_noise_near_threshold_does_not_double_count():
    c = RepCounter(profile=LEG_PRESS)
    assert synthetic(c, reps=8, period=3.0, noise=6.0) == 8


def test_too_fast_cycles_are_rejected():
    c = RepCounter(profile=LEG_PRESS)
    # 0.4 s per "rep" is faster than any human leg press → noise. Either each cycle completes
    # too fast (rejected by the plausibility window) or, after smoothing, the jitter never
    # settles into a band and the stuck cycle is abandoned after max_rep_seconds. Both paths
    # must count ZERO reps and record the rejection.
    counted = synthetic(c, reps=25, period=0.4, fs=50.0)
    assert counted == 0
    assert c.count == 0
    assert c.rejected_cycles > 0


def test_partial_range_of_motion_is_not_a_rep():
    c = RepCounter(profile=LEG_PRESS)
    t = 0.0
    for _ in range(10):
        c.update(168, 0.9, t)
        t += 0.1
    # dip only to 120° (never below bottom_threshold 95) and come back
    for v in [150, 130, 120, 125, 140, 155, 165, 168]:
        assert c.update(v, 0.9, t) is None
        t += 0.3
    assert c.count == 0
    assert c.rejected_cycles == 1
    assert c.state == RepState.START


def test_invisible_joints_freeze_then_reset():
    c = RepCounter(profile=LEG_PRESS)
    t = 0.0
    for _ in range(10):
        c.update(168, 0.9, t)
        t += 0.1
    c.update(120, 0.9, t)  # moving out
    assert c.state == RepState.MOVING_OUT
    # joints lost for longer than max_rep_seconds → cycle abandoned, state unknown
    for _ in range(100):
        t += 0.1
        c.update(math.nan, 0.0, t)
    assert c.state == RepState.UNKNOWN
    assert c.count == 0
    assert c.rejected_cycles == 1


def test_half_observed_first_rep_is_not_counted():
    """Camera starts watching mid-rep (person at the bottom): wait for a full cycle from START."""
    c = RepCounter(profile=LEG_PRESS)
    t = 0.0
    for v in [90, 90, 110, 140, 165, 168]:  # returning to top = half a rep
        c.update(v, 0.9, t)
        t += 0.3
    assert c.count == 0
    assert c.state == RepState.START
    # now a full rep counts
    for v in [150, 120, 90, 88, 120, 150, 168]:
        c.update(v, 0.9, t)
        t += 0.3
    assert c.count == 1


def test_occupancy_only_profile_never_counts():
    p = RepProfile.from_catalog({"version": 1, "signal": "none", "count_reps": False, "occupancy_only": True})
    c = RepCounter(profile=p)
    assert synthetic(c, reps=5, period=2.0) == 0


def test_rep_confidence_reflects_joint_visibility():
    c = RepCounter(profile=LEG_PRESS)
    high = synthetic(c, reps=3, period=2.5, conf=0.95)
    c2 = RepCounter(profile=LEG_PRESS)
    t = 0.0
    events = []
    for _ in range(10):
        c2.update(168, 0.4, t)
        t += 0.1
    for v in [150, 120, 90, 88, 120, 150, 168]:
        ev = c2.update(v, 0.4, t)
        if ev:
            events.append(ev)
        t += 0.3
    assert high == 3
    assert len(events) == 1
    assert events[0].confidence <= 0.4
