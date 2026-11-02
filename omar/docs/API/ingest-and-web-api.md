# HTTP API surface

## Edge Function: `POST /functions/v1/ingest-events`

Authentication: device HMAC (not a user JWT; `verify_jwt = false`).

Headers:

```
content-type: application/json
x-device-id: <edge_devices.id>
x-device-signature: hex(HMAC_SHA256(secret, canonical))
canonical = device_id + "\n" + batch_id + "\n" + sent_at + "\n" + sha256hex(JSON.stringify(events))
```

`JSON.stringify(events)` compatibility: integral floats are serialized as integers (`1`, not `1.0`); non-integral floats are rounded to 4 decimals; object keys keep insertion order. The Python client's `normalize_json()` guarantees this.

Order of checks (2026-11-02): the body is streamed with a 2 MiB cap; only JSON parsing, the four signed envelope fields, the header/device match and the clock window are evaluated before the signature; the HMAC covers the events exactly as received (extra envelope keys and a different key order still verify); only then is the batch validated against the full `events.v1` union.

| Status | Meaning |
| --- | --- |
| 200 | Batch processed (see `IngestResult`). `rejected[]` items carry `code` ∈ `branch_mismatch`, `no_consent`, `unknown_reference`, `invalid_value`, `invalid_state`, `internal` and a fixed sentence as `reason` (never raw SQL text). Events that fail the schema are rejected by the function with `invalid_value` before reaching the database. A replayed `batch_id` returns `accepted: 0, replayed: true` |
| 400 | Body not JSON or malformed envelope (no details before authentication); schema details only after authentication |
| 401 | Unknown device, bad signature, or `sent_at` outside ±`INGEST_CLOCK_SKEW_SECONDS` (response includes `server_time` for clock diagnosis) |
| 403 | `{code: "device_retired"}` — the device was retired; the edge stops syncing |
| 405 / 413 | Method / body > 2 MiB |
| 500 | Database or infrastructure error — nothing from the batch is recorded; the edge keeps the batch and retries with the same `batch_id` |

Secrets: `supabase secrets set INGEST_CLOCK_SKEW_SECONDS=300` (optional; a value outside 1..3600 falls back to 300 and is logged). The function uses `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` injected by the platform.

## Edge Function: `GET /functions/v1/device-sync`

Device-authenticated configuration snapshot (cloud → edge). Headers:

```
x-device-id:        <edge_devices.id>
x-device-timestamp: <ISO-8601, within ±INGEST_CLOCK_SKEW_SECONDS>
x-device-signature: hex(HMAC_SHA256(secret, device_id + "\n" + timestamp + "\nGET /device-sync"))
```

Returns `device_sync_snapshot(p_device_id)` (`schema_version` 2 since migration 0009):

| Field | Content |
| --- | --- |
| `generated_at`, `device_id`, `branch_id` | snapshot identity; the edge measures consent-cache staleness from `generated_at` |
| `cameras[]` | enabled cameras of the branch with their zones (`type` incl. `exclusion`, `equipment_id`, `exercise_slug`, `polygon`) and `stream_url` **without credentials** |
| `zones[]` | the same zones as a flat list with `camera_id` |
| `equipment[]`, `exercises[]` | registry + catalogue with `rep_profile` for the edge state machines |
| `consents[]` | `{ member_id, consent_type, consent_record_id, expires_at }` — only granted, unexpired `biometric_face_identification` and `cctv_workout_tracking` records of active members; ids only |
| `revoked_template_refs[]` / `active_template_refs[]` | enrollment references held by this device; revoked ones (incl. deleted/cancelled members and expired consent) must be deleted locally, and anything absent from the active list is reconciled away after a successful sync |

No member roster is sent. The edge caches the snapshot encrypted (`EDGE_DATA_DIR/device-sync.json`, AES-GCM with a sub-key of the biometric key) and the consent gate fails closed when the snapshot is older than 24 h. 401 for bad signature/clock; 403 `device_retired` for a retired device; 500 on database error (edge keeps the cached copy).

## Edge Function: `/functions/v1/qr-token`

