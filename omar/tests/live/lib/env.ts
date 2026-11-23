/**
 * Live-test configuration. Reads the gitignored root .env (never overrides variables that are
 * already set in the process) and exposes typed values. Secret VALUES are never logged: error
 * messages name missing variables only, and redact() scrubs known secrets from any text that is
 * written to an artifact.
 */
import { existsSync, readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

export const REPO_ROOT = fileURLToPath(new URL('../../../', import.meta.url));
export const LIVE_DIR = fileURLToPath(new URL('../', import.meta.url));
export const ARTIFACTS_DIR = fileURLToPath(new URL('../artifacts/', import.meta.url));

const DEFAULT_ADMIN_APP_URL = 'https://smartgym-admin.vercel.app';
const DEFAULT_MEMBER_APP_URL = 'https://smartgym-member.vercel.app';

export interface LiveEnv {
  supabaseUrl: string;
  functionsUrl: string;
  anonKey: string;
  serviceRoleKey: string;
  adminAppUrl: string;
  memberAppUrl: string;
  /** Web Push application server key (base64url, 65-byte P-256 point). Optional: only the push step needs it. */
  vapidPublicKey: string | null;
}

let loaded = false;

/** Minimal dotenv: KEY=VALUE, optional surrounding quotes, # comments. Existing env wins. */
export function loadDotEnv(path = `${REPO_ROOT}.env`): void {
  if (loaded) return;
  loaded = true;
  if (!existsSync(path)) return;
  for (const raw of readFileSync(path, 'utf8').split(/\r?\n/)) {
    const line = raw.trim();
    if (!line || line.startsWith('#')) continue;
    const m = /^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$/.exec(line);
    if (!m) continue;
    const key = m[1]!;
    let value = m[2] ?? '';
    if ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'"))) {
      value = value.slice(1, -1);
    } else {
      const hash = value.indexOf(' #');
      if (hash >= 0) value = value.slice(0, hash).trim();
    }
    if (process.env[key] === undefined) process.env[key] = value;
  }
}

function pick(...names: string[]): string | undefined {
  for (const n of names) {
    const v = process.env[n];
    if (v !== undefined && v.trim() !== '') return v.trim();
  }
  return undefined;
}

let cached: LiveEnv | null = null;

export function liveEnv(): LiveEnv {
  if (cached) return cached;
  loadDotEnv();
  const supabaseUrl = pick('SUPABASE_URL', 'NEXT_PUBLIC_SUPABASE_URL');
  const anonKey = pick('SUPABASE_ANON_KEY', 'NEXT_PUBLIC_SUPABASE_ANON_KEY');
  const serviceRoleKey = pick('SUPABASE_SERVICE_ROLE_KEY');
  const missing = [
    !supabaseUrl && 'SUPABASE_URL',
    !anonKey && 'SUPABASE_ANON_KEY',
    !serviceRoleKey && 'SUPABASE_SERVICE_ROLE_KEY',
  ].filter(Boolean);
  if (missing.length > 0) {
    throw new Error(`live tests need ${missing.join(', ')} in the root .env or the environment (values are never printed)`);
  }
  const base = supabaseUrl!.replace(/\/+$/, '');
  if (!/^https:\/\//.test(base) && process.env.LIVE_ALLOW_HTTP !== '1') {
    throw new Error('SUPABASE_URL must be https:// for the live suite (set LIVE_ALLOW_HTTP=1 for a local stack)');
  }
  cached = {
    supabaseUrl: base,
    functionsUrl: `${base}/functions/v1`,
    anonKey: anonKey!,
    serviceRoleKey: serviceRoleKey!,
    adminAppUrl: (pick('ADMIN_APP_URL') ?? DEFAULT_ADMIN_APP_URL).replace(/\/+$/, ''),
    memberAppUrl: (pick('MEMBER_APP_URL') ?? DEFAULT_MEMBER_APP_URL).replace(/\/+$/, ''),
    vapidPublicKey: pick('NEXT_PUBLIC_VAPID_PUBLIC_KEY', 'VAPID_PUBLIC_KEY') ?? null,
  };
  return cached;
}

/** Values that must never reach a log line or an artifact. */
const extraSecrets = new Set<string>();

export function registerSecret(value: string | null | undefined): void {
  if (value && value.length >= 8) extraSecrets.add(value);
}

export function redact(text: string): string {
  const env = cached;
  const secrets = [...extraSecrets];
  if (env) secrets.push(env.anonKey, env.serviceRoleKey);
  let out = text;
  for (const s of secrets) if (s) out = out.split(s).join('***');
  // Bearer tokens / JWTs that slipped through.
  return out.replace(/eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}/g, '***jwt***');
}
