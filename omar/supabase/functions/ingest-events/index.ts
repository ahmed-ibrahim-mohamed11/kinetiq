/**
 * POST /functions/v1/ingest-events
 *
 * Authenticated by a per-device HMAC-SHA256 signature (NOT a user JWT):
 *   X-Device-Id:        <uuid>
 *   X-Device-Signature: hex(HMAC_SHA256(secret, canonicalBatchString))
 *   canonicalBatchString = `${device_id}\n${batch_id}\n${sent_at}\n${sha256hex(JSON.stringify(events))}`
 *
 * Replay protection: batch_id is single-use (DB PK) and sent_at must be within
 * INGEST_CLOCK_SKEW_SECONDS of server time. Multiple active secrets are accepted
 * during rotation. Any failure returns a generic 401 to avoid oracle behaviour.
 *
 * The function validates the batch shape against events.v1, then calls the
 * `ingest_events` RPC with the service role. All business validation happens
 * in SQL so the same rules apply to every ingest path.
 */
import { createClient } from 'npm:@supabase/supabase-js@2.47.10';
import { z } from 'npm:zod@3.24.1';

// ── Inline copy of the events.v1 envelope shape (full schema lives in @smartgym/api-contracts).
// Deno edge functions cannot import workspace packages directly; tests/contracts cross-checks.
const uuid = z.string().uuid();
const EventEnvelope = z.object({
  schema_version: z.literal('1'),
  event_id: uuid,
  device_id: uuid,
  branch_id: uuid,
  sequence: z.number().int().nonnegative(),
  occurred_at: z.string().datetime({ offset: true }),
  sent_at: z.string().datetime({ offset: true }),
  event_type: z.string().min(3).max(64),
  payload: z.record(z.unknown()),
});
const EventBatch = z.object({
  schema_version: z.literal('1'),
  batch_id: uuid,
  device_id: uuid,
  sent_at: z.string().datetime({ offset: true }),
  events: z.array(EventEnvelope).min(1).max(500),
});

const SUPABASE_URL = Deno.env.get('SUPABASE_URL')!;
const SERVICE_ROLE = Deno.env.get('SUPABASE_SERVICE_ROLE_KEY')!;
const CLOCK_SKEW_SECONDS = Number(Deno.env.get('INGEST_CLOCK_SKEW_SECONDS') ?? '300');
const MAX_BODY_BYTES = 2 * 1024 * 1024;

const encoder = new TextEncoder();

async function sha256Hex(data: string): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', encoder.encode(data));
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, '0')).join('');
}

async function hmacHex(secret: string, message: string): Promise<string> {
  const key = await crypto.subtle.importKey('raw', encoder.encode(secret), { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']);
  const sig = await crypto.subtle.sign('HMAC', key, encoder.encode(message));
  return [...new Uint8Array(sig)].map((b) => b.toString(16).padStart(2, '0')).join('');
}

function timingSafeEqualHex(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });
}

const unauthorized = () => json(401, { code: 'unauthorized', message: 'invalid device credentials' });

Deno.serve(async (req) => {
  if (req.method !== 'POST') return json(405, { code: 'method_not_allowed' });

  const deviceId = req.headers.get('x-device-id');
  const signature = req.headers.get('x-device-signature');
  if (!deviceId || !signature || !uuid.safeParse(deviceId).success || !/^[0-9a-f]{64}$/.test(signature)) {
    return unauthorized();
  }

  const contentLength = Number(req.headers.get('content-length') ?? '0');
  if (contentLength > MAX_BODY_BYTES) return json(413, { code: 'payload_too_large' });

  const rawBody = await req.text();
  if (rawBody.length > MAX_BODY_BYTES) return json(413, { code: 'payload_too_large' });

  let parsedJson: unknown;
  try {
    parsedJson = JSON.parse(rawBody);
  } catch {
    return json(400, { code: 'validation_error', message: 'body is not JSON' });
  }
  const parsed = EventBatch.safeParse(parsedJson);
  if (!parsed.success) {
    return json(400, { code: 'validation_error', message: 'batch failed schema validation', details: parsed.error.flatten() });
  }
  const batch = parsed.data;
  if (batch.device_id !== deviceId) return unauthorized();

  // Clock skew check (replay window).
  const skew = Math.abs(Date.now() - Date.parse(batch.sent_at)) / 1000;
  if (!Number.isFinite(skew) || skew > CLOCK_SKEW_SECONDS) {
    return json(401, { code: 'unauthorized', message: 'batch timestamp outside accepted window', server_time: new Date().toISOString() });
  }

  const supabase = createClient(SUPABASE_URL, SERVICE_ROLE, { auth: { persistSession: false } });

  const { data: secrets, error: secretErr } = await supabase.rpc('get_device_secrets', { p_device_id: deviceId });
  if (secretErr || !secrets || secrets.length === 0) return unauthorized();

  const eventsHash = await sha256Hex(JSON.stringify(batch.events));
  const canonical = `${batch.device_id}\n${batch.batch_id}\n${batch.sent_at}\n${eventsHash}`;

  let verified = false;
  for (const row of secrets as Array<{ secret: string }>) {
    const expected = await hmacHex(row.secret, canonical);
    if (timingSafeEqualHex(expected, signature)) {
      verified = true;
      break;
    }
  }
  if (!verified) return unauthorized();

  const { data, error } = await supabase.rpc('ingest_events', {
    p_device_id: deviceId,
    p_batch_id: batch.batch_id,
    p_sent_at: batch.sent_at,
    p_events: batch.events,
  });

  if (error) {
    // 23505 = batch already processed (replay). Treat as idempotent success so the edge can drop it.
    if (error.code === '23505') {
      return json(200, { batch_id: batch.batch_id, accepted: 0, duplicates: batch.events.length, rejected: [], server_time: new Date().toISOString(), replayed: true });
    }
    console.error('ingest_events failed', { deviceId, batchId: batch.batch_id, code: error.code, message: error.message });
    return json(500, { code: 'internal', message: 'ingest failed' });
  }

  return json(200, data);
});
