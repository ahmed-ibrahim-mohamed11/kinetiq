#!/usr/bin/env node
/**
 * Bootstrap the first tenant (organization + branch) and its OWNER account on a Supabase project.
 * Idempotent: re-running with the same e-mail / slug creates nothing twice and never changes
 * an existing user's password.
 *
 * Usage (repo root; SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY from the environment or root .env):
 *   node infrastructure/scripts/bootstrap-owner.mjs --email owner@example.com \
 *        --org "Smart Gym" --branch "Main Branch" [--slug smart-gym] [--timezone Africa/Cairo] \
 *        [--password-file .env]
 *
 * A strong random password is generated for a NEW user and appended ONLY to --password-file
 * (default: the gitignored root .env) as OWNER_BOOTSTRAP_PASSWORD. It is never printed.
 * Change it after the first login (Supabase Auth → user → send password recovery, or the app).
 */
import { createClient } from '@supabase/supabase-js';
import { randomBytes } from 'node:crypto';
import { appendFileSync, existsSync, readFileSync } from 'node:fs';

function loadDotEnv(path) {
  if (!existsSync(path)) return;
  for (const line of readFileSync(path, 'utf8').split(/\r?\n/)) {
    const m = line.match(/^\s*([A-Z_][A-Z0-9_]*)=(.*)$/);
    if (m && process.env[m[1]] === undefined) process.env[m[1]] = m[2].trim();
  }
}
loadDotEnv('.env');

const argv = process.argv.slice(2);
const args = {};
for (let i = 0; i < argv.length; i++) {
  if (!argv[i].startsWith('--')) continue;
  const key = argv[i].slice(2);
  const next = argv[i + 1];
  if (next !== undefined && !next.startsWith('--')) { args[key] = next; i++; } else { args[key] = 'true'; }
}

const email = args.email;
const orgName = args.org ?? 'Smart Gym';
const branchName = args.branch ?? 'Main Branch';
const slug = args.slug ?? orgName.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
const timezone = args.timezone ?? 'Africa/Cairo';
const passwordFile = args['password-file'] ?? '.env';

if (!email) { console.error('--email is required'); process.exit(2); }
const url = process.env.SUPABASE_URL;
const key = process.env.SUPABASE_SERVICE_ROLE_KEY;
if (!url || !key) { console.error('SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set (root .env is read)'); process.exit(2); }

const admin = createClient(url, key, { auth: { persistSession: false, autoRefreshToken: false } });
const fail = (step, error) => { console.error(`${step}: ${error.message ?? error}`); process.exit(1); };

// 1. Auth user (never printed, never re-created).
let userId;
let userCreated = false;
{
  const { data, error } = await admin.auth.admin.listUsers({ perPage: 1000 });
  if (error) fail('listUsers', error);
  const existing = data.users.find((u) => u.email?.toLowerCase() === email.toLowerCase());
  if (existing) {
    userId = existing.id;
  } else {
    const password = randomBytes(18).toString('base64url');
    const res = await admin.auth.admin.createUser({ email, password, email_confirm: true });
    if (res.error) fail('createUser', res.error);
    userId = res.data.user.id;
    userCreated = true;
    appendFileSync(
      passwordFile,
      `\n# Bootstrap owner account (${email}), created ${new Date().toISOString()}. Change it after first login, then delete this line.\nOWNER_BOOTSTRAP_PASSWORD=${password}\n`,
    );
  }
}

// 2. Organization.
let org;
{
  const found = await admin.from('organizations').select('id').eq('slug', slug).maybeSingle();
  if (found.error) fail('select organization', found.error);
  org = found.data;
  if (!org) {
    const ins = await admin.from('organizations').insert({ name: orgName, slug, country_code: 'EG' }).select('id').single();
    if (ins.error) fail('insert organization', ins.error);
    org = ins.data;
  }
}

// 3. Branch.
let branch;
{
  const found = await admin.from('branches').select('id').eq('organization_id', org.id).eq('name', branchName).maybeSingle();
  if (found.error) fail('select branch', found.error);
  branch = found.data;
  if (!branch) {
    const ins = await admin.from('branches').insert({ organization_id: org.id, name: branchName, timezone }).select('id').single();
    if (ins.error) fail('insert branch', ins.error);
    branch = ins.data;
  }
}

// 4. Organization-wide OWNER role (branch_id NULL = every branch).
let roleCreated = false;
{
  const found = await admin.from('staff_roles').select('id').eq('user_id', userId).eq('organization_id', org.id).is('branch_id', null).eq('role', 'owner').maybeSingle();
  if (found.error) fail('select staff_role', found.error);
  if (!found.data) {
    const ins = await admin.from('staff_roles').insert({ user_id: userId, organization_id: org.id, branch_id: null, role: 'owner' });
    if (ins.error) fail('insert staff_role', ins.error);
    roleCreated = true;
  }
}

console.log(JSON.stringify({
  email,
  user_id: userId,
  user_created: userCreated,
  password_written_to: userCreated ? passwordFile : null,
  organization: { id: org.id, slug, name: orgName },
  branch: { id: branch.id, name: branchName, timezone },
  owner_role_created: roleCreated,
}, null, 2));
