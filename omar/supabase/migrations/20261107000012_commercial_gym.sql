-- ============================================================================
-- Commercial Gym Management, Subscriptions, Pricing & Entitlements (migration 0012)
-- ============================================================================

-- Additive fields to public.members for complete gym profile
alter table public.members
  add column if not exists date_of_birth date,
  add column if not exists emergency_contact_name text,
  add column if not exists emergency_contact_phone text,
  add column if not exists notes text;

-- Enhance _enforce_branch_org trigger so inserting members auto-populates organization_id from branch
create or replace function public._enforce_branch_org()
returns trigger language plpgsql set search_path = public as $$
begin
  if new.branch_id is not null and new.organization_id is null then
    new.organization_id := public.branch_org(new.branch_id);
  end if;
  if new.branch_id is not null and new.organization_id is distinct from public.branch_org(new.branch_id) then
    raise exception 'branch % does not belong to organization %', new.branch_id, new.organization_id
      using errcode = '42501';
  end if;
  return new;
end $$;

-- 1. Membership Package Plans
create table if not exists public.membership_plans (
  id                    uuid primary key default gen_random_uuid(),
  organization_id       uuid not null references public.organizations(id) on delete cascade,
  name                  text not null check (length(name) between 2 and 100),
  code                  text not null check (length(code) between 2 and 30),
  description           text,
  price                 numeric(10, 2) not null check (price >= 0),
  currency              text not null default 'EGP',
  duration_days         integer not null check (duration_days > 0),
  included_sessions     integer check (included_sessions is null or included_sessions > 0),
  freeze_allowance_days integer not null default 0 check (freeze_allowance_days >= 0),
  max_freeze_count      integer not null default 0 check (max_freeze_count >= 0),
  ai_training_included  boolean not null default false,
  ai_addon_fee          numeric(10, 2) not null default 150.00 check (ai_addon_fee >= 0),
  is_active             boolean not null default true,
  created_at            timestamptz not null default now(),
  updated_at            timestamptz not null default now(),
  unique (organization_id, code)
);

create index if not exists idx_membership_plans_org on public.membership_plans(organization_id);
create trigger trg_membership_plans_updated before update on public.membership_plans
  for each row execute function public.set_updated_at();

-- 2. Promotions & Discounts
create table if not exists public.promotions (
  id                      uuid primary key default gen_random_uuid(),
  organization_id         uuid not null references public.organizations(id) on delete cascade,
  code                    text not null check (length(code) between 2 and 40),
  name                    text not null check (length(name) between 2 and 120),
  description             text,
  discount_type           text not null check (discount_type in ('percentage', 'fixed_amount', 'extra_days', 'ai_addon_free')),
  discount_value          numeric(10, 2) not null check (discount_value >= 0),
  applies_to_plan_id      uuid references public.membership_plans(id) on delete cascade,
  min_subscription_amount numeric(10, 2),
  max_discount_amount     numeric(10, 2),
  usage_limit             integer check (usage_limit is null or usage_limit > 0),
  used_count              integer not null default 0 check (used_count >= 0),
  valid_from              timestamptz not null default now(),
  valid_until             timestamptz,
  is_active               boolean not null default true,
  is_ai_generated         boolean not null default false,
  ai_approval_status      text not null default 'approved' check (ai_approval_status in ('draft', 'approved', 'rejected')),
  created_at              timestamptz not null default now(),
  updated_at              timestamptz not null default now(),
  unique (organization_id, code)
);

create index if not exists idx_promotions_org on public.promotions(organization_id);
create trigger trg_promotions_updated before update on public.promotions
  for each row execute function public.set_updated_at();

-- 3. Member Subscriptions
create table if not exists public.member_subscriptions (
  id                     uuid primary key default gen_random_uuid(),
  organization_id        uuid not null references public.organizations(id) on delete cascade,
  branch_id              uuid not null references public.branches(id) on delete restrict,
  member_id              uuid not null references public.members(id) on delete cascade,
  plan_id                uuid not null references public.membership_plans(id) on delete restrict,
  promotion_id           uuid references public.promotions(id) on delete set null,
  status                 text not null default 'active' check (status in ('active', 'frozen', 'expired', 'cancelled', 'pending_payment')),
  starts_at              timestamptz not null default now(),
  expires_at             timestamptz not null,
  original_expires_at    timestamptz not null,
  total_price            numeric(10, 2) not null check (total_price >= 0),
  discount_amount        numeric(10, 2) not null default 0.00 check (discount_amount >= 0),
  paid_amount            numeric(10, 2) not null default 0.00 check (paid_amount >= 0),
  remaining_balance      numeric(10, 2) not null default 0.00 check (remaining_balance >= 0),
  currency               text not null default 'EGP',
  ai_addon_active        boolean not null default false,
  sessions_remaining     integer check (sessions_remaining is null or sessions_remaining >= 0),
  total_freeze_days_used integer not null default 0 check (total_freeze_days_used >= 0),
  freeze_count_used      integer not null default 0 check (freeze_count_used >= 0),
  created_at             timestamptz not null default now(),
  updated_at             timestamptz not null default now()
);

