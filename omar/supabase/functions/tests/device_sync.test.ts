/**
 * device-sync handler + shared device-auth helpers: INGEST_CLOCK_SKEW_SECONDS parsing, timestamp
 * window, signature, C5 device_retired (403) on both paths, secret-lookup failures → 500.
 *
 *   cd supabase/functions
 *   deno test --config deno.json --allow-env --allow-read tests/device_sync.test.ts
 */
import assert from 'node:assert/strict';
import { DEFAULT_CLOCK_SKEW_SECONDS, hmacHex, parseClockSkew, timingSafeEqual, withinSkew } from '../_shared/device_auth.ts';
import { canonicalSyncString, type DeviceSyncDeps, handle } from '../device-sync/handler.ts';

const DEVICE = '11111111-1111-4111-8111-111111111111';
const SECRET = 'test-device-secret-0123456789';
const NOW = Date.parse('2026-11-02T10:00:00.000Z');
const SNAPSHOT = { generated_at: new Date(NOW).toISOString(), device_id: DEVICE, consents: [] };

Deno.test('parseClockSkew: finite values in 1..3600 are used, everything else falls back to 300 and is logged', () => {
  const logs: string[] = [];
  const log = (msg: string) => logs.push(msg);
  assert.equal(parseClockSkew(undefined, log), DEFAULT_CLOCK_SKEW_SECONDS);
  assert.equal(parseClockSkew('', log), DEFAULT_CLOCK_SKEW_SECONDS);
  assert.equal(logs.length, 0, 'unset/empty is the normal default, not an error');
  assert.equal(parseClockSkew('120', log), 120);
  assert.equal(parseClockSkew(' 600 ', log), 600);
  assert.equal(parseClockSkew('1', log), 1);
  assert.equal(parseClockSkew('3600', log), 3600);
  for (const bad of ['300s', 'abc', 'NaN', 'Infinity', '0', '-5', '3601', '1e9', '0x10']) {
    assert.equal(parseClockSkew(bad, log), DEFAULT_CLOCK_SKEW_SECONDS, bad);
  }
  assert.equal(logs.length, 9);
});

Deno.test('withinSkew rejects unparsable timestamps and honours the window both ways', () => {
  assert.equal(withinSkew('garbage', NOW, 300), false);
  assert.equal(withinSkew(new Date(NOW - 300_000).toISOString(), NOW, 300), true);
  assert.equal(withinSkew(new Date(NOW + 301_000).toISOString(), NOW, 300), false);
});

Deno.test('timingSafeEqual', () => {
  assert.equal(timingSafeEqual('abc', 'abc'), true);
  assert.equal(timingSafeEqual('abc', 'abd'), false);
  assert.equal(timingSafeEqual('abc', 'abcd'), false);
});

interface Fake extends DeviceSyncDeps {
  calls: { snapshot: number };
  logs: string[];
}

function fakeDeps(over: Partial<DeviceSyncDeps> = {}): Fake {
  const calls = { snapshot: 0 };
  const logs: string[] = [];
  return {
    calls,
    logs,
    clockSkewSeconds: 300,
    now: () => NOW,
    log: (msg) => logs.push(msg),
    getDeviceSecrets: () => Promise.resolve({ data: [{ secret: SECRET }], error: null }),
    getDeviceStatus: () => Promise.resolve({ data: 'active', error: null }),
    snapshot: () => {
      calls.snapshot++;
      return Promise.resolve({ data: SNAPSHOT, error: null });
    },
    ...over,
  };
}

async function signedRequest(ts = new Date(NOW).toISOString(), secret = SECRET): Promise<Request> {
  return new Request('http://localhost/device-sync', {
    headers: {
      'x-device-id': DEVICE,
      'x-device-timestamp': ts,
      'x-device-signature': await hmacHex(secret, canonicalSyncString(DEVICE, ts)),
    },
  });
}

Deno.test('returns the snapshot for a correctly signed request', async () => {
  const res = await handle(await signedRequest(), fakeDeps());
  assert.equal(res.status, 200);
  assert.deepEqual(await res.json(), SNAPSHOT);
});

Deno.test('wrong secret → 401; stale timestamp → 401 before any DB call', async () => {
  assert.equal((await handle(await signedRequest(undefined, 'other-secret'), fakeDeps())).status, 401);
  let lookups = 0;
  const deps = fakeDeps({
    getDeviceSecrets: () => {
      lookups++;
      return Promise.resolve({ data: [{ secret: SECRET }], error: null });
    },
  });
  const res = await handle(await signedRequest(new Date(NOW - 10 * 60_000).toISOString()), deps);
  assert.equal(res.status, 401);
  assert.equal(lookups, 0);
});

Deno.test('device_sync_snapshot 42501 (retired device) maps to 403 device_retired', async () => {
  const deps = fakeDeps({
    snapshot: () => Promise.resolve({ data: null, error: { code: '42501', message: `device_retired: ${DEVICE}` } }),
  });
  const res = await handle(await signedRequest(), deps);
  assert.equal(res.status, 403);
  assert.equal((await res.json()).code, 'device_retired');
});

Deno.test('a retired device without valid secrets gets 403 device_retired, an unknown one 401', async () => {
  const retired = fakeDeps({
    getDeviceSecrets: () => Promise.resolve({ data: [], error: null }),
    getDeviceStatus: () => Promise.resolve({ data: 'retired', error: null }),
  });
  const res = await handle(await signedRequest(), retired);
  assert.equal(res.status, 403);
  assert.equal((await res.json()).code, 'device_retired');
  assert.equal(retired.calls.snapshot, 0);

  const unknown = fakeDeps({
    getDeviceSecrets: () => Promise.resolve({ data: [], error: null }),
    getDeviceStatus: () => Promise.resolve({ data: null, error: null }),
  });
  assert.equal((await handle(await signedRequest(), unknown)).status, 401);
});

Deno.test('a database failure in get_device_secrets is a logged 500, not "invalid device credentials"', async () => {
  const deps = fakeDeps({
    getDeviceSecrets: () =>
      Promise.resolve({ data: null, error: { code: '57014', message: 'canceling statement due to statement timeout' } }),
  });
  const res = await handle(await signedRequest(), deps);
  assert.equal(res.status, 500);
  const body = await res.json();
  assert.equal(body.code, 'internal');
  assert.doesNotMatch(JSON.stringify(body), /statement/);
  assert.ok(deps.logs.some((l) => l.includes('get_device_secrets')));
});

Deno.test('other snapshot failures are 500 internal', async () => {
  const deps = fakeDeps({ snapshot: () => Promise.resolve({ data: null, error: { code: 'XX000', message: 'boom' } }) });
  const res = await handle(await signedRequest(), deps);
  assert.equal(res.status, 500);
  assert.equal((await res.json()).code, 'internal');
});

Deno.test('non-GET methods are 405', async () => {
  assert.equal((await handle(new Request('http://localhost/device-sync', { method: 'POST' }), fakeDeps())).status, 405);
});
