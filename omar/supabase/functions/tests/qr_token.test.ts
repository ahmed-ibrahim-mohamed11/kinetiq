/**
 * qr-token handler with fake dependencies: CORS (preflight + headers), token format / expiry /
 * HMAC, cross-tenant member_code collision, single use after check-out, qr_check_in error mapping.
 *
 *   cd supabase/functions
 *   deno test --config deno.json --allow-env --allow-read tests/qr_token.test.ts
 */
import assert from 'node:assert/strict';
import {
  corsHeaders,
  handle,
  issueToken,
  type MemberRef,
  parseAllowedOrigins,
  type QrDeps,
  type SessionRef,
  type StaffRole,
  TOKEN_RE,
  tokenUsedBy,
} from '../qr-token/handler.ts';

const SECRET = 'test-qr-secret-0123456789abcdef';
const NOW = Date.parse('2026-11-02T10:00:30.000Z');
const MEMBER_APP = 'https://member.example.app';

const ORG_A = 'aaaaaaaa-0000-4000-8000-000000000001';
const ORG_B = 'bbbbbbbb-0000-4000-8000-000000000001';
const BRANCH_A = 'aaaaaaaa-0000-4000-8000-0000000000b1';
const BRANCH_B = 'bbbbbbbb-0000-4000-8000-0000000000b1';
const MEMBER_A: MemberRef = { id: 'aaaaaaaa-0000-4000-8000-0000000000a1', organization_id: ORG_A, branch_id: BRANCH_A };
const MEMBER_B: MemberRef = { id: 'bbbbbbbb-0000-4000-8000-0000000000a1', organization_id: ORG_B, branch_id: BRANCH_B };
const CODE = 'GYM-2A7K';
const SESSION = 'cccccccc-0000-4000-8000-000000000001';

const USERS: Record<string, string> = {
  'Bearer member-a': 'user-member-a',
  'Bearer staff-a': 'user-staff-a',
  'Bearer staff-b': 'user-staff-b',
  'Bearer staff-ab': 'user-staff-ab',
};
const ROLES: Record<string, StaffRole[]> = {
  'user-staff-a': [{ organization_id: ORG_A, branch_id: BRANCH_A, role: 'front_desk' }],
  'user-staff-b': [{ organization_id: ORG_B, branch_id: null, role: 'manager' }],
  'user-staff-ab': [
    { organization_id: ORG_A, branch_id: null, role: 'front_desk' },
    { organization_id: ORG_B, branch_id: null, role: 'front_desk' },
  ],
};

interface Fake extends QrDeps {
  checkIns: Array<[string, string]>;
  logs: string[];
}

function fakeDeps(over: Partial<QrDeps> = {}): Fake {
  const checkIns: Array<[string, string]> = [];
  const logs: string[] = [];
  return {
    checkIns,
    logs,
    secret: SECRET,
    allowedOrigins: [MEMBER_APP],
    now: () => NOW,
    log: (msg) => logs.push(msg),
    asUser: (authHeader) => ({
      getUserId: () => Promise.resolve(USERS[authHeader] ?? null),
      getOwnMember: (userId) =>
        Promise.resolve({ data: userId === 'user-member-a' ? { id: MEMBER_A.id, member_code: 'gym-2a7k' } : null, error: null }),
    }),
    findMembersByCode: (code) => Promise.resolve({ data: code.toUpperCase() === CODE ? [MEMBER_A, MEMBER_B] : [], error: null }),
    getStaffRoles: (userId) => Promise.resolve({ data: ROLES[userId] ?? [], error: null }),
    findEndedSessionsSince: () => Promise.resolve({ data: [], error: null }),
    qrCheckIn: (code, staff) => {
      checkIns.push([code, staff]);
      return Promise.resolve({ data: SESSION, error: null });
    },
    ...over,
  };
}

const get = (auth?: string, origin?: string) =>
  new Request('http://localhost/qr-token', { headers: { ...(auth ? { authorization: auth } : {}), ...(origin ? { origin } : {}) } });
const post = (auth: string, token: unknown) =>
  new Request('http://localhost/qr-token', {
    method: 'POST',
    headers: { authorization: auth, 'content-type': 'application/json' },
    body: JSON.stringify({ token }),
  });

async function memberAToken(nowMs = NOW): Promise<string> {
  return await issueToken(SECRET, { id: MEMBER_A.id, member_code: 'gym-2a7k' }, nowMs);
}

