-- ============================================================================
-- Row Level Security policies (migration 0002)
-- ----------------------------------------------------------------------------
-- Principles
--   * Default deny. Every table had RLS enabled in 0001.
--   * Members read ONLY their own rows (members.user_id = auth.uid()).
--   * Staff access is scoped by organization and branch through staff_roles.
--   * Writes from the browser are limited to a small set of staff operations;
--     all edge-sourced facts are written by the service role through RPCs.
--   * Biometric and camera configuration need elevated roles separately from
--     ordinary workout records.
-- ============================================================================

-- ----------------------------------------------------------------------------
-- Helper functions (SECURITY DEFINER, STABLE, fixed search_path)
-- ----------------------------------------------------------------------------
create or replace function public.current_member_id()
returns uuid language sql stable security definer set search_path = public as $$
  select id from public.members where user_id = auth.uid() limit 1
$$;

create or replace function public.branch_org(p_branch_id uuid)
returns uuid language sql stable security definer set search_path = public as $$
  select organization_id from public.branches where id = p_branch_id
$$;

-- Roles the caller holds for a given branch (branch-specific or org-wide).
create or replace function public.staff_roles_for_branch(p_branch_id uuid)
returns setof public.staff_role language sql stable security definer set search_path = public as $$
  select sr.role
  from public.staff_roles sr
  where sr.user_id = auth.uid()
    and sr.organization_id = public.branch_org(p_branch_id)
    and (sr.branch_id is null or sr.branch_id = p_branch_id)
$$;

create or replace function public.has_branch_role(p_branch_id uuid, variadic p_roles public.staff_role[])
returns boolean language sql stable security definer set search_path = public as $$
  select exists (
    select 1 from public.staff_roles_for_branch(p_branch_id) r where r = any (p_roles)
  )
$$;

create or replace function public.is_branch_staff(p_branch_id uuid)
returns boolean language sql stable security definer set search_path = public as $$
  select exists (select 1 from public.staff_roles_for_branch(p_branch_id))
$$;

create or replace function public.has_org_role(p_org_id uuid, variadic p_roles public.staff_role[])
returns boolean language sql stable security definer set search_path = public as $$
  select exists (
    select 1 from public.staff_roles sr
    where sr.user_id = auth.uid() and sr.organization_id = p_org_id and sr.role = any (p_roles)
  )
$$;

create or replace function public.is_org_staff(p_org_id uuid)
returns boolean language sql stable security definer set search_path = public as $$
  select exists (select 1 from public.staff_roles sr where sr.user_id = auth.uid() and sr.organization_id = p_org_id)
$$;

-- Branch of an attendance session (used by child-table policies).
create or replace function public.session_branch(p_session_id uuid)
returns uuid language sql stable security definer set search_path = public as $$
  select branch_id from public.attendance_sessions where id = p_session_id
$$;

create or replace function public.session_member(p_session_id uuid)
returns uuid language sql stable security definer set search_path = public as $$
  select member_id from public.attendance_sessions where id = p_session_id
$$;

create or replace function public.usage_session(p_usage_id uuid)
returns uuid language sql stable security definer set search_path = public as $$
  select attendance_session_id from public.equipment_usage_sessions where id = p_usage_id
$$;

create or replace function public.activity_session(p_activity_id uuid)
returns uuid language sql stable security definer set search_path = public as $$
  select public.usage_session(equipment_usage_session_id) from public.exercise_activity_sessions where id = p_activity_id
$$;

create or replace function public.set_session(p_set_id uuid)
returns uuid language sql stable security definer set search_path = public as $$
  select public.activity_session(exercise_activity_session_id) from public.exercise_sets where id = p_set_id
$$;

grant execute on function
  public.current_member_id(), public.branch_org(uuid), public.staff_roles_for_branch(uuid),
  public.has_branch_role(uuid, public.staff_role[]), public.is_branch_staff(uuid),
  public.has_org_role(uuid, public.staff_role[]), public.is_org_staff(uuid),
  public.session_branch(uuid), public.session_member(uuid), public.usage_session(uuid),
  public.activity_session(uuid), public.set_session(uuid)
to authenticated;

-- ----------------------------------------------------------------------------
-- organizations / branches
-- ----------------------------------------------------------------------------
create policy org_select_staff on public.organizations for select to authenticated
  using (public.is_org_staff(id) or exists (select 1 from public.members m where m.user_id = auth.uid() and m.organization_id = organizations.id));
create policy org_update_owner on public.organizations for update to authenticated
  using (public.has_org_role(id, 'owner')) with check (public.has_org_role(id, 'owner'));

create policy branch_select on public.branches for select to authenticated
  using (public.is_org_staff(organization_id) or exists (select 1 from public.members m where m.user_id = auth.uid() and m.branch_id = branches.id));
create policy branch_write_admin on public.branches for all to authenticated
  using (public.has_org_role(organization_id, 'owner', 'admin'))
  with check (public.has_org_role(organization_id, 'owner', 'admin'));

-- ----------------------------------------------------------------------------
-- profiles / staff_roles
-- ----------------------------------------------------------------------------
create policy profile_self on public.profiles for all to authenticated
  using (user_id = auth.uid()) with check (user_id = auth.uid());
