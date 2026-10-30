from edge_ai.fusion.rep_dedup import RepDeduplicator


def test_same_rep_from_two_cameras_counted_once():
    d = RepDeduplicator(window_ms=700)
    assert d.observe("g1", "eq1", "camA", 10.00, 0.9) is True
    assert d.observe("g1", "eq1", "camB", 10.12, 0.8) is False
    assert d.admitted == 1 and d.suppressed == 1


def test_distinct_reps_outside_window_both_count():
    d = RepDeduplicator(window_ms=700)
    assert d.observe("g1", "eq1", "camA", 10.0, 0.9)
    assert d.observe("g1", "eq1", "camA", 12.5, 0.9)
    assert d.admitted == 2


def test_different_identities_or_equipment_are_independent():
    d = RepDeduplicator(window_ms=700)
    assert d.observe("g1", "eq1", "camA", 10.0, 0.9)
    assert d.observe("g2", "eq1", "camA", 10.1, 0.9)
    assert d.observe("g1", "eq2", "camB", 10.2, 0.9)
    assert d.suppressed == 0


def test_primary_camera_fails_over_when_silent():
    d = RepDeduplicator(window_ms=700, primary_timeout_s=3.0)
    d.observe("g1", "eq1", "camA", 10.0, 0.9)
    d.observe("g1", "eq1", "camB", 10.1, 0.8)
    assert d.primary_camera("g1", "eq1") == "camA"
    # camA goes dark; camB keeps reporting
    d.observe("g1", "eq1", "camB", 14.0, 0.8)
    assert d.primary_camera("g1", "eq1") == "camB"


def test_forget_clears_state():
    d = RepDeduplicator()
    d.observe("g1", "eq1", "camA", 10.0, 0.9)
    d.forget("g1")
    assert d.primary_camera("g1", "eq1") is None
    assert d.observe("g1", "eq1", "camB", 10.1, 0.9) is True
