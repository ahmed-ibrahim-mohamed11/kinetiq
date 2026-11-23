/**
 * Migration 20261102000011_notification_dispatch.sql:
 *  - applies cleanly on the embedded PostgreSQL (no pg_cron / pg_net / Vault) as a NOTICE-only no-op;
 *  - with stand-ins for cron / net / vault it schedules exactly one 'smartgym-notification-dispatch'
 *    job (idempotently), whose command reads the URL and dispatch secret from Vault at run time
 *    and posts only while notification jobs are due.
 */
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { MIGRATIONS_DIR, startHarness, type Harness } from './harness';

const FILE = '20261102000011_notification_dispatch.sql';
const sql = readFileSync(join(MIGRATIONS_DIR, FILE), 'utf8');
const MEMBER = '33333333-3333-4333-8333-333333333333'; // seeded DEMO member

let h: Harness;

beforeAll(async () => {
  h = await startHarness();
});
afterAll(async () => {
  await h?.stop();
});

async function applyMigration(): Promise<string[]> {
  const c = await h.pool.connect();
  const notices: string[] = [];
  const onNotice = (n: { message?: string }) => notices.push(n.message ?? '');
  c.on('notice', onNotice);
  try {
    await c.query(sql);
  } finally {
    c.off('notice', onNotice);
    c.release();
  }
  return notices;
}

describe('notification dispatch migration (0011)', () => {
  it('is part of the applied migration set and stores no secrets', () => {
    expect(sql).toMatch(/vault\.decrypted_secrets where name = 'smartgym_functions_url'/);
    expect(sql).toMatch(/vault\.decrypted_secrets where name = 'smartgym_dispatch_secret'/);
    expect(sql).not.toMatch(/eyJ[A-Za-z0-9_-]{10,}|https:\/\/[a-z0-9]{20}\.supabase\.co/);
  });

  it('is a clean no-op with a NOTICE where pg_cron / pg_net / Vault are unavailable', async () => {
    const { rows } = await h.pool.query(
      `select to_regnamespace('cron') as cron, to_regnamespace('net') as net, to_regnamespace('vault') as vault`,
    );
    expect(rows[0]).toEqual({ cron: null, net: null, vault: null });
    const notices = await applyMigration();
    expect(notices.join('\n')).toMatch(/notification dispatch not scheduled \(pg_cron: false, pg_net: false, vault: false\)/);
    const after = await h.pool.query(`select to_regnamespace('cron') as cron, to_regnamespace('net') as net`);
    expect(after.rows[0]).toEqual({ cron: null, net: null });
  });

  describe('with Supabase platform stand-ins', () => {
    beforeAll(async () => {
      await h.pool.query(`
        create schema cron;
        create table cron.job (jobid bigserial primary key, jobname text unique, schedule text not null, command text not null);
        create function cron.schedule(job_name text, schedule text, command text) returns bigint language sql as $$
          insert into cron.job (jobname, schedule, command) values (job_name, schedule, command)
          on conflict (jobname) do update set schedule = excluded.schedule, command = excluded.command
          returning jobid $$;
        create function cron.unschedule(job_id bigint) returns boolean language sql as $$
          with d as (delete from cron.job where jobid = job_id returning 1) select exists (select 1 from d) $$;

        create schema net;
        create table net.requests (id bigserial primary key, url text not null, headers jsonb, body jsonb, timeout_milliseconds int);
        create function net.http_post(url text, body jsonb default '{}'::jsonb, params jsonb default '{}'::jsonb,
                                      headers jsonb default '{}'::jsonb, timeout_milliseconds int default 5000)
        returns bigint language sql as $$
          insert into net.requests (url, headers, body, timeout_milliseconds) values (url, headers, body, timeout_milliseconds) returning id $$;

        create schema vault;
        create table vault.decrypted_secrets (name text primary key, decrypted_secret text);
        insert into vault.decrypted_secrets values
          ('smartgym_functions_url', 'https://example-ref.supabase.co/functions/v1/'),
          ('smartgym_dispatch_secret', 'test-only-dispatch-secret');
      `);
    });
    afterAll(async () => {
      await h.pool.query(`drop schema cron cascade; drop schema net cascade; drop schema vault cascade;`);
    });

    it('schedules exactly one every-minute job, idempotently', async () => {
      const notices = await applyMigration();
      expect(notices.join('\n')).toMatch(/scheduled smartgym-notification-dispatch/);
      await applyMigration();
      const { rows } = await h.pool.query(`select jobname, schedule, command from cron.job order by jobname`);
      expect(rows.map((r) => r.jobname)).toEqual(['smartgym-notification-dispatch', 'smartgym-release-notification-locks']);
      expect(rows[0].schedule).toBe('* * * * *');
      expect(rows[0].command).not.toContain('test-only-dispatch-secret');
      expect(rows[0].command).not.toContain('example-ref');
      expect(rows[1].schedule).toBe('*/5 * * * *');
    });

    it('posts to the function with the Vault secret only while jobs are due', async () => {
      const { rows } = await h.pool.query(`select command from cron.job where jobname = 'smartgym-notification-dispatch'`);
      const command: string = rows[0].command;
      await h.pool.query(`delete from public.notification_jobs`);

      await h.pool.query(command);
      expect((await h.pool.query(`select count(*)::int as n from net.requests`)).rows[0].n).toBe(0);

      await h.pool.query(
        `insert into public.notification_jobs (member_id, channel, template, payload, next_attempt_at)
         values ($1, 'web_push', 'workout_report_ready', '{}'::jsonb, now() + interval '1 hour')`,
        [MEMBER],
      );
      await h.pool.query(command);
      expect((await h.pool.query(`select count(*)::int as n from net.requests`)).rows[0].n).toBe(0);

      await h.pool.query(`update public.notification_jobs set next_attempt_at = now() - interval '1 second'`);
      await h.pool.query(command);
      const req = await h.pool.query(`select url, headers, body, timeout_milliseconds from net.requests`);
      expect(req.rows).toEqual([
        {
          url: 'https://example-ref.supabase.co/functions/v1/notification-dispatch',
          headers: { 'content-type': 'application/json', 'x-dispatch-secret': 'test-only-dispatch-secret' },
          body: {},
          timeout_milliseconds: 25000,
        },
      ]);

      // Delivered / dead jobs do not wake the function.
      await h.pool.query(`update public.notification_jobs set status = 'delivered'`);
      await h.pool.query(command);
      expect((await h.pool.query(`select count(*)::int as n from net.requests`)).rows[0].n).toBe(1);
      await h.pool.query(`delete from public.notification_jobs`);
    });
  });
});
