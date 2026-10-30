/**
 * GET /functions/v1/device-sync
 *
 * Device-authenticated snapshot for the edge: cameras/zones/equipment/exercise
 * catalogue of its branch, granted consents (ids only), member list for
 * enrollment, and enrollment references to revoke locally.
 *
 * Headers:
 *   X-Device-Id:        <uuid>
 *   X-Device-Timestamp: <ISO-8601>   (must be within INGEST_CLOCK_SKEW_SECONDS)
 *   X-Device-Signature: hex(HMAC_SHA256(secret, `${device_id}\n${timestamp}\nGET /device-sync`))
 */
import { createClient } from 'npm:@supabase/supabase-js@2.47.10';

const SUPABASE_URL = Deno.env.get('SUPABASE_URL')!;
const SERVICE_ROLE = Deno.env.get('SUPABASE_SERVICE_ROLE_KEY')!;
const CLOCK_SKEW_SECONDS = Number(Deno.env.get('INGEST_CLOCK_SKEW_SECONDS') ?? '300');
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const enc = new TextEncoder();

async function hmacHex(secret: string, message: string): Promise<string> {
  const key = await crypto.subtle.importKey('raw', enc.encode(secret), { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']);
  const sig = await crypto.subtle.sign('HMAC', key, enc.encode(message));
  return [...new Uint8Array(sig)].map((b) => b.toString(16).padStart(2, '0')).join('');
}

function timingSafeEqualHex(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });
const unauthorized = () => json(401, { code: 'unauthorized', message: 'invalid device credentials' });

Deno.serve(async (req) => {
  if (req.method !== 'GET') return json(405, { code: 'method_not_allowed' });
  const deviceId = req.headers.get('x-device-id') ?? '';
  const timestamp = req.headers.get('x-device-timestamp') ?? '';
  const signature = req.headers.get('x-device-signature') ?? '';
  if (!UUID_RE.test(deviceId) || !/^[0-9a-f]{64}$/.test(signature)) return unauthorized();
  const skew = Math.abs(Date.now() - Date.parse(timestamp)) / 1000;
  if (!Number.isFinite(skew) || skew > CLOCK_SKEW_SECONDS) return json(401, { code: 'unauthorized', message: 'timestamp outside accepted window', server_time: new Date().toISOString() });

  const supabase = createClient(SUPABASE_URL, SERVICE_ROLE, { auth: { persistSession: false } });
  const { data: secrets, error: secretErr } = await supabase.rpc('get_device_secrets', { p_device_id: deviceId });
  if (secretErr || !secrets || secrets.length === 0) return unauthorized();

  const canonical = `${deviceId}\n${timestamp}\nGET /device-sync`;
  let ok = false;
  for (const row of secrets as Array<{ secret: string }>) {
    if (timingSafeEqualHex(await hmacHex(row.secret, canonical), signature)) {
      ok = true;
      break;
    }
  }
  if (!ok) return unauthorized();

  const { data, error } = await supabase.rpc('device_sync_snapshot', { p_device_id: deviceId });
  if (error) {
    console.error('device_sync_snapshot failed', { deviceId, code: error.code, message: error.message });
    return json(500, { code: 'internal', message: 'snapshot failed' });
  }
  return json(200, data);
});
