/**
 * Backup / restore drill (Phase 8 acceptance item) on the embedded cluster:
 * pg_dump the populated database, restore into a fresh database on the same
 * cluster, and prove row counts, RLS flags and a sample report are identical.
 */
import { execFileSync } from 'node:child_process';
import { existsSync, readdirSync } from 'node:fs';
import { join } from 'node:path';
import pg from 'pg';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { startHarness, type Harness } from './harness';

let h: Harness;

function findBinary(name: string): string | null {
  const exe = process.platform === 'win32' ? `${name}.exe` : name;
  // 1. explicit tools dir (CI installs postgresql-client; locally tests/db/.pgtools/bin holds EDB binaries)
  const candidates = [process.env.PG_TOOLS_BIN, join(import.meta.dirname ?? __dirname, '.pgtools', 'bin')].filter(Boolean) as string[];
  for (const dir of candidates) {
    const p = join(dir, exe);
    if (existsSync(p)) return p;
  }
  // 2. embedded-postgres server bundle (ships postgres/initdb/pg_ctl; client tools only on some platforms)
  const root = join(import.meta.dirname ?? __dirname, '..', '..', 'node_modules', '@embedded-postgres');
  if (existsSync(root)) {
    for (const platform of readdirSync(root)) {
      const bin = join(root, platform, 'native', 'bin', exe);
      if (existsSync(bin)) return bin;
    }
  }
  // 3. PATH
  const pathDirs = (process.env.PATH ?? '').split(process.platform === 'win32' ? ';' : ':');
  for (const dir of pathDirs) {
    const p = join(dir, exe);
    if (dir && existsSync(p)) return p;
  }
  return null;
}

const TABLES = ['members', 'consent_records', 'attendance_sessions', 'exercise_sets', 'workout_reports', 'cameras', 'camera_zones', 'equipment', 'exercise_catalog', 'audit_logs'];

beforeAll(async () => {
  h = await startHarness();
  // Populate some facts via the engine so the dump is not just reference data.
  await h.pool.query(
    `insert into public.attendance_sessions (member_id, branch_id, check_in_at, check_in_method) values ('33333333-3333-4333-8333-333333333333', 'b0000000-0000-4000-8000-000000000001', now() - interval '50 minutes', 'qr')`,
  );
  const s = await h.pool.query(`select id from public.attendance_sessions where status = 'active' limit 1`);
  await h.pool.query(`select public.finalize_attendance_session($1, now(), 'qr')`, [s.rows[0].id]);
});
afterAll(async () => {
  await h?.stop();
});

describe('backup and restore drill', () => {
  it('pg_dump → restore into a fresh database preserves data and RLS', async (ctx) => {
    const pgDump = findBinary('pg_dump');
    const psql = findBinary('psql');
    if (!pgDump || !psql) {
      console.warn('pg_dump/psql not found (set PG_TOOLS_BIN or place them in tests/db/.pgtools/bin); drill SKIPPED, not passed');
      ctx.skip();
      return;
    }
    console.log(`[drill] pg_dump=${pgDump}`);
    const env = { ...process.env, PGPASSWORD: 'postgres' };
    const dump = execFileSync(pgDump, ['-h', '127.0.0.1', '-p', String(h.port), '-U', 'postgres', '-d', 'postgres', '--no-owner', '--no-privileges'], { env, maxBuffer: 256 * 1024 * 1024 });
    expect(dump.length).toBeGreaterThan(10_000);

    await h.pool.query('create database restore_drill');
    // Roles/auth schema are cluster-level or recreated by the dump (auth schema is in the dump).
    execFileSync(psql, ['-h', '127.0.0.1', '-p', String(h.port), '-U', 'postgres', '-d', 'restore_drill', '-v', 'ON_ERROR_STOP=0', '-q'], { env, input: dump, maxBuffer: 256 * 1024 * 1024 });

    const restored = new pg.Client({ connectionString: h.connectionString.replace(/\/postgres$/, '/restore_drill') });
    await restored.connect();
    try {
      for (const t of TABLES) {
        const a = await h.pool.query(`select count(*)::int as n from public.${t}`);
        const b = await restored.query(`select count(*)::int as n from public.${t}`);
        expect(b.rows[0].n, `row count of ${t}`).toBe(a.rows[0].n);
      }
      const rls = await restored.query(`select count(*)::int as n from pg_tables where schemaname = 'public' and not rowsecurity`);
      expect(rls.rows[0].n).toBe(0);
      const a = await h.pool.query(`select summary from public.workout_reports order by generated_at limit 1`);
      const b = await restored.query(`select summary from public.workout_reports order by generated_at limit 1`);
      expect(b.rows[0].summary).toEqual(a.rows[0].summary);
      const fn = await restored.query(`select count(*)::int as n from pg_proc p join pg_namespace n on n.oid = p.pronamespace where n.nspname = 'public' and p.proname = 'ingest_events'`);
      expect(fn.rows[0].n).toBe(1);
    } finally {
      await restored.end();
    }
  });
});
