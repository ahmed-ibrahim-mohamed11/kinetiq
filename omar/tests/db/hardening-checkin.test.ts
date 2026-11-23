/**
 * Migration 20261102000010_checkin_reports_notifications.sql on a real PostgreSQL:
 *  - C2  check-in/out actors and the staff_check_in method restriction;
 *  - C1  qr_check_in (service role only, tenant/branch scoped member lookup);
 *  - C3  on-demand, revocable, audit-logged report share links; first name only;
 *        members may update only viewed_at;
 *  - build_workout_summary without set fan-out; clock-skew-safe finalisation;
 *    per-session isolation in abandon_stale_sessions; late facts refresh reports;
 *  - notification jobs: bounded retries after lock expiry, completion guard;
 *  - push endpoints restricted to known Web Push services.
 */
import { randomUUID } from 'node:crypto';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { startHarness, type Harness } from './harness';

interface Tenant {
  orgId: string;
  branchId: string;
  branch2Id: string;
  adminUid: string;
  trainerUid: string;
  memberUid: string;
  memberId: string;
  memberCode: string;
  member2Id: string;
  member2Code: string;
  deviceId: string;
  equipmentId: string;
}

let h: Harness;
let A: Tenant;
let B: Tenant;
let legPressId: string;
let outsiderUid: string;

async function makeTenant(tag: 'A' | 'B'): Promise<Tenant> {
  const q = (sql: string, params: unknown[] = []) => h.pool.query(sql, params);
  const orgId = (await q(`insert into public.organizations (name, slug) values ($1, $2) returning id`, [`Hardening ${tag}`, `hck-${tag.toLowerCase()}`])).rows[0].id;
  const branchId = (await q(`insert into public.branches (organization_id, name) values ($1, 'Main') returning id`, [orgId])).rows[0].id;
  const branch2Id = (await q(`insert into public.branches (organization_id, name) values ($1, 'Second') returning id`, [orgId])).rows[0].id;
  const user = async (who: string) => (await q(`insert into auth.users (email) values ($1) returning id`, [`${who}-${tag}@hck.test`])).rows[0].id as string;
  const adminUid = await user('admin');
  const trainerUid = await user('trainer');
  const memberUid = await user('member');
  await q(`insert into public.staff_roles (user_id, organization_id, branch_id, role) values ($1, $2, null, 'admin')`, [adminUid, orgId]);
  await q(`insert into public.staff_roles (user_id, organization_id, branch_id, role) values ($1, $2, $3, 'trainer')`, [trainerUid, orgId, branchId]);
  const memberCode = `HCK-${tag}-M2A7`;
  const memberId = (
    await q(
      `insert into public.members (organization_id, branch_id, user_id, member_code, full_name) values ($1, $2, $3, $4, $5) returning id`,
      [orgId, branchId, memberUid, memberCode, `Nour ${tag}  El-Din`],
    )
  ).rows[0].id;
  const member2Code = `HCK-${tag}-S2ND`;
  const member2Id = (
    await q(`insert into public.members (organization_id, branch_id, member_code, full_name) values ($1, $2, $3, $4) returning id`, [
      orgId,
      branch2Id,
      member2Code,
      `Second ${tag}`,
    ])
  ).rows[0].id;
  const deviceId = (await q(`insert into public.edge_devices (branch_id, name, status) values ($1, $2, 'active') returning id`, [branchId, `hck-dev-${tag}`])).rows[0].id;
  const equipmentId = (await q(`insert into public.equipment (branch_id, name, category) values ($1, $2, 'machine') returning id`, [branchId, `Leg Press ${tag}`])).rows[0].id;
  return { orgId, branchId, branch2Id, adminUid, trainerUid, memberUid, memberId, memberCode, member2Id, member2Code, deviceId, equipmentId };
}

/** The SQLSTATE a statement fails with (or 'ok'). */
async function sqlstate(p: Promise<unknown>): Promise<string> {
  try {
    await p;
    return 'ok';
  } catch (err) {
    return (err as { code?: string }).code ?? String(err);
  }
}

async function openSession(memberId: string, branchId: string, checkInAgo = '1 hour'): Promise<string> {
  const { rows } = await h.pool.query(
    `insert into public.attendance_sessions (member_id, branch_id, check_in_at, check_in_method) values ($1, $2, now() - $3::interval, 'qr') returning id`,
    [memberId, branchId, checkInAgo],
  );
  return rows[0].id;
}

