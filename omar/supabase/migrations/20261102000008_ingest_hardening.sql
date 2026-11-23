-- ============================================================================
-- Ingest hardening (migration 0008, contracts C4 and C5)
-- ----------------------------------------------------------------------------
-- Replaces the ingest functions of migration 0004 (applied migrations are
-- immutable, so behaviour changes only through `create or replace` here):
--   * Every member/session lookup is scoped to the device's branch; payloads
--     naming a member, camera, zone or equipment outside it are rejected.
--   * Server-side consent: binding CCTV-derived facts (tracks, usage, activity,
--     sets, reps, uncertain segments) to a session requires the member's granted
--     cctv_workout_tracking consent at ingest time; a face-identified checkout
--     and a biometric enrollment require a granted, unexpired biometric consent.
--   * Envelope checks are NULL-safe (`is distinct from`), text fields are
--     bounded and occurred_at must lie within p_sent_at - 7 days .. + 1 hour.
--   * Rejections carry a stable `code` and a fixed human-readable `reason`; the
--     raw error text stays only in event_ingest_log.error. Errors that are not
--     validation/consent/reference/state errors (deadlocks, serialization
--     failures, resource errors, ...) abort the whole call so the Edge Function
--     answers 500 and the device retries the batch (nothing is half-recorded).
--   * edge_devices.last_sequence is written once per batch, and the edge_devices
--     audit trigger fires only when a meaningful column changes.
--   * Secrets: get_device_secrets honours the 24 h rotation overlap and returns
--     nothing for retired devices; retiring a device retires all its secrets;
--     ingest_events raises 42501 'device_retired: ...' for a retired device.
-- ============================================================================

-- ----------------------------------------------------------------------------
-- Error vocabulary
-- ----------------------------------------------------------------------------
-- Custom SQLSTATEs raised by the ingest validators (class "SG" is
-- implementation-defined and unused by PostgreSQL):
--   SGB01 branch_mismatch   SGC01 no_consent   SGR01 unknown_reference
--   SGV01 invalid_value     SGS01 invalid_state
create or replace function public._ingest_raise(p_code text, p_message text)
returns void language plpgsql set search_path = public as $$
begin
  raise exception using
    message = p_message,
    errcode = case p_code
                when 'branch_mismatch' then 'SGB01'
                when 'no_consent' then 'SGC01'
                when 'unknown_reference' then 'SGR01'
                when 'invalid_state' then 'SGS01'
                else 'SGV01'
              end;
end $$;

-- Map a SQLSTATE to a rejection code, or NULL for infrastructure errors that must abort the batch.
create or replace function public._ingest_error_code(p_sqlstate text)
returns text language sql immutable set search_path = public as $$
  select case
    when p_sqlstate = 'SGB01' then 'branch_mismatch'
    when p_sqlstate = 'SGC01' then 'no_consent'
    when p_sqlstate in ('SGR01', '23503', 'P0002') then 'unknown_reference'
    when p_sqlstate in ('SGS01', '23505', '23P01', '23001') then 'invalid_state'
    when p_sqlstate in ('SGV01', '23502', '23514') or left(p_sqlstate, 2) = '22' then 'invalid_value'
    -- Deterministic business-rule raises from functions/triggers without an errcode:
    -- retrying cannot help, so reject the event instead of blocking the device queue.
    when p_sqlstate in ('P0001', 'P0003', 'P0004') then 'internal'
    else null
  end
$$;

create or replace function public._ingest_reason(p_code text)
returns text language sql immutable set search_path = public as $$
  select case p_code
    when 'branch_mismatch' then 'The event references a member, camera, zone or equipment that does not belong to this device''s branch.'
    when 'no_consent' then 'The member has not granted the consent this event requires.'
    when 'unknown_reference' then 'The event references a track, usage, activity, set or record that does not exist.'
    when 'invalid_value' then 'The event contains a missing or invalid value.'
    when 'invalid_state' then 'The event conflicts with the current state of the session or record.'
    else 'The event could not be applied.'
  end
$$;

-- ----------------------------------------------------------------------------
-- Small validators
-- ----------------------------------------------------------------------------
create or replace function public._is_uuid(p_value text)
returns boolean language sql immutable set search_path = public as $$
  select p_value is not null and p_value ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
$$;

