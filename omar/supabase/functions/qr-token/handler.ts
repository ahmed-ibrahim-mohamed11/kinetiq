/**
 * qr-token request handling, free of Deno.serve and supabase-js so it can be unit-tested.
 *
 * GET  (member JWT)  → { token, expires_in_seconds }
 *   token = SGM1.<MEMBER_CODE>.<unix_minute>.<mac16>
 *   mac16 = base64url(HMAC_SHA256(QR_TOKEN_SECRET, `SGM1|${member_id}|${MEMBER_CODE}|${minute}`))[0:16]
 *   The MAC binds the member's id, not only the code: member_code is unique per organization
 *   only, so a code-only MAC would let a member of gym A mint tokens for gym B's member with the
 *   same code. The visible format is unchanged (apps and packages/validation parse it).
 *
 * POST (staff JWT) { token } → { attendance_session_id }
 *   1. token shape, minute within ±1 of now (about 2-3 minutes in total)
 *   2. the member is the one whose id makes the MAC verify (constant-time compare)
 *   3. the caller holds a check-in role for that member's branch, and for no other member with
 *      the same code (otherwise qr_check_in could resolve the code to the wrong person)
 *   4. single use: a token cannot create a session once any session of the member that was open
 *      at or after the token's minute has ended (replay of a photographed code after check-out),
 *      including a session that began before the token's minute (see tokenUsedBy)
 *   5. public.qr_check_in(code, staff_user_id) with the service role (C1); SQLSTATEs map to fixed
 *      messages and anything unexpected is a 500 (details logged server-side only).
 *
 * CORS: the member app calls GET from the browser, so every response carries CORS headers and
 * OPTIONS preflights are answered before authentication.
 */
import { type DbResult, defaultLog, json, type Logger, timingSafeEqual } from '../_shared/device_auth.ts';

export const TOKEN_RE = /^SGM1\.([A-HJ-NP-Z2-9-]{4,32})\.(\d{7,10})\.([A-Za-z0-9_-]{16})$/;
/** Roles public.staff_check_in accepts for a branch (20261027000003_workout_engine.sql). */
export const CHECK_IN_ROLES = ['owner', 'admin', 'manager', 'front_desk', 'trainer'];

export interface MemberRef {
  id: string;
  organization_id: string;
  branch_id: string;
}
export interface StaffRole {
  organization_id: string;
  branch_id: string | null;
  role: string;
}

export interface SessionRef {
  id: string;
  status: string;
  check_in_at: string;
  check_out_at: string | null;
}

export interface UserScope {
  /** auth.getUser() for the caller's JWT; null when the JWT is not a valid user session. */
  getUserId(): Promise<string | null>;
  /** The caller's own member row, read under RLS. */
  getOwnMember(userId: string): Promise<DbResult<{ id: string; member_code: string }>>;
}

export interface QrDeps {
  secret: string;
  /** Exact origins allowed to call from a browser, or '*' when none are configured. */
  allowedOrigins: string[] | '*';
  now?: () => number;
  asUser(authHeader: string): UserScope;
  /** Service role: every member with this code, in any organization (citext, case-insensitive). */
  findMembersByCode(code: string): Promise<DbResult<MemberRef[]>>;
  /** Service role: the staff roles of a user. */
  getStaffRoles(userId: string): Promise<DbResult<StaffRole[]>>;
  /**
   * Service role: the member's non-active sessions that checked in OR checked out at or after
   * `sinceIso` (a superset is fine: the handler re-checks every row with tokenUsedBy).
   */
  findEndedSessionsSince(memberId: string, sinceIso: string): Promise<DbResult<SessionRef[]>>;
  /** Service role: public.qr_check_in(p_member_code, p_staff_user_id) (C1). */
  qrCheckIn(code: string, staffUserId: string): Promise<DbResult<string>>;
  log?: Logger;
}

const enc = new TextEncoder();

