"""RLS isolation tests (Phase 3). Run against a real Supabase Postgres:

    set SUPABASE_DB_URL=postgresql://postgres:...@db.<project>.supabase.co:5432/postgres
    .venv\\Scripts\\python.exe -m pytest supabase/tests -q

Skipped automatically when SUPABASE_DB_URL is not set (no local database in CI yet).
Requires the migrations AND seed.sql to be applied first.

Technique: each test opens a transaction, switches to the `authenticated` role and injects
a JWT claim (`request.jwt.claims`) for a fake user attached to org 1 or org 2 via profiles,
then asserts what is visible. Everything rolls back.
"""
import os
import uuid

import pytest

psycopg = pytest.importorskip("psycopg")

DB_URL = os.environ.get("SUPABASE_DB_URL")
pytestmark = pytest.mark.skipif(not DB_URL, reason="SUPABASE_DB_URL not set")

ORG1 = "11111111-1111-1111-1111-111111111111"
ORG2 = "22222222-2222-2222-2222-222222222222"


@pytest.fixture()
def conn():
    with psycopg.connect(DB_URL) as c:
        yield c
        c.rollback()


def _as_user(cur, user_id: str, org: str, role: str = "coach"):
    """Create an auth user + profile inside the open transaction, then assume their identity."""
    cur.execute("set local role postgres")
    cur.execute(
        "insert into auth.users (id, email) values (%s, %s) on conflict (id) do nothing",
        (user_id, f"{user_id[:8]}@test.local"))
    cur.execute(
        "insert into profiles (user_id, organization_id, display_name, role) values (%s, %s, 'T', %s) "
        "on conflict (user_id) do update set organization_id = excluded.organization_id",
        (user_id, org, role))
    cur.execute("set local role authenticated")
    cur.execute("select set_config('request.jwt.claims', %s, true)",
                ('{"sub": "%s", "role": "authenticated"}' % user_id,))


def test_user_sees_only_own_organization(conn):
    cur = conn.cursor()
    uid = str(uuid.uuid4())
    _as_user(cur, uid, ORG1)
    cur.execute("select id from organizations")
    orgs = {str(r[0]) for r in cur.fetchall()}
    assert orgs == {ORG1}


def test_sessions_and_reps_isolated_by_org(conn):
    cur = conn.cursor()
    uid = str(uuid.uuid4())
    _as_user(cur, uid, ORG1)
    cur.execute("select count(*) from sessions")
    assert cur.fetchone()[0] == 1               # only the org-1 seeded session
    cur.execute("select count(*) from reps")
    assert cur.fetchone()[0] == 2               # only org-1 reps
    # switch to org 2 inside a fresh identity: sees the OTHER rows, never org 1's
    uid2 = str(uuid.uuid4())
    _as_user(cur, uid2, ORG2)
    cur.execute("select distinct session_id from reps")
    sids = {str(r[0]) for r in cur.fetchall()}
    assert sids == {"72222222-2222-2222-2222-222222222222"}


def test_anonymous_sees_nothing(conn):
    cur = conn.cursor()
    cur.execute("set local role anon")
    for table in ("organizations", "gyms", "sessions", "reps", "alerts", "devices"):
        cur.execute(f"select count(*) from {table}")
        assert cur.fetchone()[0] == 0, table


def test_coach_cannot_write_configuration(conn):
    cur = conn.cursor()
    uid = str(uuid.uuid4())
    _as_user(cur, uid, ORG1, role="coach")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        cur.execute("insert into gyms (organization_id, name) values (%s, 'rogue gym')", (ORG1,))
    conn.rollback()


def test_admin_can_write_configuration_in_own_org_only(conn):
    cur = conn.cursor()
    uid = str(uuid.uuid4())
    _as_user(cur, uid, ORG1, role="admin")
    cur.execute("insert into gyms (organization_id, name) values (%s, 'annex') returning id", (ORG1,))
    assert cur.fetchone() is not None
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        cur.execute("insert into gyms (organization_id, name) values (%s, 'cross-org!') ", (ORG2,))
    conn.rollback()


def test_coach_can_review_alert(conn):
    cur = conn.cursor()
    uid = str(uuid.uuid4())
    _as_user(cur, uid, ORG1, role="coach")
    cur.execute("update alerts set status = 'reviewed', reviewed_at = now() "
                "where status = 'open' returning id")
    assert cur.fetchone() is not None
