/**
 * Thin HTTP helpers for the deployed Edge Functions. Device calls send exactly the headers the
 * edge sends (no apikey: ingest-events / device-sync run with verify_jwt=false); user calls send
 * the user's JWT plus the anon apikey like the apps do.
 */
import { liveEnv } from './env.ts';
import { isoNow, signBatch, signDeviceSync, type EventBatch } from './signing.ts';

export interface HttpResult<T = Record<string, unknown>> {
  status: number;
  body: T;
}

async function readJson<T>(res: Response): Promise<T> {
  const text = await res.text();
  try {
    return (text ? JSON.parse(text) : {}) as T;
  } catch {
    return { raw: text.slice(0, 500) } as T;
  }
}

export interface IngestResponse {
  batch_id?: string;
  accepted?: number;
  duplicates?: number;
  rejected?: Array<{ event_id: string; reason: string; code?: string }>;
  replayed?: boolean;
  code?: string;
  message?: string;
}

/** POST a batch to ingest-events. `signature` overrides the computed one (negative tests). */
export async function postIngest(deviceId: string, secret: string, batch: EventBatch, signature?: string): Promise<HttpResult<IngestResponse>> {
  const res = await fetch(`${liveEnv().functionsUrl}/ingest-events`, {
    method: 'POST',
    headers: {
      'content-type': 'application/json',
      'x-device-id': deviceId,
      'x-device-signature': signature ?? signBatch(secret, batch),
    },
    body: JSON.stringify(batch),
  });
  return { status: res.status, body: await readJson<IngestResponse>(res) };
}

export async function getDeviceSync(deviceId: string, secret: string, opts: { badSignature?: boolean } = {}): Promise<HttpResult> {
  const ts = isoNow();
  const res = await fetch(`${liveEnv().functionsUrl}/device-sync`, {
    method: 'GET',
    headers: {
      'x-device-id': deviceId,
      'x-device-timestamp': ts,
      'x-device-signature': opts.badSignature ? '0'.repeat(64) : signDeviceSync(secret, deviceId, ts),
    },
  });
  return { status: res.status, body: await readJson(res) };
}

function userHeaders(accessToken: string | null): Record<string, string> {
  const h: Record<string, string> = { apikey: liveEnv().anonKey };
  if (accessToken) h.authorization = `Bearer ${accessToken}`;
  return h;
}

export async function qrTokenGet(accessToken: string | null, opts: { omitApikey?: boolean } = {}): Promise<HttpResult<{ token?: string; expires_in_seconds?: number; code?: string }>> {
  const headers = userHeaders(accessToken);
  if (opts.omitApikey) delete headers.apikey;
  const res = await fetch(`${liveEnv().functionsUrl}/qr-token`, { method: 'GET', headers });
  return { status: res.status, body: await readJson(res) };
}

export async function qrTokenPost(accessToken: string | null, token: string): Promise<HttpResult<{ attendance_session_id?: string; code?: string; message?: string }>> {
  const res = await fetch(`${liveEnv().functionsUrl}/qr-token`, {
    method: 'POST',
    headers: { ...userHeaders(accessToken), 'content-type': 'application/json' },
    body: JSON.stringify({ token }),
  });
  return { status: res.status, body: await readJson(res) };
}

/** SGM1.<member_code>.<unix_minute>.<16 base64url chars> */
export const QR_TOKEN_RE = /^SGM1\.([A-HJ-NP-Z2-9-]{4,32})\.(\d{7,10})\.([A-Za-z0-9_-]{16})$/;

export async function poll<T>(what: string, fn: () => Promise<T | null | undefined | false>, timeoutMs: number, intervalMs = 2_000): Promise<T> {
  const deadline = Date.now() + timeoutMs;
  let last: unknown = null;
  for (;;) {
    try {
      const v = await fn();
      if (v) return v;
    } catch (err) {
      last = err;
    }
    if (Date.now() > deadline) {
      throw new Error(`timed out after ${Math.round(timeoutMs / 1000)} s waiting for ${what}${last ? `: ${(last as Error).message}` : ''}`);
    }
    await new Promise((r) => setTimeout(r, intervalMs));
  }
}
