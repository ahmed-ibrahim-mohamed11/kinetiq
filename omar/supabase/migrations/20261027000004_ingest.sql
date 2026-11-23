-- ============================================================================
-- Edge event ingest (migration 0004)
-- ----------------------------------------------------------------------------
-- `ingest_events` is called ONLY by the ingest-events Edge Function using the
-- service role, after it has verified the device HMAC signature. It applies a
-- batch of events.v1 envelopes idempotently:
--   * event_id is the idempotency key (event_ingest_log PK).
--   * Each event is applied inside its own sub-transaction so one bad event
--     does not poison the batch; failures are recorded as `rejected`.
--   * batch_id replay is rejected outright (ingest_batches PK).
-- ============================================================================

-- Resolve the attendance session for a member (active one), or null.
create or replace function public._active_session_for_member(p_member_id uuid)
returns uuid language sql stable security definer set search_path = public as $$
  select id from public.attendance_sessions where member_id = p_member_id and status = 'active' limit 1
$$;

create or replace function public._has_granted_consent(p_member_id uuid, p_type public.consent_type)
returns boolean language sql stable security definer set search_path = public as $$
  select exists (
    select 1 from public.consent_records
    where member_id = p_member_id and consent_type = p_type and status = 'granted'
      and (expires_at is null or expires_at > now())
  )
$$;

