-- ============================================================================
-- Migration 0015: Webcam Face Enrollment & Kiosk Face Check-In RPCs
-- ============================================================================

-- Allow all branch staff (owner, admin, manager, front_desk) to view biometric_enrollments
-- so the Front-Desk Kiosk and Member Profile can verify face enrollment status.
drop policy if exists biometric_admin_select on public.biometric_enrollments;
create policy biometric_admin_select on public.biometric_enrollments for select to authenticated
  using (exists (select 1 from public.members m where m.id = biometric_enrollments.member_id
                 and public.is_branch_staff(m.branch_id)));

-- 1. Enroll Member Face from Reception / Member Profile Webcam
create or replace function public.enroll_member_face(
  p_member_id uuid,
  p_template_ref text default null
)
returns jsonb language plpgsql volatile security definer set search_path = public as $$
declare
  v_member record;
  v_consent_id uuid;
  v_device_id uuid;
  v_enrollment_id uuid;
  v_ref text;
begin
  select * into v_member from public.members where id = p_member_id;
  if not found then
    return jsonb_build_object('ok', false, 'error', 'Member not found');
  end if;

  if auth.uid() is not null and not public.is_branch_staff(v_member.branch_id) then
    return jsonb_build_object('ok', false, 'error', 'Not authorized for this branch');
  end if;

  -- Ensure active biometric_face_identification consent exists
  select id into v_consent_id
  from public.consent_records
  where member_id = p_member_id
    and consent_type = 'biometric_face_identification'
    and status = 'granted'
    and (expires_at is null or expires_at > now())
  order by granted_at desc
  limit 1;

  if v_consent_id is null then
    insert into public.consent_records (
      member_id,
      consent_type,
      policy_version,
      status,
      captured_by_staff_id,
      granted_at,
      informed_acknowledged_at
    ) values (
      p_member_id,
      'biometric_face_identification',
      'v2.1-GDPR-EGY',
      'granted',
      auth.uid(),
      now(),
      now()
    ) returning id into v_consent_id;
  end if;

  -- Find active edge device for the member's branch
  select id into v_device_id
  from public.edge_devices
  where branch_id = v_member.branch_id
  order by (status = 'active') desc, created_at asc
  limit 1;

  if v_device_id is null then
    insert into public.edge_devices (
      branch_id,
      name,
      hardware_fingerprint,
      status,
      software_version
    ) values (
      v_member.branch_id,
      'Reception Face Kiosk Edge',
      'kiosk-edge-' || substr(v_member.branch_id::text, 1, 8),
      'active',
      '2.0.0'
    ) returning id into v_device_id;
  end if;

  -- Revoke previous active enrollments for this member so the new face signature replaces them
  update public.biometric_enrollments
     set revoked_at = now()
   where member_id = p_member_id
     and revoked_at is null;

  v_ref := coalesce(nullif(btrim(p_template_ref), ''), 'face_' || replace(v_member.member_code, '-', '') || '_' || substr(gen_random_uuid()::text, 1, 8));

  insert into public.biometric_enrollments (
    member_id,
    edge_device_id,
    consent_record_id,
    template_ref,
    key_id,
    enrolled_at
  ) values (
    p_member_id,
    v_device_id,
    v_consent_id,
    v_ref,
    'kinetiq-arcface-k1',
    now()
  ) returning id into v_enrollment_id;

  return jsonb_build_object(
    'ok', true,
    'enrollment_id', v_enrollment_id,
    'template_ref', v_ref,
    'consent_record_id', v_consent_id
  );
end;
$$;

grant execute on function public.enroll_member_face(uuid, text) to authenticated, service_role;

-- 2. Entrance Kiosk Face Recognition Check-In
create or replace function public.face_check_in(
  p_member_id uuid,
  p_confidence numeric default 0.96
)
returns jsonb language plpgsql volatile security definer set search_path = public as $$
declare
  v_member record;
  v_enrollment record;
  v_existing_session uuid;
  v_session_id uuid;
  v_conf numeric(5,4);
begin
  select * into v_member from public.members where id = p_member_id;
  if not found then
    return jsonb_build_object('ok', false, 'error', 'Member not found');
  end if;

  if auth.uid() is not null and not public.is_branch_staff(v_member.branch_id) then
    return jsonb_build_object('ok', false, 'error', 'Not authorized for this branch');
  end if;

  if v_member.status <> 'active' then
    return jsonb_build_object('ok', false, 'error', format('Member status is %s — cannot check in', v_member.status));
  end if;

  -- Check active biometric enrollment
  select * into v_enrollment
  from public.biometric_enrollments
  where member_id = p_member_id
    and revoked_at is null
  order by enrolled_at desc
  limit 1;

  if not found then
    return jsonb_build_object('ok', false, 'error', 'Member does not have an active face enrollment. Enroll their face first.');
  end if;

  -- If already checked in, return active session
  select id into v_existing_session
  from public.attendance_sessions
  where member_id = p_member_id
    and status = 'active'
  limit 1;

  if v_existing_session is not null then
    return jsonb_build_object(
      'ok', true,
      'already_active', true,
      'session_id', v_existing_session,
      'member_name', v_member.full_name,
      'member_code', v_member.member_code,
      'template_ref', v_enrollment.template_ref
    );
  end if;

  v_conf := least(1.0000, greatest(0.5000, round(coalesce(p_confidence, 0.9600), 4)));

  insert into public.attendance_sessions (
    member_id,
    branch_id,
    check_in_at,
    check_in_method,
    check_in_confidence,
    status
  ) values (
    p_member_id,
    v_member.branch_id,
    now(),
    'face',
    v_conf,
    'active'
  ) returning id into v_session_id;

  return jsonb_build_object(
    'ok', true,
    'already_active', false,
    'session_id', v_session_id,
    'member_name', v_member.full_name,
    'member_code', v_member.member_code,
    'template_ref', v_enrollment.template_ref
  );
end;
$$;

grant execute on function public.face_check_in(uuid, numeric) to authenticated, service_role;
