-- ============================================================================
-- Privacy, audit, consent and biometric-lifecycle hardening (migration 0009)
-- ----------------------------------------------------------------------------
-- Behaviour changes (applied migrations 0001-0007 are immutable, so every
-- change here is a create-or-replace / alter / drop+create):
--   1. RLS helpers: has_org_role only counts ORG-WIDE roles (branch_id is null);
--      internal helpers are no longer executable by anon.
--   2. Tenant consistency: members.organization_id and staff_roles.organization_id
--      must match the branch's organization.
--   3. staff_roles: branch admins manage only non-admin roles of their branch;
--      only org-wide owners grant or remove the auditor role;
--      admins cannot remove or demote owners; the last owner cannot be removed.
--   4. members.user_id is linked only by the service role or through
--      link_member_account (front-desk activation RPC), never by a direct UPDATE.
--      Id-resolution helpers (session_member, branch_org, ...) answer only for
--      the caller's own tenant rows.
--   5. Audit: organization and branch resolved per table, device secrets are
--      audited without the raw secret, existing copies scrubbed, org backfilled;
--      branch-scoped owners/auditors see only their branch's rows.
--   6. Consent records: immutable except granted -> withdrawn / expired;
--      captured_by_staff_id = auth.uid(); informed_acknowledged_at column;
--      archived (5 years) when deleted, including on member deletion.
--   7. Consent withdrawal: cctv_workout_tracking ends the active session as
--      abandoned (no report, no notification); biometric withdrawal/expiry
--      revokes enrollments; hourly expiry sweep (pg_cron when available).
--   8. Biometric references: browser can only read them and revoke through an
--      RPC; revocations are permanent; tombstones survive member/enrollment
--      deletion; membership cancelled/expired revokes.
--   9. device_sync_snapshot (contract C6): VOLATILE (it updates last_seen_at),
--      no member roster, consents limited to biometric + CCTV.
--  10. retention_policies entries for the two new tables.
-- The file is idempotent: re-running it is safe.
-- ============================================================================

-- ----------------------------------------------------------------------------
-- 1. RLS helpers
-- ----------------------------------------------------------------------------
-- Organization-level powers (manage org, all branches, staff roles, audit log)
-- belong to ORG-WIDE roles only. A branch-scoped row (branch_id not null) grants
-- powers in that branch through has_branch_role, never across the organization.
create or replace function public.has_org_role(p_org_id uuid, variadic p_roles public.staff_role[])
returns boolean language sql stable security definer set search_path = public as $$
  select exists (
    select 1 from public.staff_roles sr
    where sr.user_id = auth.uid()
      and sr.organization_id = p_org_id
      and sr.branch_id is null
      and sr.role = any (p_roles)
  )
$$;

-- Roles the caller holds for a branch. The branch's organization is read
-- directly (not through branch_org, which is caller-gated below).
create or replace function public.staff_roles_for_branch(p_branch_id uuid)
returns setof public.staff_role language sql stable security definer set search_path = public as $$
  select sr.role
    from public.staff_roles sr
    join public.branches b on b.id = p_branch_id and b.organization_id = sr.organization_id
   where sr.user_id = auth.uid()
     and (sr.branch_id is null or sr.branch_id = p_branch_id)
$$;

