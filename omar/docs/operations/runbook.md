# Operations runbook

## Where things run (2026-11-02)

| Component | Where | Deployed by |
| --- | --- | --- |
| Web apps | Vercel, team `ahmed-1ff6`: `smartgym-admin` → https://smartgym-admin.vercel.app, `smartgym-member` → https://smartgym-member.vercel.app | Git integration: every push to `main` on `github.com/omarahmedvibe210-droid/ai-smart-gym` builds both apps from the same commit |
| Database, Auth, Edge Functions (`ingest-events`, `device-sync`, `qr-token`, `notification-dispatch`), cron jobs | Supabase project `vpwudyxfsbyrbpwqdmew` (eu-west-2) | `infrastructure/scripts/deploy-cloud.ps1` (migrations, function secrets, Vault secrets, functions, config) |
| Notification delivery | `pg_cron` job `smartgym-notification-dispatch` (every minute) → `pg_net` → `notification-dispatch` function → Web Push / e-mail | migration 0011 + the script above; the Node worker in `services/notification-worker` is the self-hosted fallback |
| CI | GitHub Actions (`.github/workflows/ci.yml`): typecheck/tests/builds, embedded-PostgreSQL migration+RLS suite, Deno check, secret scan, edge-ai image → `ghcr.io/omarahmedvibe210-droid/ai-smart-gym-edge-ai` | push to `main` / pull requests |
| Edge AI service | Not yet: needs the per-branch GPU node (`docs/operations/production-dependencies.md`) | `docs/deployment/edge-device.md` |

Rollback: apps → `npx vercel rollback` (or promote a previous deployment in the dashboard); functions → redeploy from the previous commit; database → forward-fix migration (applied migrations are never edited).

## Dashboards and signals

| Signal | Source | Healthy |
| --- | --- | --- |
| `cron.job_run_details` for `smartgym-notification-dispatch` | SQL (`npx supabase db query --linked`) | last runs `succeeded` every minute |
| `notification_jobs` where status=`queued` and `next_attempt_at < now() - interval '5 minutes'` | SQL | 0 rows (otherwise the dispatcher is not running) |
| `edge_camera_online{name}` | edge `/metrics` | 1 for every enabled camera |
| `edge_queue_depth` | edge `/metrics`, `device_health_logs.queue_depth` | < 100 steady state |
| `edge_p95_latency_ms` | edge `/metrics` | < 400 ms |
| `edge_reps_suppressed_total` vs `edge_reps_admitted_total` | edge | suppressed ≈ admitted × (overlapping cameras − 1) |
| `edge_identity_merges_refused_total` | edge | low and flat; spikes mean ReID thresholds or camera overlap need review |
| `edge_devices.last_seen_at` | admin → Devices | < 2 min old |
| `notification_jobs` status=dead | admin SQL | 0 |
| Supabase DB CPU / connections | Supabase dashboard | < 70 % |

## Alerts and responses

### Camera offline (`EdgeCameraOffline`)
1. `docker compose logs edge-ai | grep camera.status` → error text (timeout, auth, 404 path).
2. From the edge host: `ffprobe -rtsp_transport tcp rtsp://<cam>` with credentials from the secrets file. Auth failure → camera password changed: update `camera-credentials.yaml`, restart `edge-ai`. Network → check PoE switch / VLAN.
3. Members at that machine during the outage get `no_camera_coverage` uncertainty; nothing to repair in data.

### Sync backlog (`EdgeQueueBacklog`)
1. `curl localhost:8088/healthz | jq '.sync_last_error'`.
2. `401` → device secret or clock. Check `chronyc tracking`; if time is fine, rotate the secret (admin → Devices) and update `.env`.
3. `5xx` → check Supabase status/logs for `ingest_events` errors; the edge retries automatically.
4. Network → verify egress to `*.supabase.co:443`.
5. After recovery, `edge_queue_depth` falls at ≤ 500 events/2 s per device. Nothing to replay manually.

### Events rejected (`sync.event_rejected` in logs)
A rejection is a contract or business-rule violation (e.g. member without consent). Inspect `event_ingest_log where status='rejected'` for the reason. Consent problems → front desk records consent → future check-ins succeed; past rejected events are not re-created.

### Session never checked out
`abandon_stale_sessions(6)` runs every 15 min and finalises sessions older than 6 h with status `abandoned`; their reports mark open sets unverified. Front desk can check members out manually in admin → Live Sessions.

