-- ============================================================================
-- Supabase platform shim for plain PostgreSQL.
-- Recreates the parts of a Supabase project that the migrations depend on, so
-- the real migration files can be executed and tested without Docker:
--   * roles anon / authenticated / service_role (service_role bypasses RLS, as on Supabase)
--   * schema auth with users table and auth.uid() / auth.role() reading JWT claims
--     from the same GUCs PostgREST sets (request.jwt.claim.sub / .role)
--   * schema extensions holding pgcrypto + citext (Supabase installs extensions there)
--   * publication supabase_realtime
--   * default search_path "$user", public, extensions
-- Nothing here changes application behaviour; it is test infrastructure only.
-- ============================================================================

do $$
begin
  if not exists (select 1 from pg_roles where rolname = 'anon') then create role anon nologin; end if;
  if not exists (select 1 from pg_roles where rolname = 'authenticated') then create role authenticated nologin; end if;
  if not exists (select 1 from pg_roles where rolname = 'service_role') then create role service_role nologin bypassrls; end if;
end $$;

create schema if not exists extensions;
create extension if not exists pgcrypto schema extensions;
create extension if not exists citext schema extensions;
grant usage on schema extensions to anon, authenticated, service_role;
grant execute on all functions in schema extensions to anon, authenticated, service_role;

alter database postgres set search_path = "$user", public, extensions;
set search_path = "$user", public, extensions;

create schema if not exists auth;
grant usage on schema auth to anon, authenticated, service_role;

create table if not exists auth.users (
  id uuid primary key default gen_random_uuid(),
  email text unique,
  raw_user_meta_data jsonb default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create or replace function auth.uid() returns uuid
language sql stable as $$
  select nullif(current_setting('request.jwt.claim.sub', true), '')::uuid
$$;

create or replace function auth.role() returns text
language sql stable as $$
  select nullif(current_setting('request.jwt.claim.role', true), '')
$$;

create or replace function auth.jwt() returns jsonb
language sql stable as $$
  select coalesce(nullif(current_setting('request.jwt.claims', true), ''), '{}')::jsonb
$$;

grant execute on function auth.uid(), auth.role(), auth.jwt() to anon, authenticated, service_role;

do $$
begin
  if not exists (select 1 from pg_publication where pubname = 'supabase_realtime') then
    create publication supabase_realtime;
  end if;
end $$;

-- PostgREST-like grants: the API roles may use public and (per RLS) touch its tables.
grant usage on schema public to anon, authenticated, service_role;
alter default privileges in schema public grant all on tables to anon, authenticated, service_role;
alter default privileges in schema public grant all on functions to anon, authenticated, service_role;
alter default privileges in schema public grant all on sequences to anon, authenticated, service_role;