-- Returns p_value unchanged, or rejects the event when it is longer than p_max characters.
create or replace function public._bounded_text(p_value text, p_max int, p_field text)
returns text language plpgsql immutable set search_path = public as $$
begin
  if p_value is not null and length(p_value) > p_max then
    perform public._ingest_raise('invalid_value', format('%s exceeds %s characters', p_field, p_max));
  end if;
  return p_value;
end $$;

create or replace function public._assert_camera_in_branch(p_camera_id uuid, p_branch_id uuid)
returns void language plpgsql stable security definer set search_path = public as $$
begin
  if p_camera_id is not null
     and not exists (select 1 from public.cameras where id = p_camera_id and branch_id = p_branch_id) then
    -- Same outcome for unknown and foreign ids: no existence oracle.
    perform public._ingest_raise('branch_mismatch', format('camera %s not in device branch', p_camera_id));
  end if;
end $$;

create or replace function public._assert_zone_in_branch(p_zone_id uuid, p_branch_id uuid)
returns void language plpgsql stable security definer set search_path = public as $$
begin
  if p_zone_id is not null and not exists (
    select 1 from public.camera_zones z join public.cameras c on c.id = z.camera_id
    where z.id = p_zone_id and c.branch_id = p_branch_id
  ) then
    perform public._ingest_raise('branch_mismatch', format('zone %s not in device branch', p_zone_id));
  end if;
end $$;

-- p_active_only: the member must also hold an active membership.
create or replace function public._assert_member_in_branch(p_member_id uuid, p_branch_id uuid, p_active_only boolean)
returns void language plpgsql stable security definer set search_path = public as $$
begin
  if p_member_id is null then
    perform public._ingest_raise('invalid_value', 'member_id is required');
  end if;
  if not exists (
    select 1 from public.members
    where id = p_member_id and branch_id = p_branch_id and (not p_active_only or status = 'active')
  ) then
    perform public._ingest_raise('branch_mismatch', format('member %s is not %smember of this branch', p_member_id,
      case when p_active_only then 'an active ' else 'a ' end));
  end if;
end $$;

-- Branch-scoped replacement of the 0004 helper (which ignored the branch).
drop function if exists public._active_session_for_member(uuid);
create or replace function public._active_session_for_member(p_member_id uuid, p_branch_id uuid)
returns uuid language sql stable security definer set search_path = public as $$
  select id from public.attendance_sessions
  where member_id = p_member_id and branch_id = p_branch_id and status = 'active'
  limit 1
$$;

-- CCTV-derived facts may be attached to a session only while its member consents to tracking.
create or replace function public._require_tracking_consent(p_session_id uuid)
returns void language plpgsql stable security definer set search_path = public as $$
begin
  if p_session_id is not null and not exists (
    select 1 from public.attendance_sessions s
    where s.id = p_session_id and public._has_granted_consent(s.member_id, 'cctv_workout_tracking')
  ) then
    perform public._ingest_raise('no_consent', format('session %s: member has no granted CCTV workout tracking consent', p_session_id));
  end if;
end $$;

-- ----------------------------------------------------------------------------
-- Apply a single event. Raises on validation failure; ingest_events classifies.
-- ----------------------------------------------------------------------------
create or replace function public._apply_edge_event(p_device_id uuid, p_branch_id uuid, p jsonb)
returns void language plpgsql security definer set search_path = public as $$
declare
  v_type text := p->>'event_type';
  v_payload jsonb := p->'payload';
  v_occurred timestamptz := (p->>'occurred_at')::timestamptz;
  v_member uuid;
  v_session uuid;
  v_track uuid;
  v_usage uuid;
  v_activity uuid;
  v_set uuid;
  v_conf numeric;
  v_method public.checkin_method;
  v_camera uuid;
  v_zone uuid;
  v_text text;
  v_from timestamptz;
  v_to timestamptz;
  v_check_in timestamptz;
