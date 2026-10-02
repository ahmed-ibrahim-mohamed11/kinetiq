-- GYM-AI Row Level Security (MASTER_PROMPT section 14). Phase 3.
-- Model: every user belongs to exactly one organization (profiles.organization_id).
-- Dashboard users read their organization's data; admins write configuration.
-- The edge device does NOT use these policies: it talks to a secured ingestion endpoint
-- (Edge Function with the service role) that validates its device API key hash.

-- ---------------------------------------------------------------- helpers
create or replace function auth_org_id()
returns uuid language sql stable security definer set search_path = public as $$
    select organization_id from profiles where user_id = auth.uid()
$$;

create or replace function auth_role()
returns text language sql stable security definer set search_path = public as $$
    select role from profiles where user_id = auth.uid()
$$;

-- ---------------------------------------------------------------- enable RLS everywhere
alter table organizations     enable row level security;
alter table profiles          enable row level security;
alter table gyms              enable row level security;
alter table machines          enable row level security;
alter table devices           enable row level security;
alter table cameras           enable row level security;
alter table sessions          enable row level security;
alter table reps              enable row level security;
alter table form_results      enable row level security;
alter table alerts            enable row level security;
alter table device_heartbeats enable row level security;
alter table model_versions    enable row level security;
alter table evidence_assets   enable row level security;

-- ---------------------------------------------------------------- organizations
create policy org_select on organizations for select
    using (id = auth_org_id());
-- no insert/update/delete from the dashboard; organizations are provisioned by the service role

-- ---------------------------------------------------------------- profiles
create policy profiles_select_own_org on profiles for select
    using (organization_id = auth_org_id());
create policy profiles_update_self on profiles for update
    using (user_id = auth.uid())
    with check (user_id = auth.uid() and organization_id = auth_org_id());

-- ---------------------------------------------------------------- gyms
create policy gyms_select on gyms for select
    using (organization_id = auth_org_id());
create policy gyms_admin_write on gyms for all
    using (organization_id = auth_org_id() and auth_role() = 'admin')
    with check (organization_id = auth_org_id() and auth_role() = 'admin');

-- ---------------------------------------------------------------- machines (scoped via gym)
create policy machines_select on machines for select
    using (gym_id in (select id from gyms where organization_id = auth_org_id()));
create policy machines_admin_write on machines for all
    using (gym_id in (select id from gyms where organization_id = auth_org_id()) and auth_role() = 'admin')
    with check (gym_id in (select id from gyms where organization_id = auth_org_id()) and auth_role() = 'admin');

-- ---------------------------------------------------------------- devices / cameras
create policy devices_select on devices for select
    using (gym_id in (select id from gyms where organization_id = auth_org_id()));
create policy devices_admin_write on devices for all
    using (gym_id in (select id from gyms where organization_id = auth_org_id()) and auth_role() = 'admin')
    with check (gym_id in (select id from gyms where organization_id = auth_org_id()) and auth_role() = 'admin');

create policy cameras_select on cameras for select
    using (device_id in (select d.id from devices d join gyms g on g.id = d.gym_id
                         where g.organization_id = auth_org_id()));
create policy cameras_admin_write on cameras for all
    using (device_id in (select d.id from devices d join gyms g on g.id = d.gym_id
                         where g.organization_id = auth_org_id()) and auth_role() = 'admin')
    with check (device_id in (select d.id from devices d join gyms g on g.id = d.gym_id
                              where g.organization_id = auth_org_id()) and auth_role() = 'admin');

-- ---------------------------------------------------------------- sessions / reps / form / alerts / heartbeats
-- Written by the ingestion path (service role bypasses RLS); dashboard users read their org,
-- coaches+admins may update alert/review status.
create policy sessions_select on sessions for select
    using (machine_id in (select m.id from machines m join gyms g on g.id = m.gym_id
                          where g.organization_id = auth_org_id()));

create policy reps_select on reps for select
    using (session_id in (select s.id from sessions s
                          join machines m on m.id = s.machine_id
                          join gyms g on g.id = m.gym_id
                          where g.organization_id = auth_org_id()));

create policy form_results_select on form_results for select
    using (rep_id in (select r.id from reps r
                      join sessions s on s.id = r.session_id
                      join machines m on m.id = s.machine_id
                      join gyms g on g.id = m.gym_id
                      where g.organization_id = auth_org_id()));
create policy form_results_review on form_results for update
    using (auth_role() in ('admin', 'coach') and rep_id in (
        select r.id from reps r
        join sessions s on s.id = r.session_id
        join machines m on m.id = s.machine_id
        join gyms g on g.id = m.gym_id
        where g.organization_id = auth_org_id()))
    with check (auth_role() in ('admin', 'coach'));

create policy alerts_select on alerts for select
    using (session_id in (select s.id from sessions s
                          join machines m on m.id = s.machine_id
                          join gyms g on g.id = m.gym_id
                          where g.organization_id = auth_org_id()));
create policy alerts_review on alerts for update
    using (auth_role() in ('admin', 'coach') and session_id in (
        select s.id from sessions s
        join machines m on m.id = s.machine_id
        join gyms g on g.id = m.gym_id
        where g.organization_id = auth_org_id()))
    with check (auth_role() in ('admin', 'coach'));

create policy heartbeats_select on device_heartbeats for select
    using (device_id in (select d.id from devices d join gyms g on g.id = d.gym_id
                         where g.organization_id = auth_org_id()));

-- ---------------------------------------------------------------- model_versions (read-only catalogue)
create policy model_versions_select on model_versions for select
    using (auth.uid() is not null);

-- ---------------------------------------------------------------- evidence_assets (metadata only; files live
-- in a PRIVATE storage bucket, served via signed URLs created server-side)
create policy evidence_select on evidence_assets for select
    using (
        (rep_id is not null and rep_id in (
            select r.id from reps r
            join sessions s on s.id = r.session_id
            join machines m on m.id = s.machine_id
            join gyms g on g.id = m.gym_id
            where g.organization_id = auth_org_id()))
        or
        (alert_id is not null and alert_id in (
            select a.id from alerts a
            join sessions s on s.id = a.session_id
            join machines m on m.id = s.machine_id
            join gyms g on g.id = m.gym_id
            where g.organization_id = auth_org_id()))
    );