// ── CORS ────────────────────────────────────────────────────────────────────

Deno.test('OPTIONS preflight is answered before authentication with CORS headers for an allowed origin', async () => {
  const res = await handle(
    new Request('http://localhost/qr-token', {
      method: 'OPTIONS',
      headers: { origin: MEMBER_APP, 'access-control-request-method': 'GET', 'access-control-request-headers': 'authorization, apikey' },
    }),
    fakeDeps(),
  );
  assert.ok(res.status === 204 || res.status === 200);
  assert.equal(res.headers.get('access-control-allow-origin'), MEMBER_APP);
  assert.match(res.headers.get('access-control-allow-headers') ?? '', /authorization/);
  assert.match(res.headers.get('access-control-allow-headers') ?? '', /apikey/);
  assert.match(res.headers.get('access-control-allow-methods') ?? '', /GET/);
});

Deno.test('every response carries CORS headers (including 401) and a foreign origin is not echoed', async () => {
  const unauth = await handle(get(undefined, MEMBER_APP), fakeDeps());
  assert.equal(unauth.status, 401);
  assert.equal(unauth.headers.get('access-control-allow-origin'), MEMBER_APP);

  const ok = await handle(get('Bearer member-a', MEMBER_APP), fakeDeps());
  assert.equal(ok.status, 200);
  assert.equal(ok.headers.get('access-control-allow-origin'), MEMBER_APP);

  const foreign = await handle(get('Bearer member-a', 'https://evil.example'), fakeDeps());
  assert.equal(foreign.headers.get('access-control-allow-origin'), null);
  assert.equal(foreign.headers.get('vary'), 'Origin');
});

Deno.test('allowed origins come from app URLs; none configured → wildcard', () => {
  assert.deepEqual(
    parseAllowedOrigins(['https://member.example.app/', undefined, 'https://admin.example.app/x, http://localhost:3001 , not a url']),
    [
      'https://member.example.app',
      'https://admin.example.app',
      'http://localhost:3001',
    ],
  );
  assert.equal(parseAllowedOrigins(['', undefined]), '*');
  assert.equal(corsHeaders('https://x.example', '*')['Access-Control-Allow-Origin'], '*');
});

// ── Issuance ────────────────────────────────────────────────────────────────

Deno.test('GET issues SGM1.<CODE>.<minute>.<mac16> for the signed-in member', async () => {
  const res = await handle(get('Bearer member-a'), fakeDeps());
  assert.equal(res.status, 200);
  const body = await res.json();
  const m = TOKEN_RE.exec(body.token);
  assert.ok(m, body.token);
  assert.equal(m![1], CODE);
  assert.equal(Number(m![2]), Math.floor(NOW / 60_000));
  assert.equal(body.token, await memberAToken());
  assert.ok(body.expires_in_seconds > 60 && body.expires_in_seconds <= 120);
  // The visible shape still matches the apps' / packages/validation regex.
  assert.match(body.token, /^SGM1\.[A-HJ-NP-Z2-9-]{4,32}\.\d{7,10}\.[A-Za-z0-9_-]{12,}$/);
});

Deno.test('GET without a member profile → 403; invalid JWT → 401; missing secret → 500', async () => {
  assert.equal((await handle(get('Bearer staff-a'), fakeDeps())).status, 403);
  assert.equal((await handle(get('Bearer nobody'), fakeDeps())).status, 401);
  assert.equal((await handle(get('Basic x'), fakeDeps())).status, 401);
  assert.equal((await handle(get('Bearer member-a'), fakeDeps({ secret: '' }))).status, 500);
});

// ── Verification ────────────────────────────────────────────────────────────

Deno.test('POST by authorized staff checks the member in via qr_check_in(code, staff user id)', async () => {
  const deps = fakeDeps();
  const res = await handle(post('Bearer staff-a', await memberAToken()), deps);
  assert.equal(res.status, 200);
  assert.deepEqual(await res.json(), { attendance_session_id: SESSION });
  assert.deepEqual(deps.checkIns, [[CODE, 'user-staff-a']]);
});

Deno.test('tokens from the previous minute are valid, older or future ones expire', async () => {
  assert.equal((await handle(post('Bearer staff-a', await memberAToken(NOW - 60_000)), fakeDeps())).status, 200);
  const old = await handle(post('Bearer staff-a', await memberAToken(NOW - 120_000)), fakeDeps());
  assert.equal(old.status, 401);
  assert.equal((await old.json()).message, 'token expired');
  assert.equal((await handle(post('Bearer staff-a', await memberAToken(NOW + 120_000)), fakeDeps())).status, 401);
});