begin
  if (p->>'branch_id')::uuid is distinct from p_branch_id then
    perform public._ingest_raise('branch_mismatch', format('event branch %s does not match device branch %s', p->>'branch_id', p_branch_id));
  end if;
  if v_occurred is null then
    perform public._ingest_raise('invalid_value', 'occurred_at is required');
  end if;
  if v_payload is null or jsonb_typeof(v_payload) <> 'object' then
    perform public._ingest_raise('invalid_value', 'payload must be an object');
  end if;

  case v_type

  when 'device.heartbeat' then
    v_text := public._bounded_text(v_payload->>'software_version', 100, 'software_version');
    insert into public.device_health_logs (edge_device_id, recorded_at, software_version, uptime_seconds, gpu_utilization,
      gpu_memory_used_mb, cpu_utilization, queue_depth, cameras_online, cameras_total, avg_pipeline_latency_ms)
    values (p_device_id, v_occurred, v_text, (v_payload->>'uptime_seconds')::bigint,
      (v_payload->>'gpu_utilization')::numeric, (v_payload->>'gpu_memory_used_mb')::numeric, (v_payload->>'cpu_utilization')::numeric,
      (v_payload->>'queue_depth')::int, (v_payload->>'cameras_online')::int, (v_payload->>'cameras_total')::int,
      (v_payload->>'avg_pipeline_latency_ms')::numeric);
    update public.edge_devices
       set last_seen_at = greatest(coalesce(last_seen_at, least(v_occurred, now())), least(v_occurred, now())),
           software_version = coalesce(v_text, software_version),
           status = case when status in ('provisioning', 'offline', 'degraded') then 'active' else status end
     where id = p_device_id;

  when 'camera.status' then
    v_camera := (v_payload->>'camera_id')::uuid;
    if v_camera is null then
      perform public._ingest_raise('invalid_value', 'camera_id is required');
    end if;
    perform public._assert_camera_in_branch(v_camera, p_branch_id);
    v_text := public._bounded_text(v_payload->>'status', 32, 'status');
    insert into public.camera_status_logs (camera_id, recorded_at, status, fps_measured, resolution_w, resolution_h, error)
    values (v_camera, v_occurred, v_text, (v_payload->>'fps_measured')::numeric,
      (v_payload->>'resolution_w')::int, (v_payload->>'resolution_h')::int,
      public._bounded_text(v_payload->>'error', 500, 'error'));
    update public.cameras set last_status = v_text, last_status_at = v_occurred where id = v_camera;

  when 'identity.checkin' then
    v_member := (v_payload->>'member_id')::uuid;
    v_method := (v_payload->>'method')::public.checkin_method;
    v_conf := (v_payload->>'confidence')::numeric;
    if v_method is null then
      perform public._ingest_raise('invalid_value', 'method is required');
    end if;
    perform public._assert_member_in_branch(v_member, p_branch_id, true);
    -- Hard privacy gate: face identification requires a granted biometric consent.
    if v_method = 'face' and not public._has_granted_consent(v_member, 'biometric_face_identification') then
      perform public._ingest_raise('no_consent', format('face check-in without granted biometric consent for member %s', v_member));
    end if;
    if not public._has_granted_consent(v_member, 'cctv_workout_tracking') then
      perform public._ingest_raise('no_consent', format('member %s has not consented to CCTV workout tracking', v_member));
    end if;
    v_text := public._bounded_text(v_payload->>'global_track_id', 64, 'global_track_id');
    v_session := public._active_session_for_member(v_member, p_branch_id);
    if v_session is null then
      insert into public.attendance_sessions (member_id, branch_id, check_in_at, check_in_method, check_in_confidence)
      values (v_member, p_branch_id, v_occurred, v_method, v_conf)
      returning id into v_session;
    end if;
    -- Bind the entrance track if present.
    if v_text is not null then
      update public.person_tracks
         set attendance_session_id = coalesce(attendance_session_id, v_session), member_id = coalesce(member_id, v_member)
       where edge_device_id = p_device_id and global_track_id = v_text;
    end if;

  when 'identity.checkout' then
    v_member := (v_payload->>'member_id')::uuid;
    v_method := (v_payload->>'method')::public.checkin_method;
    if v_method is null then
      perform public._ingest_raise('invalid_value', 'method is required');
    end if;
    perform public._assert_member_in_branch(v_member, p_branch_id, false);
    if v_method = 'face' and not public._has_granted_consent(v_member, 'biometric_face_identification') then
      perform public._ingest_raise('no_consent', format('face check-out without granted biometric consent for member %s', v_member));
    end if;
    select id, check_in_at into v_session, v_check_in from public.attendance_sessions
     where member_id = v_member and branch_id = p_branch_id and status = 'active'
     limit 1;
    if v_session is not null then
      if v_occurred < v_check_in then
        perform public._ingest_raise('invalid_state', format('check-out at %s precedes check-in at %s', v_occurred, v_check_in));
      end if;
      perform public.finalize_attendance_session(v_session, v_occurred, v_method);
    end if;
    -- No active session → nothing to do; not an error (duplicate checkout after replay).

  when 'track.started' then
    v_text := public._bounded_text(v_payload->>'global_track_id', 64, 'global_track_id');
    v_member := nullif(v_payload->>'member_id', '')::uuid;
    v_conf := (v_payload->>'identity_confidence')::numeric;
    v_camera := nullif(v_payload->>'first_camera_id', '')::uuid;
    perform public._assert_camera_in_branch(v_camera, p_branch_id);
    if v_member is not null then
      perform public._assert_member_in_branch(v_member, p_branch_id, false);
      if v_conf >= 0.85 then
        if not public._has_granted_consent(v_member, 'cctv_workout_tracking') then
          perform public._ingest_raise('no_consent', format('member %s has not consented to CCTV workout tracking', v_member));
        end if;
        v_session := public._active_session_for_member(v_member, p_branch_id);
      end if;
    end if;
    insert into public.person_tracks (edge_device_id, global_track_id, attendance_session_id, member_id, started_at,
      identity_confidence, tracking_confidence, first_camera_id, last_camera_id)
    values (p_device_id, v_text, v_session, case when v_session is not null then v_member end, v_occurred,
      v_conf, (v_payload->>'tracking_confidence')::numeric, v_camera, v_camera)
    on conflict (edge_device_id, global_track_id) do update
      set identity_confidence = excluded.identity_confidence,
          tracking_confidence = excluded.tracking_confidence,
          attendance_session_id = coalesce(public.person_tracks.attendance_session_id, excluded.attendance_session_id),
          member_id = coalesce(public.person_tracks.member_id, excluded.member_id);

  when 'track.ended' then
    v_camera := nullif(v_payload->>'last_camera_id', '')::uuid;
    perform public._assert_camera_in_branch(v_camera, p_branch_id);
    update public.person_tracks
       set ended_at = v_occurred, end_reason = public._bounded_text(v_payload->>'reason', 64, 'reason'),
           last_camera_id = coalesce(v_camera, last_camera_id)
     where edge_device_id = p_device_id
       and global_track_id = public._bounded_text(v_payload->>'global_track_id', 64, 'global_track_id');

  when 'tracking.uncertain' then
    v_text := public._bounded_text(v_payload->>'global_track_id', 64, 'global_track_id');
    v_from := (v_payload->>'from')::timestamptz;
    v_to := (v_payload->>'to')::timestamptz;
    if v_from is null or v_to is null
       or v_from < v_occurred - interval '7 days' or v_to > v_occurred + interval '1 hour' then
      perform public._ingest_raise('invalid_value', 'uncertain segment outside the accepted window');
    end if;
    select attendance_session_id into v_session from public.person_tracks
     where edge_device_id = p_device_id and global_track_id = v_text;
    perform public._require_tracking_consent(v_session);
    insert into public.tracking_uncertain_segments (edge_device_id, global_track_id, attendance_session_id, from_at, to_at, reason)
    values (p_device_id, v_text, v_session, v_from, v_to, public._bounded_text(v_payload->>'reason', 64, 'reason'));

  when 'equipment.usage.started' then
    v_text := public._bounded_text(v_payload->>'global_track_id', 64, 'global_track_id');
    select id, attendance_session_id into v_track, v_session from public.person_tracks
     where edge_device_id = p_device_id and global_track_id = v_text;
    v_member := nullif(v_payload->>'member_id', '')::uuid;
    if v_member is not null then
      perform public._assert_member_in_branch(v_member, p_branch_id, false);
      if v_session is null then
        v_session := public._active_session_for_member(v_member, p_branch_id);
      end if;
    end if;
    if not exists (select 1 from public.equipment where id = (v_payload->>'equipment_id')::uuid and branch_id = p_branch_id) then
      perform public._ingest_raise('branch_mismatch', format('equipment %s not in device branch', v_payload->>'equipment_id'));
    end if;
    v_zone := nullif(v_payload->>'zone_id', '')::uuid;
    v_camera := nullif(v_payload->>'camera_id', '')::uuid;
    perform public._assert_zone_in_branch(v_zone, p_branch_id);
    perform public._assert_camera_in_branch(v_camera, p_branch_id);
    perform public._require_tracking_consent(v_session);
    insert into public.equipment_usage_sessions (edge_device_id, usage_local_id, attendance_session_id, person_track_id, equipment_id,
      zone_id, camera_id, started_at, association_confidence)
    values (p_device_id, public._bounded_text(v_payload->>'usage_local_id', 64, 'usage_local_id'), v_session, v_track,
      (v_payload->>'equipment_id')::uuid, v_zone, v_camera, v_occurred, (v_payload->>'association_confidence')::numeric)
    on conflict (edge_device_id, usage_local_id) do nothing;

  when 'equipment.usage.ended' then
    update public.equipment_usage_sessions
       set ended_at = v_occurred, end_reason = public._bounded_text(v_payload->>'reason', 64, 'reason')
     where edge_device_id = p_device_id
       and usage_local_id = public._bounded_text(v_payload->>'usage_local_id', 64, 'usage_local_id')
       and ended_at is null;

  when 'exercise.activity.started' then
    select id, attendance_session_id into v_usage, v_session from public.equipment_usage_sessions
     where edge_device_id = p_device_id and usage_local_id = public._bounded_text(v_payload->>'usage_local_id', 64, 'usage_local_id');
    if v_usage is null then
      perform public._ingest_raise('unknown_reference', format('unknown usage_local_id %s', v_payload->>'usage_local_id'));
    end if;
    perform public._require_tracking_consent(v_session);
    insert into public.exercise_activity_sessions (edge_device_id, activity_local_id, equipment_usage_session_id, exercise_id,
      started_at, classification_confidence, classifier_model_version_id)
    values (p_device_id, public._bounded_text(v_payload->>'activity_local_id', 64, 'activity_local_id'), v_usage,
      (v_payload->>'exercise_id')::uuid, v_occurred,
      (v_payload->>'classification_confidence')::numeric, nullif(v_payload->>'classifier_model_version_id', '')::uuid)
    on conflict (edge_device_id, activity_local_id) do nothing;

  when 'exercise.activity.ended' then
    update public.exercise_activity_sessions
       set ended_at = v_occurred, active_seconds = (v_payload->>'active_seconds')::numeric
     where edge_device_id = p_device_id
       and activity_local_id = public._bounded_text(v_payload->>'activity_local_id', 64, 'activity_local_id');

  when 'set.started' then
    select a.id, u.attendance_session_id into v_activity, v_session
      from public.exercise_activity_sessions a
      join public.equipment_usage_sessions u on u.id = a.equipment_usage_session_id
     where a.edge_device_id = p_device_id
       and a.activity_local_id = public._bounded_text(v_payload->>'activity_local_id', 64, 'activity_local_id');
    if v_activity is null then
      perform public._ingest_raise('unknown_reference', format('unknown activity_local_id %s', v_payload->>'activity_local_id'));
    end if;
    perform public._require_tracking_consent(v_session);
    insert into public.exercise_sets (edge_device_id, set_local_id, exercise_activity_session_id, set_index, started_at, rest_before_seconds)
    values (p_device_id, public._bounded_text(v_payload->>'set_local_id', 64, 'set_local_id'), v_activity,
      (v_payload->>'set_index')::int, v_occurred, (v_payload->>'rest_before_seconds')::numeric)
    on conflict (edge_device_id, set_local_id) do nothing;

  when 'rep.counted' then
    if v_payload->'quality_label' is not null and jsonb_typeof(v_payload->'quality_label') <> 'null' then
      perform public._ingest_raise('invalid_value', 'quality_label is reserved for a future release');
    end if;
    select s.id, u.attendance_session_id into v_set, v_session
      from public.exercise_sets s
      join public.exercise_activity_sessions a on a.id = s.exercise_activity_session_id
      join public.equipment_usage_sessions u on u.id = a.equipment_usage_session_id
     where s.edge_device_id = p_device_id
       and s.set_local_id = public._bounded_text(v_payload->>'set_local_id', 64, 'set_local_id');
    if v_set is null then
      perform public._ingest_raise('unknown_reference', format('unknown set_local_id %s', v_payload->>'set_local_id'));
    end if;
    v_camera := nullif(v_payload->>'camera_id', '')::uuid;
    perform public._assert_camera_in_branch(v_camera, p_branch_id);
    perform public._require_tracking_consent(v_session);
    insert into public.repetition_events (id, exercise_set_id, rep_index, occurred_at, camera_id, confidence)
    values ((p->>'event_id')::uuid, v_set, (v_payload->>'rep_index')::int, v_occurred, v_camera, (v_payload->>'confidence')::numeric)
    on conflict (exercise_set_id, rep_index) do nothing;
    -- Live counter for dashboards; set.completed is authoritative later.
    update public.exercise_sets set rep_count = greatest(rep_count, (v_payload->>'rep_index')::int) where id = v_set;

  when 'set.completed' then
    select s.id, u.attendance_session_id into v_set, v_session
      from public.exercise_sets s
      join public.exercise_activity_sessions a on a.id = s.exercise_activity_session_id
      join public.equipment_usage_sessions u on u.id = a.equipment_usage_session_id
     where s.edge_device_id = p_device_id
       and s.set_local_id = public._bounded_text(v_payload->>'set_local_id', 64, 'set_local_id');
    perform public._require_tracking_consent(v_session);
    update public.exercise_sets
       set ended_at = v_occurred,
           rep_count = (v_payload->>'rep_count')::int,
           confidence = (v_payload->>'confidence')::numeric,
           verified = (v_payload->>'verified')::boolean
     where id = v_set;

  when 'biometric.enrolled' then
    v_member := (v_payload->>'member_id')::uuid;
    perform public._assert_member_in_branch(v_member, p_branch_id, true);
    -- The consent record must belong to the member, be biometric, granted and unexpired.
    if not exists (
      select 1 from public.consent_records
      where id = (v_payload->>'consent_record_id')::uuid and member_id = v_member
        and consent_type = 'biometric_face_identification' and status = 'granted'
        and (expires_at is null or expires_at > now())
    ) then
      perform public._ingest_raise('no_consent', format('enrollment without a granted, unexpired biometric consent record for member %s', v_member));
    end if;
    insert into public.biometric_enrollments (member_id, edge_device_id, consent_record_id, template_ref, key_id, model_version_id, enrolled_at)
    values (v_member, p_device_id, (v_payload->>'consent_record_id')::uuid,
            public._bounded_text(v_payload->>'template_ref', 64, 'template_ref'),
            public._bounded_text(v_payload->>'key_id', 64, 'key_id'),
            (select id from public.model_versions
              where component = 'face' and name = public._bounded_text(v_payload->>'model_name', 100, 'model_name') limit 1),
            v_occurred)
    on conflict (edge_device_id, template_ref) do nothing;

  when 'biometric.revoked' then
    update public.biometric_enrollments
       set revoked_at = coalesce(revoked_at, v_occurred)
     where edge_device_id = p_device_id
       and template_ref = public._bounded_text(v_payload->>'template_ref', 64, 'template_ref');

  else
    perform public._ingest_raise('invalid_value', format('unsupported event_type %s', left(v_type, 64)));
  end case;
