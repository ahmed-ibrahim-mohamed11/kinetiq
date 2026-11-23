# Notification dispatch on Supabase (Edge Function + pg_cron + pg_net + Vault)

Workout-report notifications are delivered by the Supabase project itself; no separate host is
needed in production. The Node worker in `services/notification-worker` stays available as the
self-hosted fallback (section 6).

## 1. Architecture

```
finalize_attendance_session ──insert──▶ public.notification_jobs (status queued, next_attempt_at)
                                                    ▲              │
pg_cron 'smartgym-notification-dispatch' (every minute)            │ claim_notification_jobs (SKIP LOCKED)
  └─ only while a job is due: net.http_post ─────────┐             │ complete_notification_job (backoff / dead)
       url     = vault 'smartgym_functions_url' || '/notification-dispatch'
       header  x-dispatch-secret = vault 'smartgym_dispatch_secret'
                                                     ▼             │
                        Edge Function notification-dispatch (verify_jwt = false)
                          ├─ constant-time check of x-dispatch-secret == NOTIFICATION_DISPATCH_SECRET (else 401)
                          ├─ claims up to 5 × 20 jobs per call (stops after ~15 s; pg_net waits 25 s)
                          ├─ web_push → VAPID ES256 JWT + RFC 8291 aes128gcm (WebCrypto, no npm web-push)
                          │             404/410 → disable_push_subscription; other errors → job failure
                          ├─ email    → Resend (only if RESEND_API_KEY + EMAIL_FROM are set)
                          └─ responds {claimed, delivered, failed}
```

- **One source of truth.** Templates, job processing, the Resend sender, the WebCrypto Web Push
  sender, the supabase-js adapters and the HTTP handler live in `supabase/functions/_shared/notify/`
  (runtime-neutral TypeScript). The Edge Function imports them directly; the Node worker re-exports
  them (`services/notification-worker/src/{worker,templates,email}.ts`).
- **The database owns retries.** `complete_notification_job` applies exponential backoff
  (30 s, 60 s, 120 s, ... capped at 1 h) and marks the job `dead` after `max_attempts` (6).
  `release_stale_notification_locks` (every 5 min) frees jobs left in `sending` by a crashed call.
- **No secret in SQL.** Migration `20261102000011_notification_dispatch.sql` stores only the job
  command; it reads both Vault secrets each time the job runs. Without pg_cron, pg_net or Vault
  (embedded test database, plain PostgreSQL) the migration is a no-op that raises a NOTICE.
- **Idle minutes cost nothing.** The cron command calls the function only when a `queued`/`failed`
  job has `next_attempt_at <= now()`, so function logs show calls only when work was due.
- **Interoperability is tested** without a push service: the encrypted body is byte-identical to the
  reference `http_ece` library (used by npm web-push) and decrypts with it; VAPID JWTs verify as
  ES256 against keys from `web-push generate-vapid-keys`
  (`services/notification-worker/test/webpush.test.ts`, `supabase/functions/tests/notification_*.test.ts`).

### Function secrets

| Name | Required | Value |
| --- | --- | --- |
| `NOTIFICATION_DISPATCH_SECRET` | yes | random 64-hex string; the same value goes into Vault as `smartgym_dispatch_secret` |
| `VAPID_PUBLIC_KEY` / `VAPID_PRIVATE_KEY` | yes | web-push base64url pair (`npx web-push generate-vapid-keys`); the public key must equal the member app's `NEXT_PUBLIC_VAPID_PUBLIC_KEY` |
| `VAPID_SUBJECT` | yes | `mailto:` or `https:` contact |
| `MEMBER_APP_URL` | yes | deployed member app, e.g. `https://smartgym-member.vercel.app` (report links) |
| `RESEND_API_KEY` + `EMAIL_FROM` | optional | email channel; without them email jobs fail explicitly and dead-letter |
| `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` | platform | injected by Supabase |

If a required secret is missing the function answers `500 {code:'not_configured', missing:[...]}`
**before** claiming anything, so jobs stay queued until it is fixed.