-- Apply a single event. Raises on validation failure; caller catches.
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
begin
  if (p->>'branch_id')::uuid <> p_branch_id then
    raise exception 'event branch % does not match device branch %', p->>'branch_id', p_branch_id;
  end if;

  case v_type

  when 'device.heartbeat' then
    insert into public.device_health_logs (edge_device_id, recorded_at, software_version, uptime_seconds, gpu_utilization,
      gpu_memory_used_mb, cpu_utilization, queue_depth, cameras_online, cameras_total, avg_pipeline_latency_ms)
    values (p_device_id, v_occurred, v_payload->>'software_version', (v_payload->>'uptime_seconds')::bigint,
      (v_payload->>'gpu_utilization')::numeric, (v_payload->>'gpu_memory_used_mb')::numeric, (v_payload->>'cpu_utilization')::numeric,
      (v_payload->>'queue_depth')::int, (v_payload->>'cameras_online')::int, (v_payload->>'cameras_total')::int,
      (v_payload->>'avg_pipeline_latency_ms')::numeric);
    update public.edge_devices
       set last_seen_at = greatest(coalesce(last_seen_at, v_occurred), v_occurred),
           software_version = coalesce(v_payload->>'software_version', software_version),
           status = case when status in ('provisioning', 'offline', 'degraded') then 'active' else status end
     where id = p_device_id;

  when 'camera.status' then
    v_camera := (v_payload->>'camera_id')::uuid;
    if not exists (select 1 from public.cameras where id = v_camera and branch_id = p_branch_id) then
      raise exception 'camera % not in device branch', v_camera;
    end if;
    insert into public.camera_status_logs (camera_id, recorded_at, status, fps_measured, resolution_w, resolution_h, error)
    values (v_camera, v_occurred, v_payload->>'status', (v_payload->>'fps_measured')::numeric,
      (v_payload->>'resolution_w')::int, (v_payload->>'resolution_h')::int, v_payload->>'error');
    update public.cameras set last_status = v_payload->>'status', last_status_at = v_occurred where id = v_camera;

  when 'identity.checkin' then
    v_member := (v_payload->>'member_id')::uuid;
    v_method := (v_payload->>'method')::public.checkin_method;
    v_conf := (v_payload->>'confidence')::numeric;
    if not exists (select 1 from public.members where id = v_member and branch_id = p_branch_id and status = 'active') then
      raise exception 'member % is not an active member of this branch', v_member;
    end if;
    -- Hard privacy gate: face identification requires a granted biometric consent.
    if v_method = 'face' and not public._has_granted_consent(v_member, 'biometric_face_identification') then
      raise exception 'face check-in without granted biometric consent for member %', v_member;
    end if;
    if not public._has_granted_consent(v_member, 'cctv_workout_tracking') then
      raise exception 'member % has not consented to CCTV workout tracking', v_member;
    end if;
    v_session := public._active_session_for_member(v_member);
    if v_session is null then
      insert into public.attendance_sessions (member_id, branch_id, check_in_at, check_in_method, check_in_confidence)
      values (v_member, p_branch_id, v_occurred, v_method, v_conf)
      returning id into v_session;
    end if;
    -- Bind the entrance track if present.
    if v_payload->>'global_track_id' is not null then
      update public.person_tracks
         set attendance_session_id = coalesce(attendance_session_id, v_session), member_id = coalesce(member_id, v_member)
       where edge_device_id = p_device_id and global_track_id = v_payload->>'global_track_id';
    end if;

  when 'identity.checkout' then
    v_member := (v_payload->>'member_id')::uuid;
    v_session := public._active_session_for_member(v_member);
    if v_session is not null then
      perform public.finalize_attendance_session(v_session, v_occurred, (v_payload->>'method')::public.checkin_method);
    end if;
    -- No active session → nothing to do; not an error (duplicate checkout after replay).

  when 'track.started' then
    v_member := nullif(v_payload->>'member_id', '')::uuid;
    v_conf := (v_payload->>'identity_confidence')::numeric;
    v_session := case when v_member is not null and v_conf >= 0.85 then public._active_session_for_member(v_member) end;
    insert into public.person_tracks (edge_device_id, global_track_id, attendance_session_id, member_id, started_at,
      identity_confidence, tracking_confidence, first_camera_id, last_camera_id)
    values (p_device_id, v_payload->>'global_track_id', v_session, case when v_session is not null then v_member end, v_occurred,
      v_conf, (v_payload->>'tracking_confidence')::numeric, (v_payload->>'first_camera_id')::uuid, (v_payload->>'first_camera_id')::uuid)
    on conflict (edge_device_id, global_track_id) do update
      set identity_confidence = excluded.identity_confidence,
          tracking_confidence = excluded.tracking_confidence,
          attendance_session_id = coalesce(public.person_tracks.attendance_session_id, excluded.attendance_session_id),
          member_id = coalesce(public.person_tracks.member_id, excluded.member_id);

  when 'track.ended' then
    update public.person_tracks
       set ended_at = v_occurred, end_reason = v_payload->>'reason',
           last_camera_id = coalesce(nullif(v_payload->>'last_camera_id', '')::uuid, last_camera_id)
     where edge_device_id = p_device_id and global_track_id = v_payload->>'global_track_id';

  when 'tracking.uncertain' then
    select attendance_session_id into v_session from public.person_tracks
     where edge_device_id = p_device_id and global_track_id = v_payload->>'global_track_id';
    insert into public.tracking_uncertain_segments (edge_device_id, global_track_id, attendance_session_id, from_at, to_at, reason)
    values (p_device_id, v_payload->>'global_track_id', v_session, (v_payload->>'from')::timestamptz, (v_payload->>'to')::timestamptz, v_payload->>'reason');

  when 'equipment.usage.started' then
    select id, attendance_session_id into v_track, v_session from public.person_tracks
     where edge_device_id = p_device_id and global_track_id = v_payload->>'global_track_id';
    if v_session is null and v_payload->>'member_id' is not null then
      v_session := public._active_session_for_member((v_payload->>'member_id')::uuid);
    end if;
    if not exists (select 1 from public.equipment where id = (v_payload->>'equipment_id')::uuid and branch_id = p_branch_id) then
      raise exception 'equipment % not in device branch', v_payload->>'equipment_id';
    end if;
    insert into public.equipment_usage_sessions (edge_device_id, usage_local_id, attendance_session_id, person_track_id, equipment_id,
      zone_id, camera_id, started_at, association_confidence)
    values (p_device_id, v_payload->>'usage_local_id', v_session, v_track, (v_payload->>'equipment_id')::uuid,
      nullif(v_payload->>'zone_id', '')::uuid, nullif(v_payload->>'camera_id', '')::uuid, v_occurred, (v_payload->>'association_confidence')::numeric)
    on conflict (edge_device_id, usage_local_id) do nothing;

  when 'equipment.usage.ended' then
    update public.equipment_usage_sessions
       set ended_at = v_occurred, end_reason = v_payload->>'reason'
     where edge_device_id = p_device_id and usage_local_id = v_payload->>'usage_local_id' and ended_at is null;

  when 'exercise.activity.started' then
    select id into v_usage from public.equipment_usage_sessions
     where edge_device_id = p_device_id and usage_local_id = v_payload->>'usage_local_id';
    if v_usage is null then raise exception 'unknown usage_local_id %', v_payload->>'usage_local_id'; end if;
    insert into public.exercise_activity_sessions (edge_device_id, activity_local_id, equipment_usage_session_id, exercise_id,
      started_at, classification_confidence, classifier_model_version_id)
    values (p_device_id, v_payload->>'activity_local_id', v_usage, (v_payload->>'exercise_id')::uuid, v_occurred,
      (v_payload->>'classification_confidence')::numeric, nullif(v_payload->>'classifier_model_version_id', '')::uuid)
    on conflict (edge_device_id, activity_local_id) do nothing;

  when 'exercise.activity.ended' then
    update public.exercise_activity_sessions
       set ended_at = v_occurred, active_seconds = (v_payload->>'active_seconds')::numeric
     where edge_device_id = p_device_id and activity_local_id = v_payload->>'activity_local_id';

  when 'set.started' then
    select id into v_activity from public.exercise_activity_sessions
     where edge_device_id = p_device_id and activity_local_id = v_payload->>'activity_local_id';
    if v_activity is null then raise exception 'unknown activity_local_id %', v_payload->>'activity_local_id'; end if;
    insert into public.exercise_sets (edge_device_id, set_local_id, exercise_activity_session_id, set_index, started_at, rest_before_seconds)
    values (p_device_id, v_payload->>'set_local_id', v_activity, (v_payload->>'set_index')::int, v_occurred, (v_payload->>'rest_before_seconds')::numeric)
    on conflict (edge_device_id, set_local_id) do nothing;

  when 'rep.counted' then
    if v_payload->'quality_label' is not null and jsonb_typeof(v_payload->'quality_label') <> 'null' then
      raise exception 'quality_label is reserved for a future release';
    end if;
    select id into v_set from public.exercise_sets
     where edge_device_id = p_device_id and set_local_id = v_payload->>'set_local_id';
    if v_set is null then raise exception 'unknown set_local_id %', v_payload->>'set_local_id'; end if;
    insert into public.repetition_events (id, exercise_set_id, rep_index, occurred_at, camera_id, confidence)
    values ((p->>'event_id')::uuid, v_set, (v_payload->>'rep_index')::int, v_occurred,
      nullif(v_payload->>'camera_id', '')::uuid, (v_payload->>'confidence')::numeric)
    on conflict (exercise_set_id, rep_index) do nothing;
    -- Live counter for dashboards; set.completed is authoritative later.
    update public.exercise_sets set rep_count = greatest(rep_count, (v_payload->>'rep_index')::int) where id = v_set;

  when 'set.completed' then
    update public.exercise_sets
       set ended_at = v_occurred,
           rep_count = (v_payload->>'rep_count')::int,
           confidence = (v_payload->>'confidence')::numeric,
           verified = (v_payload->>'verified')::boolean
     where edge_device_id = p_device_id and set_local_id = v_payload->>'set_local_id';

  when 'biometric.enrolled' then
    v_member := (v_payload->>'member_id')::uuid;
    if not exists (select 1 from public.members where id = v_member and branch_id = p_branch_id) then
      raise exception 'member % is not in this branch', v_member;
    end if;
    -- The consent record must belong to the member, be biometric, and be granted.
    if not exists (
      select 1 from public.consent_records
      where id = (v_payload->>'consent_record_id')::uuid and member_id = v_member
        and consent_type = 'biometric_face_identification' and status = 'granted'
    ) then
      raise exception 'enrollment without a granted biometric consent record for member %', v_member;
    end if;
    insert into public.biometric_enrollments (member_id, edge_device_id, consent_record_id, template_ref, key_id, model_version_id, enrolled_at)
    values (v_member, p_device_id, (v_payload->>'consent_record_id')::uuid, v_payload->>'template_ref', v_payload->>'key_id',
            (select id from public.model_versions where component = 'face' and name = v_payload->>'model_name' limit 1), v_occurred)
    on conflict (edge_device_id, template_ref) do nothing;

  when 'biometric.revoked' then
    update public.biometric_enrollments
       set revoked_at = coalesce(revoked_at, v_occurred)
     where edge_device_id = p_device_id and template_ref = v_payload->>'template_ref';

  else
    raise exception 'unsupported event_type %', v_type;
  end case;