end $$;

-- ----------------------------------------------------------------------------
-- Batch entry point (signature and response shape unchanged; rejections gain "code")
-- ----------------------------------------------------------------------------
create or replace function public.ingest_events(p_device_id uuid, p_batch_id uuid, p_sent_at timestamptz, p_events jsonb)
returns jsonb language plpgsql security definer set search_path = public as $$
declare
  v_branch uuid;
  v_status public.device_status;
  v_ev jsonb;
  v_event_id uuid;
  v_seq bigint;
  v_occurred timestamptz;
  v_rows int;
  v_accepted int := 0;
  v_dups int := 0;
  v_rejected jsonb := '[]'::jsonb;
  v_max_seq bigint;
  v_state text;
  v_err text;
  v_code text;
begin
  select branch_id, status into v_branch, v_status from public.edge_devices where id = p_device_id;
  if v_branch is null then
    raise exception 'unknown device %', p_device_id using errcode = '42501';
  end if;
  if v_status = 'retired' then
    raise exception 'device_retired: device % is retired', p_device_id using errcode = '42501';
  end if;
  if p_batch_id is null or p_sent_at is null then
    raise exception 'batch_id and sent_at are required' using errcode = '22023';
  end if;
  if p_sent_at > now() + interval '1 hour' then
    raise exception 'sent_at is in the future' using errcode = '22023';
  end if;
  if p_events is null or jsonb_typeof(p_events) is distinct from 'array'
     or jsonb_array_length(p_events) = 0 or jsonb_array_length(p_events) > 500 then
    raise exception 'events must be a non-empty array of at most 500 items' using errcode = '22023';
  end if;
  -- Every event must carry a well-formed event_id so it can be acknowledged or rejected individually.
  if exists (select 1 from jsonb_array_elements(p_events) e where not public._is_uuid(e->>'event_id')) then
    raise exception 'every event needs a uuid event_id' using errcode = '22023';
  end if;

  -- Replay protection: a batch id is accepted once.
  begin
    insert into public.ingest_batches (batch_id, edge_device_id, sent_at, event_count, accepted, duplicates, rejected)
    values (p_batch_id, p_device_id, p_sent_at, jsonb_array_length(p_events), 0, 0, 0);
  exception when unique_violation then
    raise exception 'batch % already processed', p_batch_id using errcode = '23505';
  end;

  for v_ev in select value from jsonb_array_elements(p_events) loop
    v_event_id := (v_ev->>'event_id')::uuid;
    v_seq := null;
    v_occurred := null;

    -- One sub-transaction per event: validation, the idempotency claim and the apply
    -- succeed or roll back together.
    begin
      if v_ev->>'schema_version' is distinct from '1' then
        perform public._ingest_raise('invalid_value', 'unsupported schema_version');
      end if;
      if not public._is_uuid(v_ev->>'device_id') or (v_ev->>'device_id')::uuid is distinct from p_device_id then
        perform public._ingest_raise('invalid_value', 'device_id does not match the authenticated device');
      end if;
      v_seq := (v_ev->>'sequence')::bigint;
      if v_seq is null or v_seq < 0 then
        perform public._ingest_raise('invalid_value', 'sequence must be a non-negative integer');
      end if;
      v_occurred := (v_ev->>'occurred_at')::timestamptz;
      if v_occurred is null
         or v_occurred < p_sent_at - interval '7 days'
         or v_occurred > p_sent_at + interval '1 hour' then
        perform public._ingest_raise('invalid_value', format('occurred_at %s outside the accepted window', v_occurred));
      end if;

      -- Idempotency: claim the event id.
      insert into public.event_ingest_log (event_id, edge_device_id, batch_id, sequence, event_type, occurred_at, status)
      values (v_event_id, p_device_id, p_batch_id, v_seq,
              public._bounded_text(v_ev->>'event_type', 64, 'event_type'), v_occurred, 'applied')
      on conflict (event_id) do nothing;
      get diagnostics v_rows = row_count;
      if v_rows = 0 then
        v_dups := v_dups + 1;
      else
        perform public._apply_edge_event(p_device_id, v_branch, v_ev);
        v_accepted := v_accepted + 1;
        v_max_seq := greatest(v_max_seq, v_seq);
      end if;
    exception when others then
      get stacked diagnostics v_state = returned_sqlstate, v_err = message_text;
      v_code := public._ingest_error_code(v_state);
      if v_code is null then
        -- Infrastructure error (deadlock, serialization failure, resources, ...): abort the
        -- whole call so nothing is recorded and the device retries the batch.
        raise;
      end if;
      insert into public.event_ingest_log (event_id, edge_device_id, batch_id, sequence, event_type, occurred_at, status, error)
      values (v_event_id, p_device_id, p_batch_id, coalesce(v_seq, -1), left(coalesce(v_ev->>'event_type', '?'), 64),
              coalesce(v_occurred, p_sent_at), 'rejected', left(v_err, 500))
      on conflict (event_id) do nothing;
      get diagnostics v_rows = row_count;
      if v_rows = 0 then
        v_dups := v_dups + 1;  -- seen before (accepted or rejected in an earlier batch)
      else
        v_rejected := v_rejected || jsonb_build_array(jsonb_build_object(
          'event_id', v_event_id, 'code', v_code, 'reason', public._ingest_reason(v_code)));
      end if;
    end;
  end loop;

  -- One device update per batch (not per event): keeps audit/realtime noise out.
  if v_max_seq is not null then
    update public.edge_devices set last_sequence = v_max_seq
     where id = p_device_id and last_sequence < v_max_seq;
  end if;

  update public.ingest_batches
     set accepted = v_accepted, duplicates = v_dups, rejected = jsonb_array_length(v_rejected)
   where batch_id = p_batch_id;

  return jsonb_build_object(
    'batch_id', p_batch_id,
    'accepted', v_accepted,
    'duplicates', v_dups,
    'rejected', v_rejected,
    'server_time', now()
  );
