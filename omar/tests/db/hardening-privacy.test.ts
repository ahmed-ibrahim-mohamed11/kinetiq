/**
 * Privacy, audit, consent and biometric-lifecycle hardening (migration 0009)
 * on the embedded PostgreSQL, emulating PostgREST roles.
 *
 * Covers: device_sync_snapshot (contract C6, callable as service_role),
 * biometric revocation paths (withdrawal, expiry, membership end, member
 * deletion tombstones), consent immutability and archive, CCTV withdrawal
 * ending the visit without a report, audit organization/branch resolution and
 * visibility, device secrets never copied into the audit log, staff-role
 * escalation guards, tenant consistency, members.user_id linking, anon access
 * to RLS helpers, and retention entries for the new tables.
 */
import { randomUUID } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import type pg from 'pg';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { MIGRATIONS_DIR, startHarness, type Harness } from './harness';

let h: Harness;

interface World {
  orgA: string;
  branchA1: string;
  branchA2: string;
  orgB: string;
  branchB1: string;
  ownerA: string;
  adminA: string;
  auditorA: string;
  branchAdminA1: string;
  branchAuditorA2: string;
  frontDeskA1: string;
  adminB: string;
  memberUser: string;
  m1: string; // member in A1 with a login
  m2: string; // member in A2
  deviceA1: string;
  cameraA1: string;
  zoneA1: string;
  consents: { bio: string; cctv: string; marketing: string; data: string };
}
let W: World;

const q = async (sql: string, params: unknown[] = []) => (await h.pool.query(sql, params)).rows;
const one = async (sql: string, params: unknown[] = []) => (await q(sql, params))[0];

async function user(tag: string): Promise<string> {
  return (await one(`insert into auth.users (email) values ($1) returning id`, [`${tag}-${randomUUID().slice(0, 8)}@example.test`])).id;
}
async function role(uid: string, org: string, branch: string | null, r: string) {
  await q(`insert into public.staff_roles (user_id, organization_id, branch_id, role) values ($1, $2, $3, $4)`, [uid, org, branch, r]);
}
let codeSeq = 0;
async function member(org: string, branch: string, opts: { userId?: string | null; name?: string; status?: string } = {}): Promise<string> {
  const letters = 'ABCDEFGHJKLMNPQRSTUVWXYZ';
  const n = codeSeq++;
  const code = `PRV-${letters[Math.floor(n / 24) % 24]}${letters[n % 24]}M2A7`;
  return (
    await one(
      `insert into public.members (organization_id, branch_id, user_id, member_code, full_name, status) values ($1, $2, $3, $4, $5, $6) returning id`,
      [org, branch, opts.userId ?? null, code, opts.name ?? `Privacy Member ${n}`, opts.status ?? 'active'],
    )
  ).id;
}
async function consent(memberId: string, type: string, extra: { expiresAt?: string } = {}): Promise<string> {
  return (
    await one(`insert into public.consent_records (member_id, consent_type, policy_version, expires_at) values ($1, $2, 'privacy-v1', $3) returning id`, [
      memberId,
      type,
      extra.expiresAt ?? null,
    ])
  ).id;
}
async function enroll(memberId: string, consentId: string, ref: string) {
  await q(
    `insert into public.biometric_enrollments (member_id, edge_device_id, consent_record_id, template_ref, key_id) values ($1, $2, $3, $4, 'k1')`,
    [memberId, W.deviceA1, consentId, ref],
  );
}
async function snapshot(): Promise<Record<string, any>> {
  return h.asService(async (c) => {
    await c.query('set local role service_role');
    return (await c.query('select public.device_sync_snapshot($1) as s', [W.deviceA1])).rows[0].s;
  });
}
const asUser = <T>(uid: string, fn: (c: pg.PoolClient) => Promise<T>) => h.asUser(uid, fn);

