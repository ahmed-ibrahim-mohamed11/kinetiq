/**
 * ingest-events handler with fake DB dependencies: signature over the events as received (extra
 * envelope keys, key order), tamper detection, signature checked before any validation, streaming
 * byte cap, per-event schema validation, C4 pass-through, C5 device_retired, infra errors → 500.
 *
 *   cd supabase/functions
 *   deno test --config deno.json --allow-env --allow-read tests/ingest_events.test.ts
 */
import assert from 'node:assert/strict';
import { hmacHex, sha256Hex } from '../_shared/device_auth.ts';
import { handle, type IngestDeps, type IngestRpcResult, INVALID_EVENT_REASON } from '../ingest-events/handler.ts';

const DEVICE = '11111111-1111-4111-8111-111111111111';
const BRANCH = '22222222-2222-4222-8222-222222222222';
const SECRET = 'test-device-secret-0123456789';
const NOW = Date.parse('2026-11-02T10:00:00.000Z');

const fixtures = JSON.parse(
  await Deno.readTextFile(new URL('../../../packages/api-contracts/fixtures/events_v1_valid.json', import.meta.url)),
) as Array<
  Record<string, unknown>
>;

type Ev = Record<string, unknown> & { event_id: string };

function heartbeat(seq: number, extra: Record<string, unknown> = {}): Ev {
  return {
    schema_version: '1',
    event_id: crypto.randomUUID(),
    device_id: DEVICE,
    branch_id: BRANCH,
    sequence: seq,
    occurred_at: '2026-11-02T09:59:59.000Z',
    sent_at: '2026-11-02T10:00:00.000Z',
    event_type: 'device.heartbeat',
    payload: {
      software_version: '1.2.3',
      uptime_seconds: 10,
      gpu_utilization: null,
      gpu_memory_used_mb: null,
      cpu_utilization: 0.5,
      queue_depth: 0,
      cameras_online: 2,
      cameras_total: 2,
      avg_pipeline_latency_ms: null,
    },
    ...extra,
  };
}

function batchOf(events: unknown[], over: Record<string, unknown> = {}) {
  return { schema_version: '1', batch_id: crypto.randomUUID(), device_id: DEVICE, sent_at: new Date(NOW).toISOString(), events, ...over };
}

async function sign(batch: { device_id: string; batch_id: string; sent_at: string; events: unknown[] }, secret = SECRET): Promise<string> {
  const hash = await sha256Hex(JSON.stringify(batch.events));
  return await hmacHex(secret, `${batch.device_id}\n${batch.batch_id}\n${batch.sent_at}\n${hash}`);
}

function request(body: string | ReadableStream<Uint8Array>, signature: string, headers: Record<string, string> = {}): Request {
  return new Request('http://localhost/ingest-events', {
    method: 'POST',
    headers: { 'content-type': 'application/json', 'x-device-id': DEVICE, 'x-device-signature': signature, ...headers },
    body,
  });
}

interface Fake extends IngestDeps {
  calls: { secrets: number; status: number; ingest: Array<{ p_events: unknown[]; p_batch_id: string }> };
  logs: string[];
}

function fakeDeps(over: Partial<IngestDeps> = {}): Fake {
  const calls: Fake['calls'] = { secrets: 0, status: 0, ingest: [] };
  const logs: string[] = [];
  return {
    calls,
    logs,
    clockSkewSeconds: 300,
    now: () => NOW,
    log: (msg) => logs.push(msg),
    getDeviceSecrets: (id) => {
      calls.secrets++;
      return Promise.resolve({ data: id === DEVICE ? [{ secret: 'old-rotated-secret' }, { secret: SECRET }] : [], error: null });
    },
    getDeviceStatus: () => {
      calls.status++;
      return Promise.resolve({ data: 'active', error: null });
    },
    ingestEvents: (args) => {
      calls.ingest.push(args);
      const result: IngestRpcResult = {
        batch_id: args.p_batch_id,
        accepted: args.p_events.length,
        duplicates: 0,
        rejected: [],
        server_time: new Date(NOW).toISOString(),
      };
      return Promise.resolve({ data: result, error: null });
    },
    ...over,
  };
}

