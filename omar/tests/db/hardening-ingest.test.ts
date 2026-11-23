/**
 * Ingest hardening (migration 0008, contracts C4/C5) on a real database:
 * branch-scoped member/session resolution, server-side consent for CCTV-derived
 * facts, NULL-safe envelope checks, bounded text and timestamp windows, branch
 * validation of camera/zone references, stable rejection codes, infrastructure
 * errors aborting the batch, once-per-batch device updates, and the device
 * secret lifecycle (rotation overlap, retirement).
 */
import { randomUUID } from 'node:crypto';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { startHarness, type Harness } from './harness';

const DEVICE = 'd0000000-0000-4000-8000-000000000001';
const ORG = 'a0000000-0000-4000-8000-000000000001';
const BRANCH = 'b0000000-0000-4000-8000-000000000001';
const MEMBER = '33333333-3333-4333-8333-333333333333';
const CAM_ENTRANCE = 'c0000000-0000-4000-8000-000000000001';
const CAM_FLOOR = 'c0000000-0000-4000-8000-000000000002';
const LEG_PRESS_EQ = 'e0000000-0000-4000-8000-000000000002';
const LEG_PRESS_ZONE = 'f0000000-0000-4000-8000-000000000003';

const CODES = ['branch_mismatch', 'no_consent', 'unknown_reference', 'invalid_value', 'invalid_state', 'internal'];

type Ev = Record<string, any>;
interface Rejection { event_id: string; code: string; reason: string }
interface IngestResult { accepted: number; duplicates: number; rejected: Rejection[] }

let h: Harness;
let seq = 1000;
let legPressId: string;
// Foreign tenant
let foreignMember: string;
let foreignSession: string;
let foreignCamera: string;
let foreignZone: string;
// DEMO-branch members with specific consent situations
let noConsentMember: string; // no consent records at all
let withdrawMember: string; // CCTV consent granted, withdrawn mid-visit
let expiredMember: string; // biometric consent past expires_at

function ev(type: string, payload: Record<string, unknown>, over: Ev = {}): Ev {
  const now = new Date().toISOString();
  return {
    schema_version: '1',
    event_id: randomUUID(),
    device_id: DEVICE,
    branch_id: BRANCH,
    sequence: seq++,
    occurred_at: now,
    sent_at: now,
    event_type: type,
    payload,
    ...over,
  };
}

const heartbeat = (over: Ev = {}, payload: Ev = {}) =>
  ev('device.heartbeat', {
    software_version: '0.1.0', uptime_seconds: 1, gpu_utilization: null, gpu_memory_used_mb: null, cpu_utilization: 0.1,
    queue_depth: 0, cameras_online: 0, cameras_total: 0, avg_pipeline_latency_ms: null, ...payload,
  }, over);

const trackStarted = (gtid: string, member: string | null, camera = CAM_ENTRANCE) =>
  ev('track.started', { global_track_id: gtid, member_id: member, identity_confidence: 0.95, tracking_confidence: 0.9, first_camera_id: camera });

const usageStarted = (usage: string, gtid: string, member: string | null, over: Ev = {}) =>
  ev('equipment.usage.started', {
    usage_local_id: usage, global_track_id: gtid, member_id: member, equipment_id: LEG_PRESS_EQ, zone_id: LEG_PRESS_ZONE,
    camera_id: CAM_FLOOR, association_confidence: 0.9, ...over,
  });

const checkout = (member: string, method = 'qr') => ev('identity.checkout', { member_id: member, method, confidence: 1, camera_id: null });

async function ingest(events: Ev[], opts: { batchId?: string; device?: string; sentAt?: string } = {}): Promise<IngestResult> {
  const { rows } = await h.pool.query(`select public.ingest_events($1::uuid, $2::uuid, coalesce($3::timestamptz, now()), $4::jsonb) as r`, [
    opts.device ?? DEVICE,
    opts.batchId ?? randomUUID(),
    opts.sentAt ?? null,
    JSON.stringify(events),
  ]);
  return rows[0].r as IngestResult;
}

async function makeMember(code: string, name: string, branch = BRANCH, org = ORG): Promise<string> {
  const { rows } = await h.pool.query(
    `insert into public.members (organization_id, branch_id, member_code, full_name) values ($1, $2, $3, $4) returning id`,
    [org, branch, code, name],
  );
  return rows[0].id;
}