beforeAll(async () => {
  h = await startHarness({ seed: false });
  const orgA = (await one(`insert into public.organizations (name, slug) values ('Privacy A', 'privacy-a') returning id`)).id;
  const orgB = (await one(`insert into public.organizations (name, slug) values ('Privacy B', 'privacy-b') returning id`)).id;
  const branchA1 = (await one(`insert into public.branches (organization_id, name) values ($1, 'A1') returning id`, [orgA])).id;
  const branchA2 = (await one(`insert into public.branches (organization_id, name) values ($1, 'A2') returning id`, [orgA])).id;
  const branchB1 = (await one(`insert into public.branches (organization_id, name) values ($1, 'B1') returning id`, [orgB])).id;
  const [ownerA, adminA, auditorA, branchAdminA1, branchAuditorA2, frontDeskA1, adminB, memberUser] = await Promise.all(
    ['owner-a', 'admin-a', 'auditor-a', 'badmin-a1', 'baud-a2', 'fd-a1', 'admin-b', 'member'].map(user),
  );
  await role(ownerA!, orgA, null, 'owner');
  await role(adminA!, orgA, null, 'admin');
  await role(auditorA!, orgA, null, 'auditor');
  await role(branchAdminA1!, orgA, branchA1, 'admin');
  await role(branchAuditorA2!, orgA, branchA2, 'auditor');
  await role(frontDeskA1!, orgA, branchA1, 'front_desk');
  await role(adminB!, orgB, null, 'admin');

  const deviceA1 = (await one(`insert into public.edge_devices (branch_id, name, status) values ($1, 'edge-a1', 'active') returning id`, [branchA1])).id;
  const cameraA1 = (
    await one(`insert into public.cameras (branch_id, edge_device_id, name, purpose, stream_url) values ($1, $2, 'cam-a1', 'floor', 'rtsp://192.0.2.1/a1') returning id`, [
      branchA1,
      deviceA1,
    ])
  ).id;
  const legPress = (await one(`select id from public.exercise_catalog where slug = 'leg-press'`)).id;
  const eq = (await one(`insert into public.equipment (branch_id, name, category, default_exercise_id) values ($1, 'Leg Press A1', 'machine', $2) returning id`, [branchA1, legPress])).id;
  const zoneA1 = (
    await one(`insert into public.camera_zones (camera_id, equipment_id, name, zone_type, polygon) values ($1, $2, 'lp', 'equipment', '[[0,0],[10,0],[10,10]]') returning id`, [
      cameraA1,
      eq,
    ])
  ).id;
  await q(`insert into public.cameras (branch_id, name, stream_url) values ($1, 'cam-b1', 'rtsp://192.0.2.2/b1')`, [branchB1]);

  W = {
    orgA, branchA1, branchA2, orgB, branchB1,
    ownerA: ownerA!, adminA: adminA!, auditorA: auditorA!, branchAdminA1: branchAdminA1!, branchAuditorA2: branchAuditorA2!,
    frontDeskA1: frontDeskA1!, adminB: adminB!, memberUser: memberUser!,
    m1: '', m2: '', deviceA1, cameraA1, zoneA1,
    consents: { bio: '', cctv: '', marketing: '', data: '' },
  };
  W.m1 = await member(orgA, branchA1, { userId: memberUser, name: 'Nadia Hassan Privacy' });
  W.m2 = await member(orgA, branchA2);
  W.consents = {
    bio: await consent(W.m1, 'biometric_face_identification'),
    cctv: await consent(W.m1, 'cctv_workout_tracking'),
    marketing: await consent(W.m1, 'marketing'),
    data: await consent(W.m1, 'data_processing'),
  };
  await consent(W.m2, 'cctv_workout_tracking');
  const frozen = await member(orgA, branchA1, { status: 'frozen' });
  await consent(frozen, 'cctv_workout_tracking');
});
afterAll(async () => {
  await h?.stop();
});

describe('device_sync_snapshot (C6)', () => {
  it('runs as service_role (volatile), returns the C6 keys and records last_seen_at', async () => {
    const s = await snapshot();
    for (const k of ['generated_at', 'device_id', 'branch_id', 'cameras', 'zones', 'equipment', 'exercises', 'consents', 'revoked_template_refs', 'active_template_refs']) {
      expect(s, `key ${k}`).toHaveProperty(k);
    }
    expect(s).not.toHaveProperty('members');
    expect(s.device_id).toBe(W.deviceA1);
    expect(s.branch_id).toBe(W.branchA1);
    expect(s.cameras.map((c: any) => c.id)).toEqual([W.cameraA1]);
    expect(s.cameras[0].zones.map((z: any) => z.id)).toEqual([W.zoneA1]);
    expect(s.zones).toEqual([expect.objectContaining({ id: W.zoneA1, camera_id: W.cameraA1, type: 'equipment', exercise_slug: 'leg-press' })]);
    expect(s.exercises.length).toBeGreaterThan(5);
    const seen = await one(`select last_seen_at from public.edge_devices where id = $1`, [W.deviceA1]);
    expect(seen.last_seen_at).not.toBeNull();
  });

  it('lists only granted biometric/CCTV consents of active branch members, with no personal data', async () => {
    const s = await snapshot();
    expect(Array.isArray(s.consents)).toBe(true);
    const mine = s.consents.filter((c: any) => c.member_id === W.m1);
    expect(mine.map((c: any) => [c.consent_type, c.consent_record_id]).sort()).toEqual(
      [['biometric_face_identification', W.consents.bio], ['cctv_workout_tracking', W.consents.cctv]].sort(),
    );
    for (const c of s.consents) {
      expect(['biometric_face_identification', 'cctv_workout_tracking']).toContain(c.consent_type);
      expect(c).toHaveProperty('expires_at');
    }
    // frozen member and the member of another branch are absent
    expect(new Set(s.consents.map((c: any) => c.member_id))).toEqual(new Set([W.m1]));
    const raw = JSON.stringify(s);
    expect(raw).not.toContain('Nadia Hassan Privacy');
    expect(raw).not.toContain('PRV-');
  });

  it('is not executable by anon or authenticated, and refuses a retired device with device_retired', async () => {
    await expect(h.asAnon((c) => c.query('select public.device_sync_snapshot($1)', [W.deviceA1]))).rejects.toThrow(/permission denied/);
    await expect(asUser(W.adminA, (c) => c.query('select public.device_sync_snapshot($1)', [W.deviceA1]))).rejects.toThrow(/permission denied/);
    const retired = (await one(`insert into public.edge_devices (branch_id, name, status) values ($1, 'edge-retired', 'retired') returning id`, [W.branchA1])).id;
    const err = await h.pool.query('select public.device_sync_snapshot($1)', [retired]).catch((e) => e);
    expect(err.code).toBe('42501');
    expect(err.message).toMatch(/^device_retired/);
  });
});

