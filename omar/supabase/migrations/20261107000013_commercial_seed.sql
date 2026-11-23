-- ============================================================================
-- Default Commercial Plan Templates and AI Promotion Seed (migration 0013)
-- ============================================================================

-- Ensure fallback tenant exists if seed.sql has not run yet
insert into public.organizations (id, name, slug, country_code)
values ('a0000000-0000-4000-8000-000000000001', 'DEMO Smart Gym (test data)', 'demo-smart-gym', 'EG')
on conflict (id) do nothing;

do $$
declare
  v_org record;
begin
  for v_org in select id from public.organizations loop
    -- 1. Standard Membership Plans
    insert into public.membership_plans (
      organization_id, name, code, description, price, currency, duration_days,
      included_sessions, freeze_allowance_days, max_freeze_count, ai_training_included, is_active
    ) values
      (v_org.id, '1 Month Standard', 'PLAN_1M', 'Full gym floor access with locker and standard amenities for 30 days.', 1200.00, 'EGP', 30, null, 0, 0, false, true),
      (v_org.id, '3 Months Standard', 'PLAN_3M', 'Quarterly commitment with 1 guest invitation included.', 3200.00, 'EGP', 90, null, 7, 1, false, true),
      (v_org.id, '6 Months Pro', 'PLAN_6M', 'Semi-annual membership with AI Workout Tracking and 15 freeze days.', 5800.00, 'EGP', 180, null, 15, 2, true, true),
      (v_org.id, '12 Months VIP', 'PLAN_12M', 'Annual all-inclusive pass with AI tracking, sauna, towel service, and 30 freeze days.', 10500.00, 'EGP', 365, null, 30, 4, true, true),
      (v_org.id, 'Day Pass', 'PLAN_DAY', 'Single calendar day full facility access.', 150.00, 'EGP', 1, 1, 0, 0, false, true),
      (v_org.id, '10-Session Punch Card', 'PLAN_SESSIONS_10', 'Flexible 10 visits valid across 60 days.', 1500.00, 'EGP', 60, 10, 0, 0, false, true),
      (v_org.id, 'Personal Training (12 Sessions)', 'PLAN_PT_12', '12 one-on-one certified coach training sessions with AI biomechanics analysis.', 3600.00, 'EGP', 45, 12, 5, 1, true, true)
    on conflict (organization_id, code) do update set
      name = excluded.name,
      price = excluded.price,
      duration_days = excluded.duration_days,
      ai_training_included = excluded.ai_training_included;

    -- 2. Initial Configured Promotions & AI Suggested Drafts
    insert into public.promotions (
      organization_id, code, name, description, discount_type, discount_value,
      min_subscription_amount, max_discount_amount, usage_limit, is_active, is_ai_generated, ai_approval_status
    ) values
      (v_org.id, 'WELCOME10', 'New Member Welcome Discount', '10% off any new subscription upon registration.', 'percentage', 10.00, 1000.00, 500.00, 200, true, false, 'approved'),
      (v_org.id, 'AI_FREE_TRIAL', 'AI Vision Add-on Free Trial', 'Free AI workout tracking add-on for 1-month and 3-month plans.', 'ai_addon_free', 0.00, null, null, 100, true, false, 'approved'),
      (v_org.id, 'ANNUAL_CASH_500', 'Annual Cash Discount', 'EGP 500 fixed reduction on 12-Month VIP plans.', 'fixed_amount', 500.00, 9000.00, 500.00, 50, true, false, 'approved'),
      (v_org.id, 'AI_SUGGEST_OFFPEAK_20', '[AI Suggestion] Off-Peak Student Campaign', 'Recommended 20% discount on 3M plans to boost off-peak capacity based on recent attendance dips.', 'percentage', 20.00, 2500.00, 800.00, 75, false, true, 'draft')
    on conflict (organization_id, code) do nothing;

  end loop;
end $$;

