/**
 * Device request signing, byte-identical to the edge (services/edge-ai/edge_ai/events/sync.py,
 * edge_ai/cloud/device_sync.py) and to what the Edge Functions verify
 * (supabase/functions/ingest-events/handler.ts, supabase/functions/device-sync/index.ts).
 *
 *   ingest:  hex(HMAC_SHA256(secret, `${device_id}\n${batch_id}\n${sent_at}\n${sha256hex(JSON.stringify(events))}`))
 *   sync:    hex(HMAC_SHA256(secret, `${device_id}\n${timestamp}\nGET /device-sync`))
 *
 * The server computes sha256(JSON.stringify(events)) over the `events` array of the parsed request
 * body, BEFORE any Zod work, so the signature binds the events exactly as sent. Because we send
 * JSON.stringify of the same objects we hash, the two always agree. The canonical key order of the
 * envelopes built here is kept only for parity with the edge (services/edge-ai), not because the
 * server reorders anything.
 */
import { createHash, createHmac, randomUUID } from 'node:crypto';

export interface EventEnvelope {
  schema_version: '1';
  event_id: string;
  device_id: string;
  branch_id: string;
  sequence: number;
  occurred_at: string;
  sent_at: string;
  event_type: string;
  payload: Record<string, unknown>;
}

export interface EventBatch {
  schema_version: '1';
  batch_id: string;
  device_id: string;
  sent_at: string;
  events: EventEnvelope[];
}

/** Rebuild an envelope with the key order of the server-side zod schema. */
export function canonicalEnvelope(e: EventEnvelope): EventEnvelope {
  return {
    schema_version: e.schema_version,
    event_id: e.event_id,
    device_id: e.device_id,
    branch_id: e.branch_id,
    sequence: e.sequence,
    occurred_at: e.occurred_at,
    sent_at: e.sent_at,
    event_type: e.event_type,
    payload: e.payload,
  };
}

export function sha256Hex(data: string): string {
  return createHash('sha256').update(data, 'utf8').digest('hex');
}

export function hmacHex(secret: string, message: string): string {
  return createHmac('sha256', secret).update(message, 'utf8').digest('hex');
}

export function canonicalBatchString(deviceId: string, batchId: string, sentAt: string, events: EventEnvelope[]): string {
  return `${deviceId}\n${batchId}\n${sentAt}\n${sha256Hex(JSON.stringify(events.map(canonicalEnvelope)))}`;
}

export function signBatch(secret: string, batch: EventBatch): string {
  return hmacHex(secret, canonicalBatchString(batch.device_id, batch.batch_id, batch.sent_at, batch.events));
}

export function signDeviceSync(secret: string, deviceId: string, timestamp: string): string {
  return hmacHex(secret, `${deviceId}\n${timestamp}\nGET /device-sync`);
}

/** ISO-8601 with milliseconds and Z, like the edge's _now_iso(). */
export function isoNow(offsetMs = 0): string {
  return new Date(Date.now() + offsetMs).toISOString();
}

export function makeEvent(
  ctx: { deviceId: string; branchId: string },
  sequence: number,
  eventType: string,
  payload: Record<string, unknown>,
  occurredAt: string = isoNow(),
): EventEnvelope {
  return canonicalEnvelope({
    schema_version: '1',
    event_id: randomUUID(),
    device_id: ctx.deviceId,
    branch_id: ctx.branchId,
    sequence,
    occurred_at: occurredAt,
    sent_at: isoNow(),
    event_type: eventType,
    payload,
  });
}

export function makeBatch(deviceId: string, events: EventEnvelope[]): EventBatch {
  return { schema_version: '1', batch_id: randomUUID(), device_id: deviceId, sent_at: isoNow(), events: events.map(canonicalEnvelope) };
}
