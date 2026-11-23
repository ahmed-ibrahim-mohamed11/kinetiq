/**
 * ingest-events request handling, free of Deno.serve and supabase-js so it can be unit-tested.
 *
 * Order of operations (cheap and unauthenticated first, expensive and detailed only after the
 * device proved possession of its secret):
 *   1. method, X-Device-Id / X-Device-Signature format
 *   2. body read with a streaming byte cap (413), UTF-8 + JSON parse (400, no details)
 *   3. the four envelope fields the signature binds (device_id, batch_id, sent_at, events)
 *   4. device_id matches the header, sent_at within INGEST_CLOCK_SKEW_SECONDS (401)
 *   5. secret lookup + HMAC over the events EXACTLY as received: sha256(JSON.stringify(events))
 *      of the parsed body, before any Zod work, so additive envelope/payload fields from newer
 *      producers do not break the signature
 *   6. batch schema (400 with details) and per-event validation against the full events.v1
 *      union (single source of truth: packages/api-contracts, vendored in _shared/events_v1.ts).
 *      Invalid events are rejected individually with code 'invalid_value'; the rest go to SQL.
 *   7. public.ingest_events; its C4 rejection codes are passed through unchanged.
 */
import { z } from 'npm:zod@3.24.1';
import {
  type DbResult,
  defaultLog,
  type DeviceAuthDeps,
  deviceRetired,
  internalError,
  isDeviceRetiredError,
  json,
  type Logger,
  readBodyCapped,
  sha256Hex,
  SIGNATURE_RE,
  unauthorized,
  UUID_RE,
  verifyDeviceSignature,
  withinSkew,
} from '../_shared/device_auth.ts';
import { EdgeEvent, EVENTS_SCHEMA_VERSION, type IngestRejectionCode } from '../_shared/events_v1.ts';

const INVALID_VALUE: IngestRejectionCode = 'invalid_value';

export const MAX_BODY_BYTES = 2 * 1024 * 1024;

export interface Rejection {
  event_id: string;
  reason: string;
  /** C4 code (IngestRejectionCode); SQL rejections are passed through as returned. */
  code?: string;
}

export interface IngestRpcResult {
  batch_id?: string;
  accepted: number;
  duplicates: number;
  rejected: Rejection[];
  server_time?: string;
  [key: string]: unknown;
}

export interface IngestDeps extends DeviceAuthDeps {
  clockSkewSeconds: number;
  maxBodyBytes?: number;
  now?: () => number;
  ingestEvents(
    args: { p_device_id: string; p_batch_id: string; p_sent_at: string; p_events: unknown[] },
  ): Promise<DbResult<IngestRpcResult>>;
  log?: Logger;
}

const uuid = z.string().uuid();
/** Batch-level shape, checked after authentication. Each event is validated on its own below. */
const BatchEnvelope = z.object({
  schema_version: z.literal(EVENTS_SCHEMA_VERSION),
  batch_id: uuid,
  device_id: uuid,
  sent_at: z.string().datetime({ offset: true }),
  events: z.array(z.object({ event_id: uuid }).passthrough()).min(1).max(500),
});

export const INVALID_EVENT_REASON = 'Event does not match the events.v1 schema.';

const malformed = () => json(400, { code: 'validation_error', message: 'batch envelope is malformed' });

export async function handle(req: Request, deps: IngestDeps): Promise<Response> {
  const log = deps.log ?? defaultLog;
  const now = deps.now ?? Date.now;
  if (req.method !== 'POST') return json(405, { code: 'method_not_allowed' });

  const deviceId = req.headers.get('x-device-id') ?? '';
  const signature = req.headers.get('x-device-signature') ?? '';
  if (!UUID_RE.test(deviceId) || !SIGNATURE_RE.test(signature)) return unauthorized();

  const bytes = await readBodyCapped(req, deps.maxBodyBytes ?? MAX_BODY_BYTES);
  if (!bytes) return json(413, { code: 'payload_too_large' });

  let body: unknown;
  try {
    body = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes));
  } catch {
    return json(400, { code: 'validation_error', message: 'body is not JSON' });
  }
  if (!body || typeof body !== 'object' || Array.isArray(body)) return malformed();
  const raw = body as Record<string, unknown>;
  const { device_id, batch_id, sent_at, events } = raw;
  if (typeof device_id !== 'string' || typeof batch_id !== 'string' || typeof sent_at !== 'string' || !Array.isArray(events)) {
    return malformed();
  }
  if (device_id !== deviceId) return unauthorized();
  if (!withinSkew(sent_at, now(), deps.clockSkewSeconds)) {
    return json(401, {
      code: 'unauthorized',
      message: 'batch timestamp outside accepted window',
      server_time: new Date(now()).toISOString(),
    });
  }

  // Signature over the events exactly as received (pre-Zod), see the module comment.
  const canonical = `${device_id}\n${batch_id}\n${sent_at}\n${await sha256Hex(JSON.stringify(events))}`;
  const authFailure = await verifyDeviceSignature(deps, deviceId, canonical, signature);
  if (authFailure) return authFailure;

  // ── Authenticated from here on: detailed validation errors are fine to return.
  const envelope = BatchEnvelope.safeParse(raw);
  if (!envelope.success) {
    return json(400, { code: 'validation_error', message: 'batch failed schema validation', details: envelope.error.flatten() });
  }

  const valid: EdgeEvent[] = [];
  const rejected: Rejection[] = [];
  for (const ev of envelope.data.events) {
    const parsed = EdgeEvent.safeParse(ev);
    if (parsed.success) {
      valid.push(parsed.data);
    } else {
      rejected.push({ event_id: ev.event_id, code: INVALID_VALUE, reason: INVALID_EVENT_REASON });
      log('event failed schema validation', {
        deviceId,
        batchId: batch_id,
        eventId: ev.event_id,
        eventType: typeof ev.event_type === 'string' ? ev.event_type.slice(0, 64) : null,
        issues: parsed.error.issues.slice(0, 5).map((i) => `${i.path.join('.')}: ${i.message}`),
      });
    }
  }

  const serverTime = () => new Date(now()).toISOString();
  if (valid.length === 0) {
    return json(200, { batch_id, accepted: 0, duplicates: 0, rejected, server_time: serverTime() });
  }

  const { data, error } = await deps.ingestEvents({ p_device_id: deviceId, p_batch_id: batch_id, p_sent_at: sent_at, p_events: valid });
  if (error) {
    // 23505 = batch already processed (replay). Idempotent success so the edge can drop it.
    if (error.code === '23505') {
      return json(200, { batch_id, accepted: 0, duplicates: valid.length, rejected, server_time: serverTime(), replayed: true });
    }
    if (isDeviceRetiredError(error)) return deviceRetired();
    log('ingest_events failed', { deviceId, batchId: batch_id, code: error.code ?? null, message: error.message });
    return internalError();
  }
  if (!data) return internalError();

  return json(200, { ...data, rejected: [...rejected, ...(data.rejected ?? [])] });
}
