-- ============================================================================
-- Check-in actors, QR check-in, workout summary fixes, report sharing,
-- notification-job and push-subscription hardening (migration 0010)
-- ----------------------------------------------------------------------------
--   C1  public.qr_check_in(member_code, staff_user_id)  (service role only)
--   C2  attendance_sessions.checked_in_by / checked_out_by; staff_check_in
--       accepts only 'staff_manual' | 'nfc'
--   C3  report share links are created on demand by the member, revocable,
--       audit-logged; get_shared_report exposes only the first name; members
--       may update only workout_reports.viewed_at
--   build_workout_summary no longer multiplies times by the number of sets;
--   finalize_attendance_session (signature unchanged) clamps closing times so
--   clock skew cannot violate check constraints; abandon_stale_sessions isolates
--   each session; facts that arrive after finalisation refresh the report.
--   Notification jobs: bounded retries after lock expiry, completion guard,
--   claim ignores exhausted jobs. Push endpoints must belong to a known
--   Web Push service (no SSRF through member-supplied URLs).
-- Applied migrations are immutable: everything here is create or replace /
-- alter / drop+create.
-- ============================================================================

-- ----------------------------------------------------------------------------
-- C2: who checked a member in / out (null for edge-originated actions)
-- ----------------------------------------------------------------------------
alter table public.attendance_sessions
  add column if not exists checked_in_by  uuid references auth.users(id) on delete set null,
  add column if not exists checked_out_by uuid references auth.users(id) on delete set null;

-- ----------------------------------------------------------------------------
-- Report summary builder. Each level is aggregated before it is combined, so
-- active_seconds is counted once per activity and equipment_seconds once per
-- usage (previously both were multiplied by the number of sets).
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
    select u.id, u.equipment_id,
           extract(epoch from (greatest(coalesce(u.ended_at, v_session.check_out_at, now()), u.started_at) - u.started_at))::numeric as usage_seconds
    from public.equipment_usage_sessions u
    where u.attendance_session_id = p_session_id
  ),
  -- one row per activity, with its exercise/equipment grouping key
  activity as (
    select a.id, a.active_seconds, a.started_at, u.id as usage_id, u.usage_seconds,
           ex.slug::text as exercise_slug, ex.name as exercise_name, eq.name as equipment_name,
           row_number() over (partition by ex.slug, ex.name, eq.name, u.id order by a.started_at, a.id) as usage_rank
    from public.exercise_activity_sessions a
    join usage u on u.id = a.equipment_usage_session_id
    join public.exercise_catalog ex on ex.id = a.exercise_id
    left join public.equipment eq on eq.id = u.equipment_id
  ),
  group_time as (
    select exercise_slug, exercise_name, equipment_name,
           coalesce(sum(usage_seconds) filter (where usage_rank = 1), 0)::numeric as equipment_seconds,
           sum(active_seconds)::numeric as active_seconds,
           min(started_at) as first_started
    from activity
    group by exercise_slug, exercise_name, equipment_name
  ),
  -- one row per set (a set belongs to exactly one activity: no fan-out)
  group_sets as (
    select a.exercise_slug, a.exercise_name, a.equipment_name,
           jsonb_agg(
             jsonb_build_object(
               'set_index', s.set_index,
               'reps', s.rep_count,
               'rest_before_seconds', s.rest_before_seconds,
               'confidence', s.confidence,
               'verified', s.verified
             ) order by s.started_at
           ) as sets,
           sum(s.rep_count)::int as total_reps
    from activity a
    join public.exercise_sets s on s.exercise_activity_session_id = a.id
    group by a.exercise_slug, a.exercise_name, a.equipment_name
  ),
  per_exercise as (
    select t.exercise_slug, t.exercise_name, t.equipment_name, t.equipment_seconds, t.active_seconds,
           coalesce(gs.sets, '[]'::jsonb) as sets,
           coalesce(gs.total_reps, 0)::int as total_reps,
           t.first_started
    from group_time t
    left join group_sets gs
      on gs.exercise_slug = t.exercise_slug
     and gs.exercise_name = t.exercise_name
     and gs.equipment_name is not distinct from t.equipment_name
  )
  select
    coalesce(jsonb_agg(to_jsonb(pe) - 'first_started' order by pe.first_started), '[]'::jsonb),
    coalesce(sum(jsonb_array_length(pe.sets)), 0)::int,
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
-- Push endpoints must be https URLs of a known Web Push service. Members
-- supply them, and the dispatcher POSTs to them, so anything else is SSRF.
-- ----------------------------------------------------------------------------
create or replace function public.is_allowed_push_endpoint(p_endpoint text)
returns boolean language sql immutable set search_path = public as $$
  select coalesce(
    length(p_endpoint) <= 2048
    and p_endpoint ~* '^https://(fcm\.googleapis\.com|android\.googleapis\.com|updates\.push\.services\.mozilla\.com|web\.push\.apple\.com|[a-z0-9-]+\.notify\.windows\.com)/\S*$',
    false)