describe('biometric revocation lifecycle', () => {
  it('biometric consent withdrawal revokes the reference and moves it to revoked_template_refs', async () => {
    const m = await member(W.orgA, W.branchA1);
    const bio = await consent(m, 'biometric_face_identification');
    await enroll(m, bio, 'tpl-withdraw');
    expect((await snapshot()).active_template_refs).toContain('tpl-withdraw');
    await asUser(W.frontDeskA1, (c) => c.query(`update public.consent_records set status = 'withdrawn', withdrawn_at = now() where id = $1`, [bio]));
    const s = await snapshot();
    expect(s.revoked_template_refs).toContain('tpl-withdraw');
    expect(s.active_template_refs).not.toContain('tpl-withdraw');
  });

  it('an expired biometric consent is revoked in the snapshot at once and by the expiry sweep', async () => {
    const m = await member(W.orgA, W.branchA1);
    const bio = await consent(m, 'biometric_face_identification', { expiresAt: new Date(Date.now() - 60_000).toISOString() });
    await enroll(m, bio, 'tpl-expired');
    const s = await snapshot();
    expect(s.revoked_template_refs).toContain('tpl-expired');
    expect(s.active_template_refs).not.toContain('tpl-expired');
    expect(s.consents.find((c: any) => c.consent_record_id === bio)).toBeUndefined();

    const swept = await one(`select public.expire_consents() as n`);
    expect(swept.n).toBeGreaterThanOrEqual(1);
    expect((await one(`select status from public.consent_records where id = $1`, [bio])).status).toBe('expired');
    expect((await one(`select revoked_at from public.biometric_enrollments where template_ref = 'tpl-expired'`)).revoked_at).not.toBeNull();
    await expect(asUser(W.adminA, (c) => c.query('select public.expire_consents()'))).rejects.toThrow(/permission denied/);
  });

  it('a cancelled or expired membership revokes the member’s references', async () => {
    const m = await member(W.orgA, W.branchA1);
    const bio = await consent(m, 'biometric_face_identification');
    await enroll(m, bio, 'tpl-cancelled');
    await asUser(W.frontDeskA1, (c) => c.query(`update public.members set status = 'cancelled' where id = $1`, [m]));
    expect((await one(`select revoked_at from public.biometric_enrollments where template_ref = 'tpl-cancelled'`)).revoked_at).not.toBeNull();
    expect((await snapshot()).revoked_template_refs).toContain('tpl-cancelled');
  });

  it('member deletion leaves a tombstone, archives consents for 5 years and keeps audit rows attributable', async () => {
    const m = await member(W.orgA, W.branchA1, { name: 'Deleted Person' });
    const bio = await consent(m, 'biometric_face_identification');
    const cctv = await consent(m, 'cctv_workout_tracking');
    await enroll(m, bio, 'tpl-deleted');
    const del = await asUser(W.adminA, (c) => c.query(`delete from public.members where id = $1`, [m]));
    expect(del.rowCount).toBe(1);

    expect(await q(`select reason from public.biometric_revocations where template_ref = 'tpl-deleted'`)).toEqual([{ reason: 'member_deleted' }]);
    const s = await snapshot();
    expect(s.revoked_template_refs).toContain('tpl-deleted');
    expect(s.active_template_refs).not.toContain('tpl-deleted');

    const archived = await q(
      `select id, organization_id, branch_id, member_full_name, archive_reason, archived_by from public.consent_records_archive where member_id = $1 order by id`,
      [m],
    );
    expect(archived.map((a) => a.id).sort()).toEqual([bio, cctv].sort());
    for (const a of archived) expect(a).toMatchObject({ organization_id: W.orgA, branch_id: W.branchA1, member_full_name: 'Deleted Person', archive_reason: 'member_deleted', archived_by: W.adminA });
    const policy = await one(`select keep_days, timestamp_col from public.retention_policies where table_name = 'consent_records_archive'`);
    expect(policy).toEqual({ keep_days: 1825, timestamp_col: 'archived_at' });

    const auditDeletes = await q(`select organization_id from public.audit_logs where table_name = 'consent_records' and action = 'DELETE' and row_id = any($1)`, [[bio, cctv]]);
    expect(auditDeletes).toHaveLength(2);
    expect(auditDeletes.every((r) => r.organization_id === W.orgA)).toBe(true);
    // Owners/auditors can read the archive; members, front desk and other tenants cannot.
    expect((await asUser(W.auditorA, (c) => c.query('select id from public.consent_records_archive where member_id = $1', [m]))).rowCount).toBe(2);
    expect((await asUser(W.frontDeskA1, (c) => c.query('select id from public.consent_records_archive'))).rowCount).toBe(0);
    expect((await asUser(W.adminB, (c) => c.query('select id from public.consent_records_archive'))).rowCount).toBe(0);
  });
});