-- The id-resolution helpers below must stay executable by authenticated (RLS
-- policies call them with the caller's privileges), so they are reachable as
-- /rest/v1/rpc/<name>. To stop them acting as a cross-tenant oracle (map a
-- leaked session/usage/set/branch UUID to another tenant's member, branch or
-- organization), they answer only for rows the caller may see anyway:
--   * no auth.uid() (service role, ingest, cron, migrations): unrestricted;
--   * otherwise only the session's own member or staff of the session's branch
--     (branch_org: staff or members of that organization).
-- Every policy that uses them already requires one of those conditions, so
-- policy results are unchanged.
create or replace function public._caller_sees_session(p_member_id uuid, p_branch_id uuid)
returns boolean language sql stable security definer set search_path = public as $$
  select auth.uid() is null
      or exists (select 1 from public.members m where m.id = p_member_id and m.user_id = auth.uid())
      or exists (select 1 from public.staff_roles sr
                   join public.branches b on b.id = p_branch_id and b.organization_id = sr.organization_id
                  where sr.user_id = auth.uid()
                    and (sr.branch_id is null or sr.branch_id = p_branch_id))
$$;
revoke all on function public._caller_sees_session(uuid, uuid) from public, anon, authenticated;

create or replace function public.branch_org(p_branch_id uuid)
returns uuid language sql stable security definer set search_path = public as $$
  select b.organization_id from public.branches b
   where b.id = p_branch_id
     and (auth.uid() is null
          or exists (select 1 from public.staff_roles sr where sr.user_id = auth.uid() and sr.organization_id = b.organization_id)
          or exists (select 1 from public.members m where m.user_id = auth.uid() and m.organization_id = b.organization_id))
$$;

create or replace function public.session_branch(p_session_id uuid)
returns uuid language sql stable security definer set search_path = public as $$
  select s.branch_id from public.attendance_sessions s
   where s.id = p_session_id and public._caller_sees_session(s.member_id, s.branch_id)
$$;

create or replace function public.session_member(p_session_id uuid)
returns uuid language sql stable security definer set search_path = public as $$
  select s.member_id from public.attendance_sessions s
   where s.id = p_session_id and public._caller_sees_session(s.member_id, s.branch_id)
$$;

-- activity_session and set_session resolve through usage_session, so they are gated too.
create or replace function public.usage_session(p_usage_id uuid)
returns uuid language sql stable security definer set search_path = public as $$
  select u.attendance_session_id
    from public.equipment_usage_sessions u
    join public.attendance_sessions s on s.id = u.attendance_session_id
   where u.id = p_usage_id and public._caller_sees_session(s.member_id, s.branch_id)
$$;

-- Policies run with the caller's privileges, and anon has no table access, so
-- anon never needs these helpers. Removing EXECUTE closes the id-resolution
-- oracle (/rest/v1/rpc/session_member etc.) for the anon key; the gating above
-- closes it for signed-in users of other tenants.
revoke execute on function
  public.current_member_id(), public.branch_org(uuid), public.staff_roles_for_branch(uuid),
  public.has_branch_role(uuid, public.staff_role[]), public.is_branch_staff(uuid),
  public.has_org_role(uuid, public.staff_role[]), public.is_org_staff(uuid),
  public.session_branch(uuid), public.session_member(uuid), public.usage_session(uuid),
  public.activity_session(uuid), public.set_session(uuid)
from public, anon;
grant execute on function
  public.current_member_id(), public.branch_org(uuid), public.staff_roles_for_branch(uuid),
  public.has_branch_role(uuid, public.staff_role[]), public.is_branch_staff(uuid),
  public.has_org_role(uuid, public.staff_role[]), public.is_org_staff(uuid),
  public.session_branch(uuid), public.session_member(uuid), public.usage_session(uuid),
  public.activity_session(uuid), public.set_session(uuid)
to authenticated, service_role;

-- ----------------------------------------------------------------------------
-- 2. Tenant consistency: a row's organization is the organization of its branch
-- ----------------------------------------------------------------------------
create or replace function public._enforce_branch_org()
returns trigger language plpgsql set search_path = public as $$
begin
  if new.branch_id is not null and new.organization_id is distinct from public.branch_org(new.branch_id) then
    raise exception 'branch % does not belong to organization %', new.branch_id, new.organization_id
      using errcode = '42501';
  end if;
  return new;
end $$;

-- Repair rows written before the guard existed.
do $$
declare r record;
begin
  for r in
    select m.id, b.organization_id as org
      from public.members m join public.branches b on b.id = m.branch_id
     where m.organization_id is distinct from b.organization_id
  loop
    begin
      update public.members set organization_id = r.org where id = r.id;
    exception when unique_violation then
      raise warning 'member % has an organization that does not match its branch and its member_code clashes in the branch organization; fix it manually', r.id;
    end;
  end loop;
  -- A branch-scoped role pointing at another organization's branch is an invalid grant.
  delete from public.staff_roles sr using public.branches b
   where b.id = sr.branch_id and b.organization_id is distinct from sr.organization_id;
end $$;

drop trigger if exists trg_members_branch_org on public.members;
create trigger trg_members_branch_org before insert or update of organization_id, branch_id on public.members
  for each row execute function public._enforce_branch_org();
drop trigger if exists trg_staff_roles_branch_org on public.staff_roles;
create trigger trg_staff_roles_branch_org before insert or update of organization_id, branch_id on public.staff_roles
  for each row execute function public._enforce_branch_org();

-- ----------------------------------------------------------------------------
-- 3. staff_roles management
-- ----------------------------------------------------------------------------
-- Who may create / change / remove a role row (evaluated on the old AND the new row):
--   * org-wide owner: any role;
--   * org-wide admin: any role except owner and auditor (auditors read the audit
--     log and the consent archive, which admins do not; an admin must not be
--     able to grant that to themselves);
--   * branch owner/admin: only non-owner, non-admin, non-auditor roles of their own branch.
create or replace function public._can_manage_staff_role(p_org_id uuid, p_branch_id uuid, p_role public.staff_role)
returns boolean language sql stable security definer set search_path = public as $$
  select public.has_org_role(p_org_id, 'owner')
      or (public.has_org_role(p_org_id, 'admin')
          and p_role not in ('owner', 'auditor'))
      or (p_branch_id is not null
          and p_role not in ('owner', 'admin', 'auditor')
          and exists (select 1 from public.branches b where b.id = p_branch_id and b.organization_id = p_org_id)
          and public.has_branch_role(p_branch_id, 'owner', 'admin'))
$$;
revoke execute on function public._can_manage_staff_role(uuid, uuid, public.staff_role) from public, anon;
grant execute on function public._can_manage_staff_role(uuid, uuid, public.staff_role) to authenticated, service_role;

drop policy if exists staff_roles_manage on public.staff_roles;
drop policy if exists staff_roles_insert on public.staff_roles;
drop policy if exists staff_roles_update on public.staff_roles;
drop policy if exists staff_roles_delete on public.staff_roles;
create policy staff_roles_insert on public.staff_roles for insert to authenticated
  with check (public._can_manage_staff_role(organization_id, branch_id, role));
create policy staff_roles_update on public.staff_roles for update to authenticated
  using (public._can_manage_staff_role(organization_id, branch_id, role))
  with check (public._can_manage_staff_role(organization_id, branch_id, role));
create policy staff_roles_delete on public.staff_roles for delete to authenticated
  using (public._can_manage_staff_role(organization_id, branch_id, role));

-- An organization must keep at least one org-wide owner. Applies to browser
-- callers; cascades (organization or auth user deleted) and the service role
-- are not blocked.
create or replace function public._guard_last_owner()
returns trigger language plpgsql set search_path = public as $$
begin
  if current_user not in ('authenticated', 'anon') then
    return coalesce(new, old);
  end if;
  if old.role = 'owner' and old.branch_id is null
     and (tg_op = 'DELETE'
          or new.role <> 'owner' or new.branch_id is not null
          or new.organization_id <> old.organization_id or new.user_id <> old.user_id)
     and not exists (
       select 1 from public.staff_roles o
        where o.organization_id = old.organization_id and o.role = 'owner'
          and o.branch_id is null and o.id <> old.id
     )
  then
    raise exception 'an organization must keep at least one owner' using errcode = '42501';
  end if;
  return coalesce(new, old);
end $$;
drop trigger if exists trg_staff_roles_last_owner on public.staff_roles;
create trigger trg_staff_roles_last_owner before update or delete on public.staff_roles
  for each row execute function public._guard_last_owner();

-- ----------------------------------------------------------------------------
-- 4. members.user_id is linked by the activation flow (service role), never by staff
-- ----------------------------------------------------------------------------
create or replace function public._guard_member_user_link()
returns trigger language plpgsql set search_path = public as $$
begin
  if current_user in ('authenticated', 'anon')
     and ((tg_op = 'INSERT' and new.user_id is not null)
          or (tg_op = 'UPDATE' and new.user_id is distinct from old.user_id)) then
    raise exception 'members.user_id can only be linked by the account activation flow' using errcode = '42501';
  end if;
  return new;
end $$;
drop trigger if exists trg_members_user_link on public.members;
create trigger trg_members_user_link before insert or update of user_id on public.members
  for each row execute function public._guard_member_user_link();

-- The account activation flow: front-desk staff link a member row to the auth
-- account that uses the given email (admin-web invites the email with the
-- service role first, then calls this). Runs as the owner, so it passes the
-- guard above; the members UPDATE is recorded by audit_members.
--   P0002 member or account not found; 42501 caller not staff of the member's
--   branch, or the account belongs to staff; 23505 member already linked to
--   another account, or the account already linked to another member;
--   21000 more than one account uses the email; 22023 empty email.
-- Re-linking the same pair is a no-op that returns the user id.
create or replace function public.link_member_account(p_member_id uuid, p_email text)
returns uuid language plpgsql security definer set search_path = public as $$
declare
  v_email  text := lower(btrim(coalesce(p_email, '')));
  v_branch uuid;
  v_linked uuid;
  v_users  uuid[];
  v_user   uuid;
begin
  if v_email = '' then
    raise exception 'an email address is required' using errcode = '22023';
  end if;
  select m.branch_id, m.user_id into v_branch, v_linked from public.members m where m.id = p_member_id for update;
  if not found then
    raise exception 'member not found' using errcode = 'P0002';
  end if;
  -- No auth.uid(): the service role (anon has no EXECUTE).
  if auth.uid() is not null and not public.has_branch_role(v_branch, 'owner', 'admin', 'manager', 'front_desk') then
    raise exception 'not authorized for this branch' using errcode = '42501';
  end if;

  select array_agg(u.id) into v_users from auth.users u where lower(u.email::text) = v_email;
  if v_users is null then
    raise exception 'no account exists for this email' using errcode = 'P0002';
  end if;
  if cardinality(v_users) > 1 then
    raise exception 'more than one account uses this email' using errcode = '21000';
  end if;
  v_user := v_users[1];

  if v_linked = v_user then
    return v_user;
  end if;
  if v_linked is not null then
    raise exception 'member is already linked to another account' using errcode = '23505';
  end if;
  if exists (select 1 from public.members m where m.user_id = v_user) then
    raise exception 'this account is already linked to a member' using errcode = '23505';
  end if;
  if exists (select 1 from public.staff_roles sr where sr.user_id = v_user) then
    raise exception 'staff accounts cannot be linked to a member' using errcode = '42501';
  end if;

  update public.members set user_id = v_user where id = p_member_id;
  return v_user;
end $$;
revoke all on function public.link_member_account(uuid, text) from public, anon;
grant execute on function public.link_member_account(uuid, text) to authenticated, service_role;

-- ----------------------------------------------------------------------------
-- 5. New tables: consent archive and biometric revocation tombstones
-- ----------------------------------------------------------------------------
alter table public.consent_records add column if not exists informed_acknowledged_at timestamptz;
comment on column public.consent_records.informed_acknowledged_at is
  'Server time at which the capturing staff confirmed the member was informed (the consent form checkbox).';

-- Legal evidence of consent outlives the member row: copied here before a
-- consent record is deleted (directly or by member deletion). Kept 5 years.
create table if not exists public.consent_records_archive (
  id                        uuid primary key,          -- original consent_records.id
  member_id                 uuid not null,             -- no FK: the member is usually gone
  organization_id           uuid,
  branch_id                 uuid,
  member_code               text,
  member_full_name          text,
  consent_type              public.consent_type not null,
  status                    public.consent_status not null,
  policy_version            text not null,
  granted_at                timestamptz not null,
  withdrawn_at              timestamptz,
  expires_at                timestamptz,
  captured_by_staff_id      uuid,
  ip_address                inet,
  informed_acknowledged_at  timestamptz,
  created_at                timestamptz not null,
  archived_at               timestamptz not null default now(),
  archive_reason            text not null check (archive_reason in ('member_deleted', 'record_deleted')),
  archived_by               uuid
);
create index if not exists idx_consent_archive_org on public.consent_records_archive(organization_id, archived_at desc);
alter table public.consent_records_archive enable row level security;
revoke all on public.consent_records_archive from anon, authenticated;
grant select on public.consent_records_archive to authenticated;
drop policy if exists consent_archive_owner_auditor on public.consent_records_archive;
create policy consent_archive_owner_auditor on public.consent_records_archive for select to authenticated
  using (organization_id is not null
         and (public.has_org_role(organization_id, 'owner', 'auditor')
              or (branch_id is not null and public.has_branch_role(branch_id, 'owner', 'auditor'))));

-- Template references the device must delete although no enrollment row is
-- left (member deleted, enrollment row deleted). Merged into revoked_template_refs.
create table if not exists public.biometric_revocations (
  id              uuid primary key default gen_random_uuid(),
  edge_device_id  uuid not null references public.edge_devices(id) on delete cascade,
  template_ref    text not null,
  member_id       uuid,                                 -- no FK: the member may be gone
  enrollment_id   uuid,
  reason          text not null check (reason in ('member_deleted', 'enrollment_deleted')),
  revoked_at      timestamptz not null default now(),
  unique (edge_device_id, template_ref)
);
alter table public.biometric_revocations enable row level security;
revoke all on public.biometric_revocations from anon, authenticated;
grant select on public.biometric_revocations to authenticated;
drop policy if exists biometric_revocations_admin on public.biometric_revocations;
create policy biometric_revocations_admin on public.biometric_revocations for select to authenticated
  using (exists (select 1 from public.edge_devices d where d.id = biometric_revocations.edge_device_id
                 and public.has_branch_role(d.branch_id, 'owner', 'admin', 'auditor')));

insert into public.retention_policies (table_name, keep_days, timestamp_col) values
  ('consent_records_archive', 1825, 'archived_at'),
  ('biometric_revocations', 365, 'revoked_at')
on conflict (table_name) do nothing;

-- ----------------------------------------------------------------------------
-- 6. Audit: per-table organization/branch, no secrets, branch-scoped visibility
-- ----------------------------------------------------------------------------
alter table public.audit_logs add column if not exists branch_id uuid;
create index if not exists idx_audit_org_time on public.audit_logs(organization_id, occurred_at desc);

-- Organization and branch an audited row belongs to. The branch is the source
-- of truth for the organization (a row's own organization_id is used only when
-- no branch can be resolved, e.g. org-wide staff roles).
create or replace function public._audit_scope(p_table text, p_row jsonb, out o_org uuid, out o_branch uuid)
language plpgsql stable security definer set search_path = public as $$
declare v_org uuid;
begin
  if p_row is null then return; end if;
  o_org := nullif(p_row->>'organization_id', '')::uuid;
  o_branch := nullif(p_row->>'branch_id', '')::uuid;
  if o_branch is null and p_row ? 'member_id' then
    select m.branch_id, m.organization_id into o_branch, v_org from public.members m where m.id = (p_row->>'member_id')::uuid;
    o_org := coalesce(o_org, v_org);
  end if;
  if o_branch is null and p_row ? 'edge_device_id' then
    select d.branch_id into o_branch from public.edge_devices d where d.id = (p_row->>'edge_device_id')::uuid;
  end if;
  if o_branch is null and p_row ? 'camera_id' then
    select c.branch_id into o_branch from public.cameras c where c.id = (p_row->>'camera_id')::uuid;
  end if;
  if o_branch is null and p_table = 'consent_records' then
    select a.branch_id, a.organization_id into o_branch, v_org from public.consent_records_archive a where a.id = (p_row->>'id')::uuid;
    o_org := coalesce(o_org, v_org);
  end if;
  if o_branch is not null then
    -- Read directly: branch_org is gated by the caller's tenant.
    o_org := coalesce((select b.organization_id from public.branches b where b.id = o_branch), o_org);
  end if;
end $$;

create or replace function public._audit_write(p_table text, p_action text, p_old jsonb, p_new jsonb)
returns void language plpgsql security definer set search_path = public as $$
declare s record;
begin
  select * into s from public._audit_scope(p_table, coalesce(p_new, p_old));
  insert into public.audit_logs (actor_user_id, actor_role, action, table_name, row_id, organization_id, branch_id, old_data, new_data)
  values (
    auth.uid(),
    coalesce(current_setting('request.jwt.claim.role', true), current_user),
    p_action,
    p_table,
    coalesce(p_new->>'id', p_old->>'id'),
    s.o_org,
    s.o_branch,
    p_old,
    p_new
  );
end $$;

-- Generic audit trigger (members, consent_records, biometric_enrollments, staff_roles,
-- cameras, edge_devices). None of these tables holds secrets or templates.
create or replace function public.audit_row_change()
returns trigger language plpgsql security definer set search_path = public as $$
begin
  perform public._audit_write(
    tg_table_name,
    tg_op,
    case when tg_op in ('UPDATE', 'DELETE') then to_jsonb(old) end,
    case when tg_op in ('INSERT', 'UPDATE') then to_jsonb(new) end
  );
  return coalesce(new, old);
end $$;

-- Device secrets: whitelisted columns only. The raw secret and its full hash are
-- never copied into the audit log; a short fingerprint correlates rotations.
create or replace function public._device_secret_audit_row(p_id uuid, p_device uuid, p_hash text, p_created timestamptz, p_retired timestamptz)
returns jsonb language sql immutable set search_path = public as $$
  select jsonb_build_object('id', p_id, 'edge_device_id', p_device, 'secret_fingerprint', left(p_hash, 12),
                            'created_at', p_created, 'retired_at', p_retired)
$$;

create or replace function public.audit_device_secret_change()
returns trigger language plpgsql security definer set search_path = public as $$
begin
  perform public._audit_write(
    tg_table_name,
    tg_op,
    case when tg_op in ('UPDATE', 'DELETE') then public._device_secret_audit_row(old.id, old.edge_device_id, old.secret_hash, old.created_at, old.retired_at) end,
    case when tg_op in ('INSERT', 'UPDATE') then public._device_secret_audit_row(new.id, new.edge_device_id, new.secret_hash, new.created_at, new.retired_at) end
  );
  return coalesce(new, old);
end $$;
drop trigger if exists audit_device_secrets on public.edge_device_secrets;
create trigger audit_device_secrets after insert or update or delete on public.edge_device_secrets
  for each row execute function public.audit_device_secret_change();

-- Scrub raw secrets (and full hashes) already copied into the audit log.
update public.audit_logs
   set old_data = case when old_data is null then null
                       else public._device_secret_audit_row((old_data->>'id')::uuid, (old_data->>'edge_device_id')::uuid,
                              coalesce(old_data->>'secret_fingerprint', old_data->>'secret_hash'),
                              (old_data->>'created_at')::timestamptz, (old_data->>'retired_at')::timestamptz) end,
       new_data = case when new_data is null then null
                       else public._device_secret_audit_row((new_data->>'id')::uuid, (new_data->>'edge_device_id')::uuid,
                              coalesce(new_data->>'secret_fingerprint', new_data->>'secret_hash'),
                              (new_data->>'created_at')::timestamptz, (new_data->>'retired_at')::timestamptz) end
 where table_name = 'edge_device_secrets'
   and (old_data ? 'secret' or new_data ? 'secret' or old_data ? 'secret_hash' or new_data ? 'secret_hash');

-- Backfill organization/branch for existing audit rows (also re-homes rows whose
-- organization_id was taken from a forged members.organization_id).
update public.audit_logs a
   set organization_id = s.o_org, branch_id = s.o_branch
  from public.audit_logs l
  cross join lateral public._audit_scope(l.table_name, coalesce(l.new_data, l.old_data)) s
 where l.id = a.id
   and l.table_name in ('members', 'consent_records', 'biometric_enrollments', 'staff_roles', 'cameras', 'edge_devices', 'edge_device_secrets')
   and (s.o_org is not null or s.o_branch is not null)
   and (a.organization_id is distinct from s.o_org or a.branch_id is distinct from s.o_branch);

-- Org-wide owners/auditors read the whole organization; branch-scoped owners/
-- auditors read only rows of their branch.
drop policy if exists audit_owner_auditor on public.audit_logs;
create policy audit_owner_auditor on public.audit_logs for select to authenticated
  using (organization_id is not null
         and (public.has_org_role(organization_id, 'owner', 'auditor')
              or (branch_id is not null and public.has_branch_role(branch_id, 'owner', 'auditor'))));

revoke all on function public._audit_scope(text, jsonb) from public, anon, authenticated;
revoke all on function public._audit_write(text, text, jsonb, jsonb) from public, anon, authenticated;
revoke all on function public._device_secret_audit_row(uuid, uuid, text, timestamptz, timestamptz) from public, anon, authenticated;

-- ----------------------------------------------------------------------------
-- 7. Consent records: capture rules and immutability
-- ----------------------------------------------------------------------------
-- Browser inserts: the capturing staff is the caller, times are server times, a
-- new record is always 'granted'.
create or replace function public._consent_before_insert()
returns trigger language plpgsql set search_path = public as $$
begin
  if current_user in ('authenticated', 'anon') then
    if new.status <> 'granted' or new.withdrawn_at is not null then
      raise exception 'a new consent record must be granted' using errcode = '22023';
    end if;
    new.captured_by_staff_id := auth.uid();
    new.granted_at := now();
    new.created_at := now();
    if new.informed_acknowledged_at is not null then
      new.informed_acknowledged_at := now();
    end if;
  end if;
  return new;
end $$;
drop trigger if exists trg_consent_before_insert on public.consent_records;
create trigger trg_consent_before_insert before insert on public.consent_records
  for each row execute function public._consent_before_insert();

-- History is never edited, by anyone: the only transitions are granted -> withdrawn
-- (withdrawn_at = server time) and granted -> expired once expires_at has passed.
create or replace function public._consent_guard_update()
returns trigger language plpgsql set search_path = public as $$
begin
  if (to_jsonb(new) - 'status' - 'withdrawn_at') is distinct from (to_jsonb(old) - 'status' - 'withdrawn_at') then
    raise exception 'consent records are immutable: only a withdrawal may be recorded' using errcode = '42501';
  end if;
  if new.status = old.status then
    if new.withdrawn_at is distinct from old.withdrawn_at then
      raise exception 'consent records are immutable: withdrawn_at cannot be changed' using errcode = '42501';
    end if;
    return new; -- no-op update
  end if;
  if old.status = 'granted' and new.status = 'withdrawn' then
    new.withdrawn_at := now();
    return new;
  end if;
  if old.status = 'granted' and new.status = 'expired' and new.withdrawn_at is null
     and old.expires_at is not null and old.expires_at <= now() then
    return new;
  end if;
  raise exception 'consent status cannot change from % to %', old.status, new.status using errcode = '42501';
end $$;
drop trigger if exists trg_consent_guard_update on public.consent_records;
create trigger trg_consent_guard_update before update on public.consent_records
  for each row execute function public._consent_guard_update();

drop policy if exists consent_staff_insert on public.consent_records;
create policy consent_staff_insert on public.consent_records for insert to authenticated
  with check (captured_by_staff_id = auth.uid()
              and exists (select 1 from public.members m where m.id = consent_records.member_id
                          and public.has_branch_role(m.branch_id, 'owner', 'admin', 'manager', 'front_desk')));

-- Archive a consent record before it is deleted.
create or replace function public.on_consent_deleted()
returns trigger language plpgsql security definer set search_path = public as $$
begin
  insert into public.consent_records_archive (id, member_id, organization_id, branch_id, member_code, member_full_name,
    consent_type, status, policy_version, granted_at, withdrawn_at, expires_at, captured_by_staff_id, ip_address,
    informed_acknowledged_at, created_at, archive_reason, archived_by)
  select old.id, old.member_id, m.organization_id, m.branch_id, m.member_code::text, m.full_name,
         old.consent_type, old.status, old.policy_version, old.granted_at, old.withdrawn_at, old.expires_at,
         old.captured_by_staff_id, old.ip_address, old.informed_acknowledged_at, old.created_at, 'record_deleted', auth.uid()
    from (select 1) one
    left join public.members m on m.id = old.member_id
  on conflict (id) do nothing; -- already archived by on_member_deleted
  return old;
end $$;
drop trigger if exists trg_consent_archive on public.consent_records;
create trigger trg_consent_archive before delete on public.consent_records
  for each row execute function public.on_consent_deleted();

-- ----------------------------------------------------------------------------
-- 8. Biometric enrollment references: lifecycle
-- ----------------------------------------------------------------------------
-- Owners/admins see enrollment references; they revoke through the RPC below.
-- Inserts come only from ingest (biometric.enrolled) with the service role.
drop policy if exists biometric_admin on public.biometric_enrollments;
drop policy if exists biometric_admin_select on public.biometric_enrollments;
create policy biometric_admin_select on public.biometric_enrollments for select to authenticated
  using (exists (select 1 from public.members m where m.id = biometric_enrollments.member_id
                 and public.has_branch_role(m.branch_id, 'owner', 'admin')));
revoke insert, update, delete on public.biometric_enrollments from anon, authenticated;

-- Only revoked_at may change, only from null to a time, and never back.
create or replace function public._biometric_guard_update()
returns trigger language plpgsql set search_path = public as $$
begin
  if (to_jsonb(new) - 'revoked_at') is distinct from (to_jsonb(old) - 'revoked_at') then
    raise exception 'biometric enrollment references are immutable' using errcode = '42501';
  end if;
  if old.revoked_at is not null and new.revoked_at is distinct from old.revoked_at then
    raise exception 'a biometric enrollment revocation is permanent' using errcode = '42501';
  end if;
  return new;
end $$;
drop trigger if exists trg_biometric_guard_update on public.biometric_enrollments;
create trigger trg_biometric_guard_update before update on public.biometric_enrollments
  for each row execute function public._biometric_guard_update();

-- A deleted enrollment row leaves a tombstone so the device still deletes the template.
create or replace function public.on_biometric_enrollment_deleted()
returns trigger language plpgsql security definer set search_path = public as $$
begin
  insert into public.biometric_revocations (edge_device_id, template_ref, member_id, enrollment_id, reason)
  values (old.edge_device_id, old.template_ref, old.member_id, old.id, 'enrollment_deleted')
  on conflict (edge_device_id, template_ref) do nothing;
  return old;
end $$;
drop trigger if exists trg_biometric_tombstone on public.biometric_enrollments;
create trigger trg_biometric_tombstone before delete on public.biometric_enrollments
  for each row execute function public.on_biometric_enrollment_deleted();

-- Member deletion: archive consents and tombstone templates while the member row
-- still exists (cascades run after it is gone), then remove the enrollment rows
-- so the consent cascade is not blocked by biometric_enrollments' RESTRICT FK.
create or replace function public.on_member_deleted()
returns trigger language plpgsql security definer set search_path = public as $$
begin
  insert into public.consent_records_archive (id, member_id, organization_id, branch_id, member_code, member_full_name,
    consent_type, status, policy_version, granted_at, withdrawn_at, expires_at, captured_by_staff_id, ip_address,
    informed_acknowledged_at, created_at, archive_reason, archived_by)
  select c.id, c.member_id, (select b.organization_id from public.branches b where b.id = old.branch_id), old.branch_id, old.member_code::text, old.full_name,
         c.consent_type, c.status, c.policy_version, c.granted_at, c.withdrawn_at, c.expires_at,
         c.captured_by_staff_id, c.ip_address, c.informed_acknowledged_at, c.created_at, 'member_deleted', auth.uid()
    from public.consent_records c
   where c.member_id = old.id
  on conflict (id) do nothing;

  insert into public.biometric_revocations (edge_device_id, template_ref, member_id, enrollment_id, reason)
  select b.edge_device_id, b.template_ref, b.member_id, b.id, 'member_deleted'
    from public.biometric_enrollments b
   where b.member_id = old.id
  on conflict (edge_device_id, template_ref) do nothing;
  delete from public.biometric_enrollments where member_id = old.id;
  return old;
end $$;
drop trigger if exists trg_members_before_delete on public.members;
create trigger trg_members_before_delete before delete on public.members
  for each row execute function public.on_member_deleted();

-- Membership ended (cancelled / expired): revoke every enrollment reference.
create or replace function public.on_member_status_changed()
returns trigger language plpgsql security definer set search_path = public as $$
begin
  if new.status in ('cancelled', 'expired') and old.status not in ('cancelled', 'expired') then
    update public.biometric_enrollments set revoked_at = now()
     where member_id = new.id and revoked_at is null;
  end if;
  return new;
end $$;
drop trigger if exists trg_members_status_biometric on public.members;
create trigger trg_members_status_biometric after update of status on public.members
  for each row execute function public.on_member_status_changed();

-- Owner/admin revocation of one enrollment reference (audited by audit_biometric).
create or replace function public.revoke_biometric_enrollment(p_enrollment_id uuid)
returns timestamptz language plpgsql security definer set search_path = public as $$
declare v_branch uuid; v_at timestamptz;
begin
  select m.branch_id into v_branch
    from public.biometric_enrollments b join public.members m on m.id = b.member_id
   where b.id = p_enrollment_id;
  if v_branch is null then raise exception 'enrollment not found' using errcode = 'P0002'; end if;
  if not public.has_branch_role(v_branch, 'owner', 'admin') then
    raise exception 'not authorized for this branch' using errcode = '42501';
  end if;
  update public.biometric_enrollments set revoked_at = coalesce(revoked_at, now())
   where id = p_enrollment_id
  returning revoked_at into v_at;
  return v_at;
end $$;

-- ----------------------------------------------------------------------------
-- 9. Consent withdrawal / expiry side effects
-- ----------------------------------------------------------------------------
-- End a member's active visit without a report or notification: open facts are
-- closed at now() (sets unverified), the session becomes 'abandoned'.
create or replace function public._end_sessions_without_report(p_member_id uuid, p_reason text)
returns int language plpgsql security definer set search_path = public as $$
declare v_id uuid; v_now timestamptz := now(); v_count int := 0;
begin
  for v_id in
    select id from public.attendance_sessions where member_id = p_member_id and status = 'active' for update
  loop
    update public.exercise_sets s
       set ended_at = coalesce(s.ended_at, v_now), verified = false
      from public.exercise_activity_sessions a
      join public.equipment_usage_sessions u on u.id = a.equipment_usage_session_id
     where s.exercise_activity_session_id = a.id and u.attendance_session_id = v_id and s.ended_at is null;
    update public.exercise_activity_sessions a
       set ended_at = coalesce(a.ended_at, v_now)
      from public.equipment_usage_sessions u
     where u.id = a.equipment_usage_session_id and u.attendance_session_id = v_id and a.ended_at is null;
    update public.equipment_usage_sessions
       set ended_at = greatest(v_now, started_at), end_reason = coalesce(end_reason, p_reason)
     where attendance_session_id = v_id and ended_at is null;
    update public.person_tracks
       set ended_at = coalesce(ended_at, v_now), end_reason = coalesce(end_reason, p_reason)
     where attendance_session_id = v_id and ended_at is null;
    update public.attendance_sessions
       set status = 'abandoned', check_out_at = greatest(v_now, check_in_at)
     where id = v_id;
    v_count := v_count + 1;
  end loop;
  return v_count;
end $$;

-- Same trigger (trg_consent_withdrawn, after update) as migration 0005, wider behaviour.
create or replace function public.on_consent_withdrawn()
returns trigger language plpgsql security definer set search_path = public as $$
begin
  if old.status = 'granted' and new.status in ('withdrawn', 'expired') then
    if new.consent_type = 'biometric_face_identification' then
      -- Enrollments made under this record, and all of the member's enrollments
      -- when no other valid biometric consent remains. The device deletes the
      -- templates on its next sync (revoked_template_refs).
      update public.biometric_enrollments set revoked_at = now()
       where member_id = new.member_id and revoked_at is null
         and (consent_record_id = new.id
              or not public._has_granted_consent(new.member_id, 'biometric_face_identification'));
    elsif new.consent_type = 'cctv_workout_tracking'
          and not public._has_granted_consent(new.member_id, 'cctv_workout_tracking') then
      -- Tracking stops now: the visit is closed without a report or notification,
      -- and no later event can bind to it (it is no longer active).
      perform public._end_sessions_without_report(new.member_id, 'consent_' || new.status::text);
    end if;
  end if;
  return new;
end $$;

-- Expiry sweep: consents past expires_at become 'expired' (firing the side
-- effects above) and enrollments of ended memberships are revoked. Gating does
-- not wait for the sweep: _has_granted_consent and device_sync_snapshot compare
-- expires_at with now() directly.
create or replace function public.expire_consents()
returns int language plpgsql security definer set search_path = public as $$
declare v int;
begin
  update public.consent_records set status = 'expired'
   where status = 'granted' and expires_at is not null and expires_at <= now();
  get diagnostics v = row_count;
  update public.biometric_enrollments b set revoked_at = now()
    from public.members m
   where m.id = b.member_id and b.revoked_at is null and m.status in ('cancelled', 'expired');
  return v;
end $$;

-- ----------------------------------------------------------------------------
-- 10. device_sync_snapshot (contract C6)
-- ----------------------------------------------------------------------------
-- VOLATILE: it records last_seen_at (a STABLE function cannot run UPDATE).
drop function if exists public.device_sync_snapshot(uuid);
create or replace function public.device_sync_snapshot(p_device_id uuid)
returns jsonb language plpgsql volatile security definer set search_path = public as $$
declare
  v_branch uuid;
  v_status public.device_status;
  v_out jsonb;
begin
  select branch_id, status into v_branch, v_status from public.edge_devices where id = p_device_id;
  if v_branch is null then
    raise exception 'unknown device %', p_device_id using errcode = '42501';
  end if;
  if v_status = 'retired' then
    raise exception 'device_retired: device % is retired', p_device_id using errcode = '42501';
  end if;

  with enrollment_state as (
    -- An enrollment is revoked when flagged, when the membership ended, or when its
    -- consent record is no longer granted or has passed expires_at.
    select b.template_ref,
           (b.revoked_at is not null
            or m.status in ('cancelled', 'expired')
            or cr.status <> 'granted'
            or (cr.expires_at is not null and cr.expires_at <= now())) as revoked
      from public.biometric_enrollments b
      join public.members m on m.id = b.member_id
      join public.consent_records cr on cr.id = b.consent_record_id
     where b.edge_device_id = p_device_id
  ),
  revoked_refs as (
    select template_ref from enrollment_state where revoked
    union
    select t.template_ref from public.biometric_revocations t
     where t.edge_device_id = p_device_id
       and not exists (select 1 from enrollment_state e where e.template_ref = t.template_ref and not e.revoked)
  ),
  consents as (
    -- Latest valid record per member and type; only the two types the edge gates on.
    select distinct on (cr.member_id, cr.consent_type)
           cr.member_id, cr.consent_type, cr.id, cr.expires_at
      from public.consent_records cr
      join public.members m on m.id = cr.member_id
     where m.branch_id = v_branch and m.status = 'active'
       and cr.status = 'granted'
       and (cr.expires_at is null or cr.expires_at > now())
       and cr.consent_type in ('biometric_face_identification', 'cctv_workout_tracking')
     order by cr.member_id, cr.consent_type, cr.granted_at desc
  )
  select jsonb_build_object(
    'schema_version', 2,
    'generated_at', now(),
    'device_id', p_device_id,
    'branch_id', v_branch,
    'cameras', coalesce((
      select jsonb_agg(jsonb_build_object(
        'id', c.id, 'name', c.name, 'purpose', c.purpose, 'protocol', c.protocol, 'stream_url', c.stream_url,
        'inference_fps', 10, 'enabled', c.enabled, 'homography', c.homography,
        'zones', coalesce((
          select jsonb_agg(jsonb_build_object(
            'id', z.id, 'type', z.zone_type, 'equipment_id', z.equipment_id, 'name', z.name, 'polygon', z.polygon,
            'exercise_slug', (select ex.slug from public.equipment e join public.exercise_catalog ex on ex.id = e.default_exercise_id where e.id = z.equipment_id)
          ) order by z.name)
          from public.camera_zones z where z.camera_id = c.id
        ), '[]'::jsonb)
      ) order by c.name)
      from public.cameras c where c.branch_id = v_branch and c.enabled
    ), '[]'::jsonb),
    'zones', coalesce((
      select jsonb_agg(jsonb_build_object(
        'id', z.id, 'camera_id', z.camera_id, 'type', z.zone_type, 'equipment_id', z.equipment_id, 'name', z.name,
        'polygon', z.polygon,
        'exercise_slug', (select ex.slug from public.equipment e join public.exercise_catalog ex on ex.id = e.default_exercise_id where e.id = z.equipment_id)
      ) order by c.name, z.name)
      from public.camera_zones z join public.cameras c on c.id = z.camera_id
      where c.branch_id = v_branch and c.enabled
    ), '[]'::jsonb),
    'equipment', coalesce((
      select jsonb_agg(jsonb_build_object('id', e.id, 'name', e.name, 'category', e.category,
        'default_exercise_id', e.default_exercise_id, 'position_x_m', e.position_x_m, 'position_y_m', e.position_y_m) order by e.name)
      from public.equipment e where e.branch_id = v_branch and e.enabled
    ), '[]'::jsonb),
    'exercises', coalesce((
      select jsonb_agg(jsonb_build_object('id', x.id, 'slug', x.slug, 'name', x.name, 'rep_profile', x.rep_profile) order by x.slug)
      from public.exercise_catalog x
    ), '[]'::jsonb),
    'consents', coalesce((
      select jsonb_agg(jsonb_build_object('member_id', s.member_id, 'consent_type', s.consent_type,
                                          'consent_record_id', s.id, 'expires_at', s.expires_at)
                       order by s.member_id, s.consent_type)
      from consents s
    ), '[]'::jsonb),
    'revoked_template_refs', coalesce((select jsonb_agg(r.template_ref order by r.template_ref) from revoked_refs r), '[]'::jsonb),
    'active_template_refs', coalesce((
      select jsonb_agg(e.template_ref order by e.template_ref) from enrollment_state e where not e.revoked
    ), '[]'::jsonb)
  ) into v_out;

  update public.edge_devices set last_seen_at = greatest(coalesce(last_seen_at, now()), now()) where id = p_device_id;
  return v_out;
end $$;

-- ----------------------------------------------------------------------------
-- 11. Privileges for the functions above
-- ----------------------------------------------------------------------------
revoke all on function public.device_sync_snapshot(uuid) from public, anon, authenticated;
grant execute on function public.device_sync_snapshot(uuid) to service_role;
revoke all on function public.expire_consents() from public, anon, authenticated;
grant execute on function public.expire_consents() to service_role;
revoke all on function public._end_sessions_without_report(uuid, text) from public, anon, authenticated;
revoke all on function public.revoke_biometric_enrollment(uuid) from public, anon;
grant execute on function public.revoke_biometric_enrollment(uuid) to authenticated, service_role;
-- Trigger functions are never called directly.
revoke all on function public._enforce_branch_org() from public, anon, authenticated;
revoke all on function public._guard_last_owner() from public, anon, authenticated;
revoke all on function public._guard_member_user_link() from public, anon, authenticated;
revoke all on function public.audit_row_change() from public, anon, authenticated;
revoke all on function public.audit_device_secret_change() from public, anon, authenticated;
revoke all on function public._consent_before_insert() from public, anon, authenticated;
revoke all on function public._consent_guard_update() from public, anon, authenticated;
revoke all on function public.on_consent_deleted() from public, anon, authenticated;
revoke all on function public._biometric_guard_update() from public, anon, authenticated;
revoke all on function public.on_biometric_enrollment_deleted() from public, anon, authenticated;
revoke all on function public.on_member_deleted() from public, anon, authenticated;
revoke all on function public.on_member_status_changed() from public, anon, authenticated;
revoke all on function public.on_consent_withdrawn() from public, anon, authenticated;

-- ----------------------------------------------------------------------------
-- 12. Schedule the expiry sweep (hourly) where pg_cron exists; no-op elsewhere.
-- ----------------------------------------------------------------------------
do $$
begin
  if to_regnamespace('cron') is null then
    raise notice 'pg_cron not available: run select public.expire_consents() at least daily from an external scheduler';
    return;
  end if;
  begin
    perform cron.unschedule(j.jobid) from cron.job j where j.jobname = 'smartgym-expire-consents';
    perform cron.schedule('smartgym-expire-consents', '5 * * * *', $job$ select public.expire_consents() $job$);
  exception when others then
    raise warning 'could not schedule smartgym-expire-consents (%); schedule select public.expire_consents() manually', sqlerrm;
  end;
end $$;
