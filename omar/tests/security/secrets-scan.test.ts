/**
 * Static security checks that run in every CI without any external service:
 *  - no service-role key or private key material committed anywhere;
 *  - no NEXT_PUBLIC_ variable that smells like a secret;
 *  - no camera URL with embedded credentials in config or seeds;
 *  - every public.* table in migrations has RLS enabled.
 */
import { describe, expect, it } from 'vitest';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';

const ROOT = join(__dirname, '..', '..');
const SKIP_DIRS = new Set(['node_modules', '.next', '.git', 'dist', '.venv', '__pycache__', '.pytest_cache', 'coverage', '.vercel']);
const TEXT_EXT = /\.(ts|tsx|js|mjs|cjs|json|sql|toml|yml|yaml|py|md|env|example|txt)$/i;

function* walk(dir: string): Generator<string> {
  for (const name of readdirSync(dir)) {
    if (SKIP_DIRS.has(name)) continue;
    const p = join(dir, name);
    const st = statSync(p);
    if (st.isDirectory()) yield* walk(p);
    else if (TEXT_EXT.test(name) || name.startsWith('.env')) yield p;
  }
}

const files = [...walk(ROOT)];
const read = (p: string) => readFileSync(p, 'utf8');

describe('repository secret hygiene', () => {
  it('contains no JWT-looking service role keys or private keys', () => {
    const offenders: string[] = [];
    for (const f of files) {
      const s = read(f);
      if (/eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}/.test(s)) offenders.push(relative(ROOT, f) + ' (JWT)');
      if (/-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----/.test(s)) offenders.push(relative(ROOT, f) + ' (private key)');
    }
    expect(offenders).toEqual([]);
  });

  it('never exposes secrets under NEXT_PUBLIC_', () => {
    const offenders: string[] = [];
    for (const f of files) {
      const matches = read(f).match(/NEXT_PUBLIC_[A-Z0-9_]*(SERVICE_ROLE|SECRET|PRIVATE)[A-Z0-9_]*/g);
      // Allow the explicit negative test in shared-config.
      if (matches && !f.includes('shared-config')) offenders.push(`${relative(ROOT, f)}: ${matches.join(', ')}`);
    }
    expect(offenders).toEqual([]);
  });

  it('has no camera URLs with embedded credentials outside of negative tests', () => {
    const offenders: string[] = [];
    for (const f of files) {
      if (/test|spec/i.test(f)) continue;
      const m = read(f).match(/rtsps?:\/\/[^\s'"@/]+:[^\s'"@/]+@/g);
      if (m) offenders.push(`${relative(ROOT, f)}: ${m.join(', ')}`);
    }
    expect(offenders).toEqual([]);
  });
});

describe('migrations', () => {
  const migrationsDir = join(ROOT, 'supabase', 'migrations');
  const sql = readdirSync(migrationsDir)
    .filter((f) => f.endsWith('.sql'))
    .sort()
    .map((f) => read(join(migrationsDir, f)))
    .join('\n');

  it('enable RLS on every created table', () => {
    const created = [...sql.matchAll(/create table (?:if not exists )?public\.([a-z_]+)/g)].map((m) => m[1]!);
    expect(created.length).toBeGreaterThan(20);
    // 0001 enables RLS on all tables via a DO block; later tables must do it explicitly.
    const laterTables = created.filter((t) => sql.indexOf(`create table public.${t}`) > sql.indexOf('-- Enable RLS everywhere'));
    for (const t of laterTables) {
      expect(sql, `table ${t} must enable RLS`).toMatch(new RegExp(`alter table public\\.${t} enable row level security`));
    }
  });

  it('never grants secrets to anon/authenticated and revokes privileged functions', () => {
    expect(sql).not.toMatch(/grant .* on public\.edge_device_secrets to (anon|authenticated)/);
    for (const fn of ['ingest_events', 'finalize_attendance_session', 'get_device_secrets', 'claim_notification_jobs', 'apply_retention']) {
      expect(sql, `${fn} must be revoked from authenticated`).toMatch(new RegExp(`revoke all on function public\\.${fn}\\([^)]*\\) from public, anon, authenticated`));
    }
  });

  it('forbids quality_label values (Phase 9 feature gated at the database)', () => {
    expect(sql).toMatch(/check \(quality_label is null\)/);
  });
});
