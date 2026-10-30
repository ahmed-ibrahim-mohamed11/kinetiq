-- ============================================================================
-- Workout engine: session finalization, report generation, notifications
-- (migration 0003)
-- ----------------------------------------------------------------------------
-- These functions are the "workout-engine" and "report-engine" of the
-- architecture. They run inside Postgres so finalization is transactional:
-- either the session is completed, the report exists and the notification is
-- queued — or none of it happened.
-- ============================================================================

-- ----------------------------------------------------------------------------
-- Report summary builder (pure read; returns jsonb matching WorkoutReportSummary v1)
-- ----------------------------------------------------------------------------
create or replace function public.build_workout_summary(p_session_id uuid)
returns jsonb language plpgsql stable security definer set search_path = public as $$
declare
  v_session public.attendance_sessions%rowtype;
  v_exercises jsonb;
  v_uncertain jsonb;
  v_total_sets int;
  v_total_reps int;
  v_active numeric;
begin
  select * into v_session from public.attendance_sessions where id = p_session_id;
  if not found then
    raise exception 'attendance session % not found', p_session_id using errcode = 'P0002';
  end if;

  with usage as (
    select u.id, u.equipment_id, u.started_at, coalesce(u.ended_at, v_session.check_out_at, now()) as ended_at
    from public.equipment_usage_sessions u
    where u.attendance_session_id = p_session_id
  ),
  activity as (
    select a.id, a.exercise_id, a.equipment_usage_session_id, a.active_seconds, a.started_at
    from public.exercise_activity_sessions a
    join usage u on u.id = a.equipment_usage_session_id
  ),
  sets as (
    select s.id, s.exercise_activity_session_id, s.set_index, s.rep_count, s.rest_before_seconds, s.confidence, s.verified, s.started_at
    from public.exercise_sets s
    join activity a on a.id = s.exercise_activity_session_id
  ),
  per_exercise as (
    select
      ex.slug as exercise_slug,
      ex.name as exercise_name,
      eq.name as equipment_name,
      sum(extract(epoch from (u.ended_at - u.started_at)))::numeric as equipment_seconds,
      sum(a.active_seconds)::numeric as active_seconds,
      coalesce(
        jsonb_agg(
          jsonb_build_object(
            'set_index', s.set_index,
            'reps', s.rep_count,
            'rest_before_seconds', s.rest_before_seconds,
            'confidence', s.confidence,
            'verified', s.verified
          ) order by s.started_at
        ) filter (where s.id is not null),
        '[]'::jsonb
      ) as sets,
      coalesce(sum(s.rep_count), 0)::int as total_reps,
      min(a.started_at) as first_started
    from activity a
    join usage u on u.id = a.equipment_usage_session_id
    join public.exercise_catalog ex on ex.id = a.exercise_id
    left join public.equipment eq on eq.id = u.equipment_id
    left join sets s on s.exercise_activity_session_id = a.id
    group by ex.slug, ex.name, eq.name
  )
  select
    coalesce(jsonb_agg(to_jsonb(pe) - 'first_started' order by pe.first_started), '[]'::jsonb),
    coalesce(sum((select count(*) from jsonb_array_elements(pe.sets))), 0)::int,
    coalesce(sum(pe.total_reps), 0)::int,
    coalesce(sum(pe.active_seconds), 0)
  into v_exercises, v_total_sets, v_total_reps, v_active
  from per_exercise pe;

  select coalesce(jsonb_agg(jsonb_build_object('from', from_at, 'to', to_at, 'reason', reason) order by from_at), '[]'::jsonb)
  into v_uncertain
  from public.tracking_uncertain_segments
  where attendance_session_id = p_session_id;

  return jsonb_build_object(
    'schema_version', 1,
    'check_in_at', v_session.check_in_at,
    'check_out_at', coalesce(v_session.check_out_at, now()),
    'visit_seconds', extract(epoch from (coalesce(v_session.check_out_at, now()) - v_session.check_in_at))::int,
    'active_exercise_seconds', round(v_active)::int,
    'total_sets', v_total_sets,
    'total_reps', v_total_reps,
    'exercises', v_exercises,
    'uncertain_segments', v_uncertain
  );