describe('biometric enrollment references cannot be forged or un-revoked (R2-10)', () => {
  it('owners/admins only read them; revocation is permanent and goes through the RPC', async () => {
    const m = await member(W.orgA, W.branchA1);
    const bio = await consent(m, 'biometric_face_identification');
    await enroll(m, bio, 'tpl-guard');
    const id = (await one(`select id from public.biometric_enrollments where template_ref = 'tpl-guard'`)).id;

    expect((await asUser(W.adminA, (c) => c.query('select id from public.biometric_enrollments where id = $1', [id]))).rowCount).toBe(1);
    await expect(asUser(W.adminA, (c) => c.query(`update public.biometric_enrollments set revoked_at = now() where id = $1`, [id]))).rejects.toThrow(/permission denied/);
    await expect(
      asUser(W.adminA, (c) =>
        c.query(`insert into public.biometric_enrollments (member_id, edge_device_id, consent_record_id, template_ref, key_id) values ($1, $2, $3, 'tpl-forged', 'k')`, [m, W.deviceA1, bio]),
      ),
    ).rejects.toThrow(/permission denied/);

    await expect(asUser(W.adminB, (c) => c.query('select public.revoke_biometric_enrollment($1)', [id]))).rejects.toThrow(/not authorized/);
    await expect(asUser(W.frontDeskA1, (c) => c.query('select public.revoke_biometric_enrollment($1)', [id]))).rejects.toThrow(/not authorized/);
    const r = await asUser(W.adminA, (c) => c.query('select public.revoke_biometric_enrollment($1) as at', [id]));
    expect(r.rows[0].at).toBeTruthy();
    const audit = await one(`select actor_user_id, organization_id from public.audit_logs where table_name = 'biometric_enrollments' and row_id = $1 and action = 'UPDATE' order by id desc limit 1`, [id]);
    expect(audit).toEqual({ actor_user_id: W.adminA, organization_id: W.orgA });

    // Even the service role cannot clear a revocation or re-point the reference.
    await expect(q(`update public.biometric_enrollments set revoked_at = null where id = $1`, [id])).rejects.toThrow(/permanent/);
    await expect(q(`update public.biometric_enrollments set consent_record_id = $2 where id = $1`, [id, W.consents.bio])).rejects.toThrow(/immutable/);
  });
});