end $$;

-- ----------------------------------------------------------------------------
-- Device secrets (contract C5)
-- ----------------------------------------------------------------------------
-- Secrets valid now: current ones and those still inside their rotation overlap window.
-- Nothing for a retired device.
create or replace function public.get_device_secrets(p_device_id uuid)
returns table (secret text) language sql stable security definer set search_path = public as $$
  select s.secret
  from public.edge_device_secrets s
  join public.edge_devices d on d.id = s.edge_device_id
  where s.edge_device_id = p_device_id
    and d.status <> 'retired'
    and (s.retired_at is null or s.retired_at > now())
  order by s.created_at desc
$$;

drop index if exists public.idx_device_secrets_device;
create index idx_device_secrets_device on public.edge_device_secrets(edge_device_id, retired_at);

-- Admin: issue a new device secret. Returns the raw secret ONCE; the previous current
-- secret stays valid for 24 h so the device can be updated without downtime.
create or replace function public.issue_device_secret(p_device_id uuid)
returns text language plpgsql security definer set search_path = public, extensions as $$
declare v_branch uuid; v_status public.device_status; v_secret text;
begin
  select branch_id, status into v_branch, v_status from public.edge_devices where id = p_device_id;
  if v_branch is null then raise exception 'device not found' using errcode = 'P0002'; end if;
  if not public.has_branch_role(v_branch, 'owner', 'admin') then
    raise exception 'not authorized' using errcode = '42501';
  end if;
  if v_status = 'retired' then
    raise exception 'device is retired' using errcode = '55000';
  end if;
  v_secret := encode(extensions.gen_random_bytes(32), 'base64');
  update public.edge_device_secrets set retired_at = now() + interval '24 hours'
   where edge_device_id = p_device_id and retired_at is null;
  insert into public.edge_device_secrets (edge_device_id, secret_hash, secret)
  values (p_device_id, encode(extensions.digest(v_secret, 'sha256'), 'hex'), v_secret);
  return v_secret;
