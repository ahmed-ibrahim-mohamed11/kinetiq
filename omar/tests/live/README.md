# Live verification (production)

End-to-end checks against the **hosted** Supabase project, the **deployed** Vercel apps and
Mozilla's public Web Push service. They prove what unit and embedded-database tests cannot: the
deployed Edge Functions, the pg_cron notification dispatcher, real push delivery and the real UI.

Not part of `npm test`. Run it deliberately, after a deploy.

## Safety

- **QA tenant only.** Every run creates its own temporary tenant: organization slug
  `qa-smart-gym-<runid>`, name `QA TEST tenant (not a real gym)`, cameras on TEST-NET addresses
  (`rtsp://192.0.2.x`, no credentials), auth users `qa-staff-<runid>@qa.smartgym.test` and
  `qa-member-<runid>@qa.smartgym.test`. No real gym, member or device is read or written.
- **Always removed.** Each test file destroys its tenant in `afterAll` (Playwright: global teardown),
  deleting in FK order (push subscriptions, notification jobs, reports, reps/sets/activity/usage,
  tracks, ingest log, sessions, enrollments, consents, members, the consent archive copies and
  biometric tombstones that migration 0009 writes on those deletes, device secrets, zones, cameras,
  equipment, devices, staff roles, branches, organization, its audit rows, then the auth users) and
  then **asserts nothing of the tenant remains**. The vitest global teardown also sweeps any tenant
  of the same run that an aborted file left behind. Teardown refuses to touch an organization that
  is not a QA tenant and only deletes auth users on the `qa.smartgym.test` domain.
- **No secrets printed.** The device secret and the temporary passwords exist only in memory (the
  UI run hands them to Playwright workers through the process environment). Artifacts contain ids
  and timings only and pass through a redactor; push endpoints and share tokens are not recorded.

## Prerequisites

1. Root `.env` (gitignored) with `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY`
   and `NEXT_PUBLIC_VAPID_PUBLIC_KEY` (or `VAPID_PUBLIC_KEY`, the same key the dispatcher signs with).
   Optional: `ADMIN_APP_URL` (default `https://smartgym-admin.vercel.app`) and `MEMBER_APP_URL`
   (default `https://smartgym-member.vercel.app`; must equal the dispatcher's `MEMBER_APP_URL`).
2. Google Chrome installed (Playwright uses `channel: 'chrome'`; no browser is downloaded).
3. Migrations `20261102000008` … `20261102000011` applied and the functions `ingest-events`,
   `device-sync`, `qr-token` and `notification-dispatch` deployed, with the apps redeployed (C9 test ids).
4. Dispatcher provisioned: function secrets `NOTIFICATION_DISPATCH_SECRET`, `VAPID_PUBLIC_KEY`,
   `VAPID_PRIVATE_KEY`, `VAPID_SUBJECT`, `MEMBER_APP_URL`; Vault secrets `smartgym_functions_url`
   and `smartgym_dispatch_secret`; pg_cron job `smartgym-notification-dispatch` scheduled.
5. The edge venv at `services/edge-ai/.venv` (override with `EDGE_PYTHON`) for the simulation.

## Commands (repo root)

```powershell
npm run test:live -w tests/live   # vitest: functions.test.ts, e2e-session.test.ts (sequential)
npm run test:ui -w tests/live     # Playwright: ui.spec.ts in headless Chrome
npm run typecheck -w tests/live
```

`QA_RUN_ID=<lowercase-id>` pins the run id (otherwise random).

## What is checked

| File | Checks |
| --- | --- |
| `functions.test.ts` | ingest-events: wrong signature → 401; signed batch → 200, all accepted; replay → no new rows; foreign-branch equipment → rejection with a C4 `code` and a fixed `reason`. device-sync: wrong signature → 401; signed → 200 with the QA cameras, zones, equipment, exercises and consents, and no `members` key (C6). qr-token: anonymous → 401; member GET → `SGM1.<code>.<minute>.<16>`; member POST → 403; staff POST → 200, session `check_in_method='qr'`, `checked_in_by` = staff (C1); second POST → conflict (or the same session), never a second session. Retired device → 403 `device_retired` (C5). |
| `e2e-session.test.ts` | QR check-in as staff → real edge simulation (`EDGE_SIM_CHECKIN=skip`, `EDGE_SIM_CHECKOUT=skip`) on the hosted ingest-events → sets and deduplicated reps on the active session → Mozilla autopush subscription stored by the member → `member_self_check_out` → session completed, report with reps, `web_push` job queued → pg_cron dispatcher delivers within 180 s; decrypted title/body/url equal `services/notification-worker` `render('workout_report_ready', …)` → member reads the report via RLS → `create_report_share` → anonymous `get_shared_report` has `member_first_name`, no `member_name`, no ids → `revoke_report_share` → `null` (C3). |
| `ui.spec.ts` | Staff signs in on the admin app → `/kiosk`; member signs in → `qr-token-text`; staff types it into `#manual-token` → `kiosk-result` success; member dashboard `active-session` → `check-out-button` → report page `report-total-reps`. Before check-out the test occupies the session's `workout_report_ready:<session>` dedupe key with a `dead` job, so no e-mail is ever sent to the reserved `.test` address. |

## Artifacts (`tests/live/artifacts`, gitignored)

- `functions.json`: tenant ids, response bodies of the ingest checks, snapshot key list, QR outcomes, teardown report.
- `e2e-session.json`: every step with start time, duration and error, plus the tenant ids, the
  simulator summary (`batches/ok/remaining/last_error`, reps admitted, duplicates suppressed, the
  last output lines), workout fact counts, report id, notification job, push latency and decrypted
  payload, and the teardown report. `ok: true` only when every step passed.
- `ui/steps/NN-*.png`: a screenshot per UI step; `ui/` also holds Playwright's own test outputs
  and, only when `QA_TRACE=1` is set, a trace of a failed test
  (`npx playwright show-trace tests/live/artifacts/ui/<test>/trace.zip`). **A trace is not
  redacted**: it contains the typed temporary QA password and the auth request/response bodies
  (JWTs). The QA users are deleted at teardown, but delete the trace once it has been read and
  never attach it to a ticket.
- `ui-report/index.html` (`npx playwright show-report tests/live/artifacts/ui-report`), `ui-results.json`, `ui-teardown.json`.

If a run is killed hard (no teardown at all), rerun with the same `QA_RUN_ID`: the global teardown
sweeps every `qa-smart-gym-<QA_RUN_ID>-*` tenant.