/** Usage → activity → sets, written the way ingest_events writes them. */
async function addWorkout(t: Tenant, sessionId: string, opts: { usageFrom: string; usageTo: string | null; activities: Array<{ active: number; reps: number[] }> }) {
  const tag = randomUUID().slice(0, 8);
  const usage = await h.pool.query(
    `insert into public.equipment_usage_sessions (edge_device_id, usage_local_id, attendance_session_id, equipment_id, started_at, ended_at, association_confidence)
     values ($1, $2, $3, $4, now() - $5::interval, case when $6::text is null then null else now() - $6::interval end, 0.9) returning id`,
    [t.deviceId, `u-${tag}`, sessionId, t.equipmentId, opts.usageFrom, opts.usageTo],
  );
  let n = 0;
  for (const [ai, act] of opts.activities.entries()) {
    const a = await h.pool.query(
      `insert into public.exercise_activity_sessions (edge_device_id, activity_local_id, equipment_usage_session_id, exercise_id, started_at, ended_at, classification_confidence, active_seconds)
       values ($1, $2, $3, $4, now() - interval '19 minutes' + make_interval(secs => $5), now() - interval '11 minutes', 0.9, $6) returning id`,
      [t.deviceId, `a-${tag}-${ai}`, usage.rows[0].id, legPressId, ai, act.active],
    );
    for (const [si, reps] of act.reps.entries()) {
      n++;
      await h.pool.query(
        `insert into public.exercise_sets (edge_device_id, set_local_id, exercise_activity_session_id, set_index, started_at, ended_at, rep_count, confidence, verified)
         values ($1, $2, $3, $4, now() - interval '18 minutes' + make_interval(secs => $5), now() - interval '17 minutes' + make_interval(secs => $5), $6, 0.9, true)`,
        [t.deviceId, `s-${tag}-${ai}-${si}`, a.rows[0].id, si + 1, n * 10, reps],
      );
    }
  }
  return usage.rows[0].id as string;
}

async function finalize(sessionId: string): Promise<string> {
  const { rows } = await h.pool.query(`select public.finalize_attendance_session($1, now(), 'staff_manual') as id`, [sessionId]);
  return rows[0].id;
}

async function summaryOf(sessionId: string) {
  const { rows } = await h.pool.query(`select summary from public.workout_reports where attendance_session_id = $1`, [sessionId]);
  return rows[0].summary;
}

beforeAll(async () => {
  h = await startHarness();
  legPressId = (await h.pool.query(`select id from public.exercise_catalog where slug = 'leg-press'`)).rows[0].id;
  A = await makeTenant('A');
  B = await makeTenant('B');
  outsiderUid = (await h.pool.query(`insert into auth.users (email) values ('outsider@hck.test') returning id`)).rows[0].id;
});
afterAll(async () => {
  await h?.stop();
});

describe('C2: check-in / check-out actors', () => {
  it('staff_check_in accepts only staff_manual and nfc', async () => {
    for (const method of ['qr', 'face']) {
      expect(await sqlstate(h.asUser(A.adminUid, (c) => c.query(`select public.staff_check_in($1, $2::public.checkin_method)`, [A.memberId, method])))).toBe('22023');
    }
    const { rows: none } = await h.pool.query(`select count(*)::int as n from public.attendance_sessions where member_id = $1`, [A.memberId]);
    expect(none[0].n).toBe(0);
  });

  it('records who checked the member in and out', async () => {
    const sid = (await h.asUser(A.adminUid, (c) => c.query(`select public.staff_check_in($1, 'nfc') as id`, [A.memberId]))).rows[0].id;
    const again = (await h.asUser(A.trainerUid, (c) => c.query(`select public.staff_check_in($1) as id`, [A.memberId]))).rows[0].id;
    expect(again).toBe(sid);
    const s = await h.pool.query(`select check_in_method, checked_in_by, checked_out_by from public.attendance_sessions where id = $1`, [sid]);
    expect(s.rows[0]).toEqual({ check_in_method: 'nfc', checked_in_by: A.adminUid, checked_out_by: null });

    await h.asUser(A.trainerUid, (c) => c.query(`select public.staff_check_out($1, 'staff_manual')`, [sid]));
    const out = await h.pool.query(`select status, checked_in_by, checked_out_by, check_out_method from public.attendance_sessions where id = $1`, [sid]);
    expect(out.rows[0]).toEqual({ status: 'completed', checked_in_by: A.adminUid, checked_out_by: A.trainerUid, check_out_method: 'staff_manual' });

    // A repeated check-out of a completed session does not rewrite the actor.
    await h.asUser(A.adminUid, (c) => c.query(`select public.staff_check_out($1, 'staff_manual')`, [sid]));
    expect((await h.pool.query(`select checked_out_by from public.attendance_sessions where id = $1`, [sid])).rows[0].checked_out_by).toBe(A.trainerUid);
  });

  it('member_self_check_out records the member as the actor', async () => {
    const sid = await openSession(A.memberId, A.branchId);
    await h.asUser(A.memberUid, (c) => c.query(`select public.member_self_check_out()`));
    const s = await h.pool.query(`select status, checked_in_by, checked_out_by from public.attendance_sessions where id = $1`, [sid]);
    expect(s.rows[0]).toEqual({ status: 'completed', checked_in_by: null, checked_out_by: A.memberUid });
  });
});

