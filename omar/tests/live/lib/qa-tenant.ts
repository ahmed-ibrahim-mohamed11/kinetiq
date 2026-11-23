/**
 * Temporary, clearly-labelled QA tenant on the hosted project.
 *
 * createQaTenant() builds a complete, isolated gym with the service-role client: organization,
 * branch (+ a second "foreign" branch used for branch-mismatch tests), an active edge device with
 * a fresh random secret, three cameras on TEST-NET addresses, equipment, zones (the leg press is
 * covered by BOTH floor cameras so cross-camera rep dedup is exercised), a consenting member and
 * two temporary auth users (front-desk staff and the member).
 *
 * destroyQaTenant() removes everything the tenant and the tests created, in FK order, and then
 * asserts that nothing of it remains. It refuses to touch anything that is not a QA tenant
 * (slug prefix + fixed name) and only deletes auth users whose e-mail is on the QA domain.
 *
 * Secrets (device secret, passwords) live only in memory on the returned object.
 */
import { createHash, randomBytes, randomInt } from 'node:crypto';
import { createClient, type SupabaseClient } from '@supabase/supabase-js';
import { liveEnv, registerSecret } from './env.ts';

export const QA_ORG_NAME = 'QA TEST tenant (not a real gym)';
export const QA_SLUG_PREFIX = 'qa-smart-gym-';
export const QA_EMAIL_DOMAIN = 'qa.smartgym.test';
const MEMBER_CODE_ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789';

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export type Db = SupabaseClient<any, 'public', any>;

export interface QaUser {
  userId: string;
  email: string;
  password: string;
}

export interface QaTenant {
  runId: string;
  orgId: string;
  orgSlug: string;
  branchId: string;
  foreignBranchId: string;
  foreignEquipmentId: string;
  device: { id: string; secret: string; secretRowId: string };
  cameras: { entrance: string; floorA: string; floorB: string };
  cameraNames: { entrance: string; floorA: string; floorB: string };
  equipment: { legPress: string; latPulldown: string };
  zones: { entrance: string; latPulldownA: string; legPressA: string; legPressB: string };
  exercises: { legPress: string; latPulldown: string };
  member: QaUser & { id: string; code: string; fullName: string; firstName: string };
  staff: QaUser;
  consents: { biometric: string; cctv: string; dataProcessing: string };
}

/** Ids only — safe to write to artifacts. */
export function tenantIds(t: QaTenant): Record<string, unknown> {
  return {
    runId: t.runId,
    orgId: t.orgId,
    orgSlug: t.orgSlug,
    branchId: t.branchId,
    foreignBranchId: t.foreignBranchId,
    deviceId: t.device.id,
    cameras: t.cameras,
    equipment: t.equipment,
    zones: t.zones,
    memberId: t.member.id,
    memberCode: t.member.code,
    memberUserId: t.member.userId,
    staffUserId: t.staff.userId,
    consents: t.consents,
  };
}

export function serviceClient(): Db {
  const env = liveEnv();
  return createClient(env.supabaseUrl, env.serviceRoleKey, {
    auth: { persistSession: false, autoRefreshToken: false, detectSessionInUrl: false },
  });
}

export function anonClient(): Db {
  const env = liveEnv();
  return createClient(env.supabaseUrl, env.anonKey, {
    auth: { persistSession: false, autoRefreshToken: false, detectSessionInUrl: false },
  });
}

/** Sign a QA user in with the anon key; returns the user-scoped client and its access token. */
export async function signIn(user: QaUser): Promise<{ client: Db; accessToken: string }> {
  const client = anonClient();
  const { data, error } = await client.auth.signInWithPassword({ email: user.email, password: user.password });
  if (error || !data.session) throw new Error(`sign-in failed for ${user.email}: ${error?.message ?? 'no session'}`);
  registerSecret(data.session.access_token);
  registerSecret(data.session.refresh_token);
  return { client, accessToken: data.session.access_token };
}