end $$;

-- Retiring a device retires every secret it holds, immediately (no overlap).
create or replace function public.retire_device_secrets_on_retire()
returns trigger language plpgsql security definer set search_path = public as $$
begin
  update public.edge_device_secrets set retired_at = now()
   where edge_device_id = new.id and (retired_at is null or retired_at > now());
  return new;
end $$;

drop trigger if exists trg_edge_devices_retire_secrets on public.edge_devices;
create trigger trg_edge_devices_retire_secrets
  after update of status on public.edge_devices
  for each row when (new.status = 'retired' and old.status is distinct from 'retired')
  execute function public.retire_device_secrets_on_retire();

-- ----------------------------------------------------------------------------
-- Audit edge_devices only for meaningful changes (not last_sequence / last_seen_at /
-- software_version churn from ingest and device sync).
-- ----------------------------------------------------------------------------
drop trigger if exists audit_edge_devices on public.edge_devices;
create trigger audit_edge_devices
  after insert or delete on public.edge_devices
  for each row execute function public.audit_row_change();
drop trigger if exists audit_edge_devices_update on public.edge_devices;
create trigger audit_edge_devices_update
  after update on public.edge_devices
  for each row when (
    old.branch_id is distinct from new.branch_id
    or old.name is distinct from new.name
    or old.status is distinct from new.status
    or old.public_key is distinct from new.public_key
  )
  execute function public.audit_row_change();