create index if not exists idx_member_subscriptions_member on public.member_subscriptions(member_id);
create index if not exists idx_member_subscriptions_status on public.member_subscriptions(status, expires_at);
create trigger trg_member_subscriptions_updated before update on public.member_subscriptions
  for each row execute function public.set_updated_at();

-- 4. Subscription Freeze Logs
create table if not exists public.subscription_freezes (
  id              uuid primary key default gen_random_uuid(),
  subscription_id uuid not null references public.member_subscriptions(id) on delete cascade,
  member_id       uuid not null references public.members(id) on delete cascade,
  start_date      timestamptz not null default now(),
  end_date        timestamptz not null,
  days_frozen     integer not null check (days_frozen > 0),
  reason          text,
  status          text not null default 'active' check (status in ('active', 'completed', 'revoked')),
  approved_by     uuid references auth.users(id) on delete set null,
  created_at      timestamptz not null default now()
);

create index if not exists idx_subscription_freezes_sub on public.subscription_freezes(subscription_id);

-- 5. Payments Ledger
create table if not exists public.payments (
  id               uuid primary key default gen_random_uuid(),
  organization_id  uuid not null references public.organizations(id) on delete cascade,
  branch_id        uuid not null references public.branches(id) on delete restrict,
  member_id        uuid not null references public.members(id) on delete cascade,
  subscription_id  uuid references public.member_subscriptions(id) on delete set null,
  amount           numeric(10, 2) not null check (amount > 0),
  currency         text not null default 'EGP',
  method           text not null check (method in ('cash', 'credit_card', 'bank_transfer', 'pos_terminal', 'online_gateway')),
  status           text not null default 'completed' check (status in ('completed', 'pending', 'refunded', 'failed')),
  reference_number text,
  notes            text,
  recorded_by      uuid references auth.users(id) on delete set null,
  created_at       timestamptz not null default now()
);

create index if not exists idx_payments_member on public.payments(member_id);
create index if not exists idx_payments_sub on public.payments(subscription_id);

-- 6. Invoices
create table if not exists public.invoices (
  id              uuid primary key default gen_random_uuid(),
  organization_id uuid not null references public.organizations(id) on delete cascade,
  branch_id       uuid not null references public.branches(id) on delete restrict,
  member_id       uuid not null references public.members(id) on delete cascade,
  subscription_id uuid references public.member_subscriptions(id) on delete set null,
  invoice_number  text not null unique,
  subtotal        numeric(10, 2) not null check (subtotal >= 0),
  discount        numeric(10, 2) not null default 0.00 check (discount >= 0),
  total           numeric(10, 2) not null check (total >= 0),
  status          text not null default 'paid' check (status in ('draft', 'paid', 'unpaid', 'void', 'partially_paid')),
  issued_at       timestamptz not null default now(),
  due_at          timestamptz not null default now(),
  created_at      timestamptz not null default now()
);

create index if not exists idx_invoices_member on public.invoices(member_id);

-- 7. Referral Records
create table if not exists public.referral_records (
  id                  uuid primary key default gen_random_uuid(),
  organization_id     uuid not null references public.organizations(id) on delete cascade,
  referrer_member_id  uuid not null references public.members(id) on delete cascade,
  referred_member_id  uuid not null references public.members(id) on delete cascade,
  referral_code       text not null,
  reward_applied      boolean not null default false,
  created_at          timestamptz not null default now(),
  unique (referrer_member_id, referred_member_id)
);

-- 8. Member Notification Preferences
create table if not exists public.notification_preferences (
  id                         uuid primary key default gen_random_uuid(),
  member_id                  uuid not null references public.members(id) on delete cascade unique,
  workout_reports            boolean not null default true,
  subscription_expiry_alerts boolean not null default true,
  promotional_offers         boolean not null default false,
  updated_at                 timestamptz not null default now()
);