describe('C1: qr_check_in', () => {
  const qr = (code: string | null, staff: string | null) => h.pool.query(`select public.qr_check_in($1, $2) as id`, [code, staff]);

  it('is executable by the service role only', async () => {
    const { rows } = await h.pool.query(
      `select has_function_privilege('service_role', 'public.qr_check_in(text, uuid)', 'execute') as svc,
              has_function_privilege('authenticated', 'public.qr_check_in(text, uuid)', 'execute') as auth,
              has_function_privilege('anon', 'public.qr_check_in(text, uuid)', 'execute') as anon`,
    );
    expect(rows[0]).toEqual({ svc: true, auth: false, anon: false });
    expect(await sqlstate(h.asUser(A.adminUid, (c) => c.query(`select public.qr_check_in($1, $2)`, [A.memberCode, A.adminUid])))).toBe('42501');
  });

  it('checks in by case-insensitive code with method qr and the staff user as actor; idempotent while active', async () => {
    const sid = (await qr(A.memberCode.toLowerCase(), A.adminUid)).rows[0].id;
    const s = await h.pool.query(`select member_id, branch_id, check_in_method, checked_in_by, status from public.attendance_sessions where id = $1`, [sid]);
    expect(s.rows[0]).toEqual({ member_id: A.memberId, branch_id: A.branchId, check_in_method: 'qr', checked_in_by: A.adminUid, status: 'active' });
    expect((await qr(` ${A.memberCode} `, A.trainerUid)).rows[0].id).toBe(sid);
    await finalize(sid);
  });

  it('resolves members only inside branches where the staff user may check in', async () => {
    expect(await sqlstate(qr(A.member2Code, A.trainerUid))).toBe('P0002'); // trainer is scoped to branch 1, member is in branch 2
    expect(await sqlstate(qr(A.memberCode, B.adminUid))).toBe('P0002'); // other organization: indistinguishable from unknown
    expect(await sqlstate(qr('HCK-ZZZZ', A.adminUid))).toBe('P0002');
    expect(await sqlstate(qr(A.member2Code, A.adminUid))).toBe('ok'); // org-wide admin
    const { rows } = await h.pool.query(`select count(*)::int as n from public.attendance_sessions where member_id = $1 and status = 'active'`, [A.member2Id]);
    expect(rows[0].n).toBe(1);
  });

  it('refuses callers without a check-in role and inactive memberships', async () => {
    expect(await sqlstate(qr(A.memberCode, outsiderUid))).toBe('42501');
    expect(await sqlstate(qr(A.memberCode, null))).toBe('42501');
    const auditorUid = (await h.pool.query(`insert into auth.users (email) values ('auditor@hck.test') returning id`)).rows[0].id;
    await h.pool.query(`insert into public.staff_roles (user_id, organization_id, role) values ($1, $2, 'auditor')`, [auditorUid, A.orgId]);
    expect(await sqlstate(qr(A.memberCode, auditorUid))).toBe('42501');
    await h.pool.query(`update public.members set status = 'frozen' where id = $1`, [B.memberId]);
    expect(await sqlstate(qr(B.memberCode, B.adminUid))).toBe('23514');
    await h.pool.query(`update public.members set status = 'active' where id = $1`, [B.memberId]);
  });
});

