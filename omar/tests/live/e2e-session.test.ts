/**
 * The full member visit against production, on a temporary QA tenant:
 *
 *   QR check-in at the front desk (qr-token as staff, C1)
 *   → edge pipeline simulation on the hosted ingest-events (EDGE_SIM_CHECKIN=skip, EDGE_SIM_CHECKOUT=skip, C8)
 *   → sets + cross-camera-deduplicated reps attached to the active session
 *   → member registers a real Web Push subscription (Mozilla autopush receiver)
 *   → member checks out in the app (member_self_check_out)
 *   → session completed, report generated, notification job queued
 *   → pg_cron dispatcher delivers the push (C7); payload matches the notification-worker template
 *   → member reads the report through RLS; share link create → anonymous read → revoke (C3)
 *
 * Evidence (ids + timings, no secrets): tests/live/artifacts/e2e-session.json
 */
import { afterAll, beforeAll, describe, expect, inject, it } from 'vitest';
import { render } from '../../services/notification-worker/src/templates.ts';
import { StepLog } from './lib/artifacts.ts';
import { AutopushReceiver } from './lib/autopush.ts';
import { runEdgeSimulation, type SimSummary } from './lib/edge-sim.ts';
import { liveEnv } from './lib/env.ts';
import { poll, qrTokenGet, qrTokenPost } from './lib/functions.ts';
import { anonClient, createQaTenant, destroyQaTenant, serviceClient, signIn, tenantIds, type Db, type QaTenant } from './lib/qa-tenant.ts';

const DISPATCH_TIMEOUT_MS = 180_000;

let tenant: QaTenant;
let db: Db;
let receiver: AutopushReceiver | null = null;
const log = new StepLog('e2e-session.json');

beforeAll(async () => {
  tenant = await log.step('create QA tenant', () => createQaTenant(`${inject('qaRunId')}-e2e`));
  db = serviceClient();
  log.set('tenant', tenantIds(tenant));
});

afterAll(async () => {
  receiver?.close();
  if (!tenant) return;
  let teardownError: unknown = null;
  try {
    const report = await log.step('destroy QA tenant', () => destroyQaTenant(tenant));
    log.set('teardown', { ok: true, deleted: report?.deleted, missingTables: report?.missingTables, warnings: report?.warnings });
  } catch (err) {
    log.set('teardown', { ok: false, error: (err as Error).message, report: (err as { report?: unknown }).report });
    teardownError = err;
  }
  log.flush();
  if (teardownError) throw teardownError;
});

