from edge_ai.reps.set_segmenter import SetSegmenter
from edge_ai.reps.state_machine import RepEvent


def rep(i: int, t: float, conf: float = 0.9) -> RepEvent:
    return RepEvent(i, t - 2.0, t, 2.0, conf, 90.0)


def test_two_sets_with_rest():
    seg = SetSegmenter(activity_local_id="a-1", idle_seconds=4.0, min_rest_seconds=4.0)
    kinds = []
    t = 10.0
    for i in range(1, 9):
        for ev in seg.on_rep(rep(i, t)):
            kinds.append(ev.kind)
        t += 2.5
    assert kinds[0] == "set_started"
    assert kinds.count("rep") == 8
    # idle → set closes once 4 s have passed; its end time is the LAST REP's completion (27.5)
    last_rep_completed = t - 2.5
    assert seg.tick(last_rep_completed + 3.9) is None
    closed = seg.tick(last_rep_completed + 4.0)
    assert closed is not None and closed.kind == "set_completed"
    assert closed.t == last_rep_completed
    assert closed.rep_count == 8
    assert closed.verified is True
    assert closed.confidence == 0.9
    # second set: first rep STARTS 20 s after the previous set's last rep completed
    first_rep_start = closed.t + 20.0
    evs = seg.on_rep(rep(1, first_rep_start + 2.0))  # rep() builds started_at = t - 2.0
    assert evs[0].kind == "set_started"
    assert evs[0].set_index == 2
    assert evs[0].rest_before_seconds == 20.0
    assert evs[0].set_local_id == "a-1:s2"


def test_short_pause_does_not_split_set():
    seg = SetSegmenter(activity_local_id="a-2", idle_seconds=4.0)
    seg.on_rep(rep(1, 10.0))
    seg.on_rep(rep(2, 12.5))
    assert seg.tick(15.0) is None  # only 2.5 s idle
    seg.on_rep(rep(3, 15.5))
    assert seg.set_index == 1


def test_forced_close_marks_unverified():
    seg = SetSegmenter(activity_local_id="a-3")
    seg.on_rep(rep(1, 10.0))
    seg.on_rep(rep(2, 12.0))
    closed = seg.close(13.0)
    assert closed is not None
    assert closed.verified is False
    assert closed.rep_count == 2


def test_low_tracking_confidence_marks_set_unverified():
    seg = SetSegmenter(activity_local_id="a-4", idle_seconds=4.0)
    seg.on_rep(rep(1, 10.0))
    seg.tracking_verified = False
    seg.on_rep(rep(2, 12.0))
    seg.tracking_verified = True
    seg.on_rep(rep(3, 14.0))
    closed = seg.tick(30.0)
    assert closed is not None and closed.verified is False and closed.rep_count == 3
