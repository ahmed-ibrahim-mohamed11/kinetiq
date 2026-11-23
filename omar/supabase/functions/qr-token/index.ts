/**
 * GET /functions/v1/qr-token   (user JWT required)
 *
 * Issues a short-lived QR check-in token for the signed-in member:
 *   SGM1.<member_code>.<unix_minute>.<base64url(hmac_sha256(secret, code|minute))[0:16]>
 *
 * POST /functions/v1/qr-token  { token }  (staff JWT required)
 *   Verifies a token presented at the front desk / kiosk and performs check-in
 *   via the `staff_check_in` RPC under the staff member's own session, so RLS
 *   and role checks still apply.
 *
 * Tokens are valid for the current minute ±1 (about 2 minutes total) which is
 * enough for a kiosk scan and short enough to make screenshots useless.
 */
import { createClient } from 'npm:@supabase/supabase-js@2.47.10';

const SUPABASE_URL = Deno.env.get('SUPABASE_URL')!;
const ANON_KEY = Deno.env.get('SUPABASE_ANON_KEY')!;
const SERVICE_ROLE = Deno.env.get('SUPABASE_SERVICE_ROLE_KEY')!;
const QR_SECRET = Deno.env.get('QR_TOKEN_SECRET')!; // set with: supabase secrets set QR_TOKEN_SECRET=...

const enc = new TextEncoder();

async function mac(message: string): Promise<string> {
  const key = await crypto.subtle.importKey('raw', enc.encode(QR_SECRET), { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']);
  const sig = new Uint8Array(await crypto.subtle.sign('HMAC', key, enc.encode(message)));
  return btoa(String.fromCharCode(...sig)).replaceAll('+', '-').replaceAll('/', '_').replaceAll('=', '').slice(0, 16);
}

function json(status: number, body: unknown) {
  return new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });
}

Deno.serve(async (req) => {
  if (!QR_SECRET) return json(500, { code: 'internal', message: 'QR_TOKEN_SECRET not configured' });
  const authHeader = req.headers.get('Authorization') ?? '';
  if (!authHeader.startsWith('Bearer ')) return json(401, { code: 'unauthorized' });

  // Client acting as the caller (RLS applies).
  const asUser = createClient(SUPABASE_URL, ANON_KEY, {
    global: { headers: { Authorization: authHeader } },
    auth: { persistSession: false },
  });
  const { data: userRes, error: userErr } = await asUser.auth.getUser();
  if (userErr || !userRes.user) return json(401, { code: 'unauthorized' });

  if (req.method === 'GET') {
    const { data: member } = await asUser.from('members').select('member_code').eq('user_id', userRes.user.id).maybeSingle();
    if (!member) return json(403, { code: 'forbidden', message: 'no member profile linked to this account' });
    const minute = Math.floor(Date.now() / 60_000);
    const code = String(member.member_code).toUpperCase();
    const token = `SGM1.${code}.${minute}.${await mac(`${code}|${minute}`)}`;
    return json(200, { token, expires_in_seconds: 60 - Math.floor((Date.now() / 1000) % 60) + 60 });
  }

  if (req.method === 'POST') {
    let body: { token?: string };
    try {
      body = await req.json();
    } catch {
      return json(400, { code: 'validation_error' });
    }
    const m = /^SGM1\.([A-HJ-NP-Z2-9-]{4,32})\.(\d{7,10})\.([A-Za-z0-9_-]{16})$/.exec(body.token ?? '');
    if (!m) return json(400, { code: 'validation_error', message: 'malformed token' });
    const [, code, minuteStr, presented] = m;
    const minute = Number(minuteStr);
    const nowMinute = Math.floor(Date.now() / 60_000);
    if (Math.abs(nowMinute - minute) > 1) return json(401, { code: 'unauthorized', message: 'token expired' });
    const expected = await mac(`${code}|${minute}`);
    if (expected !== presented) return json(401, { code: 'unauthorized', message: 'bad token' });

    // Resolve member via service role (staff may not have cross-org read, but RPC enforces branch role).
    const admin = createClient(SUPABASE_URL, SERVICE_ROLE, { auth: { persistSession: false } });
    const { data: member } = await admin.from('members').select('id').ilike('member_code', code).maybeSingle();
    if (!member) return json(404, { code: 'not_found' });

    // Check-in under the STAFF user's session → staff_check_in verifies branch role.
    const { data: sessionId, error } = await asUser.rpc('staff_check_in', { p_member_id: member.id, p_method: 'qr' });
    if (error) return json(error.code === '42501' ? 403 : 400, { code: error.code === '42501' ? 'forbidden' : 'conflict', message: error.message });
    return json(200, { attendance_session_id: sessionId });
  }

  return json(405, { code: 'method_not_allowed' });
});