create policy profile_staff_read on public.profiles for select to authenticated
  using (exists (
    select 1 from public.staff_roles mine
    join public.staff_roles theirs on theirs.organization_id = mine.organization_id
    where mine.user_id = auth.uid() and theirs.user_id = profiles.user_id
  ));

create policy staff_roles_select on public.staff_roles for select to authenticated
  using (user_id = auth.uid() or public.is_org_staff(organization_id));
create policy staff_roles_manage on public.staff_roles for all to authenticated
  using (public.has_org_role(organization_id, 'owner', 'admin'))
  with check (
    public.has_org_role(organization_id, 'owner', 'admin')
    -- Only owners may grant the owner role.
    and (role <> 'owner' or public.has_org_role(organization_id, 'owner'))
  );

-- ----------------------------------------------------------------------------
-- members / consent / biometric references
-- ----------------------------------------------------------------------------
create policy members_self_select on public.members for select to authenticated
  using (user_id = auth.uid());
create policy members_staff_select on public.members for select to authenticated
  using (public.is_branch_staff(branch_id));
create policy members_staff_write on public.members for insert to authenticated
  with check (public.has_branch_role(branch_id, 'owner', 'admin', 'manager', 'front_desk'));
create policy members_staff_update on public.members for update to authenticated
  using (public.has_branch_role(branch_id, 'owner', 'admin', 'manager', 'front_desk'))
  with check (public.has_branch_role(branch_id, 'owner', 'admin', 'manager', 'front_desk'));
create policy members_admin_delete on public.members for delete to authenticated
  using (public.has_branch_role(branch_id, 'owner', 'admin'));

create policy consent_self_select on public.consent_records for select to authenticated
  using (member_id = public.current_member_id());
create policy consent_staff_select on public.consent_records for select to authenticated
  using (exists (select 1 from public.members m where m.id = consent_records.member_id and public.is_branch_staff(m.branch_id)));
create policy consent_staff_insert on public.consent_records for insert to authenticated
  with check (exists (select 1 from public.members m where m.id = consent_records.member_id
                      and public.has_branch_role(m.branch_id, 'owner', 'admin', 'manager', 'front_desk')));
-- Members may withdraw their own consent; staff may withdraw on request. Nobody edits history.
create policy consent_withdraw on public.consent_records for update to authenticated
  using (member_id = public.current_member_id()
         or exists (select 1 from public.members m where m.id = consent_records.member_id
                    and public.has_branch_role(m.branch_id, 'owner', 'admin', 'manager', 'front_desk')))
  with check (status = 'withdrawn');

-- Biometric references: member sees existence of their own; only owner/admin manage.
create policy biometric_self_select on public.biometric_enrollments for select to authenticated
  using (member_id = public.current_member_id());
create policy biometric_admin on public.biometric_enrollments for all to authenticated
  using (exists (select 1 from public.members m where m.id = biometric_enrollments.member_id and public.has_branch_role(m.branch_id, 'owner', 'admin')))
  with check (exists (select 1 from public.members m where m.id = biometric_enrollments.member_id and public.has_branch_role(m.branch_id, 'owner', 'admin')));

-- ----------------------------------------------------------------------------
-- devices / cameras / zones / equipment / catalog / models
-- ----------------------------------------------------------------------------
create policy devices_staff_select on public.edge_devices for select to authenticated
  using (public.is_branch_staff(branch_id));
create policy devices_admin_write on public.edge_devices for all to authenticated
  using (public.has_branch_role(branch_id, 'owner', 'admin'))
  with check (public.has_branch_role(branch_id, 'owner', 'admin'));

-- edge_device_secrets: NO policies → only service role. (Forced RLS in 0001.)

create policy cameras_staff_select on public.cameras for select to authenticated
  using (public.has_branch_role(branch_id, 'owner', 'admin', 'manager', 'auditor'));
create policy cameras_admin_write on public.cameras for all to authenticated
  using (public.has_branch_role(branch_id, 'owner', 'admin'))
  with check (public.has_branch_role(branch_id, 'owner', 'admin'));

create policy zones_staff_select on public.camera_zones for select to authenticated
  using (exists (select 1 from public.cameras c where c.id = camera_zones.camera_id
                 and public.has_branch_role(c.branch_id, 'owner', 'admin', 'manager', 'trainer', 'auditor')));
create policy zones_manager_write on public.camera_zones for all to authenticated
  using (exists (select 1 from public.cameras c where c.id = camera_zones.camera_id and public.has_branch_role(c.branch_id, 'owner', 'admin', 'manager')))
  with check (exists (select 1 from public.cameras c where c.id = camera_zones.camera_id and public.has_branch_role(c.branch_id, 'owner', 'admin', 'manager')));

create policy equipment_select on public.equipment for select to authenticated
  using (public.is_branch_staff(branch_id) or exists (select 1 from public.members m where m.user_id = auth.uid() and m.branch_id = equipment.branch_id));
create policy equipment_manager_write on public.equipment for all to authenticated
  using (public.has_branch_role(branch_id, 'owner', 'admin', 'manager'))
  with check (public.has_branch_role(branch_id, 'owner', 'admin', 'manager'));

