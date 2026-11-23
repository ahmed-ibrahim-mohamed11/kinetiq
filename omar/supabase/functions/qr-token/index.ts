/**
 * /functions/v1/qr-token   (verify_jwt = true)
 *
 * GET  (member JWT): issue a short-lived QR check-in token for the signed-in member.
 * POST (staff JWT) { token }: verify a token presented at the front desk / kiosk and check the
 *      member in through public.qr_check_in with the service role (contract C1).
 * Token format, verification steps and error mapping: see handler.ts.
 *
 * Function secrets: QR_TOKEN_SECRET (required). Browser origins allowed by CORS come from
 * MEMBER_APP_URL, ADMIN_APP_URL and QR_ALLOWED_ORIGINS (comma-separated); with none set, '*'.
 * Once any of them is set the list is exact, so every other origin the member app is served
 * from (Vercel alias domains, preview or staging URLs that use this project) must be listed in
 * QR_ALLOWED_ORIGINS, or the browser QR fetch fails there with a CORS error.
 * SUPABASE_URL, SUPABASE_ANON_KEY and SUPABASE_SERVICE_ROLE_KEY are provided by the platform.
 */
import { createClient } from 'npm:@supabase/supabase-js@2.47.10';
import { handle, parseAllowedOrigins, type QrDeps } from './handler.ts';

const env = (name: string) => (Deno.env.get(name) ?? '').trim();
const SUPABASE_URL = env('SUPABASE_URL');
const ANON_KEY = env('SUPABASE_ANON_KEY');

const admin = createClient(SUPABASE_URL, env('SUPABASE_SERVICE_ROLE_KEY'), { auth: { persistSession: false } });

const deps: QrDeps = {
  secret: env('QR_TOKEN_SECRET'),
  allowedOrigins: parseAllowedOrigins([env('MEMBER_APP_URL'), env('ADMIN_APP_URL'), env('QR_ALLOWED_ORIGINS')]),
  asUser(authHeader) {
    // Client acting as the caller (RLS applies).
    const client = createClient(SUPABASE_URL, ANON_KEY, {
      global: { headers: { Authorization: authHeader } },
      auth: { persistSession: false },
    });
    return {
      getUserId: async () => {
        const { data, error } = await client.auth.getUser();
        return error || !data.user ? null : data.user.id;
      },
      getOwnMember: async (userId) => await client.from('members').select('id, member_code').eq('user_id', userId).maybeSingle(),
    };
  },
  // ilike is an exact, case-insensitive match here: TOKEN_RE admits no '%' or '_'.
  findMembersByCode: async (code) =>
    await admin.from('members').select('id, organization_id, branch_id').ilike('member_code', code).limit(50),
  getStaffRoles: async (userId) => await admin.from('staff_roles').select('organization_id, branch_id, role').eq('user_id', userId),
  // Same condition as tokenUsedBy: checked in OR checked out at/after the token's minute.
  findEndedSessionsSince: async (memberId, sinceIso) =>
    await admin
      .from('attendance_sessions')
      .select('id, status, check_in_at, check_out_at')
      .eq('member_id', memberId)
      .neq('status', 'active')
      .or(`check_in_at.gte."${sinceIso}",check_out_at.gte."${sinceIso}"`)
      .limit(5),
  qrCheckIn: async (code, staffUserId) => await admin.rpc('qr_check_in', { p_member_code: code, p_staff_user_id: staffUserId }),
};

Deno.serve((req) => handle(req, deps));
