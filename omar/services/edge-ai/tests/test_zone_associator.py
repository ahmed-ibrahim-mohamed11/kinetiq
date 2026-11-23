from edge_ai.fusion.equipment_association import ZoneAssociator, ZoneSpec

Z = ZoneSpec("z1", "camA", "eq1", "leg-press", ((0, 0), (100, 0), (100, 100), (0, 100)))


def test_walkthrough_does_not_start_usage():
    a = ZoneAssociator([Z], enter_dwell_s=2.0, exit_grace_s=5.0)
    evs = []
    for i in range(10):  # 1 s inside
        evs += a.observe("g1", "camA", (50, 50), i * 0.1, 0.9)
    for i in range(10, 80):
        evs += a.observe("g1", "camA", (500, 500), i * 0.1, 0.9)
    assert evs == []


def test_dwell_starts_usage_and_leaving_ends_it_after_grace():
    a = ZoneAssociator([Z], enter_dwell_s=2.0, exit_grace_s=5.0)
    evs = []
    t = 0.0
    while t < 3.0:
        evs += a.observe("g1", "camA", (50, 50), t, 0.9)
        t += 0.1
    assert [e.kind for e in evs] == ["usage_started"]
    assert evs[0].t == 0.0  # usage starts at first entry, not when dwell confirmed
    assert evs[0].confidence > 0.8
    # brief occlusion/jitter outside for 2 s → no end
    while t < 5.0:
        evs += a.observe("g1", "camA", (500, 500), t, 0.9)
        t += 0.1
    assert len(evs) == 1
    while t < 7.0:
        evs += a.observe("g1", "camA", (50, 50), t, 0.9)
        t += 0.1
    assert len(evs) == 1
    # leave for good
    while t < 13.0:
        evs += a.observe("g1", "camA", (500, 500), t, 0.9)
        t += 0.1
    assert [e.kind for e in evs] == ["usage_started", "usage_ended"]
    assert evs[1].reason == "left_zone"


def test_contested_zone_lowers_confidence():
    a = ZoneAssociator([Z], enter_dwell_s=1.0)
    t = 0.0
    e1 = e2 = []
    while t < 1.5:
        e1 = e1 + a.observe("g1", "camA", (30, 50), t, 0.9)
        e2 = e2 + a.observe("g2", "camA", (70, 50), t, 0.9)
        t += 0.1
    assert e1[0].kind == "usage_started" and e2[0].kind == "usage_started"
    assert e2[0].confidence < e1[0].confidence  # g2 started while g1 already active → contested


def test_track_lost_ends_active_usage():
    a = ZoneAssociator([Z], enter_dwell_s=1.0)
    t = 0.0
    while t < 1.5:
        a.observe("g1", "camA", (50, 50), t, 0.9)
        t += 0.1
    evs = a.track_lost("g1", 5.0)
    assert len(evs) == 1 and evs[0].kind == "usage_ended" and evs[0].reason == "track_lost"
    assert a.active_zone("g1") is None
