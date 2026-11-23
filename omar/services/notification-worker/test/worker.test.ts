import { describe, expect, it } from 'vitest';
import { processJob, tick, type Db, type Job, type PushSub, type WorkerDeps } from '../src/worker';
import { render } from '../src/templates';

const job = (over: Partial<Job> = {}): Job => ({
  id: 'job-1',
  member_id: 'm-1',
  channel: 'web_push',
  template: 'workout_report_ready',
  payload: { report_id: '6f1d2c3e-4b5a-4c6d-8e7f-901234567801', attendance_session_id: '6f1d2c3e-4b5a-4c6d-8e7f-901234567802' },
  attempts: 1,
  max_attempts: 6,
  ...over,
});

function fakeDb(subs: PushSub[]): Db & { completed: Array<[string, boolean, string | undefined]>; disabled: string[] } {
  const completed: Array<[string, boolean, string | undefined]> = [];
  const disabled: string[] = [];
  return {
    completed,
    disabled,
    async claim() {
      return [job()];
    },
    async complete(id, ok, err) {
      completed.push([id, ok, err]);
    },
    async memberName() {
      return 'Test Member';
    },
    async pushSubscriptions() {
      return subs;
    },
    async disablePushSubscription(id) {
      disabled.push(id);
    },
  };
}

const sub = (id: string): PushSub => ({ id, endpoint: `https://push.example/${id}`, p256dh: 'k', auth: 'a' });

describe('templates', () => {
  it('renders the report link', () => {
    const r = render('workout_report_ready', { report_id: '6f1d2c3e-4b5a-4c6d-8e7f-901234567801', attendance_session_id: '6f1d2c3e-4b5a-4c6d-8e7f-901234567802' }, 'Omar A', 'https://m.example/');
    expect(r.url).toBe('https://m.example/reports/6f1d2c3e-4b5a-4c6d-8e7f-901234567801');
    expect(r.body).toContain('Omar');
  });
  it('rejects malformed payloads', () => {
    expect(() => render('workout_report_ready', { report_id: 'nope' }, 'X', 'https://m')).toThrow();
    expect(() => render('unknown', {}, 'X', 'https://m')).toThrow(/unknown notification template/);
  });
});

describe('processJob', () => {
  it('delivers to at least one subscription and disables gone endpoints', async () => {
    const db = fakeDb([sub('gone'), sub('ok')]);
    const sent: string[] = [];
    const deps: WorkerDeps = {
      db,
      memberAppUrl: 'https://m.example',
      workerId: 'w',
      push: {
        async send(s) {
          if (s.id === 'gone') throw Object.assign(new Error('Gone'), { statusCode: 410 });
          sent.push(s.id);
        },
      },
    };
    const r = await processJob(job(), deps);
    expect(r.ok).toBe(true);
    expect(sent).toEqual(['ok']);
    expect(db.disabled).toEqual(['gone']);
  });

  it('fails when there are no subscriptions (DB schedules retry/dead-letter)', async () => {
    const db = fakeDb([]);
    const r = await processJob(job(), { db, memberAppUrl: 'https://m', workerId: 'w', push: { async send() {} } });
    expect(r.ok).toBe(false);
    expect(r.error).toMatch(/no active push/);
  });

  it('email without a configured sender fails explicitly', async () => {
    const db = fakeDb([]);
    const r = await processJob(job({ channel: 'email' }), { db, memberAppUrl: 'https://m', workerId: 'w', push: { async send() {} } });
    expect(r.ok).toBe(false);
    expect(r.error).toMatch(/email channel not configured/);
  });
});

describe('tick', () => {
  it('reports every claimed job back to the database', async () => {
    const db = fakeDb([sub('ok')]);
    const res = await tick({ db, memberAppUrl: 'https://m', workerId: 'w', push: { async send() {} } });
    expect(res).toEqual({ claimed: 1, delivered: 1, failed: 0 });
    expect(db.completed).toEqual([['job-1', true, undefined]]);
  });
});
