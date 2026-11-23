/**
 * Ingest smoke test against a running local Supabase + `supabase functions serve ingest-events`.
 *
 *   SUPABASE_URL=http://127.0.0.1:54321 SUPABASE_SERVICE_ROLE_KEY=... node tests/integration/ingest-smoke.mjs
 *
 * Signs the shared contract fixtures with the device secret, posts them, and verifies: 200, all
 * accepted; a replay of the same batch_id is idempotent (0 accepted); a wrong signature is
 * rejected with 401; and the attendance session + report exist.
 *
 * Every id defaults to the seeded DEMO tenant (supabase/seed.sql), so local behaviour is unchanged.
 * Override them to point the smoke test at another tenant (e.g. a QA tenant):
 *   SMOKE_DEVICE_ID, SMOKE_BRANCH_ID, SMOKE_DEVICE_SECRET, SMOKE_MEMBER_ID,
 *   SMOKE_BIOMETRIC_CONSENT_ID (consent_record_id used by biometric.enrolled / identity.checkin),
 *   SMOKE_CAMERA_A_ID, SMOKE_CAMERA_B_ID (cameras the fixtures alternate between; B is the leg-press camera),
 *   SMOKE_EQUIPMENT_ID, SMOKE_ZONE_ID (leg-press equipment and its zone on camera B),
 *   SMOKE_EXPECTED_TOTAL_REPS (default 10, what the fixtures count).
 * The device secret is never printed.
 */
import { createHmac, createHash, randomUUID } from 'node:crypto';
import { readFileSync } from 'node:fs';

const env = (name, fallback) => {
  const v = process.env[name];
  return v !== undefined && v.trim() !== '' ? v.trim() : fallback;
};

const URL_BASE = process.env.SUPABASE_URL ?? 'http://127.0.0.1:54321';
const FUNCTIONS_URL = process.env.FUNCTIONS_URL ?? 'http://127.0.0.1:54321/functions/v1';
const SERVICE = process.env.SUPABASE_SERVICE_ROLE_KEY;
const DEVICE = env('SMOKE_DEVICE_ID', 'd0000000-0000-4000-8000-000000000001');
const BRANCH = env('SMOKE_BRANCH_ID', 'b0000000-0000-4000-8000-000000000001');
const SECRET = env('SMOKE_DEVICE_SECRET', 'dev-only-device-secret-change-me');
const MEMBER = env('SMOKE_MEMBER_ID', '33333333-3333-4333-8333-333333333333');
const BIOMETRIC_CONSENT = env('SMOKE_BIOMETRIC_CONSENT_ID', '55555555-5555-4555-8555-555555555555');
const CAMERA_A = env('SMOKE_CAMERA_A_ID', 'c0000000-0000-4000-8000-000000000001');
const CAMERA_B = env('SMOKE_CAMERA_B_ID', 'c0000000-0000-4000-8000-000000000002');
const EQUIPMENT = env('SMOKE_EQUIPMENT_ID', 'e0000000-0000-4000-8000-000000000002');
const ZONE = env('SMOKE_ZONE_ID', 'f0000000-0000-4000-8000-000000000003');
const EXPECTED_TOTAL_REPS = Number(env('SMOKE_EXPECTED_TOTAL_REPS', '10'));

// Ids baked into packages/api-contracts/fixtures/events_v1_valid.json.
const FIXTURE_MEMBER = '33333333-3333-4333-8333-333333333333';
const FIXTURE_BIOMETRIC_CONSENT = '55555555-5555-4555-8555-555555555555';

const fixtures = JSON.parse(readFileSync(new URL('../../packages/api-contracts/fixtures/events_v1_valid.json', import.meta.url), 'utf8'));