async function activeSession(member: string, branch = BRANCH): Promise<string> {
  const { rows } = await h.pool.query(
    `insert into public.attendance_sessions (member_id, branch_id, check_in_at, check_in_method) values ($1, $2, now() - interval '10 minutes', 'qr') returning id`,
    [member, branch],
  );
  return rows[0].id;
}

async function sessionStatus(id: string): Promise<string> {
  const { rows } = await h.pool.query(`select status from public.attendance_sessions where id = $1`, [id]);
  return rows[0].status;
}

function expectRejected(r: IngestResult, code: string, n = 1) {
  expect(r.accepted, JSON.stringify(r)).toBe(0);
  expect(r.rejected).toHaveLength(n);
  for (const x of r.rejected) {
    expect(Object.keys(x).sort()).toEqual(['code', 'event_id', 'reason']);
    expect(x.code).toBe(code);
  }
}

beforeAll(async () => {
  h = await startHarness();
  legPressId = (await h.pool.query(`select id from public.exercise_catalog where slug = 'leg-press'`)).rows[0].id;

  const org = await h.pool.query(`insert into public.organizations (name, slug) values ('Foreign gym', 'foreign-gym') returning id`);
  const br = await h.pool.query(`insert into public.branches (organization_id, name) values ($1, 'Foreign branch') returning id`, [org.rows[0].id]);
  foreignMember = await makeMember('FRGN-M2A7', 'Foreign Member', br.rows[0].id, org.rows[0].id);
  await h.pool.query(
    `insert into public.consent_records (member_id, consent_type, policy_version) values ($1, 'cctv_workout_tracking', 't'), ($1, 'biometric_face_identification', 't')`,
    [foreignMember],
  );
  foreignSession = await activeSession(foreignMember, br.rows[0].id);
  foreignCamera = (await h.pool.query(`insert into public.cameras (branch_id, name, stream_url) values ($1, 'foreign-cam', 'rtsp://192.0.2.99/x') returning id`, [br.rows[0].id])).rows[0].id;
  foreignZone = (await h.pool.query(
    `insert into public.camera_zones (camera_id, name, zone_type, polygon) values ($1, 'door', 'entrance', '[[0,0],[1,0],[1,1]]') returning id`,
    [foreignCamera],
  )).rows[0].id;

  noConsentMember = await makeMember('HARD-NC-M2A7', 'No Consent Member');
  withdrawMember = await makeMember('HARD-WD-M2A7', 'Withdraw Member');
  await h.pool.query(`insert into public.consent_records (member_id, consent_type, policy_version) values ($1, 'cctv_workout_tracking', 't')`, [withdrawMember]);
  expiredMember = await makeMember('HARD-EX-M2A7', 'Expired Consent Member');
  await h.pool.query(
    `insert into public.consent_records (member_id, consent_type, policy_version, granted_at, expires_at) values ($1, 'biometric_face_identification', 't', now() - interval '2 years', now() - interval '1 day')`,
    [expiredMember],
  );

  // Activate the device once so later status assertions start from 'active'.
  const r = await ingest([heartbeat()]);
  expect(r.rejected).toEqual([]);
});
afterAll(async () => {
  await h?.stop();
});

describe('R1-1 branch-scoped member/session resolution', () => {
  it('rejects checkout, track.started and usage.started for a member of another tenant with no side effects', async () => {
    const r = await ingest([checkout(foreignMember), trackStarted('g-foreign-1', foreignMember), usageStarted('u-foreign-1', 'g-none', foreignMember)]);
    expectRejected(r, 'branch_mismatch', 3);
    expect(await sessionStatus(foreignSession)).toBe('active');
    const tracks = await h.pool.query(`select count(*)::int as n from public.person_tracks where member_id = $1 or global_track_id = 'g-foreign-1'`, [foreignMember]);
    expect(tracks.rows[0].n).toBe(0);
    const usage = await h.pool.query(`select count(*)::int as n from public.equipment_usage_sessions where usage_local_id = 'u-foreign-1'`);
    expect(usage.rows[0].n).toBe(0);
    const reports = await h.pool.query(`select count(*)::int as n from public.workout_reports where member_id = $1`, [foreignMember]);
    expect(reports.rows[0].n).toBe(0);
  });
});