async function bodyOf(res: Response): Promise<Record<string, unknown>> {
  return await res.json();
}

Deno.test('accepts a correctly signed batch of the shared fixtures and forwards them to ingest_events', async () => {
  const events = fixtures.map((e) => ({ ...e, device_id: DEVICE }));
  const batch = batchOf(events);
  const deps = fakeDeps();
  const res = await handle(request(JSON.stringify(batch), await sign(batch)), deps);
  assert.equal(res.status, 200);
  const body = await bodyOf(res);
  assert.equal(body.accepted, events.length);
  assert.deepEqual(body.rejected, []);
  assert.equal(deps.calls.ingest.length, 1);
  assert.equal(deps.calls.ingest[0].p_events.length, events.length);
});

Deno.test('signature is computed over the events as received: an extra envelope key and a different key order are accepted', async () => {
  const ev = heartbeat(1, { trace_id: 'abc-123' });
  // Same fields, reversed key order (as a different serializer might emit them).
  const reordered = Object.fromEntries(Object.entries(heartbeat(2)).reverse());
  const batch = batchOf([ev, reordered]);
  const deps = fakeDeps();
  const res = await handle(request(JSON.stringify(batch), await sign(batch)), deps);
  assert.equal(res.status, 200, JSON.stringify(await res.clone().json()));
  assert.equal((await bodyOf(res)).accepted, 2);
  assert.equal(deps.calls.ingest[0].p_events.length, 2);
});

Deno.test('a tampered body is rejected with 401 and never reaches ingest_events', async () => {
  const batch = batchOf([heartbeat(1)]);
  const signature = await sign(batch);
  const tampered = structuredClone(batch);
  (tampered.events[0] as { payload: { queue_depth: number } }).payload.queue_depth = 99;
  const deps = fakeDeps();
  const res = await handle(request(JSON.stringify(tampered), signature), deps);
  assert.equal(res.status, 401);
  assert.equal((await bodyOf(res)).code, 'unauthorized');
  assert.equal(deps.calls.ingest.length, 0);
});

Deno.test('the signature is checked before schema validation: an unsigned invalid batch gets 401 without details', async () => {
  const batch = batchOf([{ ...heartbeat(1), event_type: 'not.a.type', payload: 'x' }], { schema_version: '9' });
  const deps = fakeDeps();
  const res = await handle(request(JSON.stringify(batch), 'f'.repeat(64)), deps);
  assert.equal(res.status, 401);
  const body = await bodyOf(res);
  assert.equal(body.code, 'unauthorized');
  assert.equal(body.details, undefined);
  assert.equal(deps.calls.ingest.length, 0);
});

Deno.test('schema details are returned only after authentication', async () => {
  const batch = batchOf([heartbeat(1)], { schema_version: '9' });
  const res = await handle(request(JSON.stringify(batch), await sign(batch)), fakeDeps());
  assert.equal(res.status, 400);
  const body = await bodyOf(res);
  assert.equal(body.code, 'validation_error');
  assert.ok(body.details);
});

Deno.test('malformed bodies are 400 without details and without any database call', async () => {
  for (const raw of ['not json', '[]', JSON.stringify({ device_id: DEVICE, batch_id: 1, sent_at: 'x', events: [] })]) {
    const deps = fakeDeps();
    const res = await handle(request(raw, 'a'.repeat(64)), deps);
    assert.equal(res.status, 400, raw);
    assert.equal((await bodyOf(res)).details, undefined);
    assert.equal(deps.calls.secrets, 0);
  }
});

