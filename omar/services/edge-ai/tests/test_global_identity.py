from edge_ai.fusion.global_identity import GlobalIdentityRegistry, LocalObservation

E1 = [1.0, 0.0, 0.0, 0.0]
E1_SIMILAR = [0.97, 0.2, 0.05, 0.0]
E2 = [0.0, 1.0, 0.0, 0.0]


def test_same_person_on_two_cameras_shares_global_id():
    r = GlobalIdentityRegistry()
    g1, new1 = r.observe(LocalObservation("camA", 1, 10.0, embedding=E1))
    g2, new2 = r.observe(LocalObservation("camB", 7, 10.5, embedding=E1_SIMILAR))
    assert new1 and not new2
    assert g1.global_id == g2.global_id
    assert len(g1.local_tracks) == 2


def test_different_appearance_creates_new_identity():
    r = GlobalIdentityRegistry()
    g1, _ = r.observe(LocalObservation("camA", 1, 10.0, embedding=E1))
    g2, new = r.observe(LocalObservation("camB", 7, 10.5, embedding=E2))
    assert new and g1.global_id != g2.global_id


def test_ambiguous_match_refuses_merge():
    r = GlobalIdentityRegistry(appearance_threshold=0.5, ambiguity_margin=0.1)
    r.observe(LocalObservation("camA", 1, 10.0, embedding=[1.0, 0.0, 0.0, 0.0]))
    r.observe(
        LocalObservation("camA", 2, 10.0, embedding=[0.95, 0.31, 0.0, 0.0])
    )  # similar-looking twin on same camera
    g, new = r.observe(LocalObservation("camB", 9, 10.4, embedding=[0.99, 0.14, 0.0, 0.0]))
    assert new and g.ambiguous
    assert r.merges_refused == 1


def test_same_camera_cannot_hold_two_tracks_for_one_identity():
    r = GlobalIdentityRegistry()
    g1, _ = r.observe(LocalObservation("camA", 1, 10.0, embedding=E1))
    g2, new = r.observe(LocalObservation("camA", 2, 10.1, embedding=E1))  # identical look, same camera, same time
    assert new and g1.global_id != g2.global_id


def test_no_evidence_never_associates():
    r = GlobalIdentityRegistry()
    r.observe(LocalObservation("camA", 1, 10.0))
    _, new = r.observe(LocalObservation("camB", 2, 10.1))
    assert new


def test_member_binding_threshold_and_conflict():
    r = GlobalIdentityRegistry()
    g, _ = r.observe(LocalObservation("camA", 1, 10.0, embedding=E1))
    assert g.bind_member("m1", 0.5) is False  # below IDENTITY_BIND_THRESHOLD
    assert g.bind_member("m1", 0.9) is True
    assert g.bind_member("m2", 0.95) is False  # second member on same body → refuse + flag
    assert g.ambiguous and g.member_id == "m1"


def test_reassociation_window_and_expiry():
    r = GlobalIdentityRegistry(reassociate_seconds=30)
    g1, _ = r.observe(LocalObservation("camA", 1, 10.0, embedding=E1))
    r.end_local_track("camA", 1)
    g2, new = r.observe(LocalObservation("camB", 5, 25.0, embedding=E1))  # 15 s later → same person
    assert not new and g2.global_id == g1.global_id
    r.end_local_track("camB", 5)
    gone = r.expire(now=100.0)
    assert [g.global_id for g in gone] == [g1.global_id]
    _, new = r.observe(LocalObservation("camC", 1, 101.0, embedding=E1))
    assert new
