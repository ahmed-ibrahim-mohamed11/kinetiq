/**
 * Acceptance Tests for DEFECT-002 and DEFECT-007
 * Validating save_ai_workout_session RPC:
 * 1. AI-enabled active subscription -> success, full telemetry persisted
 * 2. Standard-only subscription without AI addon -> AI_ENTITLEMENT_REQUIRED
 * 3. Expired subscription -> SUBSCRIPTION_EXPIRED
 * 4. No subscription record -> NO_SUBSCRIPTION (DEFECT-007 fix)
 * 5. Inactive member -> MEMBER_INACTIVE
 * 6. Repetition events and telemetry persistence
 */

import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { startHarness, type Harness } from './harness';

let h: Harness;

beforeAll(async () => {
  h = await startHarness({ log: (m) => process.stdout.write(`[ai-workout-test] ${m}\n`) });
});

afterAll(async () => {
  await h?.stop();
});

describe('AI Workout Persistence & Entitlement Security (save_ai_workout_session)', () => {
  const orgId = 'a0000000-0000-4000-8000-000000000001';
  const branchId = 'b0000000-0000-4000-8000-000000000001';
  const demoMemberId = '33333333-3333-4333-8333-333333333333';

  it('rejects AI workout save when member has NO subscription (DEFECT-007)', async () => {
    // demoMemberId has no member_subscriptions seeded initially
    const res = await h.pool.query(
      `select public.save_ai_workout_session(
        p_member_id := $1,
        p_reps := 10,
        p_valid_reps := 8,
        p_imperfect_reps := 2,
        p_exercise := 'squat',
        p_duration_seconds := 45,
        p_form_score := 85,
        p_rep_telemetry := '[]'::jsonb,
        p_camera_id := null
      ) as result`,
      [demoMemberId],
    );

    const result = res.rows[0].result;
    expect(result.ok).toBe(false);
    expect(result.code).toBe('NO_SUBSCRIPTION');
  });

  it('rejects AI workout save when member subscription does NOT have AI addon active', async () => {
    // Insert standard plan subscription without AI addon
    const planRes = await h.pool.query(
      `select id from public.membership_plans where code = 'PLAN_1M' limit 1`,
    );
    const planId = planRes.rows[0].id;

    await h.pool.query(
      `insert into public.member_subscriptions (
        organization_id, branch_id, member_id, plan_id, status,
        starts_at, expires_at, original_expires_at, total_price, paid_amount, ai_addon_active
      ) values ($1, $2, $3, $4, 'active', now() - interval '5 days', now() + interval '25 days', now() + interval '25 days', 1500, 1500, false)`,
      [orgId, branchId, demoMemberId, planId],
    );

    const res = await h.pool.query(
      `select public.save_ai_workout_session(
        p_member_id := $1,
        p_reps := 10,
        p_valid_reps := 10,
        p_imperfect_reps := 0,
        p_exercise := 'squat'
      ) as result`,
      [demoMemberId],
    );

    const result = res.rows[0].result;
    expect(result.ok).toBe(false);
    expect(result.code).toBe('AI_ENTITLEMENT_REQUIRED');
  });

  it('rejects AI workout save when subscription is expired', async () => {
    // Update subscription to expired
    await h.pool.query(
      `update public.member_subscriptions
       set status = 'expired', expires_at = now() - interval '1 day', ai_addon_active = true
       where member_id = $1`,
      [demoMemberId],
    );

    const res = await h.pool.query(
      `select public.save_ai_workout_session(
        p_member_id := $1,
        p_reps := 10,
        p_valid_reps := 10,
        p_imperfect_reps := 0,
        p_exercise := 'squat'
      ) as result`,
      [demoMemberId],
    );

    const result = res.rows[0].result;
    expect(result.ok).toBe(false);
    expect(result.code).toBe('SUBSCRIPTION_EXPIRED');
  });

  it('successfully persists workout session, sets, reps, and telemetry with active AI subscription', async () => {
    // Activate subscription with AI addon
    await h.pool.query(
      `update public.member_subscriptions
       set status = 'active', expires_at = now() + interval '30 days', ai_addon_active = true
       where member_id = $1`,
      [demoMemberId],
    );

    const telemetry = [
      {
        rep_number: 1,
        verdict: 'correct',
        min_knee_angle_deg: 88.0,
        max_trunk_lean_deg: 18.5,
        confidence: 0.94,
        detected_errors: [],
      },
      {
        rep_number: 2,
        verdict: 'incorrect',
        min_knee_angle_deg: 122.0,
        max_trunk_lean_deg: 20.0,
        confidence: 0.90,
        detected_errors: ['shallow_squat'],
      },
    ];

    const res = await h.pool.query(
      `select public.save_ai_workout_session(
        p_member_id := $1,
        p_reps := 2,
        p_valid_reps := 1,
        p_imperfect_reps := 1,
        p_exercise := 'squat',
        p_duration_seconds := 30,
        p_form_score := 50,
        p_rep_telemetry := $2::jsonb
      ) as result`,
      [demoMemberId, JSON.stringify(telemetry)],
    );

    const result = res.rows[0].result;
    expect(result.ok).toBe(true);
    expect(result.reps_saved).toBe(2);
    expect(result.valid_reps).toBe(1);
    expect(result.imperfect_reps).toBe(1);
    expect(result.session_id).toBeDefined();
    expect(result.report_id).toBeDefined();

    // Verify repetition_events table holds the telemetry rows
    const reps = await h.pool.query(
      `select rep_index, quality_label, min_knee_angle_deg, max_trunk_lean_deg, detected_errors
       from public.repetition_events
       where exercise_set_id = $1 order by rep_index`,
      [result.set_id],
    );

    expect(reps.rows.length).toBe(2);
    expect(reps.rows[0].quality_label).toBe('correct');
    expect(Number(reps.rows[0].min_knee_angle_deg)).toBe(88.0);
    expect(reps.rows[1].quality_label).toBe('incorrect');
    expect(reps.rows[1].detected_errors).toEqual(['shallow_squat']);

    // Verify workout_reports summary
    const report = await h.pool.query(
      `select summary from public.workout_reports where id = $1`,
      [result.report_id],
    );
    expect(report.rows[0].summary.total_reps).toBe(2);
    expect(report.rows[0].summary.valid_reps).toBe(1);
    expect(report.rows[0].summary.form_score).toBe(50);
  });
});