describe('consent records', () => {
  it('browser inserts record the caller as capturing staff and server times', async () => {
    const m = await member(W.orgA, W.branchA1);
    const r = await asUser(W.frontDeskA1, (c) =>
      c.query(
        `insert into public.consent_records (member_id, consent_type, policy_version, captured_by_staff_id, granted_at, informed_acknowledged_at)
         values ($1, 'marketing', 'privacy-v1', $2, '2020-01-01T00:00:00Z', '2020-01-01T00:00:00Z')
         returning captured_by_staff_id, granted_at > now() - interval '1 minute' as fresh, informed_acknowledged_at > now() - interval '1 minute' as ack_fresh`,
        [m, W.ownerA],
      ),
    );
    expect(r.rows[0]).toEqual({ captured_by_staff_id: W.frontDeskA1, fresh: true, ack_fresh: true });
    await expect(
      asUser(W.frontDeskA1, (c) =>
        c.query(`insert into public.consent_records (member_id, consent_type, policy_version, status, withdrawn_at) values ($1, 'marketing', 'v', 'withdrawn', now())`, [m]),
      ),
    ).rejects.toThrow(/must be granted/);
  });

  it('are immutable: only granted -> withdrawn (server time) is allowed, for members, staff and the service role', async () => {
    const id = W.consents.marketing;
    const w = await asUser(W.memberUser, (c) =>
      c.query(`update public.consent_records set status = 'withdrawn', withdrawn_at = '2020-01-01T00:00:00Z' where id = $1 returning withdrawn_at > now() - interval '1 minute' as fresh`, [id]),
    );
    expect(w.rows[0].fresh).toBe(true);
    await expect(asUser(W.memberUser, (c) => c.query(`update public.consent_records set policy_version = 'forged' where id = $1`, [id]))).rejects.toThrow(/immutable/);
    await expect(asUser(W.frontDeskA1, (c) => c.query(`update public.consent_records set granted_at = now() - interval '9 days' where id = $1`, [id]))).rejects.toThrow(/immutable/);
    await expect(asUser(W.frontDeskA1, (c) => c.query(`update public.consent_records set withdrawn_at = now() - interval '9 days' where id = $1`, [id]))).rejects.toThrow(/immutable/);
    await expect(q(`update public.consent_records set status = 'granted', withdrawn_at = null where id = $1`, [id])).rejects.toThrow(/cannot change from withdrawn to granted/);
    await expect(q(`update public.consent_records set status = 'expired' where id = $1`, [W.consents.data])).rejects.toThrow(/cannot change from granted to expired/);
    expect((await one(`select policy_version from public.consent_records where id = $1`, [id])).policy_version).toBe('privacy-v1');
  });

  it('CCTV withdrawal mid-visit ends the session as abandoned with no report and no notification', async () => {
    const m = await member(W.orgA, W.branchA1, { userId: await user('cctv-member') });
    const muid = (await one('select user_id from public.members where id = $1', [m])).user_id;
    const cctv = await consent(m, 'cctv_workout_tracking');
    const session = (await one(`insert into public.attendance_sessions (member_id, branch_id, check_in_at, check_in_method) values ($1, $2, now() - interval '20 minutes', 'qr') returning id`, [m, W.branchA1])).id;
    const eq = (await one(`select id from public.equipment where branch_id = $1 limit 1`, [W.branchA1])).id;
    const ex = (await one(`select id from public.exercise_catalog where slug = 'leg-press'`)).id;
    const usage = (
      await one(
        `insert into public.equipment_usage_sessions (edge_device_id, usage_local_id, attendance_session_id, equipment_id, started_at, association_confidence) values ($1, $2, $3, $4, now() - interval '10 minutes', 0.9) returning id`,
        [W.deviceA1, `u-${session}`, session, eq],
      )
    ).id;
    const act = (
      await one(
        `insert into public.exercise_activity_sessions (edge_device_id, activity_local_id, equipment_usage_session_id, exercise_id, started_at, classification_confidence) values ($1, $2, $3, $4, now() - interval '9 minutes', 0.9) returning id`,
        [W.deviceA1, `a-${session}`, usage, ex],
      )
    ).id;
    await q(`insert into public.exercise_sets (edge_device_id, set_local_id, exercise_activity_session_id, set_index, started_at, rep_count) values ($1, $2, $3, 1, now() - interval '8 minutes', 4)`, [
      W.deviceA1,
      `s-${session}`,
      act,
    ]);

    await asUser(muid, (c) => c.query(`update public.consent_records set status = 'withdrawn', withdrawn_at = now() where id = $1`, [cctv]));

    const s = await one(`select status, check_out_at from public.attendance_sessions where id = $1`, [session]);
    expect(s.status).toBe('abandoned');
    expect(s.check_out_at).not.toBeNull();
    expect(await q(`select id from public.workout_reports where attendance_session_id = $1`, [session])).toEqual([]);
    expect(await q(`select id from public.notification_jobs where member_id = $1`, [m])).toEqual([]);
    expect(await one(`select ended_at is not null as closed, end_reason from public.equipment_usage_sessions where id = $1`, [usage])).toEqual({ closed: true, end_reason: 'consent_withdrawn' });
    expect(await one(`select ended_at is not null as closed, verified from public.exercise_sets where exercise_activity_session_id = $1`, [act])).toEqual({ closed: true, verified: false });
    expect(await q(`select id from public.attendance_sessions where member_id = $1 and status = 'active'`, [m])).toEqual([]);
  });
});

describe('audit log', () => {
  it('consent and device-secret changes carry the organization and branch; the raw secret is never logged', async () => {
    const m = await member(W.orgA, W.branchA2);
    const c = await consent(m, 'data_processing');
    const row = await one(`select organization_id, branch_id from public.audit_logs where table_name = 'consent_records' and row_id = $1`, [c]);
    expect(row).toEqual({ organization_id: W.orgA, branch_id: W.branchA2 });

    const secret = (await asUser(W.adminA, (cl) => cl.query('select public.issue_device_secret($1) as s', [W.deviceA1]))).rows[0].s as string;
    await asUser(W.adminA, (cl) => cl.query('select public.issue_device_secret($1) as s', [W.deviceA1])); // rotation: UPDATE + INSERT
    const rows = await q(`select organization_id, branch_id, old_data, new_data from public.audit_logs where table_name = 'edge_device_secrets'`);
    expect(rows.length).toBeGreaterThanOrEqual(3);
    for (const r of rows) {
      expect(r).toMatchObject({ organization_id: W.orgA, branch_id: W.branchA1 });
      for (const d of [r.old_data, r.new_data].filter(Boolean)) {
        expect(Object.keys(d).sort()).toEqual(['created_at', 'edge_device_id', 'id', 'retired_at', 'secret_fingerprint']);
      }
    }
    expect(JSON.stringify(rows)).not.toContain(secret);
    const stored = await one(`select secret_hash from public.edge_device_secrets where secret = $1`, [secret]);
    expect(JSON.stringify(rows)).not.toContain(stored.secret_hash);
  });

  it('org-wide owners/auditors see the whole org; branch auditors only their branch; others nothing', async () => {
    const inA1 = await consent(W.m1, 'marketing');
    const inA2 = await consent(W.m2, 'marketing');
    const visible = async (uid: string) =>
      (await asUser(uid, (c) => c.query(`select row_id from public.audit_logs where table_name = 'consent_records' and row_id = any($1)`, [[inA1, inA2]]))).rows
        .map((r) => r.row_id)
        .sort();
    expect(await visible(W.ownerA)).toEqual([inA1, inA2].sort());
    expect(await visible(W.auditorA)).toEqual([inA1, inA2].sort());
    expect(await visible(W.branchAuditorA2)).toEqual([inA2]);
    expect(await visible(W.adminA)).toEqual([]);
    expect(await visible(W.adminB)).toEqual([]);
    expect(await visible(W.memberUser)).toEqual([]);
  });
});

