#!/usr/bin/env node
/**
 * Live evidence that the bootstrapped owner can authenticate against the hosted Supabase project
 * and that RLS gives them exactly their tenant. Prints JSON without secrets.
 *
 * Usage (repo root; reads root .env):
 *   node tests/integration/live-owner-login.mjs [owner-email]
 * Needs SUPABASE_URL (or NEXT_PUBLIC_SUPABASE_URL), SUPABASE_ANON_KEY (or NEXT_PUBLIC_SUPABASE_ANON_KEY)
 * and OWNER_BOOTSTRAP_PASSWORD (or OWNER_PASSWORD) in the environment or .env.
 */
import { createClient } from '@supabase/supabase-js';
import { existsSync, readFileSync } from 'node:fs';

function loadDotEnv(path) {
  if (!existsSync(path)) return;
  for (const line of readFileSync(path, 'utf8').split(/\r?\n/)) {
    const m = line.match(/^\s*([A-Z_][A-Z0-9_]*)=(.*)$/);
    if (m && process.env[m[1]] === undefined) process.env[m[1]] = m[2].trim();
  }
}
loadDotEnv('.env');

const url = process.env.SUPABASE_URL ?? process.env.NEXT_PUBLIC_SUPABASE_URL;
const anon = process.env.SUPABASE_ANON_KEY ?? process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;
const email = process.argv[2] ?? process.env.OWNER_EMAIL;
const password = process.env.OWNER_BOOTSTRAP_PASSWORD ?? process.env.OWNER_PASSWORD;
if (!url || !anon || !email || !password) {
  console.error('Need SUPABASE_URL, SUPABASE_ANON_KEY, an owner e-mail (argv[2] or OWNER_EMAIL) and OWNER_BOOTSTRAP_PASSWORD');
  process.exit(2);
}

const client = createClient(url, anon, { auth: { persistSession: false, autoRefreshToken: false } });
const signIn = await client.auth.signInWithPassword({ email, password });
if (signIn.error) {
  console.log(JSON.stringify({ ok: false, step: 'signInWithPassword', error: signIn.error.message, status: signIn.error.status ?? null }));
  process.exit(1);
}

const [roles, orgs, branches, members, secrets, rpc] = await Promise.all([
  client.from('staff_roles').select('role, organization_id, branch_id'),
  client.from('organizations').select('id, name, slug'),
  client.from('branches').select('id, name, timezone'),
  client.from('members').select('id', { count: 'exact', head: true }),
  client.from('edge_device_secrets').select('edge_device_id'),
  client.rpc('ingest_events', { p_device_id: '00000000-0000-0000-0000-000000000000', p_batch_id: '00000000-0000-0000-0000-000000000000', p_sent_at: new Date().toISOString(), p_events: [] }),
]);
await client.auth.signOut();

console.log(JSON.stringify({
  ok: true,
  user_id: signIn.data.user.id,
  email: signIn.data.user.email,
  session_expires_in_seconds: signIn.data.session.expires_in,
  staff_roles: roles.data ?? roles.error?.message,
  organizations: orgs.data ?? orgs.error?.message,
  branches: branches.data ?? branches.error?.message,
  members_visible: members.count ?? members.error?.message,
  edge_device_secrets_rows_visible: secrets.data ? secrets.data.length : null,
  edge_device_secrets_error: secrets.error?.message ?? null,
  ingest_events_rpc_as_owner: rpc.error ? `denied: ${rpc.error.message}` : 'ALLOWED (unexpected)',
}, null, 2));