/** Lower-case alnum run id; also used in the org slug and the QA e-mail addresses. */
export function newRunId(prefix = ''): string {
  const id = `${Date.now().toString(36)}${randomBytes(3).toString('hex')}`;
  return prefix ? `${prefix}-${id}` : id;
}

function memberCode(): string {
  let s = 'QA-';
  for (let i = 0; i < 6; i++) s += MEMBER_CODE_ALPHABET[randomInt(MEMBER_CODE_ALPHABET.length)];
  return s;
}

function strongPassword(): string {
  return `Qa1!${randomBytes(24).toString('base64url')}`;
}

function fail(what: string, error: { message: string; code?: string } | null): never {
  throw new Error(`${what}: ${error?.message ?? 'no data'}${error?.code ? ` (${error.code})` : ''}`);
}

async function insertOne<T extends Record<string, unknown>>(db: Db, table: string, row: T): Promise<string> {
  const { data, error } = await db.from(table).insert(row).select('id').single();
  if (error || !data) fail(`insert into ${table}`, error);
  return (data as { id: string }).id;
}

async function createAuthUser(db: Db, email: string, fullName: string): Promise<QaUser> {
  const password = strongPassword();
  registerSecret(password);
  const { data, error } = await db.auth.admin.createUser({
    email,
    password,
    email_confirm: true,
    user_metadata: { full_name: fullName, qa_test: true },
  });
  if (error || !data.user) fail(`auth.admin.createUser ${email}`, error);
  return { userId: data.user.id, email, password };
}

const ZONE_POLYGONS = {
  entrance: [[600, 200], [1300, 200], [1300, 1000], [600, 1000]],
  latPulldownA: [[100, 300], [700, 300], [700, 1000], [100, 1000]],
  legPressA: [[800, 300], [1500, 300], [1500, 1000], [800, 1000]],
  legPressB: [[200, 250], [900, 250], [900, 1050], [200, 1050]],
} as const;

export function zonePolygon(name: keyof typeof ZONE_POLYGONS): ReadonlyArray<readonly [number, number]> {
  return ZONE_POLYGONS[name] as unknown as ReadonlyArray<readonly [number, number]>;
}

/**
 * Create the tenant. On any failure the partially created tenant is destroyed before rethrowing.
 */