Deno.test('a stale sent_at is rejected before the secret lookup', async () => {
  const batch = batchOf([heartbeat(1)], { sent_at: new Date(NOW - 301_000).toISOString() });
  const deps = fakeDeps();
  const res = await handle(request(JSON.stringify(batch), await sign(batch)), deps);
  assert.equal(res.status, 401);
  assert.match(String((await bodyOf(res)).message), /window/);
  assert.equal(deps.calls.secrets, 0);
});

Deno.test('a batch for another device than the header is rejected', async () => {
  const batch = batchOf([heartbeat(1)], { device_id: '33333333-3333-4333-8333-333333333333' });
  const res = await handle(request(JSON.stringify(batch), await sign(batch)), fakeDeps());
  assert.equal(res.status, 401);
});

Deno.test('the body cap is enforced while streaming (no content-length) and counts bytes, not UTF-16 units', async () => {
  let pulled = 0;
  const chunk = new Uint8Array(1024).fill(0x20);
  const stream = new ReadableStream<Uint8Array>({
    pull(controller) {
      pulled++;
      if (pulled > 10_000) controller.close();
      else controller.enqueue(chunk);
    },
  });
  const deps = fakeDeps({ maxBodyBytes: 8 * 1024 });
  const res = await handle(request(stream, 'a'.repeat(64)), deps);
  assert.equal(res.status, 413);
  assert.ok(pulled < 20, `stopped reading early (pulled ${pulled} chunks)`);
  assert.equal(deps.calls.secrets, 0);

  // 3000 three-byte characters: 9000 bytes but only 3000 UTF-16 units.
  const multibyte = JSON.stringify({ x: '€'.repeat(3000) });
  const res2 = await handle(request(multibyte, 'a'.repeat(64)), fakeDeps({ maxBodyBytes: 8 * 1024 }));
  assert.equal(res2.status, 413);
});

Deno.test('a declared content-length above the cap is rejected immediately', async () => {
  const res = await handle(request('{}', 'a'.repeat(64), { 'content-length': String(3 * 1024 * 1024) }), fakeDeps());
  assert.equal(res.status, 413);
});

Deno.test('events that fail the full events.v1 union are rejected individually (invalid_value); valid ones are ingested', async () => {
  const good = heartbeat(1);
  const badEnum: Ev = {
    ...heartbeat(2),
    event_type: 'camera.status',
    payload: {
      camera_id: crypto.randomUUID(),
      status: 'exploded',
      fps_measured: null,
      resolution_w: null,
      resolution_h: null,
      error: null,
    },
  };
  const outOfRange = heartbeat(3);
  (outOfRange.payload as Record<string, unknown>).cpu_utilization = 7; // outside [0,1]
  const batch = batchOf([good, badEnum, outOfRange]);
  const deps = fakeDeps();
  const res = await handle(request(JSON.stringify(batch), await sign(batch)), deps);
  assert.equal(res.status, 200);
  const body = await bodyOf(res);
  assert.equal(body.accepted, 1);
  assert.deepEqual(
    body.rejected,
    [badEnum, outOfRange].map((e) => ({ event_id: e.event_id, code: 'invalid_value', reason: INVALID_EVENT_REASON })),
  );
  assert.deepEqual(
    deps.calls.ingest[0].p_events.map((e) => (e as { event_id: string }).event_id),
    [good.event_id],
  );
});

Deno.test('a batch whose events are all invalid is answered without calling ingest_events', async () => {
  const bad = { ...heartbeat(1), event_type: 'device.heartbeat', payload: {} };
  const batch = batchOf([bad]);
  const deps = fakeDeps();
  const res = await handle(request(JSON.stringify(batch), await sign(batch)), deps);
  assert.equal(res.status, 200);
  const body = await bodyOf(res);
  assert.equal(body.accepted, 0);
  assert.equal((body.rejected as unknown[]).length, 1);
  assert.equal(deps.calls.ingest.length, 0);
});