$$;
grant execute on function public.is_allowed_push_endpoint(text) to authenticated, service_role;

create or replace function public.tg_push_subscription_endpoint()
returns trigger language plpgsql set search_path = public as $$
begin
  if not public.is_allowed_push_endpoint(new.endpoint) then
    raise exception 'push endpoint must be an https URL of a known Web Push service' using errcode = '22023';
  end if;
  return new;
end $$;
revoke all on function public.tg_push_subscription_endpoint() from public, anon, authenticated;

drop trigger if exists trg_push_endpoint_check on public.push_subscriptions;
create trigger trg_push_endpoint_check before insert or update of endpoint on public.push_subscriptions
  for each row execute function public.tg_push_subscription_endpoint();

-- Existing subscriptions that would be refused now are disabled (not deleted).
update public.push_subscriptions set disabled_at = now()
 where disabled_at is null and not public.is_allowed_push_endpoint(endpoint);

create or replace function public.member_push_subscriptions(p_member_id uuid)
returns table (id uuid, endpoint text, p256dh text, auth text) language sql stable security definer set search_path = public as $$
  select ps.id, ps.endpoint, ps.p256dh, ps.auth
  from public.push_subscriptions ps
  join public.members m on m.user_id = ps.user_id
  where m.id = p_member_id and ps.disabled_at is null and public.is_allowed_push_endpoint(ps.endpoint)
$$;

-- ----------------------------------------------------------------------------
-- Finalize a session (signature unchanged). Idempotent for completed AND
-- abandoned sessions. Closing times never precede the fact's start, so edge
-- clock skew cannot violate the ended_at >= started_at constraints.
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
  v_out timestamptz;