### Notifications not delivered (dispatcher)
1. Is the cron job scheduled and running? `select jobname, schedule, active from cron.job where jobname = 'smartgym-notification-dispatch'` and `select status, return_message, start_time from cron.job_run_details where jobid = (select jobid from cron.job where jobname = 'smartgym-notification-dispatch') order by start_time desc limit 5`.
2. Did the HTTP call reach the function? `select status_code, left(content, 200), created from net._http_response order by created desc limit 5`. `401` → the Vault secret `smartgym_dispatch_secret` and the function secret `NOTIFICATION_DISPATCH_SECRET` differ: re-run `deploy-cloud.ps1` (it sets both from the same `.env` value). Timeouts → check the function logs (Dashboard → Edge Functions → notification-dispatch → Logs).
3. Jobs: `select status, count(*) from notification_jobs group by 1`. `queued` and old → steps 1–2. `failed` → `last_error` (push `410`/`404` endpoints are disabled automatically; `no active push subscriptions` means the member never enabled push and e-mail is not configured).
4. Vault secrets missing after a restore: `select name from vault.secrets where name like 'smartgym_%'` must list `smartgym_functions_url` and `smartgym_dispatch_secret`; recreate with the deploy script.

### Notification dead-lettered
`select * from notification_jobs where status='dead'`. Common causes: no push subscription (member never enabled push → e-mail fallback needs `RESEND_API_KEY`/`EMAIL_FROM` function secrets) or expired endpoints (auto-disabled). Re-queue: `update notification_jobs set status='queued', attempts=0, next_attempt_at=now() where id=...`.

### Device reports `device_retired` (HTTP 403)
A device whose `edge_devices.status` is `retired` is refused by `ingest-events` and `device-sync` and stops retrying. Intentional after decommissioning; if the device is still in service set the status back to `active` and issue a new secret (admin → Devices).

### Owner / first staff account
`node infrastructure/scripts/bootstrap-owner.mjs --email <e-mail> --org "<gym>" --branch "<branch>"` creates the organization, branch and an organization-wide owner role; the initial password lands in the local `.env` as `OWNER_BOOTSTRAP_PASSWORD` and must be changed after the first login. Further staff are invited from the admin app (requires custom SMTP on the Supabase project, see `docs/operations/production-dependencies.md`).

### Edge host rebuilt
Restore `/data` volume from the encrypted backup (templates + queue). If templates are lost, members fall back to QR until re-enrolled; cloud `biometric_enrollments` rows for that device should be revoked to keep the registry truthful.

### Database restore
Supabase PITR to the chosen timestamp. After restore, edge devices may hold events already applied before the restore point — they are re-sent and applied idempotently; events whose `batch_id` was already consumed return `replayed`. Run the RLS test suite against the restored instance.

### Member cannot be identified at the entrance
`curl localhost:8088/healthz | jq '.identification, .consent_cache_stale, .stages.face'`. `stages.face: null` → no face model/biometric key configured (QR only). `consent_cache_stale: true` → `edge-ai sync` failing (check `config_sync_last_error`); the gate fails closed by design. High `ambiguous` counts → two enrolled members look alike at the current threshold; re-enrol with better photos or raise `FaceMatcher.ambiguity_margin`. Members can always fall back to the kiosk QR (admin → Check-in Kiosk).

### Enrolling / revoking a member's face template (on the edge host)
```bash
edge-ai sync                                   # refresh consents from the cloud first (fails closed otherwise)
edge-ai enroll --member <uuid> --image a.jpg --image b.jpg --image c.jpg
edge-ai drain                                  # pushes biometric.enrolled so the cloud registry shows it
edge-ai revoke --member <uuid> --reason member_request
```
Photos are not stored; only the encrypted embedding is. Consent withdrawn in the member app → the next `sync` deletes the template automatically.

### Discovering cameras
`edge-ai discover --interface <camera-VLAN IP> --seconds 5` lists ONVIF devices (model, name, XAddrs). Resolve RTSP URIs with `get_stream_uri()` using the viewer account, then register the camera in the admin app **without** credentials and add the credentials to `camera-credentials.yaml`.

## Routine
- Weekly: review `audit_logs` for role grants and secret rotations; check `model_versions` benchmark freshness; confirm the CI run on `main` is green and both Vercel production deployments are READY.
- Monthly: backup restore drill; dependency updates; rotate device secrets; rotate `QR_TOKEN_SECRET` and `NOTIFICATION_DISPATCH_SECRET` (edit `.env`, re-run `deploy-cloud.ps1`).
- Quarterly: re-run the camera probe and the rep-count evaluation on fresh labelled footage; recalibrate thresholds.
