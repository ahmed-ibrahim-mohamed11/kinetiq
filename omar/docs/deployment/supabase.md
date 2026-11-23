# Deploying the Supabase backend

## 1. Create the project

1. Create a Supabase project. **Region**: decide with legal counsel (see `docs/security/privacy-egypt.md`); default proposal `eu-central-1` (Frankfurt).
2. Enable **Point-in-time recovery** (Pro plan) — this is the primary backup mechanism; daily backups are on by default. Record the retention window in `docs/operations/runbook.md`.
3. Auth settings: disable public sign-ups (`[auth] enable_signup = false`, mirrored in `supabase/config.toml`); enable email confirmations; configure the SMTP provider; set Site URL to the member app and add both app URLs to redirect allow-list. **Keep `[auth.email] enable_signup = true`**: that key toggles the whole email provider, sign-in included — pushing `false` made every password login fail with "Email logins are disabled" (verified 2026-10-30).
4. Enable the `pg_cron` extension (Database → Extensions). Migration 0005 schedules jobs only if it is available; re-run that `do $$` block if you enable it later.

## 1b. Agent access (Supabase MCP)

The repo registers the hosted Supabase MCP server at project scope in `.mcp.json` (project ref `vpwudyxfsbyrbpwqdmew`; features: docs, account, database, debugging, development, functions, branching). It gives Claude Code read/write access to that project, so authenticate it the same way you would hand a teammate the dashboard:

```bash
claude            # in the repo root, in a regular terminal (not the IDE extension)
/mcp              # select "supabase" → Authenticate → complete the browser OAuth flow
```

The OAuth grant is stored per user, not in the repo. Review every schema change the agent proposes before it is applied, and prefer a Supabase **branch** for anything experimental.

## 2. Link and push migrations

```bash
supabase login
supabase link --project-ref vpwudyxfsbyrbpwqdmew   # production project, created 2026-10-30
supabase db push                                   # applies supabase/migrations in order
```

Do **not** run `supabase/seed.sql` against production — it creates a DEMO tenant and a dev device secret. The exercise catalog is a migration (0007), so production has it without the seed.

## 2b. First tenant and owner account

```bash
node infrastructure/scripts/bootstrap-owner.mjs --email owner@example.com --org "Smart Gym" --branch "Main Branch"
```

Creates (idempotently) the organization, its first branch and an organization-wide `owner` staff role for that e-mail. A new auth user gets a random password that is appended to the gitignored root `.env` as `OWNER_BOOTSTRAP_PASSWORD` and never printed; change it after the first login. Everything else (members, equipment, cameras, devices) is created from the admin app by that owner — see `docs/deployment/edge-device.md` for device registration.

Notes verified 2026-10-30 against project `vpwudyxfsbyrbpwqdmew` (eu-west-2, Postgres 17.11):
- `supabase link` and `db push` no longer need the database password; CLI ≥ 2.122 provisions a temporary login role through the Management API ("Initialising login role…").
- `db push --dry-run` listed the six migrations and nothing else (no seed, no roles).
- `supabase config diff` showed the intended changes (signups off, redirect URLs, storage limit) plus `db.major_version`, so `config.toml` now declares 17.
- `infrastructure/scripts/deploy-cloud.ps1` runs sections 2–3 (and the GitHub push) end to end with a stop-on-first-error policy.

## 3. Edge Functions

```bash
supabase secrets set QR_TOKEN_SECRET="$(openssl rand -base64 32)"
supabase secrets set INGEST_CLOCK_SKEW_SECONDS=300
supabase functions deploy ingest-events --use-api --no-verify-jwt   # --use-api bundles server-side (no Docker)
supabase functions deploy device-sync --use-api --no-verify-jwt
supabase functions deploy qr-token --use-api
```

Type-check before deploying: `cd supabase/functions && deno check --config deno.json ingest-events/index.ts qr-token/index.ts device-sync/index.ts notification-dispatch/index.ts`.

The fourth function, `notification-dispatch`, delivers Web Push / e-mail notifications and is called every minute by the `pg_cron` job created in migration 0011 (through `pg_net`, authenticated with the Vault secret `smartgym_dispatch_secret`). `infrastructure/scripts/deploy-cloud.ps1` provisions everything it needs in one run: function secrets from the root `.env` (`NOTIFICATION_DISPATCH_SECRET`, `VAPID_*`, `MEMBER_APP_URL`, optional `RESEND_API_KEY`/`EMAIL_FROM`), the two Vault secrets, the deploy itself and the cron job verification. Details: `docs/deployment/notification-dispatch.md`.

## 4. Verify

- `tests/security/rls.test.ts` against the project with a throwaway service key: `RUN_RLS_TESTS=1 SUPABASE_URL=... SUPABASE_ANON_KEY=... SUPABASE_SERVICE_ROLE_KEY=... npm run test:rls -w tests/security`. Deletes its fixtures.
- `node tests/integration/ingest-smoke.mjs` only against local/staging (it needs the DEMO device).

## 5. Backups and recovery drill

Monthly: restore the latest backup into a scratch project, run `select count(*) from attendance_sessions` and the RLS tests, record the result in `PROGRESS.md` verification log. Phase 8 gate requires one successful drill.

## 6. Generating types

`npm run gen:types -w tests/db` regenerates `packages/supabase-client/src/types/database.ts` from the migrations (executed on an embedded PostgreSQL — no Supabase project needed). Commit the result. `npm run gen:types -w tests/db -- --check` is the drift guard.