begin
  select * into v_session from public.attendance_sessions where id = p_session_id for update;
  if not found then
    raise exception 'attendance session % not found', p_session_id using errcode = 'P0002';
  end if;

  if v_session.status in ('completed', 'abandoned') then
    select id into v_report_id from public.workout_reports where attendance_session_id = p_session_id;
    return v_report_id;
  end if;

  v_out := greatest(coalesce(v_session.check_out_at, p_check_out_at, now()), v_session.check_in_at);

  update public.attendance_sessions
     set check_out_at = v_out,
         check_out_method = coalesce(check_out_method, p_method),
         status = 'finalizing'
   where id = p_session_id;

  -- Close any dangling facts at checkout time. Sets left open are marked unverified:
  -- we do not know how the set really ended.
  update public.exercise_sets s
     set ended_at = greatest(v_out, s.started_at), verified = false
    from public.exercise_activity_sessions a
    join public.equipment_usage_sessions u on u.id = a.equipment_usage_session_id
   where s.exercise_activity_session_id = a.id
     and u.attendance_session_id = p_session_id
     and s.ended_at is null;

  update public.exercise_activity_sessions a
     set ended_at = greatest(v_out, a.started_at)
    from public.equipment_usage_sessions u
   where u.id = a.equipment_usage_session_id
     and u.attendance_session_id = p_session_id
     and a.ended_at is null;

  update public.equipment_usage_sessions
     set ended_at = greatest(v_out, started_at), end_reason = coalesce(end_reason, 'checkout')
   where attendance_session_id = p_session_id and ended_at is null;

  update public.person_tracks
     set ended_at = greatest(v_out, started_at), end_reason = coalesce(end_reason, 'checkout')
   where attendance_session_id = p_session_id and ended_at is null;

  insert into public.workout_reports (attendance_session_id, member_id, summary)
  values (p_session_id, v_session.member_id, public.build_workout_summary(p_session_id))
  on conflict (attendance_session_id) do update set summary = excluded.summary, generated_at = now()
  returning id into v_report_id;

  update public.attendance_sessions set status = 'completed' where id = p_session_id;

  -- Queue delivery. Web push if the member has a usable subscription, email otherwise.
  select exists (
    select 1 from public.push_subscriptions ps
    join public.members m on m.user_id = ps.user_id
    where m.id = v_session.member_id and ps.disabled_at is null and public.is_allowed_push_endpoint(ps.endpoint)
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
-- Open (or return) the member's active session. Shared by staff_check_in and
-- qr_check_in; a concurrent check-in returns the session that won the race.
-- ----------------------------------------------------------------------------
create or replace function public._open_attendance_session(
  p_member_id uuid, p_branch_id uuid, p_method public.checkin_method, p_actor uuid
)
returns uuid language plpgsql security definer set search_path = public as $$
declare v_session_id uuid;
begin
  select id into v_session_id from public.attendance_sessions where member_id = p_member_id and status = 'active';
  if v_session_id is not null then
    return v_session_id;
  end if;
  begin
    insert into public.attendance_sessions (member_id, branch_id, check_in_at, check_in_method, check_in_confidence, checked_in_by)
    values (p_member_id, p_branch_id, now(), p_method, 1.0, p_actor)
    returning id into v_session_id;
  exception when unique_violation then
    select id into v_session_id from public.attendance_sessions where member_id = p_member_id and status = 'active';
  end;
  return v_session_id;
end $$;
revoke all on function public._open_attendance_session(uuid, uuid, public.checkin_method, uuid) from public, anon, authenticated;

-- C2: browser-callable staff check-in. QR check-ins go through qr_check_in (token verified
-- by the qr-token Edge Function); face check-ins come from the edge only.
create or replace function public.staff_check_in(p_member_id uuid, p_method public.checkin_method default 'staff_manual')
returns uuid language plpgsql security definer set search_path = public as $$
declare
  v_member public.members%rowtype;
begin
  if p_method is null or p_method not in ('staff_manual', 'nfc') then
    raise exception 'staff check-in accepts only staff_manual or nfc' using errcode = '22023';
  end if;
  select * into v_member from public.members where id = p_member_id;
  if not found then raise exception 'member not found' using errcode = 'P0002'; end if;
  if not public.has_branch_role(v_member.branch_id, 'owner', 'admin', 'manager', 'front_desk', 'trainer') then
    raise exception 'not authorized for this branch' using errcode = '42501';
  end if;
  if v_member.status <> 'active' then
    raise exception 'membership is %', v_member.status using errcode = '23514';
  end if;
  return public._open_attendance_session(p_member_id, v_member.branch_id, p_method, auth.uid());
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
  update public.attendance_sessions set checked_out_by = auth.uid() where id = p_session_id and status = 'active';
  return public.finalize_attendance_session(p_session_id, now(), p_method);
end $$;

-- Members can check themselves out from their own dashboard.
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
  update public.attendance_sessions set checked_out_by = auth.uid() where id = v_session_id and status = 'active';
  return public.finalize_attendance_session(v_session_id, now(), 'qr');
end $$;

-- ----------------------------------------------------------------------------
-- C1: QR check-in on behalf of a staff user (called by the qr-token Edge
-- Function with the service role after verifying the token and the staff JWT).
-- The member is looked up only in branches where that staff user holds a
-- check-in role, so codes of other tenants are indistinguishable from unknown.
-- ----------------------------------------------------------------------------
create or replace function public.qr_check_in(p_member_code text, p_staff_user_id uuid)
returns uuid language plpgsql security definer set search_path = public as $$
declare
  v_member public.members%rowtype;
  v_ids uuid[];
begin
  if p_staff_user_id is null or not exists (
    select 1 from public.staff_roles sr
    where sr.user_id = p_staff_user_id and sr.role in ('owner', 'admin', 'manager', 'front_desk', 'trainer')
  ) then
    raise exception 'not authorized to check members in' using errcode = '42501';
  end if;

  -- citext equality (case-insensitive); the operator is qualified because the citext
  -- operators live in schema extensions, outside this function's search_path.
  select array_agg(m.id) into v_ids
    from public.members m
   where m.member_code operator(extensions.=) btrim(coalesce(p_member_code, ''))::extensions.citext
     and exists (
       select 1 from public.staff_roles sr
       where sr.user_id = p_staff_user_id
         and sr.organization_id = m.organization_id
         and (sr.branch_id is null or sr.branch_id = m.branch_id)
         and sr.role in ('owner', 'admin', 'manager', 'front_desk', 'trainer')
     );
  if v_ids is null then
    raise exception 'member not found' using errcode = 'P0002';
  end if;
  if cardinality(v_ids) > 1 then
    raise exception 'member code is ambiguous across organizations' using errcode = '21000';
  end if;
  select * into v_member from public.members where id = v_ids[1];
  if v_member.status <> 'active' then
    raise exception 'membership is %', v_member.status using errcode = '23514';
  end if;
  return public._open_attendance_session(v_member.id, v_member.branch_id, 'qr', p_staff_user_id);
end $$;
revoke all on function public.qr_check_in(text, uuid) from public, anon, authenticated;
grant execute on function public.qr_check_in(text, uuid) to service_role;

-- ----------------------------------------------------------------------------
-- Abandon stale sessions: each session in its own sub-transaction, so one
-- session that cannot be finalized no longer blocks cleanup for every tenant.
-- ----------------------------------------------------------------------------
create or replace function public.abandon_stale_sessions(p_max_hours int default 6)
returns int language plpgsql security definer set search_path = public as $$
declare v_count int := 0; v_id uuid;
begin
  for v_id in
    select id from public.attendance_sessions
    where status = 'active' and check_in_at < now() - make_interval(hours => p_max_hours)
    order by check_in_at
    for update skip locked
  loop
    begin
      perform public.finalize_attendance_session(v_id, now(), 'staff_manual');
      update public.attendance_sessions set status = 'abandoned' where id = v_id;
      v_count := v_count + 1;
    exception when others then
      raise warning 'abandon_stale_sessions: session % not finalized (%): %', v_id, sqlstate, sqlerrm;
    end;
  end loop;
  return v_count;
end $$;

-- ----------------------------------------------------------------------------
-- Facts that reach a finalized session (edge batches still queued when the
-- member checked out in the app or at the desk) refresh the frozen report, and
-- usages opened after checkout are closed at the checkout time.
-- ----------------------------------------------------------------------------
create or replace function public.tg_close_late_usage()
returns trigger language plpgsql security definer set search_path = public as $$
declare v_out timestamptz;
begin
  if new.attendance_session_id is not null and new.ended_at is null then
    select check_out_at into v_out from public.attendance_sessions
     where id = new.attendance_session_id and status in ('completed', 'abandoned');
    if v_out is not null then
      new.ended_at := greatest(v_out, new.started_at);
      new.end_reason := coalesce(new.end_reason, 'checkout');
    end if;
  end if;
  return new;
end $$;

create or replace function public.tg_refresh_report_for_late_fact()
returns trigger language plpgsql security definer set search_path = public as $$
declare v_session uuid;
begin
  if tg_table_name = 'equipment_usage_sessions' then
    v_session := new.attendance_session_id;
  elsif tg_table_name = 'exercise_activity_sessions' then
    v_session := public.usage_session(new.equipment_usage_session_id);
  elsif tg_table_name = 'exercise_sets' then
    v_session := public.activity_session(new.exercise_activity_session_id);
  end if;
  if v_session is not null
     and exists (select 1 from public.attendance_sessions where id = v_session and status in ('completed', 'abandoned')) then
    update public.workout_reports set summary = public.build_workout_summary(v_session)
     where attendance_session_id = v_session;
  end if;
  return null;
end $$;
revoke all on function public.tg_close_late_usage() from public, anon, authenticated;
revoke all on function public.tg_refresh_report_for_late_fact() from public, anon, authenticated;

drop trigger if exists trg_usage_close_late on public.equipment_usage_sessions;
create trigger trg_usage_close_late before insert on public.equipment_usage_sessions
  for each row execute function public.tg_close_late_usage();
drop trigger if exists trg_usage_late_report on public.equipment_usage_sessions;
create trigger trg_usage_late_report after insert or update of ended_at, attendance_session_id on public.equipment_usage_sessions
  for each row execute function public.tg_refresh_report_for_late_fact();
drop trigger if exists trg_activity_late_report on public.exercise_activity_sessions;
create trigger trg_activity_late_report after insert or update of active_seconds, ended_at, exercise_id on public.exercise_activity_sessions
  for each row execute function public.tg_refresh_report_for_late_fact();
drop trigger if exists trg_sets_late_report on public.exercise_sets;
create trigger trg_sets_late_report after insert or update of rep_count, verified, confidence, ended_at on public.exercise_sets
  for each row execute function public.tg_refresh_report_for_late_fact();

-- Correct the time fields of reports frozen with the fan-out bug. Only reports whose
-- set and rep totals are unchanged by the rebuild are touched (so facts removed by a
-- device deletion never empty an old report), and their uncertain segments are kept
-- as frozen (tracking segments are subject to retention).
with rebuilt as (
  select r.id, r.summary as old_summary, public.build_workout_summary(r.attendance_session_id) as new_summary
    from public.workout_reports r
    join public.attendance_sessions s on s.id = r.attendance_session_id
   where s.status in ('completed', 'abandoned') and s.check_out_at is not null
     and r.summary ? 'exercises'
)
update public.workout_reports r
   set summary = b.new_summary || jsonb_build_object('uncertain_segments', coalesce(b.old_summary->'uncertain_segments', '[]'::jsonb))
  from rebuilt b
 where r.id = b.id
   and b.new_summary->'total_sets' = b.old_summary->'total_sets'
   and b.new_summary->'total_reps' = b.old_summary->'total_reps'
   and (b.new_summary->'active_exercise_seconds' is distinct from b.old_summary->'active_exercise_seconds'
        or b.new_summary->'exercises' is distinct from b.old_summary->'exercises');

-- ----------------------------------------------------------------------------
-- C3: report sharing on demand
-- ----------------------------------------------------------------------------
alter table public.workout_reports
  alter column share_token drop default,
  alter column share_token drop not null,
  alter column share_expires_at drop default,
  alter column share_expires_at drop not null;

-- Links that were created automatically were never chosen by the member: revoke them.
update public.workout_reports set share_token = null, share_expires_at = null
 where share_token is not null or share_expires_at is not null;

alter table public.workout_reports
  drop constraint if exists workout_reports_share_pair,
  add constraint workout_reports_share_pair check ((share_token is null) = (share_expires_at is null)),
  drop constraint if exists workout_reports_share_token_format,
  add constraint workout_reports_share_token_format check (share_token is null or share_token ~ '^[0-9a-f]{48}$');

-- Members may change only viewed_at (the row filter stays policy reports_self_mark_viewed).
revoke insert, update, delete, truncate on public.workout_reports from authenticated;
grant update (viewed_at) on public.workout_reports to authenticated;

-- Audit row for a share action, scoped to the member's organization and branch.
-- Uses the shared audit writer of migration 0009 (_audit_write) when present.
create or replace function public._audit_report_share(p_action text, p_report_id uuid, p_member_id uuid, p_old jsonb, p_new jsonb)
returns void language plpgsql security definer set search_path = public as $$
declare
  v_scope jsonb := jsonb_build_object('id', p_report_id, 'member_id', p_member_id);
begin
  if to_regprocedure('public._audit_write(text,text,jsonb,jsonb)') is not null then
    perform public._audit_write('workout_reports', p_action, v_scope || p_old, v_scope || p_new);
  else
    insert into public.audit_logs (actor_user_id, actor_role, action, table_name, row_id, organization_id, old_data, new_data)
    select auth.uid(), coalesce(current_setting('request.jwt.claim.role', true), current_user), p_action,
           'workout_reports', p_report_id::text, m.organization_id, v_scope || p_old, v_scope || p_new
      from public.members m where m.id = p_member_id;
  end if;
end $$;
revoke all on function public._audit_report_share(text, uuid, uuid, jsonb, jsonb) from public, anon, authenticated;

create or replace function public.create_report_share(p_report_id uuid, p_days int default 7)
returns jsonb language plpgsql security definer set search_path = public as $$
declare
  v_member uuid := public.current_member_id();
  v_report public.workout_reports%rowtype;
  v_token text;
  v_expires timestamptz;
begin
  if v_member is null then
    raise exception 'only members can share their own reports' using errcode = '42501';
  end if;
  if p_days is null or p_days < 1 or p_days > 30 then
    raise exception 'share link lifetime must be between 1 and 30 days' using errcode = '22023';
  end if;
  select * into v_report from public.workout_reports where id = p_report_id and member_id = v_member for update;
  if not found then
    raise exception 'report not found' using errcode = 'P0002';
  end if;

  -- A new link replaces any previous one.
  v_token := encode(extensions.gen_random_bytes(24), 'hex');
  v_expires := now() + make_interval(days => p_days);
  update public.workout_reports set share_token = v_token, share_expires_at = v_expires where id = p_report_id;

  -- The token itself is never written to the audit log.
  perform public._audit_report_share('report_share_created', p_report_id, v_member,
    jsonb_build_object('share_expires_at', v_report.share_expires_at),
    jsonb_build_object('share_expires_at', v_expires, 'days', p_days));

  return jsonb_build_object('token', v_token, 'expires_at', v_expires);
end $$;

create or replace function public.revoke_report_share(p_report_id uuid)
returns void language plpgsql security definer set search_path = public as $$
declare
  v_member uuid := public.current_member_id();
  v_report public.workout_reports%rowtype;
begin
  if v_member is null then
    raise exception 'only members can revoke their own share links' using errcode = '42501';
  end if;
  select * into v_report from public.workout_reports where id = p_report_id and member_id = v_member for update;
  if not found then
    raise exception 'report not found' using errcode = 'P0002';
  end if;

  update public.workout_reports set share_token = null, share_expires_at = null where id = p_report_id;

  perform public._audit_report_share('report_share_revoked', p_report_id, v_member,
    jsonb_build_object('share_expires_at', v_report.share_expires_at),
    jsonb_build_object('share_expires_at', null));
end $$;

revoke all on function public.create_report_share(uuid, int) from public, anon;
revoke all on function public.revoke_report_share(uuid) from public, anon;
grant execute on function public.create_report_share(uuid, int) to authenticated;
grant execute on function public.revoke_report_share(uuid) to authenticated;

-- Public (anon) report lookup by share token: first name only, never ids.
-- Null for unknown, revoked or expired tokens; the caller returns 404.
create or replace function public.get_shared_report(p_token text)
returns jsonb language plpgsql stable security definer set search_path = public as $$
declare v jsonb;
begin
  if p_token is null or p_token !~ '^[0-9a-f]{48}$' then
    return null;
  end if;
  select jsonb_build_object(
           'member_first_name', (regexp_split_to_array(btrim(m.full_name), '\s+'))[1],
           'generated_at', r.generated_at,
           'summary', r.summary
         )
    into v
    from public.workout_reports r
    join public.members m on m.id = r.member_id
   where r.share_token = p_token and r.share_expires_at > now();
  return v;
end $$;

-- ----------------------------------------------------------------------------
-- Notification jobs
-- ----------------------------------------------------------------------------
-- Claim due jobs that still have attempts left.
create or replace function public.claim_notification_jobs(p_worker text, p_limit int default 20)
returns setof public.notification_jobs language plpgsql security definer set search_path = public as $$
begin
  return query
  with due as (
    select id from public.notification_jobs
    where status in ('queued', 'failed') and next_attempt_at <= now() and attempts < max_attempts
    order by next_attempt_at
    limit p_limit
    for update skip locked
  )
  update public.notification_jobs j
     set status = 'sending', locked_at = now(), locked_by = p_worker, attempts = j.attempts + 1
    from due where j.id = due.id
  returning j.*;
end $$;

-- Report an outcome. A delivered job is final: a late or duplicate completion never
-- reopens it. A success always wins (it really was sent, so it must not be re-sent).
-- A failure only applies to a job that is being sent or awaiting retry.
create or replace function public.complete_notification_job(p_job_id uuid, p_ok boolean, p_error text default null)
returns void language plpgsql security definer set search_path = public as $$
declare v_job public.notification_jobs%rowtype;
begin
  select * into v_job from public.notification_jobs where id = p_job_id for update;
  if not found then
    return;
  end if;
  if v_job.status = 'delivered' then
    raise notice 'notification job % already delivered; completion ignored', p_job_id;
    return;
  end if;

  if p_ok then
    update public.notification_jobs set status = 'delivered', delivered_at = now(), locked_at = null, locked_by = null, last_error = null
     where id = p_job_id;
  elsif v_job.status not in ('sending', 'failed') then
    raise notice 'notification job % is %; failure report ignored', p_job_id, v_job.status;
  elsif v_job.attempts >= v_job.max_attempts then
    update public.notification_jobs set status = 'dead', locked_at = null, locked_by = null, last_error = left(p_error, 1000)
     where id = p_job_id;
  else
    -- Exponential backoff: 30s, 60s, 120s, ... capped at 1h.
    update public.notification_jobs
       set status = 'failed', locked_at = null, locked_by = null, last_error = left(p_error, 1000),
           next_attempt_at = now() + least(make_interval(secs => 30 * power(2, greatest(v_job.attempts, 1) - 1)), interval '1 hour')
     where id = p_job_id;
  end if;
end $$;

-- Jobs left in 'sending' by a crashed or timed-out sender: dead-letter once the attempts
-- are used up, otherwise retry with the same backoff as an explicit failure.
create or replace function public.release_stale_notification_locks()
returns int language plpgsql security definer set search_path = public as $$
declare v int;
begin
  update public.notification_jobs
     set status = case when attempts >= max_attempts then 'dead'::public.notification_status
                       else 'failed'::public.notification_status end,
         locked_at = null, locked_by = null,
         last_error = coalesce(last_error, 'worker lock expired'),
         next_attempt_at = case when attempts >= max_attempts then next_attempt_at
                                else now() + least(make_interval(secs => 30 * power(2, greatest(attempts, 1) - 1)), interval '1 hour') end
   where status = 'sending' and locked_at < now() - interval '5 minutes';
  get diagnostics v = row_count;
  return v;
end $$;

-- Jobs that already exhausted their attempts but were left claimable.
update public.notification_jobs set status = 'dead', locked_at = null, locked_by = null
 where status = 'failed' and attempts >= max_attempts;

-- Privileges of replaced functions are kept; restate the service-only ones for clarity.
revoke all on function public.finalize_attendance_session(uuid, timestamptz, public.checkin_method) from public, anon, authenticated;
revoke all on function public.build_workout_summary(uuid) from public, anon, authenticated;
revoke all on function public.abandon_stale_sessions(int) from public, anon, authenticated;
revoke all on function public.claim_notification_jobs(text, int) from public, anon, authenticated;
revoke all on function public.complete_notification_job(uuid, boolean, text) from public, anon, authenticated;
revoke all on function public.release_stale_notification_locks() from public, anon, authenticated;
revoke all on function public.member_push_subscriptions(uuid) from public, anon, authenticated;
