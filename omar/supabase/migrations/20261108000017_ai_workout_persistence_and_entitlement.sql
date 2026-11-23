-- ============================================================================
-- Migration 0017: Server-Side AI Entitlement Enforcement & Per-Rep Form Telemetry Persistence
-- ============================================================================

-- 1. Allow quality_label on repetition_events to store validated biomechanical verdicts
alter table public.repetition_events
  drop constraint if exists repetition_events_quality_label_check;

alter table public.repetition_events
  add constraint repetition_events_quality_label_check
  check (quality_label is null or quality_label in ('correct', 'incorrect'));

alter table public.repetition_events
  add column if not exists min_knee_angle_deg numeric(5,1),
  add column if not exists max_trunk_lean_deg numeric(5,1),
  add column if not exists detected_errors jsonb default '[]'::jsonb;

alter table public.exercise_sets
  add column if not exists valid_reps int not null default 0,
  add column if not exists imperfect_reps int not null default 0,
  add column if not exists rep_telemetry jsonb not null default '[]'::jsonb;

-- 2. RPC: Save AI Workout Session with strict server-side entitlement & identity checks
create or replace function public.save_ai_workout_session(
  p_member_id uuid default null,
  p_reps integer default 0,
  p_valid_reps integer default null,
  p_imperfect_reps integer default 0,
  p_exercise text default 'squat',
  p_duration_seconds integer default 60,
  p_form_score integer default 100,
  p_rep_telemetry jsonb default '[]'::jsonb,
  p_camera_id uuid default null
)
returns jsonb
language plpgsql
volatile
security definer
set search_path = public
as $$
declare
  v_caller_uid uuid := auth.uid();
  v_is_staff boolean := false;
  v_member record;
  v_sub record;
  v_has_any_sub boolean := false;
  v_now timestamptz := now();
  v_started_at timestamptz;
  v_session_id uuid;
  v_device_id uuid;
  v_equipment_id uuid;
  v_exercise_id uuid;
  v_usage_id uuid;
  v_activity_id uuid;
  v_set_id uuid;
  v_report_id uuid;
  v_valid int;
  v_imperfect int;
  v_rep jsonb;
  v_idx int := 0;
