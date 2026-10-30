# Deploying the Supabase backend

## 1. Create the project

1. Create a Supabase project. **Region**: decide with legal counsel (see `docs/security/privacy-egypt.md`); default proposal `eu-central-1` (Frankfurt).
2. Enable **Point-in-time recovery** (Pro plan) — this is the primary backup mechanism; daily backups are on by default. Record the retention window in `docs/operations/runbook.md`.
3. Auth settings: disable public sign-ups (`enable_signup = false`, mirrored in `supabase/config.toml`); enable email confirmations; configure the SMTP provider; set Site URL to the member app and add both app URLs to redirect allow-list.
4. Enable the `pg_cron` extension (Database → Extensions). Migration 0005 schedules jobs only if it is available; re-run that `do $$` block if you enable it later.

## 2. Link and push migrations

```bash
supabase login
supabase link --project-ref <ref>
supabase db push                 # applies supabase/migrations in order
```

Do **not** run `supabase/seed.sql` against production — it creates a DEMO tenant and a dev device secret. For the first real tenant run the onboarding SQL in `docs/deployment/edge-device.md`.

## 3. Edge Functions

```bash
supabase secrets set QR_TOKEN_SECRET="$(openssl rand -base64 32)"
supabase secrets set INGEST_CLOCK_SKEW_SECONDS=300
supabase functions deploy ingest-events --no-verify-jwt
supabase functions deploy device-sync --no-verify-jwt
supabase functions deploy qr-token
```

Type-check before deploying: `cd supabase/functions && deno check --config deno.json ingest-events/index.ts qr-token/index.ts device-sync/index.ts`.

## 4. Verify

- `tests/security/rls.test.ts` against the project with a throwaway service key: `RUN_RLS_TESTS=1 SUPABASE_URL=... SUPABASE_ANON_KEY=... SUPABASE_SERVICE_ROLE_KEY=... npm run test:rls -w tests/security`. Deletes its fixtures.
- `node tests/integration/ingest-smoke.mjs` only against local/staging (it needs the DEMO device).

## 5. Backups and recovery drill

Monthly: restore the latest backup into a scratch project, run `select count(*) from attendance_sessions` and the RLS tests, record the result in `PROGRESS.md` verification log. Phase 8 gate requires one successful drill.

## 6. Generating types

`npm run gen:types -w tests/db` regenerates `packages/supabase-client/src/types/database.ts` from the migrations (executed on an embedded PostgreSQL — no Supabase project needed). Commit the result. `npm run gen:types -w tests/db -- --check` is the drift guard.