Deno.test('a tampered MAC, code or minute is rejected and never checks anyone in', async () => {
  const token = await memberAToken();
  const [prefix, code, minute, mac] = token.split('.');
  const flipped = mac.slice(0, -1) + (mac.endsWith('A') ? 'B' : 'A');
  const deps = fakeDeps();
  for (
    const bad of [
      `${prefix}.${code}.${minute}.${flipped}`,
      `${prefix}.GYM-2A7L.${minute}.${mac}`,
      `${prefix}.${code}.${Number(minute) - 1}.${mac}`,
    ]
  ) {
    const res = await handle(post('Bearer staff-a', bad), deps);
    assert.equal(res.status, 401, bad);
  }
  assert.equal(deps.checkIns.length, 0);
});

Deno.test('malformed tokens and bodies are 400', async () => {
  for (const bad of ['', 'SGM1.GYM-2A7K.123.abc', 'SGM2.GYM-2A7K.29876543.abcdefghijklmnop', 42, null]) {
    assert.equal((await handle(post('Bearer staff-a', bad), fakeDeps())).status, 400, String(bad));
  }
  const notJson = new Request('http://localhost/qr-token', { method: 'POST', headers: { authorization: 'Bearer staff-a' }, body: '{' });
  assert.equal((await handle(notJson, fakeDeps())).status, 400);
});

Deno.test('cross-tenant: a token minted by org A member is refused by org B staff even though org B has the same member_code', async () => {
  const deps = fakeDeps();
  const res = await handle(post('Bearer staff-b', await memberAToken()), deps);
  assert.equal(res.status, 403);
  assert.equal((await res.json()).code, 'forbidden');
  assert.equal(deps.checkIns.length, 0, 'org B member must not be checked in');
});