end $$;

-- ----------------------------------------------------------------------------
-- Finalize a session: close open facts, build report, enqueue notification.
-- Idempotent: calling twice returns the existing report id.
-- ----------------------------------------------------------------------------
create or replace function public.finalize_attendance_session(
  p_session_id uuid,
  p_check_out_at timestamptz default now(),
  p_method public.checkin_method default 'staff_manual'
)
returns uuid language plpgsql security definer set search_path = public as $$
declare
  v_session public.attendance_sessions%rowtype;
  v_report_id uuid;
  v_has_push boolean;
begin
  select * into v_session from public.attendance_sessions where id = p_session_id for update;
  if not found then
    raise exception 'attendance session % not found', p_session_id using errcode = 'P0002';
  end if;

  if v_session.status = 'completed' then
    select id into v_report_id from public.workout_reports where attendance_session_id = p_session_id;
    return v_report_id;
  end if;

  update public.attendance_sessions
     set check_out_at = coalesce(check_out_at, p_check_out_at),
         check_out_method = coalesce(check_out_method, p_method),
         status = 'finalizing'
   where id = p_session_id;

  -- Close any dangling facts at checkout time. Sets left open are marked unverified:
  -- we do not know how the set really ended.
  update public.exercise_sets s
     set ended_at = coalesce(s.ended_at, p_check_out_at), verified = false
    from public.exercise_activity_sessions a
    join public.equipment_usage_sessions u on u.id = a.equipment_usage_session_id
   where s.exercise_activity_session_id = a.id
     and u.attendance_session_id = p_session_id
     and s.ended_at is null;

  update public.exercise_activity_sessions a
     set ended_at = coalesce(a.ended_at, p_check_out_at)
    from public.equipment_usage_sessions u
   where u.id = a.equipment_usage_session_id
     and u.attendance_session_id = p_session_id
     and a.ended_at is null;

  update public.equipment_usage_sessions
     set ended_at = coalesce(ended_at, p_check_out_at), end_reason = coalesce(end_reason, 'checkout')
   where attendance_session_id = p_session_id and ended_at is null;

  update public.person_tracks
     set ended_at = coalesce(ended_at, p_check_out_at), end_reason = coalesce(end_reason, 'checkout')
   where attendance_session_id = p_session_id and ended_at is null;

  insert into public.workout_reports (attendance_session_id, member_id, summary)
  values (p_session_id, v_session.member_id, public.build_workout_summary(p_session_id))
  on conflict (attendance_session_id) do update set summary = excluded.summary, generated_at = now()
  returning id into v_report_id;

  update public.attendance_sessions set status = 'completed' where id = p_session_id;

  -- Queue delivery. Web push if the member has a subscription, email as fallback when known.
  select exists (
    select 1 from public.push_subscriptions ps
    join public.members m on m.user_id = ps.user_id
    where m.id = v_session.member_id and ps.disabled_at is null
  ) into v_has_push;

  insert into public.notification_jobs (member_id, channel, template, payload, dedupe_key)
  values (
    v_session.member_id,
    case when v_has_push then 'web_push'::public.notification_channel else 'email'::public.notification_channel end,
    'workout_report_ready',
    jsonb_build_object('report_id', v_report_id, 'attendance_session_id', p_session_id),
    'workout_report_ready:' || p_session_id::text
  )
  on conflict (dedupe_key) do nothing;

  return v_report_id;
end $$;

-- ----------------------------------------------------------------------------
-- Staff check-in / check-out RPCs (callable from the browser; RLS-aware checks)
-- ----------------------------------------------------------------------------
create or replace function public.staff_check_in(p_member_id uuid, p_method public.checkin_method default 'staff_manual')
returns uuid language plpgsql security definer set search_path = public as $$
declare
  v_member public.members%rowtype;
  v_session_id uuid;