export async function createQaTenant(runId: string = newRunId()): Promise<QaTenant> {
  if (!/^[a-z0-9]+(-[a-z0-9]+)*$/.test(runId)) throw new Error(`invalid run id ${runId}`);
  const db = serviceClient();
  const orgSlug = `${QA_SLUG_PREFIX}${runId}`;
  const created: { orgId?: string; userIds: string[] } = { userIds: [] };

  try {
    const { data: catalog, error: catErr } = await db.from('exercise_catalog').select('id, slug').in('slug', ['leg-press', 'lat-pulldown']);
    if (catErr) fail('read exercise_catalog', catErr);
    const legPressEx = catalog?.find((r) => r.slug === 'leg-press')?.id as string | undefined;
    const latEx = catalog?.find((r) => r.slug === 'lat-pulldown')?.id as string | undefined;
    if (!legPressEx || !latEx) throw new Error('exercise_catalog is missing leg-press / lat-pulldown (migration 0007 not applied?)');

    const orgId = await insertOne(db, 'organizations', { name: QA_ORG_NAME, slug: orgSlug, country_code: 'EG' });
    created.orgId = orgId;
    const branchId = await insertOne(db, 'branches', {
      organization_id: orgId,
      name: `QA branch ${runId}`,
      timezone: 'Africa/Cairo',
      address: 'QA TEST DATA - not a real address',
    });
    const foreignBranchId = await insertOne(db, 'branches', {
      organization_id: orgId,
      name: `QA foreign branch ${runId}`,
      timezone: 'Africa/Cairo',
      address: 'QA TEST DATA - not a real address',
    });

    const deviceId = await insertOne(db, 'edge_devices', {
      branch_id: branchId,
      name: `qa-edge-${runId}`,
      status: 'active',
      software_version: 'qa-live',
    });
    const secret = randomBytes(32).toString('hex');
    registerSecret(secret);
    const secretRowId = await insertOne(db, 'edge_device_secrets', {
      edge_device_id: deviceId,
      secret_hash: createHash('sha256').update(secret, 'utf8').digest('hex'),
      secret,
    });

    const cameraNames = { entrance: 'QA-CAM-ENTRANCE (test)', floorA: 'QA-CAM-FLOOR-A (test)', floorB: 'QA-CAM-FLOOR-B (test)' };
    const camera = (name: string, purpose: string, host: number) =>
      insertOne(db, 'cameras', {
        branch_id: branchId,
        edge_device_id: deviceId,
        name,
        protocol: 'rtsp',
        purpose,
        stream_url: `rtsp://192.0.2.${host}:554/qa/${runId}`,
        resolution_w: 1920,
        resolution_h: 1080,
        fps: 20,
      });
    const cameras = {
      entrance: await camera(cameraNames.entrance, 'entrance', 20),
      floorA: await camera(cameraNames.floorA, 'floor', 21),
      floorB: await camera(cameraNames.floorB, 'floor', 22),
    };

    const equipment = {
      legPress: await insertOne(db, 'equipment', {
        branch_id: branchId,
        name: 'QA Leg Press',
        category: 'machine',
        position_x_m: 9,
        position_y_m: 4,
        default_exercise_id: legPressEx,
      }),
      latPulldown: await insertOne(db, 'equipment', {
        branch_id: branchId,
        name: 'QA Lat Pulldown',
        category: 'machine',
        position_x_m: 5,
        position_y_m: 4,
        default_exercise_id: latEx,
      }),
    };
    const foreignEquipmentId = await insertOne(db, 'equipment', {
      branch_id: foreignBranchId,
      name: 'QA Foreign Leg Press',
      category: 'machine',
      default_exercise_id: legPressEx,
    });

    const zones = {
      entrance: await insertOne(db, 'camera_zones', {
        camera_id: cameras.entrance,
        equipment_id: null,
        name: 'qa-entrance-door',
        zone_type: 'entrance',
        polygon: ZONE_POLYGONS.entrance,
      }),
      latPulldownA: await insertOne(db, 'camera_zones', {
        camera_id: cameras.floorA,
        equipment_id: equipment.latPulldown,
        name: 'qa-lat-pulldown',
        zone_type: 'equipment',
        polygon: ZONE_POLYGONS.latPulldownA,
      }),
      legPressA: await insertOne(db, 'camera_zones', {
        camera_id: cameras.floorA,
        equipment_id: equipment.legPress,
        name: 'qa-leg-press',
        zone_type: 'equipment',
        polygon: ZONE_POLYGONS.legPressA,
      }),
      legPressB: await insertOne(db, 'camera_zones', {
        camera_id: cameras.floorB,
        equipment_id: equipment.legPress,
        name: 'qa-leg-press-alt',
        zone_type: 'equipment',
        polygon: ZONE_POLYGONS.legPressB,
      }),
    };

    const firstName = 'QATester';
    const fullName = `${firstName} Member (not a real person)`;
    const staff = await createAuthUser(db, `qa-staff-${runId}@${QA_EMAIL_DOMAIN}`, 'QA Front Desk (test)');
    created.userIds.push(staff.userId);
    const memberUser = await createAuthUser(db, `qa-member-${runId}@${QA_EMAIL_DOMAIN}`, fullName);
    created.userIds.push(memberUser.userId);

    await insertOne(db, 'staff_roles', { user_id: staff.userId, organization_id: orgId, branch_id: branchId, role: 'front_desk' });

    const code = memberCode();
    const memberId = await insertOne(db, 'members', {
      organization_id: orgId,
      branch_id: branchId,
      user_id: memberUser.userId,
      member_code: code,
      full_name: fullName,
      status: 'active',
    });

    const consent = (type: string) =>
      insertOne(db, 'consent_records', { member_id: memberId, consent_type: type, status: 'granted', policy_version: 'qa-live-test' });
    const consents = {
      biometric: await consent('biometric_face_identification'),
      cctv: await consent('cctv_workout_tracking'),
      dataProcessing: await consent('data_processing'),
    };

    return {
      runId,
      orgId,
      orgSlug,
      branchId,
      foreignBranchId,
      foreignEquipmentId,
      device: { id: deviceId, secret, secretRowId },
      cameras,
      cameraNames,
      equipment,
      zones,
      exercises: { legPress: legPressEx, latPulldown: latEx },
      member: { ...memberUser, id: memberId, code, fullName, firstName },
      staff,
      consents,
    };
  } catch (err) {
    await destroyByOrg(db, created.orgId ?? null, created.userIds, orgSlug).catch((cleanupErr: unknown) => {
      console.error(`[qa-tenant] cleanup after failed create also failed: ${(cleanupErr as Error).message}`);
    });
    throw err;
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Teardown
// ─────────────────────────────────────────────────────────────────────────────

export interface DestroyReport {
  orgSlug: string | null;
  deleted: Record<string, number>;
  missingTables: string[];
  warnings: string[];
  residue: Record<string, number>;
}

/**
 * True only for "this table is not there" (PostgREST schema cache miss or undefined_table).
 * Every other error, including a missing column (42703 / PGRST204), propagates so a schema change
 * cannot silently hide residue.
 */
function isMissingTable(error: { code?: string; message?: string } | null): boolean {
  if (!error) return false;
  if (error.code === 'PGRST205' || error.code === '42P01') return true;
  return !error.code && /could not find the table/i.test(error.message ?? '');
}

type Filter = { column: string; values: string[] } | { column: string; eq: string };

async function selectIds(db: Db, table: string, filter: Filter, idColumn = 'id', report?: DestroyReport): Promise<string[]> {
  if ('values' in filter && filter.values.length === 0) return [];
  let q = db.from(table).select(idColumn);
  q = 'values' in filter ? q.in(filter.column, filter.values) : q.eq(filter.column, filter.eq);
  const { data, error } = await q.limit(10_000);
  if (error) {
    if (isMissingTable(error)) {
      report?.missingTables.push(table);
      return [];
    }
    throw new Error(`select ${table}.${idColumn}: ${error.message}`);
  }
  return [...new Set((data ?? []).map((r) => String((r as unknown as Record<string, unknown>)[idColumn])).filter((v) => v && v !== 'null'))];
}

async function deleteWhere(db: Db, report: DestroyReport, table: string, filter: Filter, opts: { tolerateError?: boolean } = {}): Promise<void> {
  if ('values' in filter && filter.values.length === 0) return;
  // Chunk long id lists to keep the PostgREST URL short.
  const chunks: Filter[] = 'values' in filter
    ? Array.from({ length: Math.ceil(filter.values.length / 80) }, (_, i) => ({ column: filter.column, values: filter.values.slice(i * 80, i * 80 + 80) }))
    : [filter];
  for (const f of chunks) {
    let q = db.from(table).delete({ count: 'exact' });
    q = 'values' in f ? q.in(f.column, f.values) : q.eq(f.column, f.eq);
    const { error, count } = await q;
    if (error) {
      if (isMissingTable(error)) {
        if (!report.missingTables.includes(table)) report.missingTables.push(table);
        return;
      }
      const msg = `delete ${table} by ${f.column}: ${error.message}${error.code ? ` (${error.code})` : ''}`;
      if (opts.tolerateError) {
        report.warnings.push(msg);
        return;
      }
      throw new Error(msg);
    }
    report.deleted[table] = (report.deleted[table] ?? 0) + (count ?? 0);
  }
}

async function countWhere(db: Db, table: string, filter: Filter): Promise<number> {
  if ('values' in filter && filter.values.length === 0) return 0;
  let q = db.from(table).select('*', { count: 'exact', head: true });
  q = 'values' in filter ? q.in(filter.column, filter.values.slice(0, 200)) : q.eq(filter.column, filter.eq);
  const { error, count } = await q;
  if (error) {
    if (isMissingTable(error)) return 0;
    throw new Error(`count ${table}: ${error.message}`);
  }
  return count ?? 0;
}

async function qaUserIds(db: Db, candidates: string[], report: DestroyReport): Promise<string[]> {
  const out: string[] = [];
  for (const id of new Set(candidates)) {
    const { data, error } = await db.auth.admin.getUserById(id);
    if (error || !data.user) continue; // already gone
    const email = data.user.email ?? '';
    if (email.toLowerCase().endsWith(`@${QA_EMAIL_DOMAIN}`)) out.push(id);
    else report.warnings.push(`refused to delete non-QA auth user ${id}`);
  }
  return out;
}

/** Find QA auth users of a run by e-mail (covers users created before they were linked to the tenant). */
async function qaUsersByRunId(db: Db, runId: string): Promise<string[]> {
  const out: string[] = [];
  const wanted = new Set([`qa-staff-${runId}@${QA_EMAIL_DOMAIN}`, `qa-member-${runId}@${QA_EMAIL_DOMAIN}`]);
  for (let page = 1; page <= 50; page++) {
    const { data, error } = await db.auth.admin.listUsers({ page, perPage: 1000 });
    if (error) break;
    for (const u of data.users) if (u.email && wanted.has(u.email.toLowerCase())) out.push(u.id);
    if (data.users.length < 1000) break;
  }
  return out;
}

async function destroyByOrg(db: Db, orgId: string | null, extraUserIds: string[], expectedSlug: string | null): Promise<DestroyReport> {
  const report: DestroyReport = { orgSlug: expectedSlug, deleted: {}, missingTables: [], warnings: [], residue: {} };

  if (orgId) {
    const { data: org, error } = await db.from('organizations').select('id, slug, name').eq('id', orgId).maybeSingle();
    if (error) throw new Error(`read organization: ${error.message}`);
    if (org) {
      const slug = String(org.slug);
      if (!slug.startsWith(QA_SLUG_PREFIX) || org.name !== QA_ORG_NAME) {
        throw new Error(`refusing to destroy organization ${orgId}: it is not a QA tenant (slug ${slug})`);
      }
      report.orgSlug = slug;
    }
  }
  const runId = report.orgSlug?.startsWith(QA_SLUG_PREFIX) ? report.orgSlug.slice(QA_SLUG_PREFIX.length) : null;

  // ── discover everything that belongs to the tenant ──
  const branchIds = orgId ? await selectIds(db, 'branches', { column: 'organization_id', eq: orgId }) : [];
  const deviceIds = await selectIds(db, 'edge_devices', { column: 'branch_id', values: branchIds });
  const cameraIds = [
    ...new Set([
      ...(await selectIds(db, 'cameras', { column: 'branch_id', values: branchIds })),
      ...(await selectIds(db, 'cameras', { column: 'edge_device_id', values: deviceIds })),
    ]),
  ];
  const equipmentIds = await selectIds(db, 'equipment', { column: 'branch_id', values: branchIds });
  const zoneIds = await selectIds(db, 'camera_zones', { column: 'camera_id', values: cameraIds });
  const memberIds = [
    ...new Set([
      ...(orgId ? await selectIds(db, 'members', { column: 'organization_id', eq: orgId }) : []),
      ...(await selectIds(db, 'members', { column: 'branch_id', values: branchIds })),
    ]),
  ];
  const memberUserIds = await selectIds(db, 'members', { column: 'id', values: memberIds }, 'user_id');
  const staffUserIds = orgId ? await selectIds(db, 'staff_roles', { column: 'organization_id', eq: orgId }, 'user_id') : [];
  const runUserIds = runId ? await qaUsersByRunId(db, runId) : [];
  const userIds = await qaUserIds(db, [...extraUserIds, ...memberUserIds, ...staffUserIds, ...runUserIds], report);

  const sessionIds = [
    ...new Set([
      ...(await selectIds(db, 'attendance_sessions', { column: 'member_id', values: memberIds })),
      ...(await selectIds(db, 'attendance_sessions', { column: 'branch_id', values: branchIds })),
    ]),
  ];
  const usageIds = [
    ...new Set([
      ...(await selectIds(db, 'equipment_usage_sessions', { column: 'edge_device_id', values: deviceIds })),
      ...(await selectIds(db, 'equipment_usage_sessions', { column: 'attendance_session_id', values: sessionIds })),
      ...(await selectIds(db, 'equipment_usage_sessions', { column: 'equipment_id', values: equipmentIds })),
    ]),
  ];
  const activityIds = [
    ...new Set([
      ...(await selectIds(db, 'exercise_activity_sessions', { column: 'edge_device_id', values: deviceIds })),
      ...(await selectIds(db, 'exercise_activity_sessions', { column: 'equipment_usage_session_id', values: usageIds })),
    ]),
  ];
  const setIds = [
    ...new Set([
      ...(await selectIds(db, 'exercise_sets', { column: 'edge_device_id', values: deviceIds })),
      ...(await selectIds(db, 'exercise_sets', { column: 'exercise_activity_session_id', values: activityIds })),
    ]),
  ];
  const reportIds = [
    ...new Set([
      ...(await selectIds(db, 'workout_reports', { column: 'member_id', values: memberIds })),
      ...(await selectIds(db, 'workout_reports', { column: 'attendance_session_id', values: sessionIds })),
    ]),
  ];
  const consentIds = await selectIds(db, 'consent_records', { column: 'member_id', values: memberIds });
  const enrollmentIds = [
    ...new Set([
      ...(await selectIds(db, 'biometric_enrollments', { column: 'member_id', values: memberIds })),
      ...(await selectIds(db, 'biometric_enrollments', { column: 'edge_device_id', values: deviceIds })),
    ]),
  ];
  const secretIds = await selectIds(db, 'edge_device_secrets', { column: 'edge_device_id', values: deviceIds });

  // ── delete in FK order ──
  const steps: Array<[string, Filter]> = [
    ['push_subscriptions', { column: 'user_id', values: userIds }],
    ['notification_jobs', { column: 'member_id', values: memberIds }],
    ['workout_reports', { column: 'id', values: reportIds }],
    ['repetition_events', { column: 'exercise_set_id', values: setIds }],
    ['exercise_sets', { column: 'id', values: setIds }],
    ['exercise_activity_sessions', { column: 'id', values: activityIds }],
    ['equipment_usage_sessions', { column: 'id', values: usageIds }],
    ['tracking_uncertain_segments', { column: 'edge_device_id', values: deviceIds }],
    ['tracking_uncertain_segments', { column: 'attendance_session_id', values: sessionIds }],
    ['person_tracks', { column: 'edge_device_id', values: deviceIds }],
    ['person_tracks', { column: 'attendance_session_id', values: sessionIds }],
    ['event_ingest_log', { column: 'edge_device_id', values: deviceIds }],
    ['ingest_batches', { column: 'edge_device_id', values: deviceIds }],
    ['device_health_logs', { column: 'edge_device_id', values: deviceIds }],
    ['camera_status_logs', { column: 'camera_id', values: cameraIds }],
    ['attendance_sessions', { column: 'id', values: sessionIds }],
    ['biometric_enrollments', { column: 'id', values: enrollmentIds }],
    ['consent_records', { column: 'id', values: consentIds }],
    ['members', { column: 'id', values: memberIds }],
    // 0009 copies every consent into consent_records_archive (no FK, 5-year retention) and leaves
    // biometric_revocations tombstones when consents, enrollments or members are deleted. Both
    // reference the QA member, so they are removed here and covered by the residue check below.
    ['consent_records_archive', { column: 'id', values: consentIds }],
    ['consent_records_archive', { column: 'member_id', values: memberIds }],
    // Also by organization: a teardown retried after the members are gone still finds them.
    ...(orgId ? [['consent_records_archive', { column: 'organization_id', eq: orgId }] as [string, Filter]] : []),
    ['biometric_revocations', { column: 'member_id', values: memberIds }],
    ['biometric_revocations', { column: 'edge_device_id', values: deviceIds }],
    ['edge_device_secrets', { column: 'edge_device_id', values: deviceIds }],
    ['camera_zones', { column: 'id', values: zoneIds }],
    ['cameras', { column: 'id', values: cameraIds }],
    ['equipment', { column: 'id', values: equipmentIds }],
    ['edge_devices', { column: 'id', values: deviceIds }],
  ];
  if (orgId) steps.push(['staff_roles', { column: 'organization_id', eq: orgId }]);
  steps.push(['branches', { column: 'id', values: branchIds }]);
  if (orgId) steps.push(['organizations', { column: 'id', eq: orgId }]);

  const errors: string[] = [];
  for (const [table, filter] of steps) {
    try {
      await deleteWhere(db, report, table, filter);
    } catch (err) {
      // Keep going: later steps may still succeed and the residue check reports what is left.
      errors.push((err as Error).message);
    }
  }

  for (const id of userIds) {
    const { error } = await db.auth.admin.deleteUser(id);
    if (error && !/not found/i.test(error.message)) errors.push(`auth.admin.deleteUser ${id}: ${error.message}`);
    else report.deleted['auth.users'] = (report.deleted['auth.users'] ?? 0) + 1;
  }

  // Audit rows are written by the deletes above, so they go last. If audit_logs is append-only by
  // design the failure is recorded as a warning, not as residue.
  const auditRowIds = [
    ...memberIds, ...consentIds, ...enrollmentIds, ...cameraIds, ...deviceIds, ...secretIds, ...reportIds, ...sessionIds,
  ];
  let auditDeletable = true;
  const before = report.warnings.length;
  if (orgId) await deleteWhere(db, report, 'audit_logs', { column: 'organization_id', eq: orgId }, { tolerateError: true });
  await deleteWhere(db, report, 'audit_logs', { column: 'row_id', values: auditRowIds }, { tolerateError: true });
  if (report.warnings.length > before) auditDeletable = false;

  // ── residue check ──
  const residueChecks: Array<[string, Filter]> = [...steps];
  if (auditDeletable) {
    if (orgId) residueChecks.push(['audit_logs', { column: 'organization_id', eq: orgId }]);
    residueChecks.push(['audit_logs', { column: 'row_id', values: auditRowIds }]);
  }
  for (const [table, filter] of residueChecks) {
    const n = await countWhere(db, table, filter);
    if (n > 0) report.residue[`${table}.${filter.column}`] = n;
  }
  for (const id of userIds) {
    const { data } = await db.auth.admin.getUserById(id);
    if (data?.user) report.residue[`auth.users:${id}`] = 1;
  }

  if (errors.length > 0 || Object.keys(report.residue).length > 0) {
    const detail = [...errors, ...Object.entries(report.residue).map(([k, v]) => `${k} still has ${v} row(s)`)].join('; ');
    throw Object.assign(new Error(`QA tenant ${report.orgSlug ?? orgId} was not fully removed: ${detail}`), { report });
  }
  return report;
}

/** Remove the tenant and everything the tests created for it; throws if anything remains. */
export async function destroyQaTenant(tenant: QaTenant | null | undefined): Promise<DestroyReport | null> {
  if (!tenant) return null;
  const db = serviceClient();
  return destroyByOrg(db, tenant.orgId, [tenant.staff.userId, tenant.member.userId], tenant.orgSlug);
}

/**
 * Safety net: destroy every QA tenant whose slug starts with `qa-smart-gym-<runPrefix>`
 * (tenants of this run that an aborted test file did not remove).
 */
export async function sweepQaTenants(runPrefix: string): Promise<DestroyReport[]> {
  if (!/^[a-z0-9-]{4,}$/.test(runPrefix)) throw new Error(`refusing to sweep with prefix "${runPrefix}"`);
  const db = serviceClient();
  const { data, error } = await db
    .from('organizations')
    .select('id, slug, name')
    .like('slug', `${QA_SLUG_PREFIX}${runPrefix}%`)
    .eq('name', QA_ORG_NAME);
  if (error) throw new Error(`sweep: ${error.message}`);
  const reports: DestroyReport[] = [];
  for (const org of data ?? []) reports.push(await destroyByOrg(db, String(org.id), [], String(org.slug)));
  return reports;
}