describe('staff roles and tenant boundaries', () => {
  it('a branch admin cannot become org-wide admin, manage other branches or grant admin (R2-3)', async () => {
    const self = W.branchAdminA1;
    await expect(
      asUser(self, (c) => c.query(`insert into public.staff_roles (user_id, organization_id, branch_id, role) values ($1, $2, null, 'admin')`, [self, W.orgA])),
    ).rejects.toThrow(/row-level security/);
    const newUser = await user('new-trainer');
    const ok = await asUser(self, (c) =>
      c.query(`insert into public.staff_roles (user_id, organization_id, branch_id, role) values ($1, $2, $3, 'trainer') returning id`, [newUser, W.orgA, W.branchA1]),
    );
    expect(ok.rowCount).toBe(1);
    await expect(
      asUser(self, (c) => c.query(`insert into public.staff_roles (user_id, organization_id, branch_id, role) values ($1, $2, $3, 'trainer')`, [newUser, W.orgA, W.branchA2])),
    ).rejects.toThrow(/row-level security/);
    await expect(
      asUser(self, (c) => c.query(`insert into public.staff_roles (user_id, organization_id, branch_id, role) values ($1, $2, $3, 'admin')`, [newUser, W.orgA, W.branchA1])),
    ).rejects.toThrow(/row-level security/);
    const upd = await asUser(self, (c) => c.query(`update public.branches set name = 'Hijacked' where id = $1`, [W.branchA2]));
    expect(upd.rowCount).toBe(0);
    const esc = await asUser(self, (c) => c.query(`update public.staff_roles set branch_id = null where user_id = $1`, [self]));
    expect(esc.rowCount).toBe(0);
  });

  it('admins cannot delete or demote owners, and the last owner cannot be removed (R2-5)', async () => {
    const delOwner = await asUser(W.adminA, (c) => c.query(`delete from public.staff_roles where user_id = $1 and role = 'owner'`, [W.ownerA]));
    expect(delOwner.rowCount).toBe(0);
    const demote = await asUser(W.adminA, (c) => c.query(`update public.staff_roles set role = 'trainer' where user_id = $1 and role = 'owner'`, [W.ownerA]));
    expect(demote.rowCount).toBe(0);
    await expect(asUser(W.ownerA, (c) => c.query(`delete from public.staff_roles where user_id = $1 and role = 'owner'`, [W.ownerA]))).rejects.toThrow(/at least one owner/);
    await expect(asUser(W.ownerA, (c) => c.query(`update public.staff_roles set role = 'admin' where user_id = $1 and role = 'owner'`, [W.ownerA]))).rejects.toThrow(/at least one owner/);
    const second = await user('second-owner');
    await asUser(W.ownerA, (c) => c.query(`insert into public.staff_roles (user_id, organization_id, role) values ($1, $2, 'owner')`, [second, W.orgA]));
    const removed = await asUser(W.ownerA, (c) => c.query(`delete from public.staff_roles where user_id = $1 and role = 'owner'`, [second]));
    expect(removed.rowCount).toBe(1);
    expect((await one(`select count(*)::int as n from public.staff_roles where organization_id = $1 and role = 'owner'`, [W.orgA])).n).toBe(1);
  });

  it('members and staff roles must belong to the organization of their branch (R2-4)', async () => {
    await expect(
      asUser(W.frontDeskA1, (c) =>
        c.query(`insert into public.members (organization_id, branch_id, member_code, full_name) values ($1, $2, 'PRB-B-M2A7X', 'Squatter')`, [W.orgB, W.branchA1]),
      ),
    ).rejects.toThrow(/does not belong to organization/);
    await expect(
      asUser(W.frontDeskA1, (c) => c.query(`update public.members set organization_id = $2 where id = $1`, [W.m1, W.orgB])),
    ).rejects.toThrow(/does not belong to organization/);
    await expect(
      asUser(W.adminA, (c) => c.query(`insert into public.staff_roles (user_id, organization_id, branch_id, role) values ($1, $2, $3, 'trainer')`, [W.frontDeskA1, W.orgA, W.branchB1])),
    ).rejects.toThrow(/does not belong to organization|row-level security/);
    expect(await q(`select id from public.audit_logs where new_data->>'full_name' = 'Squatter'`)).toEqual([]);
  });

  it('staff cannot link or re-link members.user_id', async () => {
    const m = await member(W.orgA, W.branchA1);
    await expect(asUser(W.frontDeskA1, (c) => c.query(`update public.members set user_id = $2 where id = $1`, [m, W.frontDeskA1]))).rejects.toThrow(/activation flow/);
    await expect(asUser(W.adminA, (c) => c.query(`update public.members set user_id = null where id = $1`, [W.m1]))).rejects.toThrow(/activation flow/);
    await expect(
      asUser(W.frontDeskA1, (c) =>
        c.query(`insert into public.members (organization_id, branch_id, user_id, member_code, full_name) values ($1, $2, $3, 'PRV-LNK-M2A7', 'Linked')`, [W.orgA, W.branchA1, W.frontDeskA1]),
      ),
    ).rejects.toThrow(/activation flow/);
    // Ordinary staff edits still work; the service role links accounts.
    const ok = await asUser(W.frontDeskA1, (c) => c.query(`update public.members set phone = '+201000000123' where id = $1`, [m]));
    expect(ok.rowCount).toBe(1);
    await q(`update public.members set user_id = $2 where id = $1`, [m, await user('activated')]);
  });

  it('front desk links a member account through link_member_account (activation flow), audited', async () => {
    const m = await member(W.orgA, W.branchA1);
    const email = `Activate-${randomUUID().slice(0, 8)}@Example.test`;
    const uid = (await one(`insert into auth.users (email) values ($1) returning id`, [email.toLowerCase()])).id;
    // The other tenant's admin, a member and anon cannot link.
    await expect(asUser(W.adminB, (c) => c.query('select public.link_member_account($1, $2)', [m, email]))).rejects.toThrow(/not authorized/);
    await expect(asUser(W.memberUser, (c) => c.query('select public.link_member_account($1, $2)', [m, email]))).rejects.toThrow(/not authorized/);
    await expect(h.asAnon((c) => c.query('select public.link_member_account($1, $2)', [m, email]))).rejects.toThrow(/permission denied/);
    // Unknown email, staff account, account already linked elsewhere.
    await expect(asUser(W.frontDeskA1, (c) => c.query('select public.link_member_account($1, $2)', [m, 'nobody@example.test']))).rejects.toThrow(/no account/);
    const staffEmail = (await one('select email from auth.users where id = $1', [W.adminA])).email;
    await expect(asUser(W.frontDeskA1, (c) => c.query('select public.link_member_account($1, $2)', [m, staffEmail]))).rejects.toThrow(/staff accounts/);
    const memberEmail = (await one('select email from auth.users where id = $1', [W.memberUser])).email;
    await expect(asUser(W.frontDeskA1, (c) => c.query('select public.link_member_account($1, $2)', [m, memberEmail]))).rejects.toThrow(/already linked to a member/);

    // Case-insensitive email; idempotent; the UPDATE is audited with the staff actor.
    const r = await asUser(W.frontDeskA1, (c) => c.query('select public.link_member_account($1, $2) as u', [m, `  ${email.toUpperCase()} `]));
    expect(r.rows[0].u).toBe(uid);
    expect((await one('select user_id from public.members where id = $1', [m])).user_id).toBe(uid);
    const again = await asUser(W.frontDeskA1, (c) => c.query('select public.link_member_account($1, $2) as u', [m, email]));
    expect(again.rows[0].u).toBe(uid);
    const audit = await q(
      `select actor_user_id, organization_id, branch_id from public.audit_logs
        where table_name = 'members' and action = 'UPDATE' and row_id = $1 and new_data->>'user_id' = $2`,
      [m, uid],
    );
    expect(audit).toEqual([{ actor_user_id: W.frontDeskA1, organization_id: W.orgA, branch_id: W.branchA1 }]);
    // A member that is already linked cannot be re-pointed to another account.
    const other = await user('other-account');
    const otherEmail = (await one('select email from auth.users where id = $1', [other])).email;
    await expect(asUser(W.frontDeskA1, (c) => c.query('select public.link_member_account($1, $2)', [m, otherEmail]))).rejects.toThrow(/already linked to another account/);
    // The newly linked member sees their own row.
    const self = await asUser(uid, (c) => c.query('select id from public.members'));
    expect(self.rows.map((x) => x.id)).toEqual([m]);
  });

  it('only org-wide owners grant the auditor role; admins cannot grant it to themselves', async () => {
    await expect(
      asUser(W.branchAdminA1, (c) =>
        c.query(`insert into public.staff_roles (user_id, organization_id, branch_id, role) values ($1, $2, $3, 'auditor')`, [W.branchAdminA1, W.orgA, W.branchA1]),
      ),
    ).rejects.toThrow(/row-level security/);
    await expect(
      asUser(W.adminA, (c) => c.query(`insert into public.staff_roles (user_id, organization_id, role) values ($1, $2, 'auditor')`, [W.adminA, W.orgA])),
    ).rejects.toThrow(/row-level security/);
    const delAud = await asUser(W.adminA, (c) => c.query(`delete from public.staff_roles where user_id = $1 and role = 'auditor'`, [W.branchAuditorA2]));
    expect(delAud.rowCount).toBe(0);
    const newAud = await user('new-auditor');
    const ok = await asUser(W.ownerA, (c) => c.query(`insert into public.staff_roles (user_id, organization_id, role) values ($1, $2, 'auditor') returning id`, [newAud, W.orgA]));
    expect(ok.rowCount).toBe(1);
    // Admins keep managing ordinary roles.
    const trainer = await user('admin-trainer');
    const t = await asUser(W.adminA, (c) => c.query(`insert into public.staff_roles (user_id, organization_id, branch_id, role) values ($1, $2, $3, 'trainer') returning id`, [trainer, W.orgA, W.branchA2]));
    expect(t.rowCount).toBe(1);
  });

  it('anon cannot execute the RLS helper functions (R2-11); signed-in users still can', async () => {
    for (const sql of ['select public.branch_org($1)', 'select public.session_member($1)', 'select public.has_branch_role($1, \'owner\')', 'select public.usage_session($1)']) {
      await expect(h.asAnon((c) => c.query(sql, [W.branchA1]))).rejects.toThrow(/permission denied/);
    }
    const r = await asUser(W.adminA, (c) => c.query('select public.branch_org($1) as o', [W.branchA1]));
    expect(r.rows[0].o).toBe(W.orgA);
  });
});

