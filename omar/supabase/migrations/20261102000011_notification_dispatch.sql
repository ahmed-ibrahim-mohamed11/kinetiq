-- ============================================================================
-- Notification dispatch on Supabase (migration 0011, contract C7)
--
-- pg_cron calls the Edge Function notification-dispatch every minute through
-- pg_net, so queued notification_jobs are delivered without a separately hosted
-- worker. The function URL and the shared dispatch secret are read from Vault
-- INSIDE the job command at run time; no secret is stored in this migration or
-- in cron.job:
--   vault secret 'smartgym_functions_url'   e.g. https://<ref>.supabase.co/functions/v1
--   vault secret 'smartgym_dispatch_secret' = Edge Function secret NOTIFICATION_DISPATCH_SECRET
-- The job runs every minute, but the HTTP call is made only while a job is
-- claimable (same predicate as claim_notification_jobs), so idle minutes cost
-- nothing. Live verification must therefore queue a due job (or POST to the
-- function directly): an idle minute produces no request.
-- Provisioning and verification: docs/deployment/notification-dispatch.md.
--
-- Where pg_cron, pg_net or Vault are unavailable (the embedded test database,
-- plain PostgreSQL) this migration is a no-op that raises a NOTICE; run the
-- self-hosted services/notification-worker there instead. Re-running it is
-- safe: an existing job of the same name is unscheduled first.
-- ============================================================================
do $migration$
declare
  v_has_cron  boolean;
  v_has_net   boolean;
  v_has_vault boolean;
begin
  -- pg_cron may already exist (migration 0005); pg_net creates schema "net".
  if to_regnamespace('cron') is null and exists (select 1 from pg_available_extensions where name = 'pg_cron') then
    create extension if not exists pg_cron;
  end if;
  if to_regnamespace('net') is null and exists (select 1 from pg_available_extensions where name = 'pg_net') then
    create extension if not exists pg_net with schema extensions;
  end if;

  v_has_cron  := to_regnamespace('cron') is not null;
  v_has_net   := to_regnamespace('net') is not null;
  v_has_vault := to_regnamespace('vault') is not null;

  if not (v_has_cron and v_has_net and v_has_vault) then
    raise notice 'notification dispatch not scheduled (pg_cron: %, pg_net: %, vault: %); use the self-hosted notification worker on this database',
      v_has_cron::text, v_has_net::text, v_has_vault::text;
    return;
  end if;

  perform cron.unschedule(j.jobid) from cron.job j where j.jobname = 'smartgym-notification-dispatch';

  perform cron.schedule(
    'smartgym-notification-dispatch',
    '* * * * *',
    $job$
    select net.http_post(
      url := rtrim((select decrypted_secret from vault.decrypted_secrets where name = 'smartgym_functions_url'), '/') || '/notification-dispatch',
      headers := jsonb_build_object(
        'content-type', 'application/json',
        'x-dispatch-secret', (select decrypted_secret from vault.decrypted_secrets where name = 'smartgym_dispatch_secret')
      ),
      body := '{}'::jsonb,
      timeout_milliseconds := 25000
    ) as request_id
    where exists (
      select 1 from public.notification_jobs
      where status in ('queued', 'failed') and next_attempt_at <= now() and attempts < max_attempts
    )
    $job$
  );

  -- Jobs left in 'sending' by a crashed dispatcher are released by this job. Migration 0005
  -- only scheduled it when pg_cron was already enabled, so make sure it exists here.
  if not exists (select 1 from cron.job where jobname = 'smartgym-release-notification-locks') then
    perform cron.schedule('smartgym-release-notification-locks', '*/5 * * * *', $job$ select public.release_stale_notification_locks() $job$);
  end if;

  raise notice 'scheduled smartgym-notification-dispatch (every minute, via pg_net + Vault)';
end
$migration$;