begin
  -- Resolve target member
  if p_member_id is not null then
    select * into v_member from public.members where id = p_member_id;
  elsif v_caller_uid is not null then
    select * into v_member from public.members where user_id = v_caller_uid limit 1;
  end if;

  if v_member.id is null then
    return jsonb_build_object('ok', false, 'code', 'MEMBER_NOT_FOUND', 'error', 'Verified member identity required to save workout.');
  end if;

  -- Authorization check: caller must be the member themselves, staff, or service_role (v_caller_uid is null)
  if v_caller_uid is not null and v_member.user_id is distinct from v_caller_uid then
    select exists(
      select 1 from public.staff_profiles
      where user_id = v_caller_uid and organization_id = v_member.organization_id
    ) into v_is_staff;
    if not v_is_staff then
      return jsonb_build_object('ok', false, 'code', 'FORBIDDEN', 'error', 'Cannot save workout for another member.');
    end if;
  end if;

  -- Check member status
  if v_member.status <> 'active' then
    return jsonb_build_object('ok', false, 'code', 'MEMBER_INACTIVE', 'error', 'Member account is not active (' || v_member.status || ').');
  end if;

  -- Check subscription and AI Coaching entitlement
  select exists(select 1 from public.member_subscriptions where member_id = v_member.id) into v_has_any_sub;
  if not v_has_any_sub then
    return jsonb_build_object(
      'ok', false,
      'code', 'NO_SUBSCRIPTION',
      'error', 'Active membership subscription required to save AI workouts.'
    );
  end if;

  select * into v_sub
  from public.member_subscriptions
  where member_id = v_member.id
  order by expires_at desc
  limit 1;

  if v_sub.status <> 'active' or v_sub.expires_at <= v_now then
    return jsonb_build_object(
      'ok', false,
      'code', 'SUBSCRIPTION_EXPIRED',
      'error', 'Membership subscription is ' || v_sub.status || ' or expired. Renew subscription to save AI workouts.'
    );
  end if;

  if not coalesce(v_sub.ai_addon_active, false) then
    return jsonb_build_object(
      'ok', false,
      'code', 'AI_ENTITLEMENT_REQUIRED',
      'error', 'Standard membership does not include AI Personal Trainer entitlement. Upgrade plan to enable AI coaching.'
    );
  end if;

  v_started_at := v_now - (greatest(1, coalesce(p_duration_seconds, 60)) * interval '1 second');
  v_valid := coalesce(p_valid_reps, greatest(0, p_reps - coalesce(p_imperfect_reps, 0)));
  v_imperfect := coalesce(p_imperfect_reps, greatest(0, p_reps - v_valid));

  -- Check if member already has an active attendance_session; if so, complete it, else insert completed session
  select id into v_session_id
  from public.attendance_sessions
  where member_id = v_member.id and status = 'active'
  limit 1;

  if v_session_id is not null then
    update public.attendance_sessions
    set check_out_at = v_now,
        check_out_method = 'qr',
        status = 'completed',
        updated_at = v_now
    where id = v_session_id;
  else
    insert into public.attendance_sessions (
      member_id, branch_id, check_in_at, check_out_at, check_in_method, check_out_method, status
    ) values (
      v_member.id, v_member.branch_id, v_started_at, v_now, 'qr', 'qr', 'completed'
    ) returning id into v_session_id;
  end if;

  -- Resolve branch edge device, equipment, and exercise catalog row for full relational traceability
  select id into v_device_id from public.edge_devices where branch_id = v_member.branch_id limit 1;
  select id into v_equipment_id from public.equipment where branch_id = v_member.branch_id limit 1;
  select id into v_exercise_id from public.exercise_catalog where lower(slug::text) = lower(coalesce(p_exercise, 'squat')) limit 1;
  if v_exercise_id is null then
    select id into v_exercise_id from public.exercise_catalog where lower(slug::text) = 'squat' limit 1;
  end if;

  if v_device_id is not null and v_equipment_id is not null and v_exercise_id is not null then
    insert into public.equipment_usage_sessions (
      edge_device_id, usage_local_id, attendance_session_id, equipment_id, camera_id,
      started_at, ended_at, end_reason, association_confidence
    ) values (
      v_device_id, 'u-ai-' || gen_random_uuid()::text, v_session_id, v_equipment_id, p_camera_id,
      v_started_at, v_now, 'checkout', 0.95
    ) returning id into v_usage_id;

    insert into public.exercise_activity_sessions (
      edge_device_id, activity_local_id, equipment_usage_session_id, exercise_id,
      started_at, ended_at, classification_confidence, active_seconds
    ) values (
      v_device_id, 'a-ai-' || gen_random_uuid()::text, v_usage_id, v_exercise_id,
      v_started_at, v_now, 0.95, greatest(1, coalesce(p_duration_seconds, 60))
    ) returning id into v_activity_id;

    insert into public.exercise_sets (
      edge_device_id, set_local_id, exercise_activity_session_id, set_index,
      started_at, ended_at, rep_count, valid_reps, imperfect_reps, confidence, verified, rep_telemetry
    ) values (
      v_device_id, 's-ai-' || gen_random_uuid()::text, v_activity_id, 1,
      v_started_at, v_now, greatest(0, p_reps), v_valid, v_imperfect,
      least(1.0, greatest(0.5, coalesce(p_form_score, 90) / 100.0)), true,
      coalesce(p_rep_telemetry, '[]'::jsonb)
    ) returning id into v_set_id;

    -- Persist individual per-rep telemetry rows if provided
    if jsonb_typeof(p_rep_telemetry) = 'array' and jsonb_array_length(p_rep_telemetry) > 0 then
      for v_rep in select * from jsonb_array_elements(p_rep_telemetry)
      loop
        v_idx := v_idx + 1;
        insert into public.repetition_events (
          id, exercise_set_id, rep_index, occurred_at, camera_id, confidence,
          quality_label, min_knee_angle_deg, max_trunk_lean_deg, detected_errors
        ) values (
          gen_random_uuid(),
          v_set_id,
          coalesce((v_rep->>'rep_number')::int, v_idx),
          v_started_at + (v_idx * interval '2 seconds'),
          p_camera_id,
          least(1.0, greatest(0.1, coalesce((v_rep->>'confidence')::numeric, 0.85))),
          coalesce(v_rep->>'verdict', 'correct'),
          (v_rep->>'min_knee_angle_deg')::numeric,
          (v_rep->>'max_trunk_lean_deg')::numeric,
          coalesce(v_rep->'detected_errors', '[]'::jsonb)
        ) on conflict (exercise_set_id, rep_index) do nothing;
      end loop;
    end if;
  end if;

  -- Upsert workout_reports summary
  insert into public.workout_reports (
    member_id, attendance_session_id, generated_at, summary
  ) values (
    v_member.id,
    v_session_id,
    v_now,
    jsonb_build_object(
      'total_reps', greatest(0, p_reps),
      'valid_reps', v_valid,
      'imperfect_reps', v_imperfect,
      'form_score', coalesce(p_form_score, 100),
      'exercise', coalesce(p_exercise, 'squat'),
      'total_sets', 1,
      'active_exercise_seconds', greatest(1, coalesce(p_duration_seconds, 60)),
      'visit_seconds', greatest(1, coalesce(p_duration_seconds, 60)),
      'rep_telemetry', coalesce(p_rep_telemetry, '[]'::jsonb),
      'uncertain_segments', '[]'::jsonb
    )
  )
  on conflict (attendance_session_id) do update
  set summary = excluded.summary,
      generated_at = excluded.generated_at
  returning id into v_report_id;

  return jsonb_build_object(
    'ok', true,
    'session_id', v_session_id,
    'report_id', v_report_id,
    'set_id', v_set_id,
    'member_id', v_member.id,
    'reps_saved', greatest(0, p_reps),
    'valid_reps', v_valid,
    'imperfect_reps', v_imperfect
  );
end;
$$;

grant execute on function public.save_ai_workout_session(uuid, integer, integer, integer, text, integer, integer, jsonb, uuid) to authenticated, service_role;