// Rebase fixtures onto the target tenant and current time (clock-skew window).
const now = Date.now();
const cameraIds = [CAMERA_A, CAMERA_B];
const events = fixtures.map((e, i) => {
  const ev = structuredClone(e);
  ev.event_id = randomUUID();
  ev.device_id = DEVICE;
  ev.branch_id = BRANCH;
  ev.sequence = 1000 + i;
  ev.occurred_at = new Date(now - (fixtures.length - i) * 1000).toISOString();
  ev.sent_at = new Date(now).toISOString();
  if (ev.payload.member_id === FIXTURE_MEMBER) ev.payload.member_id = MEMBER;
  if (ev.payload.consent_record_id === FIXTURE_BIOMETRIC_CONSENT) ev.payload.consent_record_id = BIOMETRIC_CONSENT;
  if (ev.payload.camera_id) ev.payload.camera_id = cameraIds[i % 2];
  if (ev.payload.first_camera_id) ev.payload.first_camera_id = cameraIds[0];
  if (ev.event_type === 'equipment.usage.started') {
    ev.payload.equipment_id = EQUIPMENT;
    ev.payload.zone_id = ZONE;
    ev.payload.camera_id = cameraIds[1];
  }
  if (ev.event_type === 'exercise.activity.started') {
    ev.payload.exercise_id = '__LEG_PRESS__';
  }
  return ev;
});

async function legPressCatalogRows() {
  // PostgREST read of the reference catalog with the service role.
  const res = await fetch(`${URL_BASE}/rest/v1/exercise_catalog?slug=eq.leg-press&select=id`, {
    headers: { apikey: SERVICE, Authorization: `Bearer ${SERVICE}` },
  });
  return res.json();
}

function sign(deviceId, batchId, sentAt, evs) {
  const h = createHash('sha256').update(JSON.stringify(evs)).digest('hex');
  return createHmac('sha256', SECRET).update(`${deviceId}\n${batchId}\n${sentAt}\n${h}`).digest('hex');
}

async function post(batch, signature) {
  const res = await fetch(`${FUNCTIONS_URL}/ingest-events`, {
    method: 'POST',
    headers: { 'content-type': 'application/json', 'x-device-id': DEVICE, 'x-device-signature': signature },
    body: JSON.stringify(batch),
  });
  return { status: res.status, body: await res.json().catch(() => ({})) };
}

function assert(cond, msg) {
  if (!cond) {
    console.error(`FAIL: ${msg}`);
    process.exit(1);
  }
  console.log(`ok: ${msg}`);
}

const [{ id: legPressId }] = await legPressCatalogRows();
for (const ev of events) if (ev.payload.exercise_id === '__LEG_PRESS__') ev.payload.exercise_id = legPressId;

const batchId = randomUUID();
const sentAt = new Date().toISOString();
const batch = { schema_version: '1', batch_id: batchId, device_id: DEVICE, sent_at: sentAt, events };

const bad = await post(batch, 'f'.repeat(64));
assert(bad.status === 401, 'wrong signature → 401');

const ok = await post(batch, sign(DEVICE, batchId, sentAt, events));
assert(ok.status === 200, `signed batch → 200 (got ${ok.status} ${JSON.stringify(ok.body)})`);
assert(ok.body.rejected.length === 0, `no rejected events (${JSON.stringify(ok.body.rejected)})`);
assert(ok.body.accepted === events.length, `all ${events.length} events accepted`);

const replay = await post(batch, sign(DEVICE, batchId, sentAt, events));
assert(replay.status === 200 && replay.body.accepted === 0, 'replayed batch is idempotent');

const sessions = await fetch(`${URL_BASE}/rest/v1/attendance_sessions?member_id=eq.${MEMBER}&select=id,status&order=check_in_at.desc&limit=1`, {
  headers: { apikey: SERVICE, Authorization: `Bearer ${SERVICE}` },
}).then((r) => r.json());
assert(sessions[0]?.status === 'completed', 'checkout finalized the attendance session');
const reports = await fetch(`${URL_BASE}/rest/v1/workout_reports?attendance_session_id=eq.${sessions[0].id}&select=summary`, {
  headers: { apikey: SERVICE, Authorization: `Bearer ${SERVICE}` },
}).then((r) => r.json());
assert(
  reports.length === 1 && reports[0].summary.total_reps === EXPECTED_TOTAL_REPS,
  `report generated with total_reps=${EXPECTED_TOTAL_REPS} (got ${JSON.stringify(reports[0]?.summary?.total_reps)})`,
);
console.log('ingest smoke test passed');