describe('retention', () => {
  it('covers the new tables and expires old archive rows', async () => {
    const tables = (await q(`select table_name from public.apply_retention()`)).map((r) => r.table_name);
    expect(tables).toEqual(expect.arrayContaining(['consent_records_archive', 'biometric_revocations']));
    const oldId = randomUUID();
    await q(
      `insert into public.consent_records_archive (id, member_id, consent_type, status, policy_version, granted_at, created_at, archived_at, archive_reason)
       values ($1, $2, 'marketing', 'granted', 'v', now() - interval '7 years', now() - interval '7 years', now() - interval '1900 days', 'member_deleted')`,
      [oldId, randomUUID()],
    );
    await q(`select * from public.apply_retention()`);
    expect(await q(`select id from public.consent_records_archive where id = $1`, [oldId])).toEqual([]);
    expect((await one(`select count(*)::int as n from public.consent_records_archive`)).n).toBeGreaterThan(0);
  });
});

describe('migration 0009 is re-runnable', () => {
  it('scrubs secrets already copied into audit_logs and backfills organization/branch', async () => {
    const legacySecret = (await one(`select id from public.edge_device_secrets where edge_device_id = $1 limit 1`, [W.deviceA1])).id;
    await q(
      `insert into public.audit_logs (action, table_name, row_id, organization_id, old_data, new_data) values
       ('UPDATE', 'edge_device_secrets', $1, null,
        jsonb_build_object('id', $1::text, 'edge_device_id', $2::text, 'secret_hash', repeat('ab', 32), 'secret', 'LEGACY-RAW-SECRET', 'created_at', now(), 'retired_at', null),
        jsonb_build_object('id', $1::text, 'edge_device_id', $2::text, 'secret_hash', repeat('ab', 32), 'secret', 'LEGACY-RAW-SECRET', 'created_at', now(), 'retired_at', now())),
       ('INSERT', 'consent_records', $3, null, null, jsonb_build_object('id', $3::text, 'member_id', $4::text))`,
      [legacySecret, W.deviceA1, W.consents.bio, W.m2],
    );
    const file = readFileSync(join(MIGRATIONS_DIR, '20261102000009_privacy_audit_consent.sql'), 'utf8');
    await h.pool.query(file);

    const secrets = await q(`select organization_id, old_data, new_data from public.audit_logs where table_name = 'edge_device_secrets' and row_id = $1`, [legacySecret]);
    expect(JSON.stringify(secrets)).not.toContain('LEGACY-RAW-SECRET');
    expect(JSON.stringify(secrets)).not.toContain('abababababababababababababababab');
    expect(secrets.every((r) => r.organization_id === W.orgA)).toBe(true);
    const legacy = secrets.filter((r) => r.new_data?.secret_fingerprint === 'abababababab');
    expect(legacy).toHaveLength(1);
    expect(Object.keys(legacy[0].old_data).sort()).toEqual(['created_at', 'edge_device_id', 'id', 'retired_at', 'secret_fingerprint']);
    // The legacy consent row is re-homed from the member's branch (A2), not left null.
    const legacyConsent = await one(`select organization_id, branch_id from public.audit_logs where table_name = 'consent_records' and row_id = $1 and old_data is null and new_data = jsonb_build_object('id', $1::text, 'member_id', $2::text)`, [W.consents.bio, W.m2]);
    expect(legacyConsent).toEqual({ organization_id: W.orgA, branch_id: W.branchA2 });
    const anySecret = await one(`select count(*)::int as n from public.audit_logs where old_data ? 'secret' or new_data ? 'secret'`);
    expect(anySecret.n).toBe(0);
  });
});
