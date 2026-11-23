/**
 * Live contract checks for the deployed Edge Functions against a temporary QA tenant:
 *   ingest-events (C4/C5), device-sync (C6), qr-token (C1).
 * Run: npm run test:live -w tests/live
 */
import { randomUUID } from 'node:crypto';
import { afterAll, beforeAll, describe, expect, inject, it } from 'vitest';
import { writeJsonArtifact } from './lib/artifacts.ts';
import { liveEnv } from './lib/env.ts';
import { getDeviceSync, postIngest, qrTokenGet, qrTokenPost, QR_TOKEN_RE } from './lib/functions.ts';
import { createQaTenant, destroyQaTenant, serviceClient, signIn, tenantIds, type Db, type QaTenant } from './lib/qa-tenant.ts';
import { makeBatch, makeEvent, type EventEnvelope } from './lib/signing.ts';

const REJECTION_CODES = ['branch_mismatch', 'no_consent', 'unknown_reference', 'invalid_value', 'invalid_state', 'internal'];

let tenant: QaTenant;
let db: Db;
let seq = 0;
const evidence: Record<string, unknown> = {};

function ctx() {
  return { deviceId: tenant.device.id, branchId: tenant.branchId };
}

function heartbeat(): EventEnvelope {
  return makeEvent(ctx(), ++seq, 'device.heartbeat', {
    software_version: 'qa-live',
    uptime_seconds: 1,
    gpu_utilization: null,
    gpu_memory_used_mb: null,
    cpu_utilization: null,
    queue_depth: 0,
    cameras_online: 3,
    cameras_total: 3,
    avg_pipeline_latency_ms: null,
  });
}

function cameraStatus(cameraId: string): EventEnvelope {
  return makeEvent(ctx(), ++seq, 'camera.status', {
    camera_id: cameraId,
    status: 'online',
    fps_measured: 20,
    resolution_w: 1920,
    resolution_h: 1080,
    error: null,
  });
}

async function count(table: string, column: string, values: string[]): Promise<number> {
  const { count: n, error } = await db.from(table).select('*', { count: 'exact', head: true }).in(column, values);
  if (error) throw new Error(`count ${table}: ${error.message}`);
  return n ?? 0;
}

beforeAll(async () => {
  tenant = await createQaTenant(`${inject('qaRunId')}-fn`);
  db = serviceClient();
  evidence.tenant = tenantIds(tenant);
});

afterAll(async () => {
  if (!tenant) return; // createQaTenant already cleaned up after itself
  let teardownError: unknown = null;
  try {
    evidence.teardown = { ok: true, report: await destroyQaTenant(tenant) };
  } catch (err) {
    evidence.teardown = { ok: false, error: (err as Error).message, report: (err as { report?: unknown }).report };
    teardownError = err;
  }
  writeJsonArtifact('functions.json', evidence);
  if (teardownError) throw teardownError;
});

