-- ============================================================================
-- Migration 0016: Fix device_status enum value ('active') in enroll_member_face
-- ============================================================================

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