-- Catalog & model registry are global reference data: readable by all signed-in users, writable by service role only.
create policy catalog_read on public.exercise_catalog for select to authenticated using (true);
create policy models_read on public.model_versions for select to authenticated using (true);

-- ----------------------------------------------------------------------------
-- sessions and workout facts
-- ----------------------------------------------------------------------------
create policy attendance_self on public.attendance_sessions for select to authenticated
  using (member_id = public.current_member_id());
create policy attendance_staff on public.attendance_sessions for select to authenticated
  using (public.is_branch_staff(branch_id));

create policy tracks_staff on public.person_tracks for select to authenticated
  using (exists (select 1 from public.edge_devices d where d.id = person_tracks.edge_device_id
                 and public.has_branch_role(d.branch_id, 'owner', 'admin', 'manager', 'auditor')));

create policy uncertain_self on public.tracking_uncertain_segments for select to authenticated
  using (attendance_session_id is not null and public.session_member(attendance_session_id) = public.current_member_id());
create policy uncertain_staff on public.tracking_uncertain_segments for select to authenticated
  using (attendance_session_id is not null and public.is_branch_staff(public.session_branch(attendance_session_id)));

create policy usage_self on public.equipment_usage_sessions for select to authenticated
  using (attendance_session_id is not null and public.session_member(attendance_session_id) = public.current_member_id());
create policy usage_staff on public.equipment_usage_sessions for select to authenticated
  using (exists (select 1 from public.equipment e where e.id = equipment_usage_sessions.equipment_id and public.is_branch_staff(e.branch_id)));

create policy activity_self on public.exercise_activity_sessions for select to authenticated
  using (public.session_member(public.usage_session(equipment_usage_session_id)) = public.current_member_id());
create policy activity_staff on public.exercise_activity_sessions for select to authenticated
  using (public.is_branch_staff(public.session_branch(public.usage_session(equipment_usage_session_id))));

create policy sets_self on public.exercise_sets for select to authenticated
  using (public.session_member(public.activity_session(exercise_activity_session_id)) = public.current_member_id());
create policy sets_staff on public.exercise_sets for select to authenticated
  using (public.is_branch_staff(public.session_branch(public.activity_session(exercise_activity_session_id))));

create policy reps_self on public.repetition_events for select to authenticated
  using (public.session_member(public.set_session(exercise_set_id)) = public.current_member_id());
create policy reps_staff on public.repetition_events for select to authenticated
  using (public.is_branch_staff(public.session_branch(public.set_session(exercise_set_id))));

create policy reports_self on public.workout_reports for select to authenticated
  using (member_id = public.current_member_id());
create policy reports_staff on public.workout_reports for select to authenticated
  using (public.is_branch_staff(public.session_branch(attendance_session_id)));
create policy reports_self_mark_viewed on public.workout_reports for update to authenticated
  using (member_id = public.current_member_id()) with check (member_id = public.current_member_id());

-- ----------------------------------------------------------------------------
-- notifications
-- ----------------------------------------------------------------------------
create policy push_self on public.push_subscriptions for all to authenticated
  using (user_id = auth.uid()) with check (user_id = auth.uid());

create policy notif_self_select on public.notification_jobs for select to authenticated
  using (member_id = public.current_member_id());
create policy notif_staff_select on public.notification_jobs for select to authenticated
  using (exists (select 1 from public.members m where m.id = notification_jobs.member_id and public.has_branch_role(m.branch_id, 'owner', 'admin', 'manager')));

-- ----------------------------------------------------------------------------
-- operations / audit
-- ----------------------------------------------------------------------------
create policy ingest_log_admin on public.event_ingest_log for select to authenticated
  using (exists (select 1 from public.edge_devices d where d.id = event_ingest_log.edge_device_id and public.has_branch_role(d.branch_id, 'owner', 'admin', 'auditor')));
create policy ingest_batches_admin on public.ingest_batches for select to authenticated
  using (exists (select 1 from public.edge_devices d where d.id = ingest_batches.edge_device_id and public.has_branch_role(d.branch_id, 'owner', 'admin', 'auditor')));
create policy health_staff on public.device_health_logs for select to authenticated
  using (exists (select 1 from public.edge_devices d where d.id = device_health_logs.edge_device_id and public.has_branch_role(d.branch_id, 'owner', 'admin', 'manager', 'auditor')));
create policy camera_status_staff on public.camera_status_logs for select to authenticated
  using (exists (select 1 from public.cameras c where c.id = camera_status_logs.camera_id and public.has_branch_role(c.branch_id, 'owner', 'admin', 'manager', 'auditor')));

create policy audit_owner_auditor on public.audit_logs for select to authenticated
  using (organization_id is not null and public.has_org_role(organization_id, 'owner', 'auditor'));

-- ----------------------------------------------------------------------------
-- Lock down table grants for anon entirely; authenticated gets DML gated by RLS.
-- ----------------------------------------------------------------------------
revoke all on all tables in schema public from anon;
grant select on public.exercise_catalog to anon; -- public catalog for marketing pages only