-- ----------------------------------------------------------------------------
-- RLS Activation on New Tables
-- ----------------------------------------------------------------------------
alter table public.membership_plans enable row level security;
alter table public.promotions enable row level security;
alter table public.member_subscriptions enable row level security;
alter table public.subscription_freezes enable row level security;
alter table public.payments enable row level security;
alter table public.invoices enable row level security;
alter table public.referral_records enable row level security;
alter table public.notification_preferences enable row level security;

-- Policies for membership_plans: All authenticated users can read plans; staff can manage
create policy plans_select_auth on public.membership_plans for select to authenticated using (true);
create policy plans_write_staff on public.membership_plans for all to authenticated using (
  public.has_org_role(organization_id, 'owner', 'admin', 'manager')
);

-- Policies for promotions: Authenticated can read active promotions; staff can manage
create policy promotions_select on public.promotions for select to authenticated using (true);
create policy promotions_write on public.promotions for all to authenticated using (
  public.has_org_role(organization_id, 'owner', 'admin', 'manager')
);

-- Policies for member_subscriptions: Members can view their own; branch staff can manage
create policy subscriptions_member_select on public.member_subscriptions for select to authenticated using (
  member_id = public.current_member_id() or public.is_branch_staff(branch_id)
);
create policy subscriptions_staff_write on public.member_subscriptions for all to authenticated using (
  public.is_branch_staff(branch_id)
);

-- Policies for subscription_freezes:
create policy freezes_select on public.subscription_freezes for select to authenticated using (
  member_id = public.current_member_id() or exists (
    select 1 from public.members m where m.id = subscription_freezes.member_id and public.is_branch_staff(m.branch_id)
  )
);
create policy freezes_write on public.subscription_freezes for all to authenticated using (
  exists (
    select 1 from public.members m where m.id = subscription_freezes.member_id and public.is_branch_staff(m.branch_id)
  )
);

-- Policies for payments and invoices:
create policy payments_select on public.payments for select to authenticated using (
  member_id = public.current_member_id() or public.is_branch_staff(branch_id)
);
create policy payments_write on public.payments for all to authenticated using (
  public.is_branch_staff(branch_id)
);

create policy invoices_select on public.invoices for select to authenticated using (
  member_id = public.current_member_id() or public.is_branch_staff(branch_id)
);
create policy invoices_write on public.invoices for all to authenticated using (
  public.is_branch_staff(branch_id)
);

-- Policies for notification_preferences:
create policy notif_pref_select on public.notification_preferences for select to authenticated using (
  member_id = public.current_member_id() or exists (
    select 1 from public.members m where m.id = notification_preferences.member_id and public.is_branch_staff(m.branch_id)
  )
);
create policy notif_pref_write on public.notification_preferences for all to authenticated using (
  member_id = public.current_member_id() or exists (
    select 1 from public.members m where m.id = notification_preferences.member_id and public.is_branch_staff(m.branch_id)
  )
);

-- ----------------------------------------------------------------------------
-- Transaction-Safe Business Logic Functions
-- ----------------------------------------------------------------------------

-- A. Evaluate Member Commercial Entitlement & Validity
create or replace function public.fn_get_member_entitlement(p_member_id uuid)
returns table (
  is_active boolean,
  status text,
  days_remaining integer,
  ai_training_entitled boolean,
  plan_name text,
  plan_code text,
  expires_at timestamptz,
  sessions_remaining integer,
  subscription_id uuid
) language plpgsql stable security definer set search_path = public as $$
declare
  v_sub record;
