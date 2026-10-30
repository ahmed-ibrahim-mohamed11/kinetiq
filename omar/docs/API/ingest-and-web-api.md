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

| Status | Meaning |
| --- | --- |
| 200 | Batch processed (see `IngestResult`); may include per-event rejections |
| 400 | Body not JSON or fails `events.v1` validation (details included) |
| 401 | Unknown device, bad signature, or `sent_at` outside ±`INGEST_CLOCK_SKEW_SECONDS` (response includes `server_time` for clock diagnosis) |
| 405 / 413 | Method / body > 2 MB |
| 500 | Database error — the edge keeps the batch and retries |

Secrets: `supabase secrets set INGEST_CLOCK_SKEW_SECONDS=300` (optional). The function uses `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` injected by the platform.

## Edge Function: `GET /functions/v1/device-sync`

Device-authenticated configuration snapshot (cloud → edge). Headers:

```
x-device-id:        <edge_devices.id>
x-device-timestamp: <ISO-8601, within ±INGEST_CLOCK_SKEW_SECONDS>
x-device-signature: hex(HMAC_SHA256(secret, device_id + "\n" + timestamp + "\nGET /device-sync"))
```

Returns `device_sync_snapshot(p_device_id)`:

| Field | Content |
| --- | --- |
| `cameras[]` | enabled cameras of the branch with their zones (`type`, `equipment_id`, `exercise_slug`, `polygon`) and `stream_url` **without credentials** |
| `equipment[]`, `exercises[]` | registry + catalogue with `rep_profile` for the edge state machines |
| `consents` | `{ member_id: { consent_type: consent_record_id } }` for granted, unexpired consents of active members — ids only |
| `members[]` | `id`, `member_code`, `full_name` of active members (for enrollment look-ups) |
| `revoked_template_refs[]` / `active_template_refs[]` | enrollment references held by this device; revoked ones must be deleted locally |

The edge caches the snapshot (`EDGE_DATA_DIR/device-sync.json`) and the consent gate fails closed when the cache is older than 24 h. 401 for bad signature/clock; 500 on database error (edge keeps the cached copy).

## Edge Function: `/functions/v1/qr-token`

- `GET` (member JWT) → `{ token, expires_in_seconds }` where `token = SGM1.<member_code>.<unix_minute>.<mac16>`.
- `POST` (staff JWT) `{ token }` → `{ attendance_session_id }` via `staff_check_in` under the staff session (branch role enforced). 401 for expired/forged tokens, 403 when the staff member lacks a role in the member's branch.

Requires secret `QR_TOKEN_SECRET`.

## PostgREST / RPC (through supabase-js, RLS applies)

| RPC | Caller | Purpose |
| --- | --- | --- |
| `staff_check_in(p_member_id, p_method)` | staff | Manual/QR/NFC check-in; idempotent on an active session |
| `staff_check_out(p_session_id, p_method)` | staff | Finalise + report + notification |
| `member_self_check_out()` | member | Same for the caller's own active session |
| `get_shared_report(p_token)` | anon | Public report by share token (summary + first name only) |
| `issue_device_secret(p_device_id)` | owner/admin | Rotate device secret; returns the raw secret once |

Service-role only (never callable from clients): `ingest_events`, `finalize_attendance_session`, `build_workout_summary`, `abandon_stale_sessions`, `apply_retention`, `get_device_secrets`, `device_sync_snapshot`, `claim_notification_jobs`, `complete_notification_job`, `member_push_subscriptions`, `disable_push_subscription`, `release_stale_notification_locks`.

Verified by execution: `tests/db` runs every RPC above on an embedded PostgreSQL with PostgREST-equivalent role switching (see `tests/README.md`).

## Web app server actions

Inputs are validated with `packages/api-contracts/src/api.ts` (`CreateCameraInput`, `CreateCameraZoneInput`, `CreateEquipmentInput`, `CreateMemberInput`, `RecordConsentInput`, `WithdrawConsentInput`, `ManualCheckInInput`, `ManualCheckOutInput`, `RegisterPushSubscriptionInput`) and executed with the caller's session, so RLS is the final authority. Errors are returned as `{ ok: false, error }` using the `ApiError` code set.

## Health endpoints

- Edge: `GET :8088/healthz` (JSON, 200/503), `GET :8088/metrics` (Prometheus).
- Web apps: `GET /api/health`.