describe('workout summary and finalisation', () => {
  it('counts active and equipment time once, whatever the number of sets and activities', async () => {
    const sid = await openSession(B.memberId, B.branchId);
    // One 600 s usage; activity 1 = 120 s with 3 sets of 10, activity 2 = 60 s with 1 set of 5.
    await addWorkout(B, sid, { usageFrom: '20 minutes', usageTo: '10 minutes', activities: [{ active: 120, reps: [10, 10, 10] }, { active: 60, reps: [5] }] });
    await finalize(sid);
    const s = await summaryOf(sid);
    expect(s.total_sets).toBe(4);
    expect(s.total_reps).toBe(35);
    expect(s.active_exercise_seconds).toBe(180);
    expect(s.exercises).toHaveLength(1);
    expect(Number(s.exercises[0].equipment_seconds)).toBeCloseTo(600, 0);
    expect(Number(s.exercises[0].active_seconds)).toBe(180);
    expect(s.exercises[0].total_reps).toBe(35);
    expect(s.exercises[0].sets.map((x: { reps: number }) => x.reps)).toEqual([10, 10, 10, 5]);
  });

  it('finalisation survives a usage that starts after the checkout time (edge clock ahead)', async () => {
    const sid = await openSession(B.memberId, B.branchId, '9 hours');
    const usage = await addWorkout(B, sid, { usageFrom: '-2 minutes', usageTo: null, activities: [] });
    const { rows } = await h.pool.query(`select public.abandon_stale_sessions(6) as n`);
    expect(rows[0].n).toBeGreaterThanOrEqual(1);
    expect((await h.pool.query(`select status from public.attendance_sessions where id = $1`, [sid])).rows[0].status).toBe('abandoned');
    const u = await h.pool.query(`select ended_at = started_at as clamped from public.equipment_usage_sessions where id = $1`, [usage]);
    expect(u.rows[0].clamped).toBe(true);
    // A finalized (abandoned) session is not finalized again by a late check-out.
    await h.asUser(B.adminUid, (c) => c.query(`select public.staff_check_out($1)`, [sid]));
    expect((await h.pool.query(`select status from public.attendance_sessions where id = $1`, [sid])).rows[0].status).toBe('abandoned');
  });

  it('abandon_stale_sessions isolates a session whose finalisation fails', async () => {
    const poisoned = await openSession(A.memberId, A.branchId, '10 hours');
    const healthy = await openSession(B.memberId, B.branchId, '10 hours');
    await h.pool.query(`
      create function public.test_poison_report() returns trigger language plpgsql as $$
      begin
        if new.attendance_session_id = '${poisoned}'::uuid then raise exception 'poisoned session'; end if;
        return new;
      end $$;
      create trigger test_poison before insert on public.workout_reports for each row execute function public.test_poison_report();`);
    try {
      const { rows } = await h.pool.query(`select public.abandon_stale_sessions(6) as n`);
      expect(rows[0].n).toBe(1);
    } finally {
      await h.pool.query(`drop trigger test_poison on public.workout_reports; drop function public.test_poison_report();`);
    }
    const st = await h.pool.query(`select id, status from public.attendance_sessions where id = any($1::uuid[])`, [[poisoned, healthy]]);
    const by = Object.fromEntries(st.rows.map((r) => [r.id, r.status]));
    expect(by).toEqual({ [poisoned]: 'active', [healthy]: 'abandoned' });
    await finalize(poisoned);
  });

  it('facts that arrive after finalisation refresh the report, and late usages are closed', async () => {
    const sid = await openSession(A.memberId, A.branchId);
    await addWorkout(A, sid, { usageFrom: '20 minutes', usageTo: null, activities: [{ active: 30, reps: [5] }] });
    await finalize(sid);
    expect((await summaryOf(sid)).total_reps).toBe(5);

    // Trailing edge batch: the set gets more reps (rep.counted / set.completed) and a new set starts.
    const set = await h.pool.query(
      `select s.id, s.exercise_activity_session_id as activity from public.exercise_sets s
         join public.exercise_activity_sessions a on a.id = s.exercise_activity_session_id
         join public.equipment_usage_sessions u on u.id = a.equipment_usage_session_id where u.attendance_session_id = $1`,
      [sid],
    );
    await h.pool.query(`update public.exercise_sets set rep_count = 8, verified = true where id = $1`, [set.rows[0].id]);
    await h.pool.query(
      `insert into public.exercise_sets (edge_device_id, set_local_id, exercise_activity_session_id, set_index, started_at, rep_count, confidence)
       values ($1, $2, $3, 2, now(), 4, 0.8)`,
      [A.deviceId, `late-${randomUUID()}`, set.rows[0].activity],
    );
    const s = await summaryOf(sid);
    expect(s.total_reps).toBe(12);
    expect(s.total_sets).toBe(2);

    const lateUsage = await addWorkout(A, sid, { usageFrom: '5 minutes', usageTo: null, activities: [] });
    const u = await h.pool.query(
      `select u.ended_at = a.check_out_at as at_checkout, u.end_reason from public.equipment_usage_sessions u
         join public.attendance_sessions a on a.id = u.attendance_session_id where u.id = $1`,
      [lateUsage],
    );
    expect(u.rows[0]).toEqual({ at_checkout: true, end_reason: 'checkout' });
  });
});