describe('R1-2 / R1-21 CCTV consent is enforced for every CCTV-derived fact', () => {
  it('does not bind tracks or usage to a staff/QR session of a member without CCTV consent', async () => {
    const session = await activeSession(noConsentMember);
    const r = await ingest([trackStarted('g-nc-1', noConsentMember), usageStarted('u-nc-1', 'g-nc-none', noConsentMember)]);
    expectRejected(r, 'no_consent', 2);
    const bound = await h.pool.query(
      `select (select count(*) from public.person_tracks where attendance_session_id = $1)::int as tracks,
              (select count(*) from public.equipment_usage_sessions where attendance_session_id = $1)::int as usage`,
      [session],
    );
    expect(bound.rows[0]).toEqual({ tracks: 0, usage: 0 });
  });

  it('stops attaching activity, sets and reps once tracking consent is withdrawn mid-visit', async () => {
    const session = await activeSession(withdrawMember);
    const ok = await ingest([
      trackStarted('g-wd-1', withdrawMember),
      usageStarted('u-wd-1', 'g-wd-1', withdrawMember),
      ev('exercise.activity.started', { activity_local_id: 'a-wd-1', usage_local_id: 'u-wd-1', exercise_id: legPressId, classification_confidence: 0.9, classifier_model_version_id: null }),
      ev('set.started', { set_local_id: 's-wd-1', activity_local_id: 'a-wd-1', set_index: 1, rest_before_seconds: null }),
    ]);
    expect(ok.rejected, JSON.stringify(ok.rejected)).toEqual([]);
    const usage = await h.pool.query(`select attendance_session_id from public.equipment_usage_sessions where usage_local_id = 'u-wd-1'`);
    expect(usage.rows[0].attendance_session_id).toBe(session);

    await h.pool.query(
      `update public.consent_records set status = 'withdrawn', withdrawn_at = now() where member_id = $1 and consent_type = 'cctv_workout_tracking'`,
      [withdrawMember],
    );
    const r = await ingest([
      ev('rep.counted', { set_local_id: 's-wd-1', rep_index: 1, camera_id: CAM_FLOOR, confidence: 0.9, quality_label: null }),
      ev('set.completed', { set_local_id: 's-wd-1', rep_count: 5, confidence: 0.9, verified: true }),
      ev('exercise.activity.started', { activity_local_id: 'a-wd-2', usage_local_id: 'u-wd-1', exercise_id: legPressId, classification_confidence: 0.9, classifier_model_version_id: null }),
      ev('tracking.uncertain', { global_track_id: 'g-wd-1', member_id: withdrawMember, from: new Date(Date.now() - 10_000).toISOString(), to: new Date().toISOString(), reason: 'occlusion' }),
    ]);
    expectRejected(r, 'no_consent', 4);
    const reps = await h.pool.query(`select s.rep_count, (select count(*)::int from public.repetition_events e where e.exercise_set_id = s.id) as n from public.exercise_sets s where set_local_id = 's-wd-1'`);
    expect(reps.rows[0]).toEqual({ rep_count: 0, n: 0 });
  });
});

describe('R1-3 face checkout requires biometric consent', () => {
  it('rejects a face checkout for a member without biometric consent and leaves the session open; QR checkout still works', async () => {
    const session = await h.pool.query(`select id from public.attendance_sessions where member_id = $1 and status = 'active'`, [noConsentMember]);
    const r = await ingest([checkout(noConsentMember, 'face')]);
    expectRejected(r, 'no_consent');
    expect(await sessionStatus(session.rows[0].id)).toBe('active');
    const ok = await ingest([checkout(noConsentMember, 'qr')]);
    expect(ok.rejected).toEqual([]);
    expect(await sessionStatus(session.rows[0].id)).toBe('completed');
  });

  it('rejects a checkout that precedes the check-in as invalid_state', async () => {
    const session = await activeSession(MEMBER);
    const r = await ingest([{ ...checkout(MEMBER), occurred_at: new Date(Date.now() - 60 * 60_000).toISOString() }]);
    expectRejected(r, 'invalid_state');
    expect(await sessionStatus(session)).toBe('active');
    await ingest([checkout(MEMBER)]);
    expect(await sessionStatus(session)).toBe('completed');
  });
});