-- ----------------------------------------------------------------------------
-- Privileges: every ingest helper is service-role only.
-- ----------------------------------------------------------------------------
revoke all on function public._ingest_raise(text, text) from public, anon, authenticated;
revoke all on function public._ingest_error_code(text) from public, anon, authenticated;
revoke all on function public._ingest_reason(text) from public, anon, authenticated;
revoke all on function public._is_uuid(text) from public, anon, authenticated;
revoke all on function public._bounded_text(text, int, text) from public, anon, authenticated;
revoke all on function public._assert_camera_in_branch(uuid, uuid) from public, anon, authenticated;
revoke all on function public._assert_zone_in_branch(uuid, uuid) from public, anon, authenticated;
revoke all on function public._assert_member_in_branch(uuid, uuid, boolean) from public, anon, authenticated;
revoke all on function public._active_session_for_member(uuid, uuid) from public, anon, authenticated;
revoke all on function public._require_tracking_consent(uuid) from public, anon, authenticated;
revoke all on function public.retire_device_secrets_on_retire() from public, anon, authenticated;
revoke all on function public.ingest_events(uuid, uuid, timestamptz, jsonb) from public, anon, authenticated;
revoke all on function public._apply_edge_event(uuid, uuid, jsonb) from public, anon, authenticated;
revoke all on function public.get_device_secrets(uuid) from public, anon, authenticated;
grant execute on function public.issue_device_secret(uuid) to authenticated;