end $$;

-- ----------------------------------------------------------------------------
-- Batch entry point
-- ----------------------------------------------------------------------------
create or replace function public.ingest_events(p_device_id uuid, p_batch_id uuid, p_sent_at timestamptz, p_events jsonb)
returns jsonb language plpgsql security definer set search_path = public as $$
declare
  v_branch uuid;
  v_ev jsonb;
  v_event_id uuid;
  v_seq bigint;
  v_rows int;
  v_accepted int := 0;
  v_dups int := 0;
  v_rejected jsonb := '[]'::jsonb;
  v_err text;
begin
  select branch_id into v_branch from public.edge_devices where id = p_device_id and status <> 'retired';
  if v_branch is null then
    raise exception 'unknown or retired device %', p_device_id using errcode = '42501';
  end if;
  if jsonb_typeof(p_events) <> 'array' or jsonb_array_length(p_events) = 0 or jsonb_array_length(p_events) > 500 then
    raise exception 'events must be a non-empty array of at most 500 items' using errcode = '22023';
  end if;

  -- Replay protection: a batch id is accepted once.
  begin
    insert into public.ingest_batches (batch_id, edge_device_id, sent_at, event_count, accepted, duplicates, rejected)
    values (p_batch_id, p_device_id, p_sent_at, jsonb_array_length(p_events), 0, 0, 0);
  exception when unique_violation then
    raise exception 'batch % already processed', p_batch_id using errcode = '23505';
  end;

  for v_ev in select * from jsonb_array_elements(p_events) loop
    v_event_id := (v_ev->>'event_id')::uuid;
    v_seq := (v_ev->>'sequence')::bigint;

    if (v_ev->>'schema_version') <> '1' then
      v_rejected := v_rejected || jsonb_build_object('event_id', v_event_id, 'reason', 'unsupported schema_version');
      insert into public.event_ingest_log (event_id, edge_device_id, batch_id, sequence, event_type, occurred_at, status, error)
      values (v_event_id, p_device_id, p_batch_id, v_seq, coalesce(v_ev->>'event_type', '?'), coalesce((v_ev->>'occurred_at')::timestamptz, now()), 'rejected', 'unsupported schema_version')
      on conflict (event_id) do nothing;
      continue;
    end if;
    if (v_ev->>'device_id')::uuid <> p_device_id then
      v_rejected := v_rejected || jsonb_build_object('event_id', v_event_id, 'reason', 'device_id mismatch');
      continue;
    end if;

    -- Idempotency: claim the event id first.
    insert into public.event_ingest_log (event_id, edge_device_id, batch_id, sequence, event_type, occurred_at, status)
    values (v_event_id, p_device_id, p_batch_id, v_seq, v_ev->>'event_type', (v_ev->>'occurred_at')::timestamptz, 'applied')
    on conflict (event_id) do nothing;
    get diagnostics v_rows = row_count;
    if v_rows = 0 then
      v_dups := v_dups + 1;
      continue;
    end if;

    begin
      perform public._apply_edge_event(p_device_id, v_branch, v_ev);
      v_accepted := v_accepted + 1;
      update public.edge_devices set last_sequence = greatest(last_sequence, v_seq) where id = p_device_id;
    exception when others then
      v_err := left(sqlerrm, 500);
      update public.event_ingest_log set status = 'rejected', error = v_err where event_id = v_event_id;
      v_rejected := v_rejected || jsonb_build_object('event_id', v_event_id, 'reason', v_err);
    end;
  end loop;

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