describe('ingest-events', () => {
  it('rejects a wrong signature with 401', async () => {
    const batch = makeBatch(tenant.device.id, [heartbeat()]);
    const res = await postIngest(tenant.device.id, tenant.device.secret, batch, 'f'.repeat(64));
    expect(res.status).toBe(401);
    expect(res.body.code).toBe('unauthorized');
    // Nothing of that batch was recorded.
    expect(await count('ingest_batches', 'batch_id', [batch.batch_id])).toBe(0);
  });

  it('accepts a signed batch (200, every event accepted) and a replay has no new effects', async () => {
    const cams = [tenant.cameras.entrance, tenant.cameras.floorA, tenant.cameras.floorB];
    const events = [heartbeat(), ...cams.map(cameraStatus)];
    const batch = makeBatch(tenant.device.id, events);

    const ok = await postIngest(tenant.device.id, tenant.device.secret, batch);
    expect(ok.status, JSON.stringify(ok.body)).toBe(200);
    expect(ok.body.rejected ?? []).toEqual([]);
    expect(ok.body.accepted).toBe(events.length);

    const before = {
      batches: await count('ingest_batches', 'batch_id', [batch.batch_id]),
      log: await count('event_ingest_log', 'event_id', events.map((e) => e.event_id)),
      health: await count('device_health_logs', 'edge_device_id', [tenant.device.id]),
      cameraStatus: await count('camera_status_logs', 'camera_id', cams),
    };
    expect(before).toEqual({ batches: 1, log: events.length, health: 1, cameraStatus: 3 });

    const replay = await postIngest(tenant.device.id, tenant.device.secret, batch);
    expect(replay.status, JSON.stringify(replay.body)).toBe(200);
    expect(replay.body.accepted).toBe(0);
    expect(replay.body.rejected ?? []).toEqual([]);

    const after = {
      batches: await count('ingest_batches', 'batch_id', [batch.batch_id]),
      log: await count('event_ingest_log', 'event_id', events.map((e) => e.event_id)),
      health: await count('device_health_logs', 'edge_device_id', [tenant.device.id]),
      cameraStatus: await count('camera_status_logs', 'camera_id', cams),
    };
    expect(after).toEqual(before);
    evidence.ingest = { first: ok.body, replay: replay.body };
  });

  it('rejects a foreign-branch equipment id with a C4 code and a fixed reason', async () => {
    const good = heartbeat();
    const bad = makeEvent(ctx(), ++seq, 'equipment.usage.started', {
      usage_local_id: `qa-foreign-${randomUUID()}`,
      global_track_id: `qa-g-${randomUUID()}`,
      member_id: null,
      equipment_id: tenant.foreignEquipmentId,
      zone_id: tenant.zones.legPressA,
      camera_id: tenant.cameras.floorA,
      association_confidence: 0.9,
    });
    const batch = makeBatch(tenant.device.id, [good, bad]);
    const res = await postIngest(tenant.device.id, tenant.device.secret, batch);
    expect(res.status, JSON.stringify(res.body)).toBe(200);
    expect(res.body.accepted).toBe(1);
    const rejected = res.body.rejected ?? [];
    expect(rejected).toHaveLength(1);
    const r = rejected[0]!;
    expect(r.event_id).toBe(bad.event_id);
    // Every rejection carries a machine-readable code from the fixed set.
    for (const rej of rejected) expect(REJECTION_CODES).toContain(rej.code);
    // Equipment that exists but belongs to another branch of the same organization.
    expect(['branch_mismatch', 'unknown_reference']).toContain(r.code);
    // Reason is a fixed human-readable sentence: no raw SQL error text, no echoed ids.
    expect(typeof r.reason).toBe('string');
    expect(r.reason.length).toBeGreaterThan(5);
    expect(r.reason).not.toContain(tenant.foreignEquipmentId);
    expect(r.reason).not.toMatch(/SQLSTATE|ERROR:|violates|syntax error|relation "|function public\./i);

    // Raw detail stays server-side in event_ingest_log.error.
    const { data: log, error } = await db.from('event_ingest_log').select('status, error').eq('event_id', bad.event_id).single();
    expect(error).toBeNull();
    expect(log?.status).toBe('rejected');
    expect(String(log?.error ?? '')).not.toBe('');
    // Nothing was written for the rejected usage.
    expect(await count('equipment_usage_sessions', 'equipment_id', [tenant.foreignEquipmentId])).toBe(0);
    evidence.rejection = { code: r.code, reason: r.reason };
  });
});

describe('device-sync', () => {
  it('rejects a wrong signature with 401', async () => {
    const res = await getDeviceSync(tenant.device.id, tenant.device.secret, { badSignature: true });
    expect(res.status).toBe(401);
  });

  it('returns the signed snapshot of the QA branch without a members list (C6)', async () => {
    const res = await getDeviceSync(tenant.device.id, tenant.device.secret);
    expect(res.status, JSON.stringify(res.body).slice(0, 500)).toBe(200);
    const snap = res.body as Record<string, unknown>;

    for (const key of ['generated_at', 'device_id', 'branch_id', 'cameras', 'zones', 'equipment', 'exercises', 'consents', 'revoked_template_refs', 'active_template_refs']) {
      expect(snap, `snapshot key ${key}`).toHaveProperty(key);
    }
    expect(snap).not.toHaveProperty('members');
    expect(snap.device_id).toBe(tenant.device.id);
    expect(snap.branch_id).toBe(tenant.branchId);

    const cameras = snap.cameras as Array<{ id: string; zones?: Array<{ id: string }> }>;
    expect(cameras.map((c) => c.id)).toEqual(expect.arrayContaining(Object.values(tenant.cameras)));

    const zoneIds = new Set<string>();
    for (const z of (Array.isArray(snap.zones) ? snap.zones : []) as Array<{ id?: string }>) if (z.id) zoneIds.add(z.id);
    for (const c of cameras) for (const z of c.zones ?? []) zoneIds.add(z.id);
    expect(Array.isArray(snap.zones)).toBe(true);
    expect([...zoneIds]).toEqual(expect.arrayContaining(Object.values(tenant.zones)));

    const equipmentIds = (snap.equipment as Array<{ id: string }>).map((e) => e.id);
    expect(equipmentIds).toEqual(expect.arrayContaining(Object.values(tenant.equipment)));
    expect(equipmentIds).not.toContain(tenant.foreignEquipmentId);

    const exerciseIds = (snap.exercises as Array<{ id: string }>).map((e) => e.id);
    expect(exerciseIds).toEqual(expect.arrayContaining([tenant.exercises.legPress, tenant.exercises.latPulldown]));

    expect(Array.isArray(snap.consents)).toBe(true);
    const consents = (snap.consents as Array<{ member_id: string; consent_type: string; consent_record_id: string; expires_at: string | null }>).filter(
      (c) => c.member_id === tenant.member.id,
    );
    expect(consents.map((c) => [c.consent_type, c.consent_record_id]).sort()).toEqual(
      [
        ['biometric_face_identification', tenant.consents.biometric],
        ['cctv_workout_tracking', tenant.consents.cctv],
      ].sort(),
    );
    for (const c of snap.consents as Array<{ consent_type: string }>) {
      expect(['biometric_face_identification', 'cctv_workout_tracking']).toContain(c.consent_type);
    }
    for (const c of consents) expect(c).toHaveProperty('expires_at');

    // No personal data of the member in the device snapshot.
    const raw = JSON.stringify(snap);
    expect(raw).not.toContain(tenant.member.fullName);
    expect(raw.toUpperCase()).not.toContain(tenant.member.code.toUpperCase());
    evidence.deviceSync = { keys: Object.keys(snap).sort(), cameras: cameras.length, zones: zoneIds.size, consentsForMember: consents.length };
  });
});

describe('qr-token', () => {
  let memberToken: string;
  let staffToken: string;
  let sessionId: string;

  beforeAll(async () => {
    memberToken = (await signIn(tenant.member)).accessToken;
    staffToken = (await signIn(tenant.staff)).accessToken;
  });

  it('rejects anonymous callers with 401', async () => {
    expect((await qrTokenGet(null, { omitApikey: true })).status).toBe(401);
    expect((await qrTokenGet(null)).status).toBe(401);
    // The anon key is a valid JWT but not a user.
    const anonAsBearer = await qrTokenGet(liveEnv().anonKey);
    expect(anonAsBearer.status).toBe(401);
  });

  it('issues a member token, refuses member check-in (403) and checks in as staff (C1)', async () => {
    const issued = await qrTokenGet(memberToken);
    expect(issued.status, JSON.stringify(issued.body)).toBe(200);
    const token = issued.body.token ?? '';
    const m = QR_TOKEN_RE.exec(token);
    expect(m, `token shape ${token.replace(/\.[^.]+$/, '.<mac>')}`).not.toBeNull();
    expect(m![1]).toBe(tenant.member.code.toUpperCase());
    expect(Math.abs(Number(m![2]) - Math.floor(Date.now() / 60_000))).toBeLessThanOrEqual(1);

    const asMember = await qrTokenPost(memberToken, token);
    expect(asMember.status, JSON.stringify(asMember.body)).toBe(403);
    const { data: none } = await db.from('attendance_sessions').select('id').eq('member_id', tenant.member.id);
    expect(none ?? []).toHaveLength(0);

    const asStaff = await qrTokenPost(staffToken, token);
    expect(asStaff.status, JSON.stringify(asStaff.body)).toBe(200);
    sessionId = asStaff.body.attendance_session_id ?? '';
    expect(sessionId).toMatch(/^[0-9a-f-]{36}$/);

    const { data: session, error } = await db
      .from('attendance_sessions')
      .select('id, member_id, branch_id, status, check_in_method, checked_in_by')
      .eq('id', sessionId)
      .single();
    expect(error).toBeNull();
    expect(session).toMatchObject({
      member_id: tenant.member.id,
      branch_id: tenant.branchId,
      status: 'active',
      check_in_method: 'qr',
      checked_in_by: tenant.staff.userId,
    });
    evidence.qr = { sessionId, firstPost: asStaff.status };
  });

  it('a second staff check-in while the session is active is a conflict and creates nothing', async () => {
    expect(sessionId).toBeTruthy();
    const issued = await qrTokenGet(memberToken);
    expect(issued.status).toBe(200);
    const again = await qrTokenPost(staffToken, issued.body.token ?? '');
    // C1: same behaviour as staff_check_in for an existing active session. Accept an explicit
    // conflict (409 / code 'conflict') or an idempotent 200 that returns the SAME session.
    const isConflict = again.status === 409 || again.body.code === 'conflict';
    const isIdempotent = again.status === 200 && again.body.attendance_session_id === sessionId;
    expect(isConflict || isIdempotent, `second POST → ${again.status} ${JSON.stringify(again.body)}`).toBe(true);
    const { data: active } = await db.from('attendance_sessions').select('id').eq('member_id', tenant.member.id).eq('status', 'active');
    expect((active ?? []).map((r) => r.id)).toEqual([sessionId]);
    evidence.qrSecondPost = { status: again.status, code: again.body.code ?? null, outcome: isConflict ? 'conflict' : 'idempotent' };
  });
});

describe('retired device (C5)', () => {
  it('ingest-events answers 403 device_retired and records nothing', async () => {
    const { error: upErr } = await db.from('edge_devices').update({ status: 'retired' }).eq('id', tenant.device.id);
    expect(upErr).toBeNull();
    try {
      const batch = makeBatch(tenant.device.id, [heartbeat()]);
      const res = await postIngest(tenant.device.id, tenant.device.secret, batch);
      expect(res.status, JSON.stringify(res.body)).toBe(403);
      expect(res.body.code).toBe('device_retired');
      expect(await count('event_ingest_log', 'event_id', batch.events.map((e) => e.event_id))).toBe(0);
      evidence.retired = { status: res.status, code: res.body.code };
    } finally {
      const { error } = await db.from('edge_devices').update({ status: 'active' }).eq('id', tenant.device.id);
      expect(error).toBeNull();
    }
  });
});
