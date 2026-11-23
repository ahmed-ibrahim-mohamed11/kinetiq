/**
 * Embedded PostgreSQL harness.
 *
 * Starts a throwaway PostgreSQL (binaries downloaded by `embedded-postgres`),
 * applies the Supabase shim, then every file in supabase/migrations in order,
 * then supabase/seed.sql. Exposes helpers that emulate how PostgREST runs
 * queries for a given user: `asUser(uid)` switches to the `authenticated`
 * role and sets request.jwt.claim.sub, `asAnon()` switches to `anon`,
 * `asService()` uses the superuser connection (service_role semantics).
 */
import EmbeddedPostgres from 'embedded-postgres';
import { readdirSync, readFileSync, mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import pg from 'pg';

const ROOT = join(import.meta.dirname ?? __dirname, '..', '..');
export const MIGRATIONS_DIR = join(ROOT, 'supabase', 'migrations');
export const SEED_FILE = join(ROOT, 'supabase', 'seed.sql');
const SHIM_FILE = join(import.meta.dirname ?? __dirname, 'supabase-shim.sql');

export interface Harness {
  port: number;
  connectionString: string;
  pool: pg.Pool;
  dataDir: string;
  stop(): Promise<void>;
  /** Run a callback inside a transaction with PostgREST-style role + claims, then roll back nothing (commit). */
  asUser<T>(userId: string, fn: (c: pg.PoolClient) => Promise<T>): Promise<T>;
  asAnon<T>(fn: (c: pg.PoolClient) => Promise<T>): Promise<T>;
  asService<T>(fn: (c: pg.PoolClient) => Promise<T>): Promise<T>;
}

/**
 * All migrations in apply order. `TEST_DB_SKIP_MIGRATIONS` (comma-separated filename prefixes,
 * e.g. "20261102000009,20261102000010") excludes work-in-progress files so several people/agents
 * can develop independent migrations side by side. CI never sets it.
 */
export function migrationFiles(): string[] {
  const skip = (process.env.TEST_DB_SKIP_MIGRATIONS ?? '')
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean);
  return readdirSync(MIGRATIONS_DIR)
    .filter((f) => f.endsWith('.sql') && !skip.some((prefix) => f.startsWith(prefix)))
    .sort();
}

async function pickPort(): Promise<number> {
  const net = await import('node:net');
  return new Promise((resolve, reject) => {
    const srv = net.createServer();
    srv.listen(0, '127.0.0.1', () => {
      const addr = srv.address();
      const port = typeof addr === 'object' && addr ? addr.port : 54329;
      srv.close(() => resolve(port));
    });
    srv.on('error', reject);
  });
}

export async function startHarness(opts: { seed?: boolean; log?: (m: string) => void } = {}): Promise<Harness> {
  const log = opts.log ?? (() => {});
  const port = await pickPort();
  const dataDir = mkdtempSync(join(tmpdir(), 'smartgym-pg-'));
  const pgInstance = new EmbeddedPostgres({
    databaseDir: dataDir,
    user: 'postgres',
    password: 'postgres',
    port,
    persistent: false,
    initdbFlags: ['--encoding=UTF8', '--locale=C'],
  });
  log(`initdb → ${dataDir}`);
  await pgInstance.initialise();
  log('starting postgres');
  await pgInstance.start();
  const connectionString = `postgresql://postgres:postgres@127.0.0.1:${port}/postgres`;
  const pool = new pg.Pool({ connectionString, max: 4 });

  const client = await pool.connect();
  try {
    log('applying supabase shim');
    await client.query(readFileSync(SHIM_FILE, 'utf8'));
    for (const f of migrationFiles()) {
      log(`applying migration ${f}`);
      await client.query(readFileSync(join(MIGRATIONS_DIR, f), 'utf8'));
    }
    if (opts.seed !== false) {
      log('applying seed.sql');
      await client.query(readFileSync(SEED_FILE, 'utf8'));
    }
  } finally {
    client.release();
  }

  async function withRole<T>(role: 'anon' | 'authenticated' | null, uid: string | null, fn: (c: pg.PoolClient) => Promise<T>): Promise<T> {
    const c = await pool.connect();
    try {
      await c.query('begin');
      if (role) {
        await c.query(`select set_config('request.jwt.claim.role', $1, true)`, [role]);
        await c.query(`select set_config('request.jwt.claim.sub', $1, true)`, [uid ?? '']);
        await c.query(`set local role ${role}`);
      }
      const result = await fn(c);
      await c.query('commit');
      return result;
    } catch (err) {
      await c.query('rollback').catch(() => undefined);
      throw err;
    } finally {
      c.release();
    }
  }

  return {
    port,
    connectionString,
    pool,
    dataDir,
    async stop() {
      await pool.end();
      await pgInstance.stop();
      // On Windows the postmaster can still hold files for a moment after stop(),
      // so retry; a leftover temp directory is not a test failure.
      await new Promise((r) => setTimeout(r, 800));
      try {
        rmSync(dataDir, { recursive: true, force: true, maxRetries: 15, retryDelay: 400 });
      } catch (err) {
        console.warn(`could not remove ${dataDir}: ${(err as Error).message}`);
      }
    },
    asUser: (uid, fn) => withRole('authenticated', uid, fn),
    asAnon: (fn) => withRole('anon', null, fn),
    asService: (fn) => withRole(null, null, fn),
  };
}
