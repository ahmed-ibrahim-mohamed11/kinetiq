-- ============================================================================
-- Fix Subscription Promotion Reference Bug (migration 0014)
-- Resolves runtime exception: record "v_promo" is not assigned yet
-- ============================================================================

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

revoke all on function public.fn_renew_subscription(uuid, uuid, uuid, text, boolean, text, uuid) from public, anon;
grant execute on function public.fn_renew_subscription(uuid, uuid, uuid, text, boolean, text, uuid) to authenticated, service_role;