Deno.test('a token minted with only the code (the old code-only MAC) no longer verifies', async () => {
  const minute = Math.floor(NOW / 60_000);
  const key = await crypto.subtle.importKey('raw', new TextEncoder().encode(SECRET), { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']);
  const sig = new Uint8Array(await crypto.subtle.sign('HMAC', key, new TextEncoder().encode(`${CODE}|${minute}`)));
  const mac = btoa(String.fromCharCode(...sig)).replaceAll('+', '-').replaceAll('/', '_').replaceAll('=', '').slice(0, 16);
  const deps = fakeDeps();
  assert.equal((await handle(post('Bearer staff-b', `SGM1.${CODE}.${minute}.${mac}`), deps)).status, 401);
  assert.equal(deps.checkIns.length, 0);
});

Deno.test('staff authorized in both organizations sharing the code get 409 ambiguous instead of a guess', async () => {
  const deps = fakeDeps();
  const res = await handle(post('Bearer staff-ab', await memberAToken()), deps);
  assert.equal(res.status, 409);
  assert.equal((await res.json()).code, 'ambiguous_member_code');
  assert.equal(deps.checkIns.length, 0);
});

Deno.test('a member posting their own token is refused (403)', async () => {
  const deps = fakeDeps();
  assert.equal((await handle(post('Bearer member-a', await memberAToken()), deps)).status, 403);
  assert.equal(deps.checkIns.length, 0);
});

const MINUTE_START = Math.floor(NOW / 60_000) * 60_000;
const iso = (ms: number) => new Date(ms).toISOString();

/** findEndedSessionsSince over an in-memory session table, returning every row (a superset). */
function sessionsDeps(rows: SessionRef[], since: string[] = []): Partial<QrDeps> {
  return {
    findEndedSessionsSince: (memberId, sinceIso) => {
      since.push(sinceIso);
      return Promise.resolve({ data: memberId === MEMBER_A.id ? rows : [], error: null });
    },
  };
}

Deno.test('single use: once a session started after the token was issued has ended, a replay is 409 token_used', async () => {
  const token = await memberAToken();
  const since: string[] = [];
  const deps = fakeDeps(sessionsDeps([
    { id: SESSION, status: 'completed', check_in_at: iso(MINUTE_START + 5_000), check_out_at: iso(MINUTE_START + 20_000) },
  ], since));
  const res = await handle(post('Bearer staff-a', token), deps);
  assert.equal(res.status, 409);
  assert.equal((await res.json()).code, 'token_used');
  assert.equal(deps.checkIns.length, 0);
  assert.deepEqual(since, [iso(MINUTE_START)]);
});

Deno.test('single use: a session that began before the token minute and ended after it also uses the token', async () => {
  // 09:00 face check-in → 10:00 QR scan returns that session → 10:00:20 check-out → replay.
  const deps = fakeDeps(sessionsDeps([
    { id: SESSION, status: 'completed', check_in_at: iso(MINUTE_START - 3_600_000), check_out_at: iso(MINUTE_START + 20_000) },
  ]));
  const res = await handle(post('Bearer staff-a', await memberAToken()), deps);
  assert.equal(res.status, 409);
  assert.equal((await res.json()).code, 'token_used');
  assert.equal(deps.checkIns.length, 0);
});

Deno.test('single use: sessions that ended before the token minute, or are still active, do not block check-in', async () => {
  const deps = fakeDeps(sessionsDeps([
    { id: 'old', status: 'completed', check_in_at: iso(MINUTE_START - 7_200_000), check_out_at: iso(MINUTE_START - 1) },
    { id: 'gone', status: 'abandoned', check_in_at: iso(MINUTE_START - 90_000_000), check_out_at: null },
    { id: SESSION, status: 'active', check_in_at: iso(MINUTE_START - 600_000), check_out_at: null },
  ]));
  const res = await handle(post('Bearer staff-a', await memberAToken()), deps);
  assert.equal(res.status, 200);
  assert.equal(deps.checkIns.length, 1);
});

Deno.test('tokenUsedBy: boundaries are inclusive at the token minute', () => {
  const s = (check_in_at: number, check_out_at: number | null, status = 'completed'): SessionRef => ({
    id: 'x',
    status,
    check_in_at: iso(check_in_at),
    check_out_at: check_out_at === null ? null : iso(check_out_at),
  });
  assert.equal(tokenUsedBy(s(MINUTE_START, MINUTE_START), MINUTE_START), true);
  assert.equal(tokenUsedBy(s(MINUTE_START - 10, MINUTE_START), MINUTE_START), true);
  assert.equal(tokenUsedBy(s(MINUTE_START - 10, MINUTE_START - 1), MINUTE_START), false);
  assert.equal(tokenUsedBy(s(MINUTE_START + 10, null, 'active'), MINUTE_START), false);
  assert.equal(tokenUsedBy(s(MINUTE_START + 10, null, 'finalizing'), MINUTE_START), true);
});

Deno.test('qr_check_in SQLSTATEs map to fixed messages; anything else is 500 without the SQL text', async () => {
  const cases: Array<[string, number, string]> = [
    ['P0002', 404, 'not_found'],
    ['42501', 403, 'forbidden'],
    ['23514', 409, 'member_inactive'],
    ['22023', 400, 'invalid_method'],
    ['23505', 409, 'conflict'],
    ['21000', 409, 'ambiguous_member_code'],
    ['XX000', 500, 'internal'],
    ['08006', 500, 'internal'],
  ];
  for (const [sqlstate, status, code] of cases) {
    const deps = fakeDeps({
      qrCheckIn: () =>
        Promise.resolve({ data: null, error: { code: sqlstate, message: 'relation "public.attendance_sessions" secret detail' } }),
    });
    const res = await handle(post('Bearer staff-a', await memberAToken()), deps);
    assert.equal(res.status, status, sqlstate);
    const text = await res.text();
    assert.equal(JSON.parse(text).code, code, sqlstate);
    assert.doesNotMatch(text, /relation|secret detail/, sqlstate);
  }
});

Deno.test('database failures while verifying are 500 internal (logged), never a business error', async () => {
  const deps = fakeDeps({ getStaffRoles: () => Promise.resolve({ data: null, error: { code: '57014', message: 'statement timeout' } }) });
  const res = await handle(post('Bearer staff-a', await memberAToken()), deps);
  assert.equal(res.status, 500);
  assert.equal((await res.json()).code, 'internal');
  assert.ok(deps.logs.some((l) => l.includes('staff role lookup')));
});

Deno.test('unsupported methods are 405 with CORS headers', async () => {
  const res = await handle(new Request('http://localhost/qr-token', { method: 'DELETE', headers: { origin: MEMBER_APP } }), fakeDeps());
  assert.equal(res.status, 405);
  assert.equal(res.headers.get('access-control-allow-origin'), MEMBER_APP);
});
