/**
 * notification-dispatch handler with fake DB / push dependencies:
 * auth (405/401), configuration guard, claim → send → complete, 410 → disable, batching, DB errors.
 *
 *   cd supabase/functions
 *   deno test --config deno.json --allow-env --allow-read tests/notification_dispatch.test.ts
 */
import assert from 'node:assert/strict';
import { createDispatchHandler, INVALID_VAPID, secretsEqual } from '../_shared/notify/dispatch.ts';
import type { Db, Job, PushSender, PushSub, WorkerDeps } from '../_shared/notify/worker.ts';

const SECRET = 'test-dispatch-secret-0123456789abcdef';
const REPORT = '6f1d2c3e-4b5a-4c6d-8e7f-901234567801';
const SESSION = '6f1d2c3e-4b5a-4c6d-8e7f-901234567802';

const job = (id: string, over: Partial<Job> = {}): Job => ({
  id,
  member_id: `m-${id}`,
  channel: 'web_push',
  template: 'workout_report_ready',
  payload: { report_id: REPORT, attendance_session_id: SESSION },
  attempts: 1,
  max_attempts: 6,
  ...over,
});

const sub = (id: string): PushSub => ({ id, endpoint: `https://push.example/${id}`, p256dh: 'k', auth: 'a' });

interface FakeDb extends Db {
  claims: Array<[string, number]>;
  completed: Array<[string, boolean, string | undefined]>;
  disabled: string[];
}

function fakeDb(batches: Job[][], subs: (memberId: string) => PushSub[]): FakeDb {
  const queue = [...batches];
  const db: FakeDb = {
    claims: [],
    completed: [],
    disabled: [],
    claim(worker, limit) {
      db.claims.push([worker, limit]);
      return Promise.resolve(queue.shift() ?? []);
    },
    complete(id, ok, err) {
      db.completed.push([id, ok, err]);
      return Promise.resolve();
    },
    memberName() {
      return Promise.resolve('Omar Ahmed');
    },
    pushSubscriptions(memberId) {
      return Promise.resolve(subs(memberId));
    },
    disablePushSubscription(id) {
      db.disabled.push(id);
      return Promise.resolve();
    },
  };
  return db;
}

function recordingPush(fail: Record<string, number> = {}): PushSender & { sent: Array<[string, unknown]> } {
  const sent: Array<[string, unknown]> = [];
  return {
    sent,
    send(s, json) {
      const status = fail[s.id];
      if (status) return Promise.reject(Object.assign(new Error(`status ${status}`), { statusCode: status }));
      sent.push([s.id, JSON.parse(json)]);
      return Promise.resolve();
    },
  };
}

function handlerWith(db: Db, push: PushSender, extra: Partial<Parameters<typeof createDispatchHandler>[0]> = {}) {
  const deps: WorkerDeps = { db, push, memberAppUrl: 'https://members.example/', workerId: 'edge-fn-test' };
  return createDispatchHandler({ secret: SECRET, deps: () => deps, ...extra });
}

const post = (headers: Record<string, string> = { 'x-dispatch-secret': SECRET }) =>
  new Request('http://localhost/notification-dispatch', { method: 'POST', headers: { 'content-type': 'application/json', ...headers }, body: '{}' });

Deno.test('secretsEqual compares in constant time over digests', async () => {
  assert.equal(await secretsEqual('abc', 'abc'), true);
  assert.equal(await secretsEqual('abc', 'abd'), false);
  assert.equal(await secretsEqual('abc', 'abcd'), false);
  assert.equal(await secretsEqual('', 'x'), false);
});

Deno.test('rejects non-POST with 405', async () => {
  const db = fakeDb([[job('1')]], () => [sub('s')]);
  const res = await handlerWith(db, recordingPush())(new Request('http://localhost/', { method: 'GET' }));
  assert.equal(res.status, 405);
  assert.equal(db.claims.length, 0);
});

Deno.test('missing, wrong or unconfigured secret → 401 and nothing is claimed', async () => {
  const db = fakeDb([[job('1')]], () => [sub('s')]);
  const handler = handlerWith(db, recordingPush());
  for (const req of [post({}), post({ 'x-dispatch-secret': 'wrong' }), post({ 'x-dispatch-secret': SECRET.slice(0, -1) })]) {
    const res = await handler(req);
    assert.equal(res.status, 401);
    assert.deepEqual(await res.json(), { code: 'unauthorized' });
  }
  for (const secret of [undefined, '']) {
    const res = await handlerWith(db, recordingPush(), { secret })(post({ 'x-dispatch-secret': '' }));
    assert.equal(res.status, 401);
    await res.body?.cancel();
  }
  assert.equal(db.claims.length, 0);
});

Deno.test('missing configuration → 500 not_configured without claiming (jobs stay queued)', async () => {
  const db = fakeDb([[job('1')]], () => [sub('s')]);
  const res = await handlerWith(db, recordingPush(), { missingConfig: ['VAPID_PRIVATE_KEY'] })(post());
  assert.equal(res.status, 500);
  assert.deepEqual(await res.json(), { code: 'not_configured', missing: ['VAPID_PRIVATE_KEY'] });
  assert.equal(db.claims.length, 0);
});