describe('R1-9 biometric enrollment needs an unexpired consent', () => {
  it('rejects biometric.enrolled against an expired consent record', async () => {
    const consent = await h.pool.query(`select id from public.consent_records where member_id = $1`, [expiredMember]);
    const r = await ingest([
      ev('biometric.enrolled', { member_id: expiredMember, consent_record_id: consent.rows[0].id, template_ref: 'tpl-expired-0001', key_id: 'k-test', model_name: 'm', quality: null }),
    ]);
    expectRejected(r, 'no_consent');
    const n = await h.pool.query(`select count(*)::int as n from public.biometric_enrollments where member_id = $1`, [expiredMember]);
    expect(n.rows[0].n).toBe(0);
  });
});

describe('R1-10 NULL-safe envelope checks, bounded text, timestamp window', () => {
  it('rejects events whose branch_id, schema_version or device_id is missing', async () => {
    const noBranch = heartbeat();
    delete noBranch.branch_id;
    const noSchema = heartbeat();
    delete noSchema.schema_version;
    const noDevice = heartbeat();
    delete noDevice.device_id;
    const r = await ingest([noBranch, noSchema, noDevice]);
    expect(r.accepted).toBe(0);
    expect(r.rejected.map((x) => [x.event_id, x.code])).toEqual([
      [noBranch.event_id, 'branch_mismatch'],
      [noSchema.event_id, 'invalid_value'],
      [noDevice.event_id, 'invalid_value'],
    ]);
  });

  it('rejects over-long text fields', async () => {
    const r = await ingest([
      heartbeat({}, { software_version: 'x'.repeat(5000) }),
      ev('camera.status', { camera_id: CAM_FLOOR, status: 'online', fps_measured: 10, resolution_w: null, resolution_h: null, error: 'e'.repeat(2000) }),
      trackStarted('g'.repeat(200), null),
    ]);
    expectRejected(r, 'invalid_value', 3);
    const sw = await h.pool.query(`select count(*)::int as n from public.device_health_logs where length(software_version) > 100`);
    expect(sw.rows[0].n).toBe(0);
  });

  it('rejects occurred_at outside sent_at - 7 days .. sent_at + 1 hour', async () => {
    const future = new Date(Date.now() + 2 * 3600_000).toISOString();
    const old = new Date(Date.now() - 8 * 86400_000).toISOString();
    const r = await ingest([
      ev('identity.checkin', { member_id: MEMBER, method: 'qr', confidence: 1, camera_id: null, global_track_id: null, consent_record_id: null, presentation_attack_score: null }, { occurred_at: future }),
      heartbeat({ occurred_at: old }),
    ]);
    expectRejected(r, 'invalid_value', 2);
    const s = await h.pool.query(`select count(*)::int as n from public.attendance_sessions where check_in_at > now()`);
    expect(s.rows[0].n).toBe(0);
  });

  it('rejects a batch whose sent_at is far in the future', async () => {
    await expect(ingest([heartbeat()], { sentAt: new Date(Date.now() + 3 * 3600_000).toISOString() })).rejects.toThrow(/sent_at is in the future/);
  });
});

describe('R1-11 camera and zone references must belong to the device branch', () => {
  it('rejects foreign and unknown camera/zone ids with the same code and reason', async () => {
    const r = await ingest([
      trackStarted('g-cam-foreign', null, foreignCamera),
      trackStarted('g-cam-unknown', null, randomUUID()),
      usageStarted('u-zone-foreign', 'g-none', null, { zone_id: foreignZone }),
      usageStarted('u-cam-foreign', 'g-none', null, { camera_id: foreignCamera }),
    ]);
    expectRejected(r, 'branch_mismatch', 4);
    expect(new Set(r.rejected.map((x) => x.reason)).size).toBe(1);
  });
});

describe('R1-15 / C4 rejection codes and fixed reasons', () => {
  it('never returns raw SQL error text; the raw text stays in event_ingest_log.error', async () => {
    const bad = ev('rep.counted', { set_local_id: 's-unknown', rep_index: 1, camera_id: 'not-a-uuid', confidence: 0.9, quality_label: null });
    const unknown = ev('rep.counted', { set_local_id: 's-unknown', rep_index: 1, camera_id: CAM_FLOOR, confidence: 0.9, quality_label: null });
    const unsupported = ev('device.reboot', {});
    const r = await ingest([bad, unknown, unsupported]);
    expect(r.rejected.map((x) => x.code)).toEqual(['unknown_reference', 'unknown_reference', 'invalid_value']);
    for (const x of r.rejected) {
      expect(CODES).toContain(x.code);
      expect(x.reason).not.toMatch(/uuid|syntax|constraint|s-unknown|device\.reboot|%/i);
    }
    const badCamera = ev('track.started', { global_track_id: 'g-bad-cam', member_id: null, identity_confidence: 0.5, tracking_confidence: 0.5, first_camera_id: 'not-a-uuid' });
    const r2 = await ingest([badCamera]);
    expectRejected(r2, 'invalid_value');
    const log = await h.pool.query(`select status, error from public.event_ingest_log where event_id = $1`, [badCamera.event_id]);
    expect(log.rows[0]).toMatchObject({ status: 'rejected' });
    expect(log.rows[0].error).toMatch(/invalid input syntax for type uuid/);
  });

  it('a resent rejected event counts as a duplicate', async () => {
    const e = ev('device.reboot', {});
    expectRejected(await ingest([e]), 'invalid_value');
    const again = await ingest([e]);
    expect(again).toMatchObject({ accepted: 0, duplicates: 1, rejected: [] });
  });
});