## 2. Provisioning (PowerShell 5.1, repo root)

The commands read values from the gitignored root `.env` and never print them.

```powershell
$ref = 'vpwudyxfsbyrbpwqdmew'
function Get-DotEnv([string]$name) {
  $line = Get-Content .env | Where-Object { $_ -match "^\s*$name\s*=" } | Select-Object -First 1
  if ($line) { ($line -split '=', 2)[1].Trim().Trim('"').Trim("'") } else { '' }
}

# 2.1 Dispatch secret: generate once and keep it in .env (not printed).
if (-not (Get-DotEnv 'NOTIFICATION_DISPATCH_SECRET')) {
  $bytes = New-Object byte[] 32
  [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
  Add-Content -Path .env -Encoding ascii -Value ("NOTIFICATION_DISPATCH_SECRET=" + (-join ($bytes | ForEach-Object { $_.ToString('x2') })))
}

# 2.2 Function secrets from .env through a temporary env file (UTF-8 without BOM), then delete it.
$names = 'NOTIFICATION_DISPATCH_SECRET','VAPID_PUBLIC_KEY','VAPID_PRIVATE_KEY','VAPID_SUBJECT','MEMBER_APP_URL','RESEND_API_KEY','EMAIL_FROM'
$lines = foreach ($n in $names) { $v = Get-DotEnv $n; if ($v) { "$n=`"$v`"" } }
$tmp = Join-Path $env:TEMP 'smartgym-notify-secrets.env'
[IO.File]::WriteAllLines($tmp, [string[]]$lines)
npx supabase secrets set --env-file $tmp --project-ref $ref
Remove-Item $tmp -Force

# 2.3 Vault secrets read by the cron job (create or update), via a temporary SQL file.
$functionsUrl = (Get-DotEnv 'SUPABASE_URL').TrimEnd('/') + '/functions/v1'
$dispatch = Get-DotEnv 'NOTIFICATION_DISPATCH_SECRET'
$sql = @"
do `$`$
declare
  v_url    text := '$($functionsUrl.Replace("'", "''"))';
  v_secret text := '$($dispatch.Replace("'", "''"))';
begin
  if exists (select 1 from vault.secrets where name = 'smartgym_functions_url') then
    perform vault.update_secret((select id from vault.secrets where name = 'smartgym_functions_url'), v_url);
  else
    perform vault.create_secret(v_url, 'smartgym_functions_url', 'Edge Functions base URL for pg_cron notification dispatch');
  end if;
  if exists (select 1 from vault.secrets where name = 'smartgym_dispatch_secret') then
    perform vault.update_secret((select id from vault.secrets where name = 'smartgym_dispatch_secret'), v_secret);
  else
    perform vault.create_secret(v_secret, 'smartgym_dispatch_secret', 'x-dispatch-secret for notification-dispatch');
  end if;
end
`$`$;
"@
$sqlFile = Join-Path $env:TEMP 'smartgym-notify-vault.sql'
[IO.File]::WriteAllText($sqlFile, $sql)
npx supabase db query --linked -f $sqlFile
Remove-Item $sqlFile -Force

# 2.4 Migration 0011 (creates pg_net if needed and schedules the cron job) and the function.
npx supabase db push
npx supabase functions deploy notification-dispatch --use-api --no-verify-jwt --project-ref $ref
```

Type-check / test before deploying (from `supabase/functions`):

```powershell
deno check --config deno.json notification-dispatch/index.ts
deno test --config deno.json --allow-env --allow-read tests/notification_dispatch.test.ts tests/notification_webpush.test.ts
```

Rotating the dispatch secret: put a new value in `.env`, re-run 2.2 and 2.3 (Vault is updated in
place; the next cron run uses it).

## 3. Verify

```powershell
# Jobs are scheduled (both should be active).
npx supabase db query --linked "select jobname, schedule, active from cron.job where jobname in ('smartgym-notification-dispatch','smartgym-release-notification-locks')"

# Vault secrets exist (names only).
npx supabase db query --linked "select name, updated_at from vault.secrets where name like 'smartgym_%'"