describe('member visit end to end (production)', () => {
  it('QR check-in → tracked workout → app check-out → report → push → share', async () => {
    const env = liveEnv();
    const staff = await signIn(tenant.staff);
    const member = await signIn(tenant.member);

    // 1. Front desk QR check-in through the qr-token function (staff JWT).
    const sessionId = await log.step('qr check-in as staff', async () => {
      const issued = await qrTokenGet(member.accessToken);
      expect(issued.status, JSON.stringify(issued.body)).toBe(200);
      const res = await qrTokenPost(staff.accessToken, issued.body.token ?? '');
      expect(res.status, JSON.stringify(res.body)).toBe(200);
      const id = res.body.attendance_session_id ?? '';
      const { data: s, error } = await db.from('attendance_sessions').select('status, check_in_method, checked_in_by').eq('id', id).single();
      expect(error).toBeNull();
      expect(s).toMatchObject({ status: 'active', check_in_method: 'qr', checked_in_by: tenant.staff.userId });
      return id;
    });
    log.set('attendanceSessionId', sessionId);

    // 2. Real push subscription, stored by the member's own client (mirrors registerPushSubscription).
    await log.step('register autopush receiver + store subscription as the member', async () => {
      if (!env.vapidPublicKey) throw new Error('NEXT_PUBLIC_VAPID_PUBLIC_KEY / VAPID_PUBLIC_KEY missing from the root .env');
      receiver = await AutopushReceiver.connect(env.vapidPublicKey);
      const sub = receiver.subscription;
      const { error } = await member.client.from('push_subscriptions').upsert(
        {
          user_id: tenant.member.userId,
          endpoint: sub.endpoint,
          p256dh: sub.p256dh,
          auth: sub.auth,
          user_agent: 'smartgym-live-tests (Mozilla autopush receiver)',
          disabled_at: null,
          last_used_at: null,
        },
        { onConflict: 'endpoint' },
      );
      expect(error).toBeNull();
      const { data: mine } = await member.client.from('push_subscriptions').select('id').eq('endpoint', sub.endpoint);
      expect(mine ?? []).toHaveLength(1);
    });

    // 3. Edge simulation: the member is already checked in (kiosk) and will check out in the app.
    const sim: SimSummary = await log.step('edge simulation against hosted ingest-events', async () => {
      const s = await runEdgeSimulation(tenant, { checkin: 'skip', checkout: 'skip' });
      log.set('simulation', s);
      expect(s.exitCode, s.outputTail.join('\n')).toBe(0);
      expect(s.ok, s.outputTail.join('\n')).toBe(true);
      expect(s.remaining).toBe(0);
      expect(s.lastError).toBeNull();
      expect(s.batches ?? 0).toBeGreaterThan(0);
      return s;
    });

    // 4. Sets and deduplicated reps are attached to the active session.
    const facts = await log.step('sets and deduplicated reps attached to the session', async () => {
      const { data: session } = await db.from('attendance_sessions').select('status').eq('id', sessionId).single();
      expect(session?.status).toBe('active'); // EDGE_SIM_CHECKOUT=skip: the edge did not check out

      const { data: usage, error: uErr } = await db.from('equipment_usage_sessions').select('id, equipment_id, camera_id').eq('attendance_session_id', sessionId);
      expect(uErr).toBeNull();
      expect((usage ?? []).length).toBeGreaterThan(0);
      for (const u of usage ?? []) expect(u.equipment_id).toBe(tenant.equipment.legPress);

      const { data: acts } = await db.from('exercise_activity_sessions').select('id, exercise_id').in('equipment_usage_session_id', (usage ?? []).map((u) => u.id));
      expect((acts ?? []).length).toBeGreaterThan(0);
      for (const a of acts ?? []) expect(a.exercise_id).toBe(tenant.exercises.legPress);

      const { data: sets } = await db.from('exercise_sets').select('id, set_index, rep_count').in('exercise_activity_session_id', (acts ?? []).map((a) => a.id));
      expect((sets ?? []).length).toBeGreaterThan(0);

      const { data: reps } = await db.from('repetition_events').select('id, exercise_set_id, rep_index, camera_id').in('exercise_set_id', (sets ?? []).map((s) => s.id));
      const repRows = reps ?? [];
      expect(repRows.length).toBeGreaterThan(0);
      // One row per physical rep: unique (set, rep_index), consecutive 1..n per set, and the set's count matches.
      for (const s of sets ?? []) {
        const idx = repRows.filter((r) => r.exercise_set_id === s.id).map((r) => r.rep_index).sort((a, b) => a - b);
        expect(idx).toEqual(Array.from({ length: idx.length }, (_, i) => i + 1));
        expect(s.rep_count).toBe(idx.length);
      }
      for (const r of repRows) expect([tenant.cameras.floorA, tenant.cameras.floorB]).toContain(r.camera_id);
      // Both floor cameras saw every rep; fusion suppressed the second sighting.
      if (sim.duplicatesSuppressed !== null) expect(sim.duplicatesSuppressed).toBeGreaterThan(0);
      if (sim.repsAdmitted !== null) expect(repRows.length).toBeLessThanOrEqual(sim.repsAdmitted);
      const totalReps = (sets ?? []).reduce((n, s) => n + s.rep_count, 0);
      return { usage: usage?.length ?? 0, activities: acts?.length ?? 0, sets: sets?.length ?? 0, reps: repRows.length, totalReps };
    });
    log.set('workoutFacts', facts);

    // 5. The member checks out in the app.
    const reportId = await log.step('member_self_check_out as the member', async () => {
      const { data, error } = await member.client.rpc('member_self_check_out');
      expect(error).toBeNull();
      expect(String(data)).toMatch(/^[0-9a-f-]{36}$/);
      return String(data);
    });
    log.set('reportId', reportId);
    const checkedOutAt = Date.now();

    // 6. Session completed, report generated, notification queued.
    const job = await log.step('session completed, report exists, notification job queued', async () => {
      const { data: s } = await db.from('attendance_sessions').select('status, check_out_at').eq('id', sessionId).single();
      expect(s?.status).toBe('completed');
      expect(s?.check_out_at).toBeTruthy();
      const { data: r, error } = await db.from('workout_reports').select('id, member_id, attendance_session_id, summary').eq('id', reportId).single();
      expect(error).toBeNull();
      expect(r).toMatchObject({ member_id: tenant.member.id, attendance_session_id: sessionId });
      const summary = r!.summary as { total_reps: number; total_sets: number };
      expect(summary.total_reps).toBeGreaterThan(0);
      expect(summary.total_reps).toBe(facts.totalReps);
      const { data: jobs } = await db
        .from('notification_jobs')
        .select('id, channel, template, status, payload, dedupe_key')
        .eq('member_id', tenant.member.id)
        .eq('template', 'workout_report_ready');
      expect(jobs ?? []).toHaveLength(1);
      const j = jobs![0]!;
      expect(j.channel).toBe('web_push');
      expect(j.dedupe_key).toBe(`workout_report_ready:${sessionId}`);
      expect(j.payload).toMatchObject({ report_id: reportId, attendance_session_id: sessionId });
      return { id: String(j.id), initialStatus: String(j.status), summaryTotalReps: summary.total_reps, summaryTotalSets: summary.total_sets };
    });
    log.set('notificationJob', job);

    // 7. pg_cron → notification-dispatch → Mozilla push service → this process.
    const push = await log.step('push delivered by the dispatcher within 180 s', async () => {
      const received = await receiver!.waitForNotification(DISPATCH_TIMEOUT_MS);
      const expected = render('workout_report_ready', { report_id: reportId, attendance_session_id: sessionId }, tenant.member.fullName, env.memberAppUrl);
      const payload = received.json as { title?: string; body?: string; url?: string; tag?: string } | null;
      expect(payload, `push payload was not JSON: ${received.text.slice(0, 200)}`).not.toBeNull();
      expect(payload!.title).toBe(expected.title);
      expect(payload!.body).toBe(expected.body);
      expect(payload!.url).toBe(expected.url);
      expect(payload!.url!.startsWith(`${env.memberAppUrl}/`)).toBe(true);
      const delivered = await poll(
        'notification_jobs.status = delivered',
        async () => {
          const { data } = await db.from('notification_jobs').select('status, attempts, delivered_at').eq('id', job.id).single();
          return data?.status === 'delivered' ? data : null;
        },
        60_000,
      );
      return {
        latencyMsFromCheckout: Date.now() - checkedOutAt,
        encoding: received.encoding,
        title: payload!.title,
        body: payload!.body,
        url: payload!.url,
        tag: payload!.tag ?? null,
        jobAttempts: delivered.attempts,
        jobDeliveredAt: delivered.delivered_at,
      };
    });
    log.set('push', push);

    // 8. The member reads the report through RLS.
    await log.step('member reads the report through RLS', async () => {
      const { data, error } = await member.client.from('workout_reports').select('id, summary').eq('id', reportId).maybeSingle();
      expect(error).toBeNull();
      expect(data?.id).toBe(reportId);
      expect((data?.summary as { total_reps: number }).total_reps).toBeGreaterThan(0);
      // Anonymous callers cannot read reports directly (only through a share token).
      const { data: anonRows } = await anonClient().from('workout_reports').select('id').eq('id', reportId);
      expect(anonRows ?? []).toHaveLength(0);
    });

    // 9. Share link lifecycle (C3).
    await log.step('share: create → anonymous read → revoke → anonymous read is null', async () => {
      const anon = anonClient();
      const { data: share, error } = await member.client.rpc('create_report_share', { p_report_id: reportId, p_days: 7 });
      expect(error).toBeNull();
      const { token, expires_at } = share as { token: string; expires_at: string };
      expect(token).toMatch(/^[0-9a-f]{48}$/);
      const days = (Date.parse(expires_at) - Date.now()) / 86_400_000;
      expect(days).toBeGreaterThan(6.9);
      expect(days).toBeLessThan(7.1);

      const { data: shared, error: sErr } = await anon.rpc('get_shared_report', { p_token: token });
      expect(sErr).toBeNull();
      const sharedObj = shared as Record<string, unknown>;
      expect(sharedObj).toBeTruthy();
      expect(sharedObj.member_first_name).toBe(tenant.member.firstName);
      expect(sharedObj).not.toHaveProperty('member_name');
      expect((sharedObj.summary as { total_reps: number }).total_reps).toBe(job.summaryTotalReps);
      const raw = JSON.stringify(sharedObj);
      for (const id of [tenant.member.id, tenant.member.userId, reportId, sessionId, tenant.orgId, tenant.branchId, tenant.device.id]) {
        expect(raw, 'shared report leaks an id').not.toContain(id);
      }
      expect(raw).not.toContain(tenant.member.fullName);
      expect(raw.toUpperCase()).not.toContain(tenant.member.code.toUpperCase());

      const { error: rErr } = await member.client.rpc('revoke_report_share', { p_report_id: reportId });
      expect(rErr).toBeNull();
      const { data: afterRevoke, error: aErr } = await anon.rpc('get_shared_report', { p_token: token });
      expect(aErr).toBeNull();
      expect(afterRevoke).toBeNull();
    });
  });
});