- `GET` (member JWT) → `{ token, expires_in_seconds }` where `token = SGM1.<member_code>.<unix_minute>.<mac16>`; the MAC is bound to the member id, so a member of another gym with the same code cannot mint a usable token. Valid for the current minute ±1.
- `POST` (staff JWT) `{ token }` → `{ attendance_session_id }`. The function verifies the MAC in constant time, requires the caller to hold a check-in role (owner, admin, manager, front_desk, trainer) for the member's branch, refuses a token already used for a check-in in its validity window, and then calls `qr_check_in(p_member_code, p_staff_user_id)` with the service role. Codes: 400 `validation_error` / `invalid_method`; 401 `unauthorized` (expired or bad token); 403 `forbidden`; 404 `not_found`; 409 `ambiguous_member_code` / `token_used` / `member_inactive` / `conflict`; 500 `internal`.
- CORS: `OPTIONS` is answered; allowed origins come from `MEMBER_APP_URL`, `ADMIN_APP_URL` and `QR_ALLOWED_ORIGINS` (comma-separated), `*` when none is set.

Requires secret `QR_TOKEN_SECRET`.

## Edge Function: `POST /functions/v1/notification-dispatch`

Called every minute by the `pg_cron` job `smartgym-notification-dispatch` through `pg_net` with header `x-dispatch-secret` (Vault secret `smartgym_dispatch_secret` = function secret `NOTIFICATION_DISPATCH_SECRET`, compared in constant time; 401 otherwise). It claims due `notification_jobs` with `claim_notification_jobs`, sends Web Push (RFC 8291 `aes128gcm`, VAPID ES256) or e-mail (Resend, when configured) and completes each job with `complete_notification_job`; it responds `{ claimed, delivered, failed }` and `500 {code: "not_configured", missing}` when a secret is absent. Details: `docs/deployment/notification-dispatch.md`.

## PostgREST / RPC (through supabase-js, RLS applies)

| RPC | Caller | Purpose |
| --- | --- | --- |
| `staff_check_in(p_member_id, p_method)` | staff | Manual or NFC check-in only (`qr`/`face` are refused with 22023); idempotent on an active session; records `checked_in_by` |
| `staff_check_out(p_session_id, p_method)` | staff | Finalise + report + notification; records `checked_out_by` |
| `member_self_check_out()` | member | Same for the caller's own active session |
| `create_report_share(p_report_id, p_days = 7)` | member (own report) | Creates the share token on demand (1–30 days), audit-logged; returns `{token, expires_at}` |
| `revoke_report_share(p_report_id)` | member (own report) | Revokes the token, audit-logged |
| `get_shared_report(p_token)` | anon | Public report by share token: summary + `member_first_name`; `null` when revoked, expired or unknown |
| `revoke_biometric_enrollment(p_enrollment_id)` | branch owner/admin | Revokes a template reference (the edge deletes the template on its next sync) |
| `issue_device_secret(p_device_id)` | owner/admin | Rotate device secret (24 h overlap); returns the raw secret once; refused for retired devices |

Service-role only (never callable from clients): `ingest_events`, `qr_check_in`, `finalize_attendance_session`, `build_workout_summary`, `abandon_stale_sessions`, `apply_retention`, `expire_consents`, `get_device_secrets`, `device_sync_snapshot`, `claim_notification_jobs`, `complete_notification_job`, `member_push_subscriptions`, `disable_push_subscription`, `release_stale_notification_locks`, and the `_`-prefixed helpers. The RLS helper functions are no longer executable by `anon`.

Member-facing write restrictions enforced in the database (migrations 0009–0010): `workout_reports` updates may change only `viewed_at`; `consent_records` may only move from `granted` to `withdrawn` (or `expired`), and browser inserts have `captured_by_staff_id`/`granted_at` forced to the caller and `now()`; `members.user_id` can only be linked by the service role; `push_subscriptions.endpoint` must belong to a known push service (FCM, Mozilla, Apple, WNS).

Verified by execution: `tests/db` runs every RPC above on an embedded PostgreSQL with PostgREST-equivalent role switching (see `tests/README.md`).

## Web app server actions

Inputs are validated with `packages/api-contracts/src/api.ts` (`CreateCameraInput`, `CreateCameraZoneInput`, `CreateEquipmentInput`, `CreateMemberInput`, `RecordConsentInput`, `WithdrawConsentInput`, `ManualCheckInInput`, `ManualCheckOutInput`, `RegisterPushSubscriptionInput`) and executed with the caller's session, so RLS is the final authority. Errors are returned as `{ ok: false, error }` using the `ApiError` code set.

## Health endpoints

- Edge: `GET :8088/healthz` (JSON, 200/503), `GET :8088/metrics` (Prometheus).
- Web apps: `GET /api/health`.