-- Device secret lookup for the Edge Function (service role only).
create or replace function public.get_device_secrets(p_device_id uuid)
returns table (secret text) language sql stable security definer set search_path = public as $$
  select s.secret from public.edge_device_secrets s
  where s.edge_device_id = p_device_id and s.retired_at is null
$$;

-- Admin: issue a new device secret. Returns the raw secret ONCE; store it on the device.
create or replace function public.issue_device_secret(p_device_id uuid)
returns text language plpgsql security definer set search_path = public, extensions as $$
declare v_branch uuid; v_secret text;
begin
  select branch_id into v_branch from public.edge_devices where id = p_device_id;
  if v_branch is null then raise exception 'device not found' using errcode = 'P0002'; end if;
  if not public.has_branch_role(v_branch, 'owner', 'admin') then
    raise exception 'not authorized' using errcode = '42501';
  end if;
  v_secret := encode(extensions.gen_random_bytes(32), 'base64');
  update public.edge_device_secrets set retired_at = now() + interval '24 hours'
   where edge_device_id = p_device_id and retired_at is null;
  insert into public.edge_device_secrets (edge_device_id, secret_hash, secret)
  values (p_device_id, encode(extensions.digest(v_secret, 'sha256'), 'hex'), v_secret);
  return v_secret;
end $$;

revoke all on function public.ingest_events(uuid, uuid, timestamptz, jsonb) from public, anon, authenticated;
revoke all on function public._apply_edge_event(uuid, uuid, jsonb) from public, anon, authenticated;
revoke all on function public._active_session_for_member(uuid) from public, anon, authenticated;
revoke all on function public._has_granted_consent(uuid, public.consent_type) from public, anon, authenticated;
revoke all on function public.get_device_secrets(uuid) from public, anon, authenticated;
grant execute on function public.issue_device_secret(uuid) to authenticated;