Deno.test('C4 rejection codes from ingest_events are passed through unchanged', async () => {
  const ev = heartbeat(1);
  const batch = batchOf([ev]);
  const sqlRejection = { event_id: ev.event_id, code: 'branch_mismatch', reason: 'The event belongs to another branch.' };
  const deps = fakeDeps({
    ingestEvents: (args) =>
      Promise.resolve({ data: { batch_id: args.p_batch_id, accepted: 0, duplicates: 0, rejected: [sqlRejection] }, error: null }),
  });
  const res = await handle(request(JSON.stringify(batch), await sign(batch)), deps);
  assert.equal(res.status, 200);
  assert.deepEqual((await bodyOf(res)).rejected, [sqlRejection]);
});

Deno.test('ingest_events 42501 (device_retired) maps to 403 {code: device_retired}', async () => {
  const batch = batchOf([heartbeat(1)]);
  const deps = fakeDeps({
    ingestEvents: () => Promise.resolve({ data: null, error: { code: '42501', message: `device_retired: ${DEVICE}` } }),
  });
  const res = await handle(request(JSON.stringify(batch), await sign(batch)), deps);
  assert.equal(res.status, 403);
  assert.equal((await bodyOf(res)).code, 'device_retired');
});

Deno.test('a retired device (no valid secrets left) gets 403 device_retired; an unknown device 401', async () => {
  const batch = batchOf([heartbeat(1)]);
  const signature = await sign(batch);
  const retired = fakeDeps({
    getDeviceSecrets: () => Promise.resolve({ data: [], error: null }),
    getDeviceStatus: () => Promise.resolve({ data: 'retired', error: null }),
  });
  const res = await handle(request(JSON.stringify(batch), signature), retired);
  assert.equal(res.status, 403);
  assert.equal((await bodyOf(res)).code, 'device_retired');
  assert.equal(retired.calls.ingest.length, 0);

  const unknown = fakeDeps({
    getDeviceSecrets: () => Promise.resolve({ data: [], error: null }),
    getDeviceStatus: () => Promise.resolve({ data: null, error: null }),
  });
  assert.equal((await handle(request(JSON.stringify(batch), signature), unknown)).status, 401);
});

Deno.test('a database failure during the secret lookup is a logged 500, not 401', async () => {
  const batch = batchOf([heartbeat(1)]);
  const deps = fakeDeps({
    getDeviceSecrets: () => Promise.resolve({ data: null, error: { code: '08006', message: 'connection failure' } }),
  });
  const res = await handle(request(JSON.stringify(batch), await sign(batch)), deps);
  assert.equal(res.status, 500);
  assert.equal((await bodyOf(res)).code, 'internal');
  assert.ok(deps.logs.some((l) => l.includes('get_device_secrets')));
});

Deno.test('infrastructure errors from ingest_events are 500 without SQL text; a replayed batch is an idempotent 200', async () => {
  const batch = batchOf([heartbeat(1)]);
  const signature = await sign(batch);
  const failing = fakeDeps({
    ingestEvents: () => Promise.resolve({ data: null, error: { code: '53300', message: 'too many connections for role "x"' } }),
  });
  const res = await handle(request(JSON.stringify(batch), signature), failing);
  assert.equal(res.status, 500);
  assert.doesNotMatch(await res.text(), /connections/);

  const replay = fakeDeps({
    ingestEvents: () => Promise.resolve({ data: null, error: { code: '23505', message: 'batch already processed' } }),
  });
  const res2 = await handle(request(JSON.stringify(batch), signature), replay);
  assert.equal(res2.status, 200);
  const body = await bodyOf(res2);
  assert.equal(body.replayed, true);
  assert.equal(body.accepted, 0);
});

Deno.test('non-POST methods and malformed auth headers', async () => {
  assert.equal((await handle(new Request('http://localhost/ingest-events'), fakeDeps())).status, 405);
  const batch = batchOf([heartbeat(1)]);
  const res = await handle(request(JSON.stringify(batch), 'not-hex'), fakeDeps());
  assert.equal(res.status, 401);
});