describe('R1-6 infrastructure errors abort the whole call', () => {
  it('a deadlock inside an event leaves nothing recorded so the batch can be retried as-is', async () => {
    await h.pool.query(`
      create or replace function public.t_fail_health() returns trigger language plpgsql as $$
      begin raise exception 'simulated deadlock' using errcode = '40P01'; end $$;
      create trigger t_fail_health before insert on public.device_health_logs for each row execute function public.t_fail_health();`);
    const batchId = randomUUID();
    const events = [ev('camera.status', { camera_id: CAM_FLOOR, status: 'online', fps_measured: 10, resolution_w: null, resolution_h: null, error: null }), heartbeat()];
    try {
      await expect(ingest(events, { batchId })).rejects.toMatchObject({ code: '40P01' });
    } finally {
      await h.pool.query(`drop trigger t_fail_health on public.device_health_logs; drop function public.t_fail_health();`);
    }
    const batch = await h.pool.query(`select count(*)::int as n from public.ingest_batches where batch_id = $1`, [batchId]);
    expect(batch.rows[0].n).toBe(0);
    const logged = await h.pool.query(`select count(*)::int as n from public.event_ingest_log where event_id = any($1::uuid[])`, [events.map((e) => e.event_id)]);
    expect(logged.rows[0].n).toBe(0);
    const retry = await ingest(events, { batchId });
    expect(retry).toMatchObject({ accepted: 2, duplicates: 0, rejected: [] });
  });

  it('a plain business-rule raise from a trigger is a rejection with code internal', async () => {
    await h.pool.query(`
      create or replace function public.t_rule_health() returns trigger language plpgsql as $$
      begin raise exception 'some business rule'; end $$;
      create trigger t_rule_health before insert on public.device_health_logs for each row execute function public.t_rule_health();`);
    try {
      expectRejected(await ingest([heartbeat()]), 'internal');
    } finally {
      await h.pool.query(`drop trigger t_rule_health on public.device_health_logs; drop function public.t_rule_health();`);
    }
  });
});

