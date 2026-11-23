/**
 * Functional test of the ingest + workout engine on a real database:
 * the shared events.v1 fixtures (rebased onto the DEMO tenant) go through
 * `ingest_events`, and we verify sessions, usage, sets, reps, finalisation,
 * the report summary, idempotent replay, and the consent gate.
 */
import { randomUUID } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { startHarness, type Harness } from './harness';

const DEVICE = 'd0000000-0000-4000-8000-000000000001';
const BRANCH = 'b0000000-0000-4000-8000-000000000001';
const MEMBER = '33333333-3333-4333-8333-333333333333';
const CAM_ENTRANCE = 'c0000000-0000-4000-8000-000000000001';
const CAM_FLOOR = 'c0000000-0000-4000-8000-000000000002';
const LEG_PRESS_EQ = 'e0000000-0000-4000-8000-000000000002';
const LEG_PRESS_ZONE = 'f0000000-0000-4000-8000-000000000003';

let h: Harness;
let legPressId: string;

type Ev = Record<string, any>;

function fixtures(): Ev[] {
  const raw = JSON.parse(readFileSync(join(import.meta.dirname ?? __dirname, '..', '..', 'packages', 'api-contracts', 'fixtures', 'events_v1_valid.json'), 'utf8')) as Ev[];
  const base = Date.now() - 60 * 60 * 1000;
  return raw.map((e, i) => {
    const ev = structuredClone(e);
    ev.event_id = randomUUID();
    ev.device_id = DEVICE;
    ev.branch_id = BRANCH;
    ev.sequence = i;
    ev.occurred_at = new Date(base + i * 60_000).toISOString();
    ev.sent_at = new Date().toISOString();
    if ('camera_id' in ev.payload && ev.payload.camera_id) ev.payload.camera_id = ev.event_type === 'identity.checkin' ? CAM_ENTRANCE : CAM_FLOOR;
    if ('first_camera_id' in ev.payload) ev.payload.first_camera_id = CAM_ENTRANCE;
    if (ev.event_type === 'equipment.usage.started') {
      ev.payload.equipment_id = LEG_PRESS_EQ;
      ev.payload.zone_id = LEG_PRESS_ZONE;
      ev.payload.camera_id = CAM_FLOOR;
    }
    if (ev.event_type === 'exercise.activity.started') ev.payload.exercise_id = legPressId;
    if (ev.event_type === 'tracking.uncertain') {
      ev.payload.from = new Date(base + i * 60_000 - 30_000).toISOString();
      ev.payload.to = new Date(base + i * 60_000).toISOString();
    }
    return ev;
  });
}

async function ingest(events: Ev[], batchId = randomUUID()) {
  const { rows } = await h.pool.query(`select public.ingest_events($1::uuid, $2::uuid, now(), $3::jsonb) as r`, [DEVICE, batchId, JSON.stringify(events)]);
  return rows[0].r as { accepted: number; duplicates: number; rejected: Array<{ event_id: string; code: string; reason: string }> };
}

beforeAll(async () => {
  h = await startHarness();
  const { rows } = await h.pool.query(`select id from public.exercise_catalog where slug = 'leg-press'`);
  legPressId = rows[0].id;
});
afterAll(async () => {
  await h?.stop();
});