begin
  select s.id, s.status, s.expires_at, s.ai_addon_active, s.sessions_remaining,
         p.name as plan_name, p.code as plan_code, p.ai_training_included
  into v_sub
  from public.member_subscriptions s
  join public.membership_plans p on p.id = s.plan_id
  where s.member_id = p_member_id
    and s.status in ('active', 'frozen')
  order by s.expires_at desc
  limit 1;

  if not found then
    return query select
      false, 'expired'::text, 0, false, 'No Active Subscription'::text, 'NONE'::text, null::timestamptz, 0, null::uuid;
    return;
  end if;

  if v_sub.status = 'frozen' then
    return query select
      false, 'frozen'::text, greatest(0, ceil(extract(epoch from (v_sub.expires_at - now())) / 86400.0)::integer),
      false, v_sub.plan_name, v_sub.plan_code, v_sub.expires_at, v_sub.sessions_remaining, v_sub.id;
    return;
  end if;

  if v_sub.expires_at < now() then
    -- Mark expired in query
    return query select
      false, 'expired'::text, 0, false, v_sub.plan_name, v_sub.plan_code, v_sub.expires_at, 0, v_sub.id;
    return;
  end if;

  return query select
    true,
    'active'::text,
    greatest(0, ceil(extract(epoch from (v_sub.expires_at - now())) / 86400.0)::integer),
    (v_sub.ai_training_included or v_sub.ai_addon_active),
    v_sub.plan_name,
    v_sub.plan_code,
    v_sub.expires_at,
    v_sub.sessions_remaining,
    v_sub.id;
end;
$$;

-- B. Freeze Subscription Procedure
create or replace function public.fn_freeze_subscription(
  p_subscription_id uuid,
  p_days integer,
  p_reason text,
  p_staff_profile_id uuid
)
returns jsonb language plpgsql volatile security definer set search_path = public as $$
declare
  v_sub record;
  v_plan record;
  v_new_expires timestamptz;
  v_freeze_id uuid;
begin
  select * into v_sub from public.member_subscriptions where id = p_subscription_id for update;
  if not found then
    return jsonb_build_object('ok', false, 'error', 'Subscription not found');
  end if;

  if v_sub.status <> 'active' then
    return jsonb_build_object('ok', false, 'error', 'Only active subscriptions can be frozen');
  end if;

  select * into v_plan from public.membership_plans where id = v_sub.plan_id;

  if (v_sub.total_freeze_days_used + p_days) > v_plan.freeze_allowance_days then
    return jsonb_build_object('ok', false, 'error', format('Exceeds plan freeze allowance of %s days (%s already used)', v_plan.freeze_allowance_days, v_sub.total_freeze_days_used));
  end if;

  if (v_sub.freeze_count_used + 1) > v_plan.max_freeze_count then
    return jsonb_build_object('ok', false, 'error', format('Exceeds maximum freeze count of %s', v_plan.max_freeze_count));
  end if;

  v_new_expires := v_sub.expires_at + (p_days * interval '1 day');

  insert into public.subscription_freezes (
    subscription_id, member_id, start_date, end_date, days_frozen, reason, status, approved_by
  ) values (
    v_sub.id, v_sub.member_id, now(), now() + (p_days * interval '1 day'), p_days, p_reason, 'active', p_staff_profile_id
  ) returning id into v_freeze_id;

  update public.member_subscriptions
  set status = 'frozen',
      expires_at = v_new_expires,
      total_freeze_days_used = total_freeze_days_used + p_days,
      freeze_count_used = freeze_count_used + 1,
      updated_at = now()
  where id = v_sub.id;

  update public.members set status = 'frozen', updated_at = now() where id = v_sub.member_id;

  return jsonb_build_object(
    'ok', true,
    'freeze_id', v_freeze_id,
    'new_expires_at', v_new_expires,
    'days_frozen', p_days
  );
end;
$$;

-- C. Unfreeze Subscription Procedure
create or replace function public.fn_unfreeze_subscription(p_subscription_id uuid)
returns jsonb language plpgsql volatile security definer set search_path = public as $$
declare
  v_sub record;
begin
  select * into v_sub from public.member_subscriptions where id = p_subscription_id for update;
  if not found then
    return jsonb_build_object('ok', false, 'error', 'Subscription not found');
  end if;

  if v_sub.status <> 'frozen' then
    return jsonb_build_object('ok', false, 'error', 'Subscription is not frozen');
  end if;

  update public.subscription_freezes
  set status = 'completed'
  where subscription_id = v_sub.id and status = 'active';

  update public.member_subscriptions
  set status = 'active', updated_at = now()
  where id = v_sub.id;

  update public.members set status = 'active', updated_at = now() where id = v_sub.member_id;

  return jsonb_build_object('ok', true, 'status', 'active');
end;
$$;

