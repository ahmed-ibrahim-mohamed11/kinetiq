-- ============================================================================
-- Retention, scheduled jobs, notification worker helpers (migration 0005)
-- ============================================================================

-- ----------------------------------------------------------------------------
-- Explicit retention rules (see docs/security/data-retention.md)
--   * Raw video: never stored by this platform.
--   * Biometric templates: edge only; cloud holds references, revoked on withdrawal.
--   * repetition_events: 365 days (aggregates in exercise_sets/reports are kept).
--   * device_health_logs / camera_status_logs: 90 days.
--   * event_ingest_log: 30 days (idempotency window; the edge never replays older).
--   * audit_logs: 2 years (legal hold may extend).
--   * person_tracks / tracking_uncertain_segments: 180 days.
-- ----------------------------------------------------------------------------
create table public.retention_policies (
  table_name    text primary key,
  keep_days     int not null check (keep_days > 0),
  timestamp_col text not null,
  enabled       boolean not null default true
);
alter table public.retention_policies enable row level security;
insert into public.retention_policies (table_name, keep_days, timestamp_col) values
  ('repetition_events', 365, 'occurred_at'),
  ('device_health_logs', 90, 'recorded_at'),
  ('camera_status_logs', 90, 'recorded_at'),
  ('event_ingest_log', 30, 'received_at'),
  ('ingest_batches', 30, 'received_at'),
  ('audit_logs', 730, 'occurred_at'),
  ('person_tracks', 180, 'started_at'),
  ('tracking_uncertain_segments', 180, 'from_at');

create or replace function public.apply_retention()
returns table (table_name text, deleted bigint) language plpgsql security definer set search_path = public as $$
declare r record; v bigint;
begin
  for r in select * from public.retention_policies where enabled loop
    execute format('delete from public.%I where %I < now() - make_interval(days => %s)', r.table_name, r.timestamp_col, r.keep_days);
    get diagnostics v = row_count;
    table_name := r.table_name; deleted := v;
    return next;
  end loop;
end $$;
revoke all on function public.apply_retention() from public, anon, authenticated;

-- Consent withdrawal must revoke biometric enrollment references immediately.
create or replace function public.on_consent_withdrawn()
returns trigger language plpgsql security definer set search_path = public as $$
begin
  if new.status = 'withdrawn' and old.status <> 'withdrawn' and new.consent_type = 'biometric_face_identification' then
    update public.biometric_enrollments set revoked_at = now()
     where member_id = new.member_id and revoked_at is null;
    -- The edge device polls revoked enrollments and deletes its local template.
  end if;
  return new;
end $$;
create trigger trg_consent_withdrawn after update on public.consent_records
  for each row execute function public.on_consent_withdrawn();

-- ----------------------------------------------------------------------------
-- Notification worker: claim due jobs with SKIP LOCKED, report outcomes.
-- ----------------------------------------------------------------------------
create or replace function public.claim_notification_jobs(p_worker text, p_limit int default 20)
returns setof public.notification_jobs language plpgsql security definer set search_path = public as $$
begin
  return query
  with due as (
    select id from public.notification_jobs
    where status in ('queued', 'failed') and next_attempt_at <= now()
    order by next_attempt_at
    limit p_limit
    for update skip locked
  )
  update public.notification_jobs j
     set status = 'sending', locked_at = now(), locked_by = p_worker, attempts = j.attempts + 1
    from due where j.id = due.id
  returning j.*;
end $$;

create or replace function public.complete_notification_job(p_job_id uuid, p_ok boolean, p_error text default null)
returns void language plpgsql security definer set search_path = public as $$
declare v_attempts int; v_max int;
begin
  select attempts, max_attempts into v_attempts, v_max from public.notification_jobs where id = p_job_id;
  if p_ok then
    update public.notification_jobs set status = 'delivered', delivered_at = now(), locked_at = null, locked_by = null, last_error = null
     where id = p_job_id;
  elsif v_attempts >= v_max then
    update public.notification_jobs set status = 'dead', locked_at = null, locked_by = null, last_error = left(p_error, 1000)
     where id = p_job_id;
  else
    -- Exponential backoff: 30s, 60s, 120s, ... capped at 1h.
    update public.notification_jobs
       set status = 'failed', locked_at = null, locked_by = null, last_error = left(p_error, 1000),
           next_attempt_at = now() + least(make_interval(secs => 30 * power(2, v_attempts - 1)), interval '1 hour')
     where id = p_job_id;
  end if;
end $$;

-- Push subscriptions for a member (worker use).
create or replace function public.member_push_subscriptions(p_member_id uuid)
returns table (id uuid, endpoint text, p256dh text, auth text) language sql stable security definer set search_path = public as $$
  select ps.id, ps.endpoint, ps.p256dh, ps.auth
  from public.push_subscriptions ps
  join public.members m on m.user_id = ps.user_id
  where m.id = p_member_id and ps.disabled_at is null
$$;

create or replace function public.disable_push_subscription(p_id uuid)
returns void language sql security definer set search_path = public as $$
  update public.push_subscriptions set disabled_at = now() where id = p_id
$$;

revoke all on function public.claim_notification_jobs(text, int) from public, anon, authenticated;
revoke all on function public.complete_notification_job(uuid, boolean, text) from public, anon, authenticated;
revoke all on function public.member_push_subscriptions(uuid) from public, anon, authenticated;
revoke all on function public.disable_push_subscription(uuid) from public, anon, authenticated;

-- Stale sending jobs (worker crashed): release after 5 minutes.
create or replace function public.release_stale_notification_locks()
returns int language plpgsql security definer set search_path = public as $$
declare v int;
begin
  update public.notification_jobs set status = 'failed', locked_at = null, locked_by = null,
         last_error = coalesce(last_error, 'worker lock expired'), next_attempt_at = now()
   where status = 'sending' and locked_at < now() - interval '5 minutes';
  get diagnostics v = row_count;
  return v;
end $$;
revoke all on function public.release_stale_notification_locks() from public, anon, authenticated;

-- ----------------------------------------------------------------------------
-- pg_cron schedules (only if the extension is available on this instance).
-- ----------------------------------------------------------------------------
do $$
begin
  if exists (select 1 from pg_available_extensions where name = 'pg_cron') then
    create extension if not exists pg_cron;
    perform cron.schedule('smartgym-abandon-stale-sessions', '*/15 * * * *', $job$ select public.abandon_stale_sessions(6) $job$);
    perform cron.schedule('smartgym-apply-retention', '30 3 * * *', $job$ select * from public.apply_retention() $job$);
    perform cron.schedule('smartgym-release-notification-locks', '*/5 * * * *', $job$ select public.release_stale_notification_locks() $job$);
  else
    raise notice 'pg_cron not available: schedule abandon_stale_sessions, apply_retention and release_stale_notification_locks externally';
  end if;
end $$;
