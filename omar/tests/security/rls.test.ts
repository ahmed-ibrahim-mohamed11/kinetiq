/**
 * Cross-member and cross-tenant authorization tests against a REAL Supabase
 * instance (local `supabase start` or a staging project).
 *
 * Run:  RUN_RLS_TESTS=1 SUPABASE_URL=http://127.0.0.1:54321 SUPABASE_ANON_KEY=... SUPABASE_SERVICE_ROLE_KEY=... npm run test:rls -w tests/security
 *
 * The suite creates two organizations with one branch, one staff user and one
 * member each, inserts workout data for both, then proves:
 *   1. a member reads only their own sessions/reports/consents (0 rows across the fence);
 *   2. staff of org A read nothing from org B;
 *   3. members cannot write cameras/equipment/members;
 *   4. anon reads nothing except the exercise catalog;
 *   5. edge_device_secrets is invisible to authenticated users entirely;
 *   6. ingest RPC is not executable by authenticated users.
 * It is skipped (not silently passed) when RUN_RLS_TESTS is not set.
 */
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { createClient, type SupabaseClient } from '@supabase/supabase-js';

const RUN = process.env.RUN_RLS_TESTS === '1';
const URL = process.env.SUPABASE_URL ?? '';
const ANON = process.env.SUPABASE_ANON_KEY ?? '';
const SERVICE = process.env.SUPABASE_SERVICE_ROLE_KEY ?? '';

const d = RUN ? describe : describe.skip;

interface Tenant {
  orgId: string;
  branchId: string;
  staff: SupabaseClient;
  member: SupabaseClient;
  memberId: string;
  sessionId: string;
  userIds: string[];
}

/**
 * Tear a tenant down in FK order. Branch-scoped tables use ON DELETE RESTRICT on purpose
 * (deleting a branch must never silently erase attendance history), so the org row cannot
 * be deleted first. Auth users are removed through the admin API.
 */
async function destroyTenant(admin: SupabaseClient, t: Tenant): Promise<void> {
  await admin.from('attendance_sessions').delete().eq('branch_id', t.branchId).throwOnError();
  await admin.from('members').delete().eq('organization_id', t.orgId).throwOnError();
  for (const table of ['edge_devices', 'cameras', 'equipment'] as const) {
    await admin.from(table).delete().eq('branch_id', t.branchId).throwOnError();
  }
  await admin.from('organizations').delete().eq('id', t.orgId).throwOnError();
  for (const id of t.userIds) {
    const { error } = await admin.auth.admin.deleteUser(id);
    if (error) throw error;
  }
}

const stamp = Date.now().toString(36);

async function signUp(admin: SupabaseClient, email: string): Promise<{ id: string; client: SupabaseClient }> {
  const password = `Pw-${stamp}-${Math.random().toString(36).slice(2)}!`;
  const { data, error } = await admin.auth.admin.createUser({ email, password, email_confirm: true });
  if (error) throw error;
  const client = createClient(URL, ANON, { auth: { persistSession: false } });
  const { error: signInErr } = await client.auth.signInWithPassword({ email, password });
  if (signInErr) throw signInErr;
  return { id: data.user.id, client };
}

async function makeTenant(admin: SupabaseClient, tag: string): Promise<Tenant> {
  const { data: org } = await admin.from('organizations').insert({ name: `RLS ${tag} ${stamp}`, slug: `rls-${tag}-${stamp}` }).select().single().throwOnError();
  const { data: branch } = await admin.from('branches').insert({ organization_id: org!.id, name: 'Main' }).select().single().throwOnError();
  const staff = await signUp(admin, `staff-${tag}-${stamp}@example.test`);
  await admin.from('staff_roles').insert({ user_id: staff.id, organization_id: org!.id, branch_id: null, role: 'admin' }).throwOnError();
  const member = await signUp(admin, `member-${tag}-${stamp}@example.test`);
  const { data: m } = await admin
    .from('members')
    .insert({ organization_id: org!.id, branch_id: branch!.id, user_id: member.id, member_code: `RLS-${tag.toUpperCase()}-${stamp.toUpperCase().replace(/[01IO]/g, 'X')}`, full_name: `Member ${tag}` })
    .select()
    .single()
    .throwOnError();
  await admin.from('consent_records').insert([
    { member_id: m!.id, consent_type: 'cctv_workout_tracking', policy_version: 'test' },
    { member_id: m!.id, consent_type: 'data_processing', policy_version: 'test' },
  ]).throwOnError();
  const { data: s } = await admin
    .from('attendance_sessions')
    .insert({ member_id: m!.id, branch_id: branch!.id, check_in_at: new Date().toISOString(), check_in_method: 'qr', status: 'completed', check_out_at: new Date().toISOString() })
    .select()
    .single()
    .throwOnError();
  await admin.from('workout_reports').insert({ attendance_session_id: s!.id, member_id: m!.id, summary: { schema_version: 1 } }).throwOnError();
  await admin.from('edge_devices').insert({ branch_id: branch!.id, name: `dev-${tag}` }).throwOnError();
  await admin.from('cameras').insert({ branch_id: branch!.id, name: `cam-${tag}`, stream_url: 'rtsp://192.0.2.1/x' }).throwOnError();
  return { orgId: org!.id, branchId: branch!.id, staff: staff.client, member: member.client, memberId: m!.id, sessionId: s!.id, userIds: [staff.id, member.id] };
}