Deno.test('claim → send → complete, response counts', async () => {
  const db = fakeDb([[job('1'), job('2')]], (m) => [sub(`s-${m}`)]);
  const push = recordingPush();
  const res = await handlerWith(db, push)(post());
  assert.equal(res.status, 200);
  assert.deepEqual(await res.json(), { claimed: 2, delivered: 2, failed: 0 });
  assert.deepEqual(db.claims, [['edge-fn-test', 20]]);
  assert.deepEqual(db.completed, [['1', true, undefined], ['2', true, undefined]]);
  assert.equal(push.sent.length, 2);
  assert.deepEqual(push.sent[0], ['s-m-1', {
    title: 'Your workout report is ready',
    body: "Omar, today's session summary is available.",
    url: `https://members.example/reports/${REPORT}`,
    tag: `report-${REPORT}`,
  }]);
});

Deno.test('410 disables the subscription and fails the job (DB applies backoff)', async () => {
  const db = fakeDb([[job('1')]], () => [sub('gone')]);
  const res = await handlerWith(db, recordingPush({ gone: 410 }))(post());
  assert.deepEqual(await res.json(), { claimed: 1, delivered: 0, failed: 1 });
  assert.deepEqual(db.disabled, ['gone']);
  assert.equal(db.completed.length, 1);
  const [id, ok, err] = db.completed[0]!;
  assert.equal(id, '1');
  assert.equal(ok, false);
  assert.match(err ?? '', /push 410/);
});

Deno.test('other push failures do not disable but fail the job; one good subscription delivers', async () => {
  const db = fakeDb([[job('1'), job('2')]], (m) => (m === 'm-1' ? [sub('flaky')] : [sub('down'), sub('ok')]));
  const res = await handlerWith(db, recordingPush({ flaky: 500, down: 503 }))(post());
  assert.deepEqual(await res.json(), { claimed: 2, delivered: 1, failed: 1 });
  assert.deepEqual(db.disabled, []);
  assert.deepEqual(db.completed.map(([id, ok]) => [id, ok]), [['1', false], ['2', true]]);
});

Deno.test('malformed payloads and unknown channels are completed as failures', async () => {
  const db = fakeDb([[job('1', { payload: { report_id: 'nope' } }), job('2', { channel: 'sms' }), job('3', { channel: 'email' })]], () => [sub('s')]);
  const res = await handlerWith(db, recordingPush())(post());
  assert.deepEqual(await res.json(), { claimed: 3, delivered: 0, failed: 3 });
  assert.match(db.completed[0]![2] ?? '', /invalid workout_report_ready payload/);
  assert.match(db.completed[1]![2] ?? '', /channel sms not supported/);
  assert.match(db.completed[2]![2] ?? '', /email channel not configured/);
});

Deno.test('keeps claiming full batches up to maxBatches', async () => {
  const full = (p: string) => Array.from({ length: 2 }, (_, i) => job(`${p}${i}`));
  const db = fakeDb([full('a'), full('b'), [job('c0')], full('never')], () => [sub('s')]);
  const res = await handlerWith(db, recordingPush(), { batchSize: 2, maxBatches: 5 })(post());
  assert.deepEqual(await res.json(), { claimed: 5, delivered: 5, failed: 0 });
  assert.equal(db.claims.length, 3);

  const capped = fakeDb([full('a'), full('b'), full('c')], () => [sub('s')]);
  const res2 = await handlerWith(capped, recordingPush(), { batchSize: 2, maxBatches: 2 })(post());
  assert.deepEqual(await res2.json(), { claimed: 4, delivered: 4, failed: 0 });
});

Deno.test('database errors → 500 internal', async () => {
  const db = fakeDb([], () => []);
  db.claim = () => Promise.reject(new Error('claim_notification_jobs failed: permission denied'));
  const logs: string[] = [];
  const res = await handlerWith(db, recordingPush(), { log: (m) => logs.push(m) })(post());
  assert.equal(res.status, 500);
  assert.deepEqual(await res.json(), { code: 'internal', message: 'dispatch failed' });
  assert.ok(logs.includes('dispatch.error'));
});

Deno.test('push sender not ready (malformed VAPID key) → 500 not_configured before claiming (jobs stay queued)', async () => {
  const db = fakeDb([[job('1')]], () => [sub('s')]);
  const push = Object.assign(recordingPush(), { ready: () => Promise.reject(new Error('VAPID_PRIVATE_KEY must be a 32-byte P-256 private key')) });
  const logs: string[] = [];
  const res = await handlerWith(db, push, { log: (m) => logs.push(m) })(post());
  assert.equal(res.status, 500);
  assert.deepEqual(await res.json(), { code: 'not_configured', missing: [INVALID_VAPID] });
  assert.equal(db.claims.length, 0);
  assert.equal(db.completed.length, 0);
  assert.ok(logs.includes('dispatch.push_not_ready'));
});

Deno.test('a ready push sender is checked once per invocation and jobs are then delivered', async () => {
  const db = fakeDb([[job('1')]], () => [sub('s')]);
  let readyCalls = 0;
  const push = Object.assign(recordingPush(), { ready: () => (readyCalls++, Promise.resolve()) });
  const res = await handlerWith(db, push)(post());
  assert.deepEqual(await res.json(), { claimed: 1, delivered: 1, failed: 0 });
  assert.equal(readyCalls, 1);
});

Deno.test('failure while building dependencies → 500 internal without claiming', async () => {
  const db = fakeDb([[job('1')]], () => [sub('s')]);
  const handler = createDispatchHandler({
    secret: SECRET,
    deps: () => {
      throw new Error('VAPID_SUBJECT must be a mailto: or https: URI');
    },
  });
  const res = await handler(post());
  assert.equal(res.status, 500);
  assert.equal((await res.json()).code, 'internal');
  assert.equal(db.claims.length, 0);
});