describe('R1-7 one device update per batch, audit only meaningful changes', () => {
  it('updates edge_devices once per batch and writes no audit row for last_sequence churn', async () => {
    await h.pool.query(`
      create table public.t_device_updates (n int);
      create or replace function public.t_count_device_update() returns trigger language plpgsql as $$
      begin insert into public.t_device_updates values (1); return new; end $$;
      create trigger t_count_device_update after update on public.edge_devices for each row execute function public.t_count_device_update();`);
    try {
      const audit0 = (await h.pool.query(`select count(*)::int as n from public.audit_logs where table_name = 'edge_devices'`)).rows[0].n;
      const events = Array.from({ length: 5 }, () => ev('camera.status', { camera_id: CAM_FLOOR, status: 'online', fps_measured: 10, resolution_w: null, resolution_h: null, error: null }));
      const r = await ingest(events);
      expect(r).toMatchObject({ accepted: 5, rejected: [] });
      const updates = (await h.pool.query(`select count(*)::int as n from public.t_device_updates`)).rows[0].n;
      expect(updates).toBe(1);
      const dev = await h.pool.query(`select last_sequence from public.edge_devices where id = $1`, [DEVICE]);
      expect(Number(dev.rows[0].last_sequence)).toBe(Math.max(...events.map((e) => e.sequence)));
      const audit1 = (await h.pool.query(`select count(*)::int as n from public.audit_logs where table_name = 'edge_devices'`)).rows[0].n;
      expect(audit1).toBe(audit0);
      // A replay with only older sequences does not touch the row at all.
      await h.pool.query(`delete from public.t_device_updates`);
      await ingest([ev('camera.status', { camera_id: CAM_FLOOR, status: 'online', fps_measured: 10, resolution_w: null, resolution_h: null, error: null }, { sequence: 1 })]);
      expect((await h.pool.query(`select count(*)::int as n from public.t_device_updates`)).rows[0].n).toBe(0);
    } finally {
      await h.pool.query(`drop trigger t_count_device_update on public.edge_devices; drop function public.t_count_device_update(); drop table public.t_device_updates;`);
    }
  });

  it('still audits status changes', async () => {
    const audit0 = (await h.pool.query(`select count(*)::int as n from public.audit_logs where table_name = 'edge_devices'`)).rows[0].n;
    await h.pool.query(`update public.edge_devices set status = 'degraded' where id = $1`, [DEVICE]);
    await ingest([heartbeat()]); // heartbeat brings it back to active: a second audited change
    const rows = await h.pool.query(
      `select new_data->>'status' as status from public.audit_logs where table_name = 'edge_devices' order by id desc limit 2`,
    );
    expect(rows.rows.map((r) => r.status)).toEqual(['active', 'degraded']);
    const audit1 = (await h.pool.query(`select count(*)::int as n from public.audit_logs where table_name = 'edge_devices'`)).rows[0].n;
    expect(audit1).toBe(audit0 + 2);
  });
});

describe('R1-4 / R1-12 / C5 device secret lifecycle', () => {
  let device: string;
  let owner: string;

  beforeAll(async () => {
    device = (await h.pool.query(`insert into public.edge_devices (branch_id, name, status) values ($1, 'edge-secrets-test', 'active') returning id`, [BRANCH])).rows[0].id;
    await h.pool.query(`insert into public.edge_device_secrets (edge_device_id, secret_hash, secret) values ($1, 'h-old', 'old-secret-for-test')`, [device]);
    owner = (await h.pool.query(`insert into auth.users (email) values ('owner-hardening@example.test') returning id`)).rows[0].id;
    await h.pool.query(`insert into public.staff_roles (user_id, organization_id, branch_id, role) values ($1, $2, null, 'owner')`, [owner, ORG]);
  });

  const secrets = async () => (await h.pool.query(`select secret from public.get_device_secrets($1)`, [device])).rows.map((r) => r.secret as string);

  it('keeps the previous secret valid during the 24 h rotation overlap, and only the new one after it', async () => {
    const fresh = (await h.asUser(owner, (c) => c.query('select public.issue_device_secret($1) as s', [device]))).rows[0].s as string;
    expect((await secrets()).sort()).toEqual([fresh, 'old-secret-for-test'].sort());
    await h.pool.query(`update public.edge_device_secrets set retired_at = now() - interval '1 second' where edge_device_id = $1 and secret = 'old-secret-for-test'`, [device]);
    expect(await secrets()).toEqual([fresh]);
  });

  it('retiring a device retires all its secrets, hides them, refuses ingest with device_retired and refuses rotation', async () => {
    await h.pool.query(`update public.edge_devices set status = 'retired' where id = $1`, [device]);
    const live = await h.pool.query(`select count(*)::int as n from public.edge_device_secrets where edge_device_id = $1 and (retired_at is null or retired_at > now())`, [device]);
    expect(live.rows[0].n).toBe(0);
    expect(await secrets()).toEqual([]);
    const err = await ingest([heartbeat({ device_id: device })], { device }).catch((e) => e);
    expect(err).toMatchObject({ code: '42501' });
    expect(String(err.message)).toMatch(/^device_retired/);
    await expect(h.asUser(owner, (c) => c.query('select public.issue_device_secret($1)', [device]))).rejects.toThrow(/retired/);
  });

  it('returns nothing for a retired device even if a secret row is still marked current', async () => {
    await h.pool.query(`update public.edge_device_secrets set retired_at = null where edge_device_id = $1`, [device]);
    expect(await secrets()).toEqual([]);
  });

  it('keeps unknown devices on 42501 without the device_retired prefix', async () => {
    const err = await ingest([heartbeat()], { device: randomUUID() }).catch((e) => e);
    expect(err).toMatchObject({ code: '42501' });
    expect(String(err.message)).not.toMatch(/^device_retired/);
  });
});