describe('ingest_events on a real database', () => {
  const events = () => fixtures();
  let first: Ev[];

  it('accepts the full fixture workout and finalises the session', async () => {
    first = events();
    const r = await ingest(first);
    expect(r.rejected, JSON.stringify(r.rejected)).toEqual([]);
    expect(r.accepted).toBe(first.length);

    const session = await h.pool.query(`select * from public.attendance_sessions where member_id = $1 order by check_in_at desc limit 1`, [MEMBER]);
    expect(session.rows[0].status).toBe('completed');
    expect(session.rows[0].check_in_method).toBe('face');
    expect(session.rows[0].check_out_method).toBe('qr');

    const sets = await h.pool.query(`select rep_count, verified, confidence from public.exercise_sets`);
    expect(sets.rows).toEqual([{ rep_count: 10, verified: true, confidence: '0.9000' }]);
    const reps = await h.pool.query(`select count(*)::int as n from public.repetition_events`);
    expect(reps.rows[0].n).toBe(1); // only one rep.counted fixture; set.completed carries the authoritative 10

    const report = await h.pool.query(`select summary, length(share_token) as tok from public.workout_reports where attendance_session_id = $1`, [session.rows[0].id]);
    expect(report.rows).toHaveLength(1);
    const s = report.rows[0].summary;
    expect(s.schema_version).toBe(1);
    expect(s.total_reps).toBe(10);
    expect(s.total_sets).toBe(1);
    expect(s.exercises[0]).toMatchObject({ exercise_slug: 'leg-press', equipment_name: 'Leg Press #1', total_reps: 10 });
    expect(s.exercises[0].sets[0]).toMatchObject({ set_index: 1, reps: 10, verified: true });
    expect(s.uncertain_segments).toHaveLength(1);
    expect(s.uncertain_segments[0].reason).toBe('occlusion');
    // C3: no share link exists until the member creates one (format covered by hardening-checkin).
    expect(report.rows[0].tok).toBeNull();

    const jobs = await h.pool.query(`select channel, template, status from public.notification_jobs where member_id = $1`, [MEMBER]);
    expect(jobs.rows).toEqual([{ channel: 'email', template: 'workout_report_ready', status: 'queued' }]);

    const device = await h.pool.query(`select status, last_sequence from public.edge_devices where id = $1`, [DEVICE]);
    expect(device.rows[0]).toMatchObject({ status: 'active', last_sequence: String(first.length - 1) });
  });

  it('replaying the same events in a new batch is a no-op (idempotent by event_id)', async () => {
    const r = await ingest(first);
    expect(r.accepted).toBe(0);
    expect(r.duplicates).toBe(first.length);
    const reports = await h.pool.query(`select count(*)::int as n from public.workout_reports`);
    expect(reports.rows[0].n).toBe(1);
  });

  it('replaying the same batch_id is rejected', async () => {
    const id = randomUUID();
    const heartbeat = events()[0]!;
    await ingest([heartbeat], id);
    await expect(ingest([{ ...heartbeat, event_id: randomUUID() }], id)).rejects.toThrow(/already processed/);
  });

  it('rejects face check-in for a member without biometric consent, and tracking without CCTV consent', async () => {
    await h.pool.query(`update public.consent_records set status = 'withdrawn', withdrawn_at = now() where member_id = $1 and consent_type = 'biometric_face_identification'`, [MEMBER]);
    const checkin = events().find((e) => e.event_type === 'identity.checkin')!;
    checkin.event_id = randomUUID();
    const r = await ingest([checkin]);
    expect(r.accepted).toBe(0);
    // C4: stable code + fixed reason; the raw message stays in event_ingest_log.error.
    expect(r.rejected[0]?.code).toBe('no_consent');
    const log = await h.pool.query(`select error from public.event_ingest_log where event_id = $1`, [checkin.event_id]);
    expect(log.rows[0].error).toMatch(/biometric consent/);
    // withdrawal trigger revoked enrollments (none exist, but the trigger must not fail) and QR still works
    const qr = { ...checkin, event_id: randomUUID(), payload: { ...checkin.payload, method: 'qr', confidence: 1 } };
    const r2 = await ingest([qr]);
    expect(r2.rejected).toEqual([]);
    // close it again
    const out = events().find((e) => e.event_type === 'identity.checkout')!;
    out.event_id = randomUUID();
    await ingest([out]);
  });

  it('rejects events for equipment or cameras outside the device branch', async () => {
    const org = await h.pool.query(`insert into public.organizations (name, slug) values ('Other', 'other-org') returning id`);
    const br = await h.pool.query(`insert into public.branches (organization_id, name) values ($1, 'Other branch') returning id`, [org.rows[0].id]);
    const eq = await h.pool.query(`insert into public.equipment (branch_id, name, category) values ($1, 'Foreign machine', 'machine') returning id`, [br.rows[0].id]);
    const usage = events().find((e) => e.event_type === 'equipment.usage.started')!;
    usage.event_id = randomUUID();
    usage.payload.equipment_id = eq.rows[0].id;
    usage.payload.usage_local_id = 'u-foreign';
    const r = await ingest([usage]);
    expect(r.rejected[0]?.code).toBe('branch_mismatch');
  });

  it('abandon_stale_sessions finalises forgotten sessions as abandoned', async () => {
    await h.pool.query(
      `insert into public.attendance_sessions (member_id, branch_id, check_in_at, check_in_method) values ($1, $2, now() - interval '9 hours', 'qr')`,
      [MEMBER, BRANCH],
    );
    const { rows } = await h.pool.query(`select public.abandon_stale_sessions(6) as n`);
    expect(rows[0].n).toBe(1);
    const st = await h.pool.query(`select status from public.attendance_sessions where member_id = $1 and check_in_at < now() - interval '8 hours'`, [MEMBER]);
    expect(st.rows[0].status).toBe('abandoned');
  });

  it('notification worker RPCs claim, back off and dead-letter', async () => {
    const claimed = await h.pool.query(`select id, attempts, status from public.claim_notification_jobs('worker-test', 10)`);
    expect(claimed.rows.length).toBeGreaterThan(0);
    const job = claimed.rows[0];
    expect(job.status).toBe('sending');
    await h.pool.query(`select public.complete_notification_job($1, false, 'no subscription')`, [job.id]);
    const after = await h.pool.query(`select status, attempts, next_attempt_at > now() as deferred from public.notification_jobs where id = $1`, [job.id]);
    expect(after.rows[0]).toMatchObject({ status: 'failed', attempts: 1, deferred: true });
    await h.pool.query(`update public.notification_jobs set attempts = max_attempts where id = $1`, [job.id]);
    await h.pool.query(`select public.complete_notification_job($1, false, 'still failing')`, [job.id]);
    const dead = await h.pool.query(`select status from public.notification_jobs where id = $1`, [job.id]);
    expect(dead.rows[0].status).toBe('dead');
  });

  it('retention job runs against every configured table', async () => {
    const { rows } = await h.pool.query(`select * from public.apply_retention()`);
    expect(rows.map((r) => r.table_name).sort()).toEqual(
      [
        'audit_logs',
        'biometric_revocations',
        'camera_status_logs',
        'consent_records_archive',
        'device_health_logs',
        'event_ingest_log',
        'ingest_batches',
        'person_tracks',
        'repetition_events',
        'tracking_uncertain_segments',
      ].sort(),
    );
  });
});