-- D. Renew / Extend Subscription Procedure
create or replace function public.fn_renew_subscription(
  p_member_id uuid,
  p_plan_id uuid,
  p_branch_id uuid,
  p_payment_method text,
  p_ai_addon boolean,
  p_promo_code text,
  p_staff_profile_id uuid
)
returns jsonb language plpgsql volatile security definer set search_path = public as $$
declare
  v_plan record;
  v_member record;
  v_promo record;
  v_promo_id uuid := null;
  v_curr_sub record;
  v_start_t timestamptz;
  v_expire_t timestamptz;
  v_price numeric(10, 2);
  v_discount numeric(10, 2) := 0.00;
  v_final_price numeric(10, 2);
  v_new_sub_id uuid;
  v_invoice_num text;
begin
  select * into v_member from public.members where id = p_member_id;
  if not found then return jsonb_build_object('ok', false, 'error', 'Member not found'); end if;

  select * into v_plan from public.membership_plans where id = p_plan_id and is_active = true;
  if not found then return jsonb_build_object('ok', false, 'error', 'Plan not found or inactive'); end if;

  v_price := v_plan.price;
  if p_ai_addon and not v_plan.ai_training_included then
    v_price := v_price + coalesce(v_plan.ai_addon_fee, 0.00);
  end if;

  -- Apply promotion if valid
  if p_promo_code is not null and length(trim(p_promo_code)) > 0 then
    select * into v_promo from public.promotions
    where organization_id = v_member.organization_id
      and lower(code) = lower(trim(p_promo_code))
      and is_active = true
      and ai_approval_status = 'approved'
      and (valid_until is null or valid_until > now())
      and (usage_limit is null or used_count < usage_limit);

    if found then
      v_promo_id := v_promo.id;
      if v_promo.discount_type = 'percentage' then
        v_discount := round((v_price * (v_promo.discount_value / 100.0)), 2);
        if v_promo.max_discount_amount is not null and v_discount > v_promo.max_discount_amount then
          v_discount := v_promo.max_discount_amount;
        end if;
      elsif v_promo.discount_type = 'fixed_amount' then
        v_discount := least(v_price, v_promo.discount_value);
      end if;

      update public.promotions set used_count = used_count + 1 where id = v_promo.id;
    end if;
  end if;

  v_final_price := greatest(0.00, v_price - v_discount);

  -- Determine renewal start date: If member has active unexpired subscription, chain from expiry
  select expires_at into v_curr_sub from public.member_subscriptions
  where member_id = p_member_id and status = 'active' and expires_at > now()
  order by expires_at desc limit 1;

  if found and v_curr_sub.expires_at > now() then
    v_start_t := v_curr_sub.expires_at;
  else
    v_start_t := now();
  end if;

  v_expire_t := v_start_t + (v_plan.duration_days * interval '1 day');

  insert into public.member_subscriptions (
    organization_id, branch_id, member_id, plan_id, promotion_id, status,
    starts_at, expires_at, original_expires_at, total_price, discount_amount,
    paid_amount, remaining_balance, currency, ai_addon_active, sessions_remaining
  ) values (
    v_member.organization_id, p_branch_id, v_member.id, v_plan.id,
    v_promo_id, 'active', v_start_t, v_expire_t, v_expire_t,
    v_final_price, v_discount, v_final_price, 0.00, v_plan.currency,
    (p_ai_addon or v_plan.ai_training_included), v_plan.included_sessions
  ) returning id into v_new_sub_id;

  -- Record payment ledger entry
  insert into public.payments (
    organization_id, branch_id, member_id, subscription_id,
    amount, currency, method, status, recorded_by
  ) values (
    v_member.organization_id, p_branch_id, v_member.id, v_new_sub_id,
    v_final_price, v_plan.currency, p_payment_method, 'completed', p_staff_profile_id
  );

  -- Generate invoice
  v_invoice_num := 'INV-' || to_char(now(), 'YYYYMMDD') || '-' || upper(substring(gen_random_uuid()::text from 1 for 6));
  insert into public.invoices (
    organization_id, branch_id, member_id, subscription_id,
    invoice_number, subtotal, discount, total, status
  ) values (
    v_member.organization_id, p_branch_id, v_member.id, v_new_sub_id,
    v_invoice_num, v_price, v_discount, v_final_price, 'paid'
  );

  update public.members set status = 'active', updated_at = now() where id = p_member_id;

  return jsonb_build_object(
    'ok', true,
    'subscription_id', v_new_sub_id,
    'invoice_number', v_invoice_num,
    'starts_at', v_start_t,
    'expires_at', v_expire_t,
    'total_paid', v_final_price
  );
end;
$$;

