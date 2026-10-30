/**
 * Row Level Security executed against the real policies on an embedded
 * PostgreSQL, emulating PostgREST (role switch + JWT claim GUCs).
 *
 * Two tenants, one staff admin and one member each. Proves cross-member and
 * cross-tenant isolation, write restrictions, anon exposure, and that the
 * secrets table and privileged RPCs are unreachable.
 */
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { startHarness, type Harness } from './harness';

interface Tenant {
  orgId: string;
  branchId: string;
  staffUid: string;
  memberUid: string;
  memberId: string;
  sessionId: string;
  cameraId: string;
}

let h: Harness;
let A: Tenant;
let B: Tenant;

async function makeTenant(tag: string): Promise<Tenant> {
  const org = await h.pool.query(`insert into public.organizations (name, slug) values ($1, $2) returning id`, [`Org ${tag}`, `org-${tag}`]);
  const orgId = org.rows[0].id;
  const br = await h.pool.query(`insert into public.branches (organization_id, name) values ($1, 'Main') returning id`, [orgId]);
  const branchId = br.rows[0].id;
  const staff = await h.pool.query(`insert into auth.users (email) values ($1) returning id`, [`staff-${tag}@example.test`]);
  const member = await h.pool.query(`insert into auth.users (email) values ($1) returning id`, [`member-${tag}@example.test`]);
  await h.pool.query(`insert into public.staff_roles (user_id, organization_id, branch_id, role) values ($1, $2, null, 'admin')`, [staff.rows[0].id, orgId]);
  const m = await h.pool.query(
    `insert into public.members (organization_id, branch_id, user_id, member_code, full_name) values ($1, $2, $3, $4, $5) returning id`,
    [orgId, branchId, member.rows[0].id, `RLS-${tag.toUpperCase()}-M2A7`, `Member ${tag}`],
  );
  const memberId = m.rows[0].id;
  await h.pool.query(`insert into public.consent_records (member_id, consent_type, policy_version) values ($1, 'cctv_workout_tracking', 'test'), ($1, 'data_processing', 'test')`, [memberId]);
  const s = await h.pool.query(
    `insert into public.attendance_sessions (member_id, branch_id, check_in_at, check_in_method, check_out_at, status) values ($1, $2, now() - interval '1 hour', 'qr', now(), 'completed') returning id`,
    [memberId, branchId],
  );
  await h.pool.query(`insert into public.workout_reports (attendance_session_id, member_id, summary) values ($1, $2, '{"schema_version":1}')`, [s.rows[0].id, memberId]);
  const dev = await h.pool.query(`insert into public.edge_devices (branch_id, name) values ($1, $2) returning id`, [branchId, `dev-${tag}`]);
  await h.pool.query(`insert into public.edge_device_secrets (edge_device_id, secret_hash, secret) values ($1, 'h', 's')`, [dev.rows[0].id]);
  const cam = await h.pool.query(`insert into public.cameras (branch_id, name, stream_url) values ($1, $2, 'rtsp://192.0.2.1/x') returning id`, [branchId, `cam-${tag}`]);
  return { orgId, branchId, staffUid: staff.rows[0].id, memberUid: member.rows[0].id, memberId, sessionId: s.rows[0].id, cameraId: cam.rows[0].id };
}

beforeAll(async () => {
  h = await startHarness({ seed: false });
  A = await makeTenant('a');
  B = await makeTenant('b');
});
afterAll(async () => {
  await h?.stop();
});

const ids = (rows: Array<Record<string, unknown>>, k = 'id') => rows.map((r) => r[k]).sort();