describe('C3: report sharing', () => {
  let reportId: string;
  let otherReportId: string;

  beforeAll(async () => {
    const sid = await openSession(A.memberId, A.branchId);
    reportId = await finalize(sid);
    otherReportId = await finalize(await openSession(B.memberId, B.branchId));
  });

  const create = (uid: string, id: string, days?: number) =>
    h.asUser(uid, (c) => c.query(days === undefined ? `select public.create_report_share($1) as r` : `select public.create_report_share($1, $2) as r`, days === undefined ? [id] : [id, days]));
  const shared = (token: string) => h.asAnon((c) => c.query(`select public.get_shared_report($1) as r`, [token])).then((r) => r.rows[0].r);

  it('has no share link until the member creates one', async () => {
    const { rows } = await h.pool.query(`select share_token, share_expires_at from public.workout_reports where id = $1`, [reportId]);
    expect(rows[0]).toEqual({ share_token: null, share_expires_at: null });
    const col = await h.pool.query(
      `select is_nullable, column_default from information_schema.columns where table_schema = 'public' and table_name = 'workout_reports' and column_name = 'share_token'`,
    );
    expect(col.rows[0]).toEqual({ is_nullable: 'YES', column_default: null });
  });

  it('creates a 48-hex link for the own member only, 1..30 days', async () => {
    const r = (await create(A.memberUid, reportId)).rows[0].r;
    expect(r.token).toMatch(/^[0-9a-f]{48}$/);
    const days = (Date.parse(r.expires_at) - Date.now()) / 86_400_000;
    expect(days).toBeGreaterThan(6.9);
    expect(days).toBeLessThan(7.1);
    const db = await h.pool.query(`select share_token from public.workout_reports where id = $1`, [reportId]);
    expect(db.rows[0].share_token).toBe(r.token);

    expect(await sqlstate(create(A.memberUid, reportId, 0))).toBe('22023');
    expect(await sqlstate(create(A.memberUid, reportId, 31))).toBe('22023');
    expect(await sqlstate(create(A.memberUid, otherReportId))).toBe('P0002');
    expect(await sqlstate(create(A.adminUid, reportId))).toBe('42501');
    expect(await sqlstate(h.asAnon((c) => c.query(`select public.create_report_share($1)`, [reportId])))).toBe('42501');
    expect(await sqlstate(h.asUser(B.memberUid, (c) => c.query(`select public.revoke_report_share($1)`, [reportId])))).toBe('P0002');
  });

  it('resolves for anon with the first name only and no identifiers', async () => {
    const { token } = (await create(A.memberUid, reportId, 30)).rows[0].r;
    const r = await shared(token);
    expect(r).toMatchObject({ member_first_name: 'Nour' });
    expect(r).not.toHaveProperty('member_name');
    expect(Object.keys(r).sort()).toEqual(['generated_at', 'member_first_name', 'summary']);
    const text = JSON.stringify(r);
    for (const id of [A.memberId, reportId, A.orgId, A.branchId]) expect(text).not.toContain(id);
    expect(text).not.toContain('El-Din');
    expect(await shared('nope')).toBeNull();
    expect(await shared(token.toUpperCase())).toBeNull();
  });

  it('a new link replaces the old one; revoked and expired links resolve to null', async () => {
    const first = (await create(A.memberUid, reportId)).rows[0].r.token;
    const second = (await create(A.memberUid, reportId)).rows[0].r.token;
    expect(await shared(first)).toBeNull();
    expect(await shared(second)).not.toBeNull();

    await h.asUser(A.memberUid, (c) => c.query(`select public.revoke_report_share($1)`, [reportId]));
    expect(await shared(second)).toBeNull();
    const db = await h.pool.query(`select share_token, share_expires_at from public.workout_reports where id = $1`, [reportId]);
    expect(db.rows[0]).toEqual({ share_token: null, share_expires_at: null });

    const third = (await create(A.memberUid, reportId, 1)).rows[0].r.token;
    await h.pool.query(`update public.workout_reports set share_expires_at = now() - interval '1 second' where id = $1`, [reportId]);
    expect(await shared(third)).toBeNull();
  });

  it('audit-logs creation and revocation without the token', async () => {
    const { rows } = await h.pool.query(
      `select action, actor_user_id, organization_id, to_jsonb(a) ? 'branch_id' as has_branch, to_jsonb(a)->>'branch_id' as branch_id, old_data, new_data
         from public.audit_logs a where table_name = 'workout_reports' and row_id = $1 order by id`,
      [reportId],
    );
    const actions = rows.map((r) => r.action);
    expect(actions).toContain('report_share_created');
    expect(actions).toContain('report_share_revoked');
    expect(rows.every((r) => r.actor_user_id === A.memberUid && r.organization_id === A.orgId)).toBe(true);
    // With migration 0009's branch-scoped audit log, the rows carry the member's branch too.
    expect(rows.every((r) => !r.has_branch || r.branch_id === A.branchId)).toBe(true);
    expect(JSON.stringify(rows)).not.toMatch(/[0-9a-f]{48}/);
  });

  it('members may update only viewed_at of their own reports', async () => {
    expect(await sqlstate(h.asUser(A.memberUid, (c) => c.query(`update public.workout_reports set share_expires_at = '2099-01-01' where id = $1`, [reportId])))).toBe('42501');
    expect(await sqlstate(h.asUser(A.memberUid, (c) => c.query(`update public.workout_reports set share_token = repeat('a', 48) where id = $1`, [reportId])))).toBe('42501');
    expect(await sqlstate(h.asUser(A.memberUid, (c) => c.query(`update public.workout_reports set summary = '{}' where id = $1`, [reportId])))).toBe('42501');
    const ok = await h.asUser(A.memberUid, (c) => c.query(`update public.workout_reports set viewed_at = now() where id = $1 and viewed_at is null`, [reportId]));
    expect(ok.rowCount).toBe(1);
    const foreign = await h.asUser(A.memberUid, (c) => c.query(`update public.workout_reports set viewed_at = now() where id = $1`, [otherReportId]));
    expect(foreign.rowCount).toBe(0);
  });
});