begin
  if p_method = 'face' then
    raise exception 'face check-in is performed by the edge device only' using errcode = '42501';
  end if;
  select * into v_member from public.members where id = p_member_id;
  if not found then raise exception 'member not found' using errcode = 'P0002'; end if;
  if not public.has_branch_role(v_member.branch_id, 'owner', 'admin', 'manager', 'front_desk', 'trainer') then
    raise exception 'not authorized for this branch' using errcode = '42501';
  end if;
  if v_member.status <> 'active' then
    raise exception 'membership is %', v_member.status using errcode = '23514';
  end if;

  select id into v_session_id from public.attendance_sessions where member_id = p_member_id and status = 'active';
  if v_session_id is not null then
    return v_session_id;
  end if;

  insert into public.attendance_sessions (member_id, branch_id, check_in_at, check_in_method, check_in_confidence)
  values (p_member_id, v_member.branch_id, now(), p_method, 1.0)
  returning id into v_session_id;
  return v_session_id;
end $$;

create or replace function public.staff_check_out(p_session_id uuid, p_method public.checkin_method default 'staff_manual')
returns uuid language plpgsql security definer set search_path = public as $$
declare
  v_branch uuid;
begin
  select branch_id into v_branch from public.attendance_sessions where id = p_session_id;
  if v_branch is null then raise exception 'session not found' using errcode = 'P0002'; end if;
  if not public.has_branch_role(v_branch, 'owner', 'admin', 'manager', 'front_desk', 'trainer') then
    raise exception 'not authorized for this branch' using errcode = '42501';
  end if;
  return public.finalize_attendance_session(p_session_id, now(), p_method);
end $$;

-- Members can check themselves out from their own dashboard (QR flow lands here too).
create or replace function public.member_self_check_out()
returns uuid language plpgsql security definer set search_path = public as $$
declare
  v_session_id uuid;
begin
  select id into v_session_id from public.attendance_sessions
   where member_id = public.current_member_id() and status = 'active';
  if v_session_id is null then
    raise exception 'no active session' using errcode = 'P0002';
  end if;
  return public.finalize_attendance_session(v_session_id, now(), 'qr');
end $$;

-- Abandon stale sessions (no checkout within N hours). Scheduled by pg_cron in 0005.
create or replace function public.abandon_stale_sessions(p_max_hours int default 6)
returns int language plpgsql security definer set search_path = public as $$
declare v_count int := 0; v_id uuid;
begin
  for v_id in
    select id from public.attendance_sessions
    where status = 'active' and check_in_at < now() - make_interval(hours => p_max_hours)
  loop
    perform public.finalize_attendance_session(v_id, now(), 'staff_manual');
    update public.attendance_sessions set status = 'abandoned' where id = v_id;
    v_count := v_count + 1;
  end loop;
  return v_count;
end $$;

-- Public (anon) report lookup by share token — returns only the summary, never ids.
create or replace function public.get_shared_report(p_token text)
returns jsonb language plpgsql stable security definer set search_path = public as $$
declare v jsonb;
begin
  select jsonb_build_object(
           'member_name', m.full_name,
           'generated_at', r.generated_at,
           'summary', r.summary
         )
    into v
    from public.workout_reports r
    join public.members m on m.id = r.member_id
   where r.share_token = p_token and r.share_expires_at > now();
  return v; -- null when not found/expired; caller returns 404
end $$;

revoke all on function public.finalize_attendance_session(uuid, timestamptz, public.checkin_method) from public, anon, authenticated;
revoke all on function public.build_workout_summary(uuid) from public, anon, authenticated;
revoke all on function public.abandon_stale_sessions(int) from public, anon, authenticated;
grant execute on function public.staff_check_in(uuid, public.checkin_method) to authenticated;
grant execute on function public.staff_check_out(uuid, public.checkin_method) to authenticated;
grant execute on function public.member_self_check_out() to authenticated;
grant execute on function public.get_shared_report(text) to anon, authenticated;