describe('members', () => {
  it('see only their own member row, sessions, reports and consents', async () => {
    await h.asUser(A.memberUid, async (c) => {
      expect(ids((await c.query('select id from public.members')).rows)).toEqual([A.memberId]);
      expect(ids((await c.query('select id from public.attendance_sessions')).rows)).toEqual([A.sessionId]);
      expect(ids((await c.query('select member_id as id from public.workout_reports')).rows)).toEqual([A.memberId]);
      const consents = (await c.query('select member_id from public.consent_records')).rows;
      expect(consents.length).toBe(2);
      expect(consents.every((r) => r.member_id === A.memberId)).toBe(true);
    });
  });

  it('cannot see cameras, devices, secrets, tracks or audit logs', async () => {
    await h.asUser(A.memberUid, async (c) => {
      expect((await c.query('select id from public.cameras')).rows).toEqual([]);
      expect((await c.query('select id from public.edge_devices')).rows).toEqual([]);
      expect((await c.query('select id from public.person_tracks')).rows).toEqual([]);
      expect((await c.query('select id from public.audit_logs')).rows).toEqual([]);
      await expect(c.query('select * from public.edge_device_secrets')).rejects.toThrow(/permission denied/);
    });
  });

  it('cannot write infrastructure or rename themselves', async () => {
    await expect(
      h.asUser(A.memberUid, (c) => c.query(`insert into public.cameras (branch_id, name, stream_url) values ($1, 'hack', 'rtsp://192.0.2.9/x')`, [A.branchId])),
    ).rejects.toThrow(/row-level security/);
    const upd = await h.asUser(A.memberUid, (c) => c.query(`update public.members set full_name = 'Renamed' where id = $1`, [A.memberId]));
    expect(upd.rowCount).toBe(0);
    const { rows } = await h.pool.query('select full_name from public.members where id = $1', [A.memberId]);
    expect(rows[0].full_name).toBe('Member a');
  });

  it('can withdraw their own consent but not re-grant or edit history', async () => {
    const withdrawn = await h.asUser(A.memberUid, (c) =>
      c.query(`update public.consent_records set status = 'withdrawn', withdrawn_at = now() where member_id = $1 and consent_type = 'data_processing'`, [A.memberId]),
    );
    expect(withdrawn.rowCount).toBe(1);
    await expect(
      h.asUser(A.memberUid, (c) => c.query(`update public.consent_records set status = 'granted', withdrawn_at = null where member_id = $1`, [A.memberId])),
    ).rejects.toThrow(/row-level security|check constraint/);
  });

  it('can check themselves out via RPC only for their own active session', async () => {
    await h.pool.query(`insert into public.attendance_sessions (member_id, branch_id, check_in_at, check_in_method) values ($1, $2, now(), 'qr')`, [A.memberId, A.branchId]);
    const r = await h.asUser(A.memberUid, (c) => c.query('select public.member_self_check_out() as report'));
    expect(r.rows[0].report).toBeTruthy();
    await expect(h.asUser(B.memberUid, (c) => c.query('select public.member_self_check_out()'))).rejects.toThrow(/no active session/);
  });
});