describe('notification jobs', () => {
  async function job(fields: Record<string, unknown> = {}): Promise<string> {
    const cols = ['member_id', 'channel', 'template', ...Object.keys(fields)];
    const vals = [A.memberId, 'email', 'workout_report_ready', ...Object.values(fields)];
    const { rows } = await h.pool.query(
      `insert into public.notification_jobs (${cols.join(', ')}) values (${cols.map((_, i) => `$${i + 1}`).join(', ')}) returning id`,
      vals,
    );
    return rows[0].id;
  }
  const state = async (id: string) =>
    (await h.pool.query(`select status, attempts, next_attempt_at > now() as deferred, delivered_at from public.notification_jobs where id = $1`, [id])).rows[0];

  it('a lock that expires after the last attempt dead-letters the job; earlier ones back off', async () => {
    const exhausted = await job({ status: 'sending', attempts: 2, max_attempts: 2, locked_at: new Date(Date.now() - 10 * 60_000), locked_by: 'w' });
    const retry = await job({ status: 'sending', attempts: 1, max_attempts: 3, locked_at: new Date(Date.now() - 10 * 60_000), locked_by: 'w' });
    const fresh = await job({ status: 'sending', attempts: 1, max_attempts: 3, locked_at: new Date(), locked_by: 'w' });
    await h.pool.query(`select public.release_stale_notification_locks()`);
    expect(await state(exhausted)).toMatchObject({ status: 'dead', attempts: 2 });
    expect(await state(retry)).toMatchObject({ status: 'failed', attempts: 1, deferred: true });
    expect(await state(fresh)).toMatchObject({ status: 'sending' });
  });

  it('claim never hands out a job without attempts left', async () => {
    const spent = await job({ status: 'failed', attempts: 3, max_attempts: 3, next_attempt_at: new Date(Date.now() - 60_000) });
    const due = await job();
    const { rows } = await h.pool.query(`select id from public.claim_notification_jobs('hck', 1000)`);
    const claimed = rows.map((r) => r.id);
    expect(claimed).toContain(due);
    expect(claimed).not.toContain(spent);
  });

  it('a late or duplicate completion never reopens a delivered job', async () => {
    const id = await job();
    const { rows } = await h.pool.query(`select id from public.claim_notification_jobs('hck', 1000)`);
    expect(rows.map((r) => r.id)).toContain(id);
    await h.pool.query(`select public.complete_notification_job($1, true)`, [id]);
    const delivered = await state(id);
    await h.pool.query(`select public.complete_notification_job($1, false, 'late failure')`, [id]);
    await h.pool.query(`select public.complete_notification_job($1, true)`, [id]);
    expect(await state(id)).toEqual(delivered);
    expect(delivered.status).toBe('delivered');
  });

  it('a success reported after the lock was released still marks the job delivered', async () => {
    const id = await job({ status: 'sending', attempts: 1, locked_at: new Date(Date.now() - 10 * 60_000), locked_by: 'slow' });
    await h.pool.query(`select public.release_stale_notification_locks()`);
    expect((await state(id)).status).toBe('failed');
    await h.pool.query(`select public.complete_notification_job($1, true)`, [id]);
    expect((await state(id)).status).toBe('delivered');
  });
});

