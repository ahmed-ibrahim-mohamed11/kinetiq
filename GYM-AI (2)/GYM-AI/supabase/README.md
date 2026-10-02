# supabase/ — cloud data plane (Phase 3)

## Layout
- `migrations/20261002000001_core_schema.sql` — all MASTER_PROMPT §13 tables + indexes + heartbeat retention function
- `migrations/20261002000002_rls.sql` — RLS on every table, organization-scoped via `profiles`; `auth_org_id()` / `auth_role()` helpers
- `seed.sql` — two organizations (so isolation is testable), one gym/machine/device/session each, demo reps + one open alert, the active `rep-seg-core-hsmm 0.2.0` model version
- `tests/test_rls.py` — pytest RLS isolation suite (skips unless `SUPABASE_DB_URL` is set)

## Applying (needs the owner's Supabase project)
1. Create a free project at supabase.com → copy URL, anon key, service-role key, DB password into `.env` (template: `.env.example`).
2. Either use the Supabase CLI (`supabase link`, `supabase db push`) or run the two migration files then `seed.sql` in the SQL editor **in order**.
3. Run the RLS tests:
   ```powershell
   $env:SUPABASE_DB_URL="postgresql://postgres:...@db.<project>.supabase.co:5432/postgres"
   .venv\Scripts\python.exe -m pytest supabase/tests -q
   ```

## Security model (MASTER_PROMPT §14)
- Dashboard users: Supabase Auth; every table RLS-scoped by organization; `admin` writes config, `coach` reviews alerts/form results, `viewer` reads.
- Edge devices: NO direct table access. They call a secured ingestion endpoint (Phase 6: Edge Function using the service role after validating `devices.api_key_hash`). Raw device keys are never stored.
- Service-role key: server-side only. Anon key + RLS is all the browser ever gets.
- Evidence files: private storage bucket, signed URLs only (bucket created at Phase 6).
- `device_heartbeats` retention: call `prune_device_heartbeats('14 days')` from a scheduled job (pg_cron or an Edge Function schedule).

## Event contract
`shared/event-schemas/v1/events.schema.json` is the source of truth (v1.0); mirrors:
`shared/event-schemas/python/gym_ai_events` (pydantic) and `shared/event-schemas/typescript/events.ts`.
Sync enforced by `ai/tests/test_event_schema.py`.
