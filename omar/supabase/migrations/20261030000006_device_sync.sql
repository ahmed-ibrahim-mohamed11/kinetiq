-- ============================================================================
-- Device sync snapshot (migration 0006)
-- ----------------------------------------------------------------------------
-- `device_sync_snapshot` returns everything an edge device needs to operate
-- offline: cameras + zones + equipment + exercise catalogue for its branch,
-- the granted consents of the branch's members (ids only — no biometric data),
-- revoked enrollment references held by this device, and the member list for
-- enrollment look-ups. Called only by the device-sync Edge Function with the
-- service role after HMAC verification.
-- ============================================================================

create or replace function public.device_sync_snapshot(p_device_id uuid)
returns jsonb language plpgsql stable security definer set search_path = public as $$
declare
  v_branch uuid;
  v_out jsonb;
begin
  select branch_id into v_branch from public.edge_devices where id = p_device_id and status <> 'retired';
  if v_branch is null then
    raise exception 'unknown or retired device %', p_device_id using errcode = '42501';
  end if;

  select jsonb_build_object(
    'schema_version', 1,
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
      select jsonb_object_agg(member_id, types) from (
        select cr.member_id, jsonb_object_agg(cr.consent_type::text, cr.id) as types
        from public.consent_records cr
        join public.members m on m.id = cr.member_id
        where m.branch_id = v_branch and m.status = 'active' and cr.status = 'granted'
          and (cr.expires_at is null or cr.expires_at > now())
        group by cr.member_id
      ) s
    ), '{}'::jsonb),
    'members', coalesce((
      select jsonb_agg(jsonb_build_object('id', m.id, 'member_code', m.member_code, 'full_name', m.full_name) order by m.full_name)
      from public.members m where m.branch_id = v_branch and m.status = 'active'
    ), '[]'::jsonb),
    'revoked_template_refs', coalesce((
      select jsonb_agg(b.template_ref) from public.biometric_enrollments b
      where b.edge_device_id = p_device_id and b.revoked_at is not null
    ), '[]'::jsonb),
    'active_template_refs', coalesce((
      select jsonb_agg(b.template_ref) from public.biometric_enrollments b
      where b.edge_device_id = p_device_id and b.revoked_at is null
    ), '[]'::jsonb)
  ) into v_out;

  update public.edge_devices set last_seen_at = greatest(coalesce(last_seen_at, now()), now()) where id = p_device_id;
  return v_out;
end $$;

revoke all on function public.device_sync_snapshot(uuid) from public, anon, authenticated;