describe('staff', () => {
  it('of org A read members, cameras, sessions and devices of org A only', async () => {
    await h.asUser(A.staffUid, async (c) => {
      const members = (await c.query('select organization_id from public.members')).rows;
      expect(members.length).toBe(1);
      expect(members[0].organization_id).toBe(A.orgId);
      expect(ids((await c.query('select id from public.cameras')).rows)).toEqual([A.cameraId]);
      const sessions = (await c.query('select id, branch_id from public.attendance_sessions')).rows;
      expect(sessions.map((s) => s.id)).toContain(A.sessionId);
      expect(sessions.every((s) => s.branch_id === A.branchId)).toBe(true);
      expect(ids((await c.query('select id from public.organizations')).rows)).toEqual([A.orgId]);
      expect((await c.query('select id from public.edge_devices')).rows.length).toBe(1);
      await expect(c.query('select * from public.edge_device_secrets')).rejects.toThrow(/permission denied/);
    });
  });

  it('cannot insert into another org branch, but can into their own', async () => {
    await expect(
      h.asUser(A.staffUid, (c) => c.query(`insert into public.equipment (branch_id, name, category) values ($1, 'intruder', 'machine')`, [B.branchId])),
    ).rejects.toThrow(/row-level security/);
    const ok = await h.asUser(A.staffUid, (c) => c.query(`insert into public.equipment (branch_id, name, category) values ($1, 'Row #1', 'machine') returning id`, [A.branchId]));
    expect(ok.rowCount).toBe(1);
  });

  it('admin can check a member in and out through RPCs; staff of org B cannot', async () => {
    const sid = await h.asUser(A.staffUid, (c) => c.query(`select public.staff_check_in($1, 'staff_manual') as id`, [A.memberId]));
    expect(sid.rows[0].id).toBeTruthy();
    await expect(h.asUser(B.staffUid, (c) => c.query(`select public.staff_check_out($1, 'staff_manual')`, [sid.rows[0].id]))).rejects.toThrow(/not authorized/);
    const rep = await h.asUser(A.staffUid, (c) => c.query(`select public.staff_check_out($1, 'staff_manual') as r`, [sid.rows[0].id]));
    expect(rep.rows[0].r).toBeTruthy();
  });

  it('admin can rotate device secrets, but the raw secret table stays unreadable', async () => {
    const dev = await h.pool.query('select id from public.edge_devices where branch_id = $1', [A.branchId]);
    const sec = await h.asUser(A.staffUid, (c) => c.query('select public.issue_device_secret($1) as s', [dev.rows[0].id]));
    expect(sec.rows[0].s).toMatch(/^[A-Za-z0-9+/=]{40,}$/);
    await expect(h.asUser(B.staffUid, (c) => c.query('select public.issue_device_secret($1)', [dev.rows[0].id]))).rejects.toThrow(/not authorized/);
    const active = await h.pool.query('select count(*)::int as n from public.edge_device_secrets where edge_device_id = $1 and (retired_at is null or retired_at > now())', [dev.rows[0].id]);
    expect(active.rows[0].n).toBe(2); // old one valid for 24 h overlap
  });

  it('only owners can grant the owner role', async () => {
    const newUser = await h.pool.query(`insert into auth.users (email) values ('newowner@example.test') returning id`);
    await expect(
      h.asUser(A.staffUid, (c) => c.query(`insert into public.staff_roles (user_id, organization_id, role) values ($1, $2, 'owner')`, [newUser.rows[0].id, A.orgId])),
    ).rejects.toThrow(/row-level security/);
    const ok = await h.asUser(A.staffUid, (c) => c.query(`insert into public.staff_roles (user_id, organization_id, role) values ($1, $2, 'trainer')`, [newUser.rows[0].id, A.orgId]));
    expect(ok.rowCount).toBe(1);
  });
});

describe('anon and privileged RPCs', () => {
  it('anon reads only the exercise catalogue', async () => {
    // Each denied statement aborts its transaction, so every probe runs in its own anon transaction.
    await expect(h.asAnon((c) => c.query('select id from public.exercise_catalog'))).resolves.toBeTruthy();
    await expect(h.asAnon((c) => c.query('select id from public.members'))).rejects.toThrow(/permission denied/);
    await expect(h.asAnon((c) => c.query('select id from public.attendance_sessions'))).rejects.toThrow(/permission denied/);
    await expect(h.asAnon((c) => c.query('select id from public.workout_reports'))).rejects.toThrow(/permission denied/);
    await expect(h.asAnon((c) => c.query('select id from public.cameras'))).rejects.toThrow(/permission denied/);
  });

  it('shared report token resolves for anon and exposes no identifiers', async () => {
    const tok = await h.pool.query('select share_token from public.workout_reports where member_id = $1 limit 1', [A.memberId]);
    const r = await h.asAnon((c) => c.query('select public.get_shared_report($1) as r', [tok.rows[0].share_token]));
    expect(r.rows[0].r).toMatchObject({ member_name: 'Member a' });
    expect(JSON.stringify(r.rows[0].r)).not.toContain(A.memberId);
    const miss = await h.asAnon((c) => c.query(`select public.get_shared_report('nope') as r`));
    expect(miss.rows[0].r).toBeNull();
  });

  it('ingest and finalisation RPCs are not executable by authenticated users', async () => {
    await expect(h.asUser(A.staffUid, (c) => c.query(`select public.ingest_events($1, $1, now(), '[]'::jsonb)`, [A.orgId]))).rejects.toThrow(/permission denied/);
    await expect(h.asUser(A.memberUid, (c) => c.query(`select public.finalize_attendance_session($1)`, [A.sessionId]))).rejects.toThrow(/permission denied/);
    await expect(h.asUser(A.staffUid, (c) => c.query(`select * from public.get_device_secrets($1)`, [A.orgId]))).rejects.toThrow(/permission denied/);
  });
});
