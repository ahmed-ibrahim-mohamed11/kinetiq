/**
 * Shared helpers for the device-authenticated Edge Functions (ingest-events, device-sync):
 * hashing/HMAC, constant-time comparison, clock-skew configuration, a streaming body cap and
 * the common "look up the device secrets and verify the signature" step.
 *
 * Nothing here touches the network: every database call goes through the injected DeviceAuthDeps,
 * so handlers can be unit-tested with fakes.
 */

const encoder = new TextEncoder();

export const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
export const SIGNATURE_RE = /^[0-9a-f]{64}$/;

export const DEFAULT_CLOCK_SKEW_SECONDS = 300;
export const MAX_CLOCK_SKEW_SECONDS = 3600;

/** Result shape of a supabase-js RPC call, reduced to what the handlers use. */
export interface DbError {
  code?: string;
  message: string;
}
export interface DbResult<T> {
  data: T | null;
  error: DbError | null;
}

export type Logger = (msg: string, data?: Record<string, unknown>) => void;

export const defaultLog: Logger = (msg, data) => console.error(JSON.stringify({ ts: new Date().toISOString(), msg, ...data }));

const toHex = (buf: ArrayBuffer): string => [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, '0')).join('');

export async function sha256Hex(data: string | Uint8Array): Promise<string> {
  const bytes = typeof data === 'string' ? encoder.encode(data) : data;
  return toHex(await crypto.subtle.digest('SHA-256', bytes as Uint8Array<ArrayBuffer>));
}

export async function hmacHex(secret: string, message: string): Promise<string> {
  const key = await crypto.subtle.importKey('raw', encoder.encode(secret), { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']);
  return toHex(await crypto.subtle.sign('HMAC', key, encoder.encode(message)));
}

/** Constant-time string comparison (length is not secret: all compared values have fixed lengths). */
export function timingSafeEqual(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

/**
 * INGEST_CLOCK_SKEW_SECONDS → a finite number of seconds in 1..3600. Anything else (unset, empty,
 * "300s", NaN, 0, negative, too large) falls back to the 300 s default, and a misconfiguration is
 * logged, so a typo can never silently disable the replay window.
 */
export function parseClockSkew(raw: string | undefined | null, log: Logger = defaultLog): number {
  const text = (raw ?? '').trim();
  if (text === '') return DEFAULT_CLOCK_SKEW_SECONDS;
  const n = /^\d+(\.\d+)?$/.test(text) ? Number(text) : Number.NaN;
  if (Number.isFinite(n) && n >= 1 && n <= MAX_CLOCK_SKEW_SECONDS) return n;
  log('INGEST_CLOCK_SKEW_SECONDS is invalid; using the default', { value: text.slice(0, 40), effective: DEFAULT_CLOCK_SKEW_SECONDS });
  return DEFAULT_CLOCK_SKEW_SECONDS;
}

/** True when `iso` parses and lies within `skewSeconds` of `nowMs`. */
export function withinSkew(iso: string, nowMs: number, skewSeconds: number): boolean {
  const t = Date.parse(iso);
  return Number.isFinite(t) && Math.abs(nowMs - t) / 1000 <= skewSeconds;
}

export function json(status: number, body: unknown, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json', ...headers } });
}

export const unauthorized = () => json(401, { code: 'unauthorized', message: 'invalid device credentials' });
export const deviceRetired = () => json(403, { code: 'device_retired', message: 'device has been retired' });
export const internalError = () => json(500, { code: 'internal', message: 'internal error' });

/**
 * Read the request body while counting bytes; stops reading (and cancels the stream) as soon as
 * more than `maxBytes` arrived, so a chunked upload cannot make the function buffer more than the
 * cap. Returns null when the body is too large.
 */
export async function readBodyCapped(req: Request, maxBytes: number): Promise<Uint8Array | null> {
  const declared = Number(req.headers.get('content-length') ?? '');
  if (Number.isFinite(declared) && declared > maxBytes) return null;
  if (!req.body) return new Uint8Array(0);
  const reader = req.body.getReader();
  const chunks: Uint8Array[] = [];
  let total = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    total += value.byteLength;
    if (total > maxBytes) {
      await reader.cancel().catch(() => {});
      return null;
    }
    chunks.push(value);
  }
  const out = new Uint8Array(total);
  let offset = 0;
  for (const c of chunks) {
    out.set(c, offset);
    offset += c.byteLength;
  }
  return out;
}

export interface DeviceAuthDeps {
  /** public.get_device_secrets(p_device_id): secrets valid now (C5). */
  getDeviceSecrets(deviceId: string): Promise<DbResult<Array<{ secret: string }>>>;
  /** edge_devices.status for the id, or null when the device does not exist. */
  getDeviceStatus(deviceId: string): Promise<DbResult<string>>;
  log?: Logger;
}

/**
 * Verify `signature` = hex(HMAC_SHA256(secret, canonical)) against every secret valid now.
 * Returns null when verified, otherwise the Response to send:
 *   - 500 when the secret lookup itself fails (infrastructure, so the device retries),
 *   - 403 device_retired when the device has no valid secret because it was retired (C5),
 *   - 401 for everything else (unknown device, no secret, wrong signature).
 */
export async function verifyDeviceSignature(
  deps: DeviceAuthDeps,
  deviceId: string,
  canonical: string,
  signature: string,
): Promise<Response | null> {
  const log = deps.log ?? defaultLog;
  const { data: secrets, error } = await deps.getDeviceSecrets(deviceId);
  if (error) {
    log('get_device_secrets failed', { deviceId, code: error.code ?? null });
    return internalError();
  }
  if (!secrets || secrets.length === 0) {
    const status = await deps.getDeviceStatus(deviceId);
    if (status.error) {
      log('device status lookup failed', { deviceId, code: status.error.code ?? null });
      return internalError();
    }
    return status.data === 'retired' ? deviceRetired() : unauthorized();
  }
  let verified = false;
  for (const row of secrets) {
    // Check every secret (no early exit) so timing does not reveal which rotation slot matched.
    if (timingSafeEqual(await hmacHex(row.secret, canonical), signature)) verified = true;
  }
  return verified ? null : unauthorized();
}

/** errcode 42501 from a device RPC after the signature was verified means the device is retired (C5). */
export function isDeviceRetiredError(error: DbError | null | undefined): boolean {
  return !!error && error.code === '42501';
}
