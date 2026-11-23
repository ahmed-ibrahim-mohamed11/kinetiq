import os
import sqlite3
from pathlib import Path

import pytest

from edge_ai.identity.consent_gate import ConsentGate
from edge_ai.identity.face_store import EncryptedTemplateStore, FaceMatcher


def test_templates_are_encrypted_at_rest(tmp_path: Path):
    key = os.urandom(32)
    store = EncryptedTemplateStore(tmp_path / "faces.sqlite", key)
    ref = store.enroll("m1", "consent-1", [0.1, 0.2, 0.3], "test-model")
    raw = sqlite3.connect(str(tmp_path / "faces.sqlite")).execute("select ciphertext from templates").fetchone()[0]
    assert b"0.1" not in raw and b"0.2" not in raw
    assert ref.startswith("tpl-")
    assert store.all_templates()[0][2] == [0.1, 0.2, 0.3]
    # wrong key cannot decrypt: the row is skipped and counted, never returned (and never crashes the caller)
    other = EncryptedTemplateStore(tmp_path / "faces.sqlite", os.urandom(32))
    assert other.all_templates() == []
    assert other.undecryptable == 1
    # same key id but tampered ciphertext → InvalidTag, also skipped
    forged = EncryptedTemplateStore(tmp_path / "faces.sqlite", os.urandom(32), key_id=store.key_id)
    assert forged.all_templates() == [] and forged.undecryptable == 1


def test_enrollment_requires_consent(tmp_path: Path):
    store = EncryptedTemplateStore(tmp_path / "f.sqlite", os.urandom(32))
    with pytest.raises(PermissionError):
        store.enroll("m1", "", [1.0], "m")


def test_revoke_deletes_ciphertext(tmp_path: Path):
    store = EncryptedTemplateStore(tmp_path / "f.sqlite", os.urandom(32))
    store.enroll("m1", "c1", [1.0, 0.0], "m")
    store.enroll("m2", "c2", [0.0, 1.0], "m")
    assert store.revoke_member("m1") == 1
    assert store.count() == 1


def test_matcher_thresholds_and_ambiguity(tmp_path: Path):
    store = EncryptedTemplateStore(tmp_path / "f.sqlite", os.urandom(32))
    store.enroll("alice", "c1", [1.0, 0.0, 0.0], "m")
    store.enroll("bob", "c2", [0.0, 1.0, 0.0], "m")
    m = FaceMatcher(store, match_threshold=0.6, ambiguity_margin=0.1)
    assert m.match([0.98, 0.1, 0.0]).member_id == "alice"
    assert m.match([0.0, 0.0, 1.0]).member_id is None  # stranger
    amb = m.match([0.72, 0.69, 0.0])  # between alice and bob
    assert amb.member_id is None and amb.ambiguous is True


def test_consent_gate_fails_closed_when_stale(tmp_path: Path):
    g = ConsentGate(tmp_path / "consent.json", max_age_seconds=3600)
    assert g.allows_face("m1") is None  # nothing loaded → deny
    g.replace({"m1": {"biometric_face_identification": "c1", "cctv_workout_tracking": "c2"}})
    assert g.allows_face("m1") == "c1"
    assert g.allows_tracking("m1") == "c2"
    assert g.allows_face("m2") is None
    stale = ConsentGate(tmp_path / "consent.json", max_age_seconds=0)
    stale.load()
    assert stale.allows_face("m1") is None
