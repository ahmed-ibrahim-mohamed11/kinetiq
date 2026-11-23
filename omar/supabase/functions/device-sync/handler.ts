/**
 * device-sync request handling, free of Deno.serve and supabase-js so it can be unit-tested.
 *
 * Headers:
 *   X-Device-Id:        <uuid>
 *   X-Device-Timestamp: <ISO-8601>   (must be within INGEST_CLOCK_SKEW_SECONDS, 1..3600, default 300)
 *   X-Device-Signature: hex(HMAC_SHA256(secret, `${device_id}\n${timestamp}\nGET /device-sync`))
 *
 * 401 for bad credentials, 403 {code:'device_retired'} for a retired device (C5), 500 when the
 * database fails (the edge keeps its cached snapshot and retries).
 */
import {
  type DbResult,
  defaultLog,
  type DeviceAuthDeps,
  deviceRetired,
  internalError,
  isDeviceRetiredError,
  json,
  type Logger,
  SIGNATURE_RE,
  unauthorized,
  UUID_RE,
  verifyDeviceSignature,
  withinSkew,
} from '../_shared/device_auth.ts';

export interface DeviceSyncDeps extends DeviceAuthDeps {
  clockSkewSeconds: number;
  now?: () => number;
  /** public.device_sync_snapshot(p_device_id) (C6). */
  snapshot(deviceId: string): Promise<DbResult<unknown>>;
  log?: Logger;
}

export function canonicalSyncString(deviceId: string, timestamp: string): string {
  return `${deviceId}\n${timestamp}\nGET /device-sync`;
}

export async function handle(req: Request, deps: DeviceSyncDeps): Promise<Response> {
  const log = deps.log ?? defaultLog;
  const now = deps.now ?? Date.now;
  if (req.method !== 'GET') return json(405, { code: 'method_not_allowed' });

  const deviceId = req.headers.get('x-device-id') ?? '';
  const timestamp = req.headers.get('x-device-timestamp') ?? '';
  const signature = req.headers.get('x-device-signature') ?? '';
  if (!UUID_RE.test(deviceId) || !SIGNATURE_RE.test(signature) || timestamp.length > 64) return unauthorized();
  if (!withinSkew(timestamp, now(), deps.clockSkewSeconds)) {
    return json(401, { code: 'unauthorized', message: 'timestamp outside accepted window', server_time: new Date(now()).toISOString() });
  }

  const authFailure = await verifyDeviceSignature(deps, deviceId, canonicalSyncString(deviceId, timestamp), signature);
  if (authFailure) return authFailure;

  const { data, error } = await deps.snapshot(deviceId);
  if (error) {
    if (isDeviceRetiredError(error)) return deviceRetired();
    log('device_sync_snapshot failed', { deviceId, code: error.code ?? null, message: error.message });
    return internalError();
  }
  return json(200, data);
}