describe('push subscription endpoints', () => {
  const insertAs = (uid: string, endpoint: string) =>
    h.asUser(uid, (c) => c.query(`insert into public.push_subscriptions (user_id, endpoint, p256dh, auth) values ($1, $2, 'k', 'a') returning id`, [uid, endpoint]));

  it('accept only https URLs of known Web Push services', async () => {
    for (const bad of [
      'https://169.254.169.254/latest/meta-data',
      'http://fcm.googleapis.com/fcm/send/x',
      'https://fcm.googleapis.com.evil.test/x',
      'https://fcm.googleapis.com@evil.test/x',
      'https://fcm.googleapis.com:8443/x',
      'https://internal.service.local/hook',
    ]) {
      expect(await sqlstate(insertAs(A.memberUid, bad)), bad).toBe('22023');
    }
    for (const good of [
      'https://fcm.googleapis.com/fcm/send/abc:def',
      'https://updates.push.services.mozilla.com/wpush/v2/gAAAA',
      'https://web.push.apple.com/QGuQyavXutnMH',
      'https://wns2-par02p.notify.windows.com/w/?token=BQYAAA',
    ]) {
      expect(await sqlstate(insertAs(A.memberUid, good)), good).toBe('ok');
    }
    expect(
      await sqlstate(h.asUser(A.memberUid, (c) => c.query(`update public.push_subscriptions set endpoint = 'https://evil.test/x' where user_id = $1`, [A.memberUid]))),
    ).toBe('22023');
  });

  it('member_push_subscriptions never returns a non-conforming endpoint', async () => {
    await h.pool.query(`alter table public.push_subscriptions disable trigger trg_push_endpoint_check`);
    try {
      await h.pool.query(`insert into public.push_subscriptions (user_id, endpoint, p256dh, auth) values ($1, 'https://legacy.evil.test/x', 'k', 'a')`, [A.memberUid]);
    } finally {
      await h.pool.query(`alter table public.push_subscriptions enable trigger trg_push_endpoint_check`);
    }
    const { rows } = await h.pool.query(`select endpoint from public.member_push_subscriptions($1)`, [A.memberId]);
    expect(rows.length).toBe(4);
    expect(rows.map((r) => r.endpoint)).not.toContain('https://legacy.evil.test/x');
  });
});