# Recent cron runs (status 'succeeded'; a failure message here usually means a missing Vault secret).
npx supabase db query --linked "select d.status, d.return_message, d.start_time from cron.job_run_details d join cron.job j using (jobid) where j.jobname = 'smartgym-notification-dispatch' order by d.start_time desc limit 5"

# HTTP results of pg_net calls (expect 200 with {claimed, delivered, failed}; 401 = Vault/function secret mismatch).
npx supabase db query --linked "select id, status_code, left(content::text, 200) as body, created from net._http_response order by created desc limit 5"

# Job outcomes.
npx supabase db query --linked "select status, count(*) from public.notification_jobs group by status"
npx supabase db query --linked "select id, channel, status, attempts, left(last_error, 120) as last_error, updated_at from public.notification_jobs order by updated_at desc limit 10"
```

Direct call (no output of the secret; expect `401` without the header and `200 {claimed,...}` with it):

```powershell
$url = (Get-DotEnv 'SUPABASE_URL').TrimEnd('/') + '/functions/v1/notification-dispatch'
try { Invoke-RestMethod -Method Post -Uri $url -ContentType 'application/json' -Body '{}' } catch { $_.Exception.Response.StatusCode.value__ }
Invoke-RestMethod -Method Post -Uri $url -ContentType 'application/json' -Body '{}' -Headers @{ 'x-dispatch-secret' = (Get-DotEnv 'NOTIFICATION_DISPATCH_SECRET') }
```

Function logs: Dashboard → Edge Functions → `notification-dispatch` → Logs. Each call logs JSON lines
`notification.delivered` / `notification.failed` (job id, template, error, attempt) and
`dispatch.done` with the counts; `dispatch.not_configured` lists missing secrets.

End to end: subscribe to notifications in the member app, finish a session (check out), and within a
minute the job moves `queued → sending → delivered` and the device shows "Your workout report is ready".

## 4. Failure handling

| Symptom | Meaning / action |
| --- | --- |
| `net._http_response.status_code = 401` | Vault `smartgym_dispatch_secret` differs from the function secret — re-run 2.2 + 2.3 |
| `500 not_configured` | a required function secret is missing (see `missing`) — re-run 2.2; jobs remain queued |
| job `failed` with `push 4xx/5xx` | push service rejected; retried with backoff, `dead` after 6 attempts |
| subscription `disabled_at` set | push service answered 404/410 (browser unsubscribed) |
| job `failed` with `email channel not configured` | set `RESEND_API_KEY` + `EMAIL_FROM` or ignore (members without push) |
| no rows in `cron.job` | pg_cron/pg_net unavailable when 0011 ran — enable them (Database → Extensions) and re-run the migration's `do` block |

## 5. Local development / tests

- `npm run test -w services/notification-worker` — shared logic, Web Push interoperability with `http_ece`, VAPID verification.
- `deno test --config deno.json --allow-env --allow-read tests/notification_dispatch.test.ts tests/notification_webpush.test.ts` (in `supabase/functions`) — handler auth/claim/complete/410 with fakes.
- `npm run test -w tests/db` — `tests/db/notification-dispatch.test.ts` proves 0011 is a NOTICE-only no-op on the embedded
  PostgreSQL and, with cron/net/vault stand-ins, schedules one idempotent job that reads Vault at run time.

## 6. Self-hosted fallback: Node worker

`services/notification-worker` polls the same RPCs and uses the same shared code (push via npm
`web-push`). Use it where the database has no pg_cron/pg_net, or alongside the Edge Function
(`claim_notification_jobs` uses `FOR UPDATE SKIP LOCKED`, so they never deliver a job twice).

```powershell
npm run start -w services/notification-worker   # needs SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, VAPID_*, MEMBER_APP_URL
docker build -f infrastructure/docker/notification-worker.Dockerfile -t smartgym/notification-worker:0.1.0 .
```

To switch production to it, unschedule the cron job:
`select cron.unschedule('smartgym-notification-dispatch');`