export async function qrMac(secret: string, memberId: string, code: string, minute: number): Promise<string> {
  const key = await crypto.subtle.importKey('raw', enc.encode(secret), { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']);
  const sig = new Uint8Array(await crypto.subtle.sign('HMAC', key, enc.encode(`SGM1|${memberId}|${code}|${minute}`)));
  return btoa(String.fromCharCode(...sig)).replaceAll('+', '-').replaceAll('/', '_').replaceAll('=', '').slice(0, 16);
}

export async function issueToken(secret: string, member: { id: string; member_code: string }, nowMs: number): Promise<string> {
  const minute = Math.floor(nowMs / 60_000);
  const code = String(member.member_code).toUpperCase();
  return `SGM1.${code}.${minute}.${await qrMac(secret, member.id, code, minute)}`;
}

/** Comma-separated origins or URLs (MEMBER_APP_URL, ADMIN_APP_URL, QR_ALLOWED_ORIGINS) → exact origins. */
export function parseAllowedOrigins(values: Array<string | undefined>): string[] | '*' {
  const out = new Set<string>();
  for (const value of values) {
    for (const part of (value ?? '').split(',')) {
      const v = part.trim();
      if (!v) continue;
      try {
        out.add(new URL(v).origin);
      } catch {
        // ignore malformed entries
      }
    }
  }
  // Bearer-token auth without cookies: a wildcard does not let another site act as the user,
  // so it is the fallback when no app URL is configured.
  return out.size ? [...out] : '*';
}

export function corsHeaders(origin: string | null, allowed: string[] | '*'): Record<string, string> {
  const h: Record<string, string> = {
    'Access-Control-Allow-Headers': 'authorization, apikey, content-type, x-client-info',
    'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
    'Access-Control-Max-Age': '600',
  };
  if (allowed === '*') {
    h['Access-Control-Allow-Origin'] = '*';
  } else {
    h['Vary'] = 'Origin';
    if (origin && allowed.includes(origin)) h['Access-Control-Allow-Origin'] = origin;
  }
  return h;
}

const canCheckIn = (roles: StaffRole[], m: MemberRef) =>
  roles.some((r) =>
    r.organization_id === m.organization_id && (r.branch_id === null || r.branch_id === m.branch_id) && CHECK_IN_ROLES.includes(r.role)
  );

/**
 * True when an ended session shows the token minute starting at `sinceMs` was already used: the
 * session was open at some point at or after that minute (it checked in then, or it was still
 * running then and checked out later). A session that began before the token's minute and ended
 * after it counts too, because a scan during that window returned it (qr_check_in is idempotent
 * for an active session). Side effect: right after a check-out the member needs the next minute's
 * code to check in again, which the member app shows within 60 s.
 */
export function tokenUsedBy(s: SessionRef, sinceMs: number): boolean {
  if (s.status === 'active') return false;
  const at = (v: string | null) => (v ? Date.parse(v) : Number.NaN);
  return at(s.check_in_at) >= sinceMs || at(s.check_out_at) >= sinceMs;
}

const AMBIGUOUS_MESSAGE = 'This member code is used by more than one of your gyms; check the member in manually.';

/** Fixed, user-facing outcome for each SQLSTATE qr_check_in can raise; null = unexpected (500). */
export function mapCheckInError(code: string | undefined): { status: number; code: string; message: string } | null {
  switch (code) {
    case 'P0002':
      return { status: 404, code: 'not_found', message: 'No member with this code at your branches.' };
    case '42501':
      return { status: 403, code: 'forbidden', message: 'You are not allowed to check members in at this branch.' };
    case '23514':
      return { status: 409, code: 'member_inactive', message: 'This membership is not active.' };
    case '22023':
      return { status: 400, code: 'invalid_method', message: 'This check-in method is not allowed.' };
    case '23505':
      return { status: 409, code: 'conflict', message: 'The member already has an active session.' };
    case '21000':
      // qr_check_in: the code matches members in more than one of the caller's organizations.
      return { status: 409, code: 'ambiguous_member_code', message: AMBIGUOUS_MESSAGE };
    default:
      return null;
  }
}

export async function handle(req: Request, deps: QrDeps): Promise<Response> {
  const log = deps.log ?? defaultLog;
  const now = deps.now ?? Date.now;
  const cors = corsHeaders(req.headers.get('origin'), deps.allowedOrigins);
  const respond = (status: number, body: unknown) => json(status, body, cors);

  if (req.method === 'OPTIONS') return new Response(null, { status: 204, headers: cors });
  if (req.method !== 'GET' && req.method !== 'POST') return respond(405, { code: 'method_not_allowed' });
  if (!deps.secret) {
    log('QR_TOKEN_SECRET is not configured');
    return respond(500, { code: 'internal', message: 'QR check-in is not configured' });
  }

  const authHeader = req.headers.get('authorization') ?? '';
  if (!authHeader.startsWith('Bearer ')) return respond(401, { code: 'unauthorized' });
  const user = deps.asUser(authHeader);
  const userId = await user.getUserId();
  if (!userId) return respond(401, { code: 'unauthorized' });

  if (req.method === 'GET') {
    const { data: member, error } = await user.getOwnMember(userId);
    if (error) {
      log('member lookup failed', { code: error.code ?? null, message: error.message });
      return respond(500, { code: 'internal', message: 'Could not issue a check-in code. Please try again.' });
    }
    if (!member) return respond(403, { code: 'forbidden', message: 'no member profile linked to this account' });
    const nowMs = now();
    const token = await issueToken(deps.secret, member, nowMs);
    return respond(200, { token, expires_in_seconds: 60 - Math.floor((nowMs / 1000) % 60) + 60 });
  }

  // POST: staff verifies a presented token and checks the member in.
  let body: { token?: unknown };
  try {
    body = await req.json();
  } catch {
    return respond(400, { code: 'validation_error', message: 'body is not JSON' });
  }
  const m = TOKEN_RE.exec(typeof body?.token === 'string' ? body.token : '');
  if (!m) return respond(400, { code: 'validation_error', message: 'malformed token' });
  const [, code, minuteStr, presented] = m;
  const minute = Number(minuteStr);
  if (Math.abs(Math.floor(now() / 60_000) - minute) > 1) return respond(401, { code: 'unauthorized', message: 'token expired' });

  const internal = (what: string, error: { code?: string; message: string }) => {
    log(`${what} failed`, { code: error.code ?? null, message: error.message });
    return respond(500, { code: 'internal', message: 'Check-in failed. Please try again.' });
  };

  const candidates = await deps.findMembersByCode(code);
  if (candidates.error) return internal('member lookup', candidates.error);
  let member: MemberRef | null = null;
  for (const c of candidates.data ?? []) {
    // Compare against every candidate (no early exit) in constant time.
    if (timingSafeEqual(await qrMac(deps.secret, c.id, code, minute), presented)) member = c;
  }
  if (!member) return respond(401, { code: 'unauthorized', message: 'bad token' });

  const roles = await deps.getStaffRoles(userId);
  if (roles.error) return internal('staff role lookup', roles.error);
  const staffRoles = roles.data ?? [];
  if (!canCheckIn(staffRoles, member)) {
    return respond(403, { code: 'forbidden', message: 'You are not allowed to check members in at this branch.' });
  }
  const verified = member;
  if ((candidates.data ?? []).some((c) => c.id !== verified.id && canCheckIn(staffRoles, c))) {
    return respond(409, { code: 'ambiguous_member_code', message: AMBIGUOUS_MESSAGE });
  }

  const sinceMs = minute * 60_000;
  const used = await deps.findEndedSessionsSince(member.id, new Date(sinceMs).toISOString());
  if (used.error) return internal('session lookup', used.error);
  if ((used.data ?? []).some((s) => tokenUsedBy(s, sinceMs))) {
    return respond(409, { code: 'token_used', message: 'This check-in code was already used. Ask the member to show a fresh code.' });
  }

  const { data: sessionId, error } = await deps.qrCheckIn(code, userId);
  if (error) {
    const mapped = mapCheckInError(error.code);
    if (!mapped) return internal('qr_check_in', error);
    return respond(mapped.status, { code: mapped.code, message: mapped.message });
  }
  if (!sessionId) return internal('qr_check_in', { message: 'no session id returned' });
  return respond(200, { attendance_session_id: sessionId });
}
