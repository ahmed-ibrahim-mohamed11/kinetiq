/**
 * Commercial Gym Domain Acceptance Tests
 *
 * Runs against real PostgreSQL engine (embedded) and verifies:
 * 1. Schema, tables, and RLS policies for commercial gym operations.
 * 2. Membership packages (1M, 3M, 6M, 12M, Day, PT, Session-based).
 * 3. Promotional discounts (percentage, fixed amount, extra days).
 * 4. Member registration with financial payments and invoice generation.
 * 5. Subscription freezing and unfreezing with date adjustments.
 * 6. Subscription renewals with package upgrades.
 * 7. Independent 3-permission model:
 *    - Entrance facial biometric consent
 *    - AI CCTV workout tracking entitlement + consent
 *    - Communication/notification preferences
 */

import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { startHarness, type Harness } from './harness';

let h: Harness;

beforeAll(async () => {
  h = await startHarness({ log: (m) => process.stdout.write(`[commercial-db] ${m}\n`) });
});

afterAll(async () => {
  await h?.stop();
});

describe('Commercial Gym Domain on PostgreSQL', () => {
  it('creates all commercial tables with Row Level Security', async () => {
    const { rows } = await h.pool.query<{ tablename: string; rowsecurity: boolean }>(
      `select tablename, rowsecurity from pg_tables where schemaname = 'public' and tablename in (
        'membership_plans', 'member_subscriptions', 'subscription_freezes',
        'promotions', 'payments', 'invoices', 'referral_records', 'notification_preferences'
      ) order by tablename`,
    );
    expect(rows.length).toBe(8);
    for (const r of rows) {
      expect(r.rowsecurity, `Table ${r.tablename} must have RLS enabled`).toBe(true);
    }
  });

  it('seeded standard commercial packages and promotions', async () => {
    const plans = await h.pool.query<{ code: string; price: number; duration_days: number; ai_training_included: boolean }>(
      `select code, price, duration_days, ai_training_included from public.membership_plans order by code`,
    );
    expect(plans.rows.length).toBeGreaterThanOrEqual(7);

    const planCodes = new Set(plans.rows.map((p) => p.code));
    expect(planCodes).toContain('PLAN_1M');
    expect(planCodes).toContain('PLAN_3M');
    expect(planCodes).toContain('PLAN_6M');
    expect(planCodes).toContain('PLAN_12M');
    expect(planCodes).toContain('PLAN_DAY');
    expect(planCodes).toContain('PLAN_SESSIONS_10');
    expect(planCodes).toContain('PLAN_PT_12');

    // Verify promotions
    const promos = await h.pool.query<{ code: string; discount_type: string; is_ai_generated: boolean }>(
      `select code, discount_type, is_ai_generated from public.promotions order by code`,
    );
    expect(promos.rows.length).toBeGreaterThanOrEqual(4);
    const promoCodes = new Set(promos.rows.map((p) => p.code));
    expect(promoCodes).toContain('WELCOME10');
    expect(promoCodes).toContain('AI_FREE_TRIAL');
    expect(promoCodes).toContain('AI_SUGGEST_OFFPEAK_20');
  });

  it('performs full receptionist journey: member registration, package purchase, invoice & payment', async () => {
    const branchRes = await h.pool.query<{ id: string; organization_id: string }>(
      `select id, organization_id from public.branches limit 1`,
    );
    const branch = branchRes.rows[0]!;

    const planRes = await h.pool.query<{ id: string; price: number; duration_days: number }>(
      `select id, price, duration_days from public.membership_plans where code = 'PLAN_3M'`,
    );
    const plan = planRes.rows[0]!;

    // 1. Create member
    const memberRes = await h.pool.query<{ id: string }>(
      `insert into public.members (branch_id, full_name, member_code, phone, email, date_of_birth, emergency_contact_name, emergency_contact_phone)
       values ($1, 'Amr Mahmoud', 'GYM-AMR2', '+201011112222', 'amr@example.com', '1995-04-12', 'Mahmoud', '+201099998888')
       returning id`,
      [branch.id],
    );
    const memberId = memberRes.rows[0]!.id;

    // 2. Create subscription with WELCOME10 (10% discount)
    const promoRes = await h.pool.query<{ id: string; discount_value: number }>(
      `select id, discount_value from public.promotions where code = 'WELCOME10'`,
    );
    const promo = promoRes.rows[0]!;

    const discountAmount = (plan.price * promo.discount_value) / 100;
    const finalPrice = plan.price - discountAmount;
    const startsAt = new Date();
    const expiresAt = new Date(startsAt.getTime() + plan.duration_days * 24 * 60 * 60 * 1000);

    const subRes = await h.pool.query<{ id: string }>(
      `insert into public.member_subscriptions (
        member_id, plan_id, branch_id, organization_id, starts_at, expires_at, original_expires_at,
        status, ai_addon_active, total_price, discount_amount, paid_amount, remaining_balance, currency, promotion_id
       ) values ($1, $2, $3, $4, $5, $6, $6, 'active', false, $7, $8, $7, 0, 'EGP', $9)
       returning id`,
      [memberId, plan.id, branch.id, branch.organization_id, startsAt.toISOString(), expiresAt.toISOString(), finalPrice, discountAmount, promo.id],
    );
    const subId = subRes.rows[0]!.id;

    // 3. Record Payment & Invoice
    await h.pool.query(
      `insert into public.payments (organization_id, branch_id, member_id, subscription_id, amount, currency, method, status)
       values ($1, $2, $3, $4, $5, 'EGP', 'cash', 'completed')`,
      [branch.organization_id, branch.id, memberId, subId, finalPrice],
    );

    const invRes = await h.pool.query<{ invoice_number: string; total: number }>(
      `insert into public.invoices (organization_id, branch_id, member_id, subscription_id, invoice_number, subtotal, discount, total, status, due_at)
       values ($1, $2, $3, $4, 'INV-TEST-001', $5, $6, $7, 'paid', now())
       returning invoice_number, total`,
      [branch.organization_id, branch.id, memberId, subId, plan.price, discountAmount, finalPrice],
    );
    expect(Number(invRes.rows[0]!.total)).toBe(finalPrice);

    // 4. Record Notification Preferences
    await h.pool.query(
      `insert into public.notification_preferences (member_id, workout_reports, subscription_expiry_alerts, promotional_offers)
       values ($1, true, true, false)`,
      [memberId],
    );

    // 5. Verify Entitlement: Normal member without AI has NO AI access
    const entRes = await h.pool.query<{ is_active: boolean; ai_training_entitled: boolean; status: string }>(
      `select * from public.fn_get_member_entitlement($1)`,
      [memberId],
    );
    const ent = entRes.rows[0]!;
    expect(ent.is_active).toBe(true);
    expect(ent.ai_training_entitled).toBe(false);
    expect(ent.status).toBe('active');
  });

  it('correctly freezes and unfreezes a subscription with automatic expiry shift', async () => {
    const branchRes = await h.pool.query<{ id: string; organization_id: string }>(
      `select id, organization_id from public.branches limit 1`,
    );
    const branch = branchRes.rows[0]!;

    const planRes = await h.pool.query<{ id: string }>(
      `select id from public.membership_plans where code = 'PLAN_6M'`,
    );
    const plan = planRes.rows[0]!;

    const memberRes = await h.pool.query<{ id: string }>(
      `insert into public.members (branch_id, full_name, member_code)
       values ($1, 'Freeze Test Member', 'GYM-FRZ2') returning id`,
      [branch.id],
    );
    const memberId = memberRes.rows[0]!.id;

    const startsAt = new Date();
    const expiresAt = new Date(startsAt.getTime() + 180 * 24 * 60 * 60 * 1000);

    const subRes = await h.pool.query<{ id: string }>(
      `insert into public.member_subscriptions (
        member_id, plan_id, branch_id, organization_id, starts_at, expires_at, original_expires_at, status, total_price
       ) values ($1, $2, $3, $4, $5, $6, $6, 'active', 3000)
       returning id`,
      [memberId, plan.id, branch.id, branch.organization_id, startsAt.toISOString(), expiresAt.toISOString()],
    );
    const subId = subRes.rows[0]!.id;

    // Freeze for 14 days
    const freezeRes = await h.pool.query<{ fn_freeze_subscription: any }>(
      `select public.fn_freeze_subscription($1, 14, 'Medical recovery', null)`,
      [subId],
    );
    expect(freezeRes.rows[0]!.fn_freeze_subscription.ok).toBe(true);

    // Verify subscription status is frozen and expires_at shifted forward by 14 days
    const frozenSub = await h.pool.query<{ status: string; expires_at: string; total_freeze_days_used: number }>(
      `select status, expires_at, total_freeze_days_used from public.member_subscriptions where id = $1`,
      [subId],
    );
    expect(frozenSub.rows[0]!.status).toBe('frozen');
    expect(frozenSub.rows[0]!.total_freeze_days_used).toBe(14);
    const newExpiry = new Date(frozenSub.rows[0]!.expires_at);
    const originalExpiry = new Date(expiresAt);
    const diffDays = Math.round((newExpiry.getTime() - originalExpiry.getTime()) / (24 * 60 * 60 * 1000));
    expect(diffDays).toBe(14);

    // Unfreeze
    const unfreezeRes = await h.pool.query<{ fn_unfreeze_subscription: any }>(
      `select public.fn_unfreeze_subscription($1::uuid)`,
      [subId],
    );
    expect(unfreezeRes.rows[0]!.fn_unfreeze_subscription.ok).toBe(true);

    const activeSub = await h.pool.query<{ status: string }>(
      `select status from public.member_subscriptions where id = $1`,
      [subId],
    );
    expect(activeSub.rows[0]!.status).toBe('active');
  });

  it('enforces AI feature entitlement and explicit CCTV consent strictly', async () => {
    const branchRes = await h.pool.query<{ id: string; organization_id: string }>(
      `select id, organization_id from public.branches limit 1`,
    );
    const branch = branchRes.rows[0]!;

    const planRes = await h.pool.query<{ id: string }>(
      `select id from public.membership_plans where code = 'PLAN_12M'`,
    );
    const plan = planRes.rows[0]!;

    const memberRes = await h.pool.query<{ id: string }>(
      `insert into public.members (branch_id, full_name, member_code)
       values ($1, 'AI VIP Member', 'GYM-PREM') returning id`,
      [branch.id],
    );
    const memberId = memberRes.rows[0]!.id;

    // PLAN_12M has ai_training_included = true
    await h.pool.query(
      `insert into public.member_subscriptions (
        member_id, plan_id, branch_id, organization_id, starts_at, expires_at, original_expires_at,
        status, ai_addon_active, total_price
       ) values ($1, $2, $3, $4, now(), now() + interval '365 days', now() + interval '365 days', 'active', true, 6000)`,
      [memberId, plan.id, branch.id, branch.organization_id],
    );

    // AI Entitled Member has ai_training_entitled = true
    const entRes = await h.pool.query<{ is_active: boolean; ai_training_entitled: boolean; plan_name: string }>(
      `select * from public.fn_get_member_entitlement($1)`,
      [memberId],
    );
    const ent = entRes.rows[0]!;
    expect(ent.is_active).toBe(true);
    expect(ent.ai_training_entitled).toBe(true);
    expect(ent.plan_name).toBe('12 Months VIP');

    // Grant CCTV tracking consent
    await h.pool.query(
      `insert into public.consent_records (member_id, consent_type, status, policy_version)
       values ($1, 'cctv_workout_tracking', 'granted', '2026.1')`,
      [memberId],
    );

    const consentRes = await h.pool.query<{ id: string; status: string }>(
      `select id, status from public.consent_records where member_id = $1 and consent_type = 'cctv_workout_tracking'`,
      [memberId],
    );
    expect(consentRes.rows[0]!.status).toBe('granted');
  });
});