d('Row Level Security', () => {
  let admin: SupabaseClient;
  let A: Tenant;
  let B: Tenant;

  beforeAll(async () => {
    admin = createClient(URL, SERVICE, { auth: { persistSession: false } });
    A = await makeTenant(admin, 'a');
    B = await makeTenant(admin, 'b');
  }, 60_000);

  afterAll(async () => {
    if (!admin) return;
    // Fails loudly: leftover fixtures in a shared project are a real problem, not a warning.
    for (const t of [A, B]) if (t) await destroyTenant(admin, t);
  }, 60_000);

  it('member sees only their own sessions and reports', async () => {
    const { data: sessions } = await A.member.from('attendance_sessions').select('id');
    expect(sessions?.map((s) => s.id)).toEqual([A.sessionId]);
    const { data: reports } = await A.member.from('workout_reports').select('member_id');
    expect(reports?.every((r) => r.member_id === A.memberId)).toBe(true);
    expect(reports?.length).toBe(1);
    const { data: others } = await A.member.from('members').select('id');
    expect(others?.map((m) => m.id)).toEqual([A.memberId]);
  });

  it('member cannot read other members consents', async () => {
    const { data } = await A.member.from('consent_records').select('member_id');
    expect(data?.every((c) => c.member_id === A.memberId)).toBe(true);
  });

  it('staff of org A read nothing from org B', async () => {
    const { data: members } = await A.staff.from('members').select('organization_id');
    expect(members?.length).toBeGreaterThan(0);
    expect(members?.every((m) => m.organization_id === A.orgId)).toBe(true);
    const { data: cams } = await A.staff.from('cameras').select('branch_id');
    expect(cams?.every((c) => c.branch_id === A.branchId)).toBe(true);
    const { data: sessions } = await A.staff.from('attendance_sessions').select('branch_id');
    expect(sessions?.every((s) => s.branch_id === A.branchId)).toBe(true);
    const { data: orgs } = await A.staff.from('organizations').select('id');
    expect(orgs?.map((o) => o.id)).toEqual([A.orgId]);
  });

  it('staff cannot insert into another org branch', async () => {
    const { error } = await A.staff.from('equipment').insert({ branch_id: B.branchId, name: 'intruder', category: 'machine' });
    expect(error).not.toBeNull();
  });

  it('members cannot write infrastructure or other members', async () => {
    const cam = await A.member.from('cameras').insert({ branch_id: A.branchId, name: 'hack', stream_url: 'rtsp://192.0.2.9/x' });
    expect(cam.error).not.toBeNull();
    const mem = await A.member.from('members').update({ full_name: 'Renamed' }).eq('id', A.memberId);
    // update is denied by policy → zero rows affected and no leak
    expect(mem.error ?? null).toBeNull();
    const { data } = await admin.from('members').select('full_name').eq('id', A.memberId).single();
    expect(data?.full_name).toBe('Member a');
  });

  it('members cannot see cameras or biometric infrastructure at all', async () => {
    const { data: cams } = await A.member.from('cameras').select('id');
    expect(cams).toEqual([]);
    const { data: devices } = await A.member.from('edge_devices').select('id');
    expect(devices).toEqual([]);
  });

  it('edge_device_secrets is invisible to any authenticated user', async () => {
    const r1 = await A.staff.from('edge_device_secrets').select('*');
    expect(r1.data ?? []).toEqual([]);
    const r2 = await A.member.from('edge_device_secrets').select('*');
    expect(r2.data ?? []).toEqual([]);
  });

  it('privileged RPCs are not executable by authenticated users', async () => {
    const r = await A.staff.rpc('ingest_events', { p_device_id: A.orgId, p_batch_id: A.orgId, p_sent_at: new Date().toISOString(), p_events: [] });
    expect(r.error).not.toBeNull();
    const f = await A.member.rpc('finalize_attendance_session', { p_session_id: A.sessionId });
    expect(f.error).not.toBeNull();
  });

  it('anon reads only the exercise catalog', async () => {
    const anon = createClient(URL, ANON, { auth: { persistSession: false } });
    const { data: catalog, error } = await anon.from('exercise_catalog').select('slug').limit(1);
    expect(error).toBeNull();
    expect(Array.isArray(catalog)).toBe(true);
    const { data: members, error: mErr } = await anon.from('members').select('id');
    expect(mErr !== null || (members ?? []).length === 0).toBe(true);
  });

  it('shared report token resolves without auth but exposes no ids', async () => {
    const { data: rep } = await admin.from('workout_reports').select('share_token').eq('member_id', A.memberId).single();
    const anon = createClient(URL, ANON, { auth: { persistSession: false } });
    const { data, error } = await anon.rpc('get_shared_report', { p_token: rep!.share_token });
    expect(error).toBeNull();
    expect(data).toMatchObject({ member_name: 'Member a' });
    expect(JSON.stringify(data)).not.toContain(A.memberId);
    const miss = await anon.rpc('get_shared_report', { p_token: 'nope' });
    expect(miss.data).toBeNull();
  });
});

describe('RLS suite wiring', () => {
  it('is explicit about being skipped without a database', () => {
    if (!RUN) console.warn('RLS tests skipped: set RUN_RLS_TESTS=1 with SUPABASE_URL/ANON/SERVICE keys to run against a real instance');
    expect(true).toBe(true);
  });
});
