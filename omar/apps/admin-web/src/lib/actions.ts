'use server';

import 'server-only';
import { revalidatePath } from 'next/cache';
import { z, type ZodError } from 'zod';
import {
  CreateCameraInput,
  CreateCameraZoneInput,
  CreateEquipmentInput,
  CreateMemberInput,
  CreateMembershipPlanInput,
  CreatePromotionInput,
  DeleteCameraZoneInput,
  FreezeSubscriptionInput,
  IssueDeviceSecretInput,
  ManualCheckInInput,
  ManualCheckOutInput,
  RecordConsentInput,
  RecordPaymentInput,
  RegisterNewMemberFullInput,
  RenewSubscriptionInput,
  RevokeBiometricEnrollmentInput,
  UnfreezeSubscriptionInput,
  WithdrawConsentInput,
} from '@smartgym/api-contracts';
import { MemberCode, NonDegeneratePolygon, normalizePhone } from '@smartgym/validation';
import type { ActionResult } from './action-result';
import { createClient } from '@smartgym/supabase-client/server';

// ────────────────────────────────────────────────────────────────────────────
// helpers
// ────────────────────────────────────────────────────────────────────────────

function str(fd: FormData, key: string): string {
  const v = fd.get(key);
  return typeof v === 'string' ? v.trim() : '';
}

function optStr(fd: FormData, key: string): string | null {
  const s = str(fd, key);
  return s === '' ? null : s;
}

function optNum(fd: FormData, key: string): number | null {
  const s = str(fd, key);
  if (s === '') return null;
  return Number(s);
}

function bool(fd: FormData, key: string, fallback = false): boolean {
  const v = fd.get(key);
  if (v === null) return fallback;
  return v === 'on' || v === 'true' || v === '1';
}

function zodMessage(err: ZodError): string {
  return err.issues.map((i) => (i.path.length ? `${i.path.join('.')}: ${i.message}` : i.message)).join('; ');
}

function dbMessage(error: { code?: string; message: string; details?: string | null }): string {
  switch (error.code) {
    case '42501':
      return 'Not authorized for this branch or action.';
    case '22P02':
      return 'Invalid identifier.';
    case '22023':
      return `Invalid value: ${error.message}`;
    case '55000':
      return `Not possible in the current state: ${error.message}`;
    case '23505':
      return 'A record with the same unique value already exists.';
    case '23503':
      return 'Referenced record does not exist.';
    case '23514':
      return `Rejected by a database rule: ${error.message}`;
    case 'P0002':
      return 'Record not found.';
    default:
      return error.message || 'Database error.';
  }
}

/** Parse FormData fields into a Zod schema; empty strings count as missing. */
function parseForm<T extends z.ZodTypeAny>(schema: T, fd: FormData, keys: readonly string[]): z.SafeParseReturnType<z.input<T>, z.output<T>> {
  const raw: Record<string, string | undefined> = {};
  for (const k of keys) raw[k] = optStr(fd, k) ?? undefined;
  return schema.safeParse(raw);
}

// ────────────────────────────────────────────────────────────────────────────
// members & consent
// ────────────────────────────────────────────────────────────────────────────

export async function createMember(_prev: ActionResult | null, fd: FormData): Promise<ActionResult> {
  const codeParse = MemberCode.safeParse(str(fd, 'member_code'));
  if (!codeParse.success) return { ok: false, error: `member_code: ${codeParse.error.issues[0]?.message ?? 'invalid'}` };

  const rawPhone = optStr(fd, 'phone');
  const phone = rawPhone ? normalizePhone(rawPhone) : null;
  if (rawPhone && !phone) return { ok: false, error: 'phone: use an Egyptian mobile number (010/011/012/015…) or E.164 format (+20…).' };

  const parsed = CreateMemberInput.safeParse({
    branch_id: str(fd, 'branch_id'),
    full_name: str(fd, 'full_name'),
    member_code: codeParse.data,
    phone,
    email: optStr(fd, 'email'),
  });
  if (!parsed.success) return { ok: false, error: zodMessage(parsed.error) };

  const supabase = await createClient();
  const { data: branch, error: branchErr } = await supabase
    .from('branches')
    .select('id, organization_id')
    .eq('id', parsed.data.branch_id)
    .maybeSingle();
  if (branchErr) return { ok: false, error: dbMessage(branchErr) };
  if (!branch) return { ok: false, error: 'Branch not found or not accessible.' };

  const { data: member, error } = await supabase
    .from('members')
    .insert({
      organization_id: (branch as { organization_id: string }).organization_id,
      branch_id: parsed.data.branch_id,
      full_name: parsed.data.full_name,
      member_code: parsed.data.member_code,
      phone: parsed.data.phone,
      email: parsed.data.email,
    })
    .select('id')
    .single();
  if (error) return { ok: false, error: dbMessage(error) };

  revalidatePath('/members');
  revalidatePath('/dashboard');
  return { ok: true, message: `Member ${parsed.data.full_name} created.`, data: (member as { id: string }).id };
}

export async function recordConsent(_prev: ActionResult | null, fd: FormData): Promise<ActionResult> {
  const parsed = RecordConsentInput.safeParse({
    member_id: str(fd, 'member_id'),
    consent_type: str(fd, 'consent_type'),
    policy_version: str(fd, 'policy_version'),
    informed_acknowledged: bool(fd, 'informed_acknowledged') ? true : undefined,
  });
  if (!parsed.success) return { ok: false, error: zodMessage(parsed.error) };

  const supabase = await createClient();
  const { data: auth } = await supabase.auth.getUser();
  if (!auth.user) return { ok: false, error: 'Your session expired. Sign in again.' };
  // The database stamps captured_by_staff_id, granted_at and informed_acknowledged_at
  // with the caller and server time; the policy requires captured_by_staff_id = auth.uid().
  const { error } = await supabase.from('consent_records').insert({
    member_id: parsed.data.member_id,
    consent_type: parsed.data.consent_type,
    policy_version: parsed.data.policy_version,
    captured_by_staff_id: auth.user.id,
    informed_acknowledged_at: new Date().toISOString(),
  });
  if (error) return { ok: false, error: dbMessage(error) };

  revalidatePath(`/members/${parsed.data.member_id}`);
  return { ok: true, message: 'Consent recorded.' };
}

export async function withdrawConsent(_prev: ActionResult | null, fd: FormData): Promise<ActionResult> {
  const parsed = parseForm(WithdrawConsentInput, fd, ['consent_record_id', 'member_id']);
  if (!parsed.success) return { ok: false, error: zodMessage(parsed.error) };
  const { consent_record_id: consentId, member_id: memberId } = parsed.data;

  const supabase = await createClient();
  const { data, error } = await supabase
    .from('consent_records')
    .update({ status: 'withdrawn', withdrawn_at: new Date().toISOString() })
    .eq('id', consentId)
    .eq('status', 'granted')
    .select('id');
  if (error) return { ok: false, error: dbMessage(error) };
  if (!data || (data as unknown[]).length === 0) return { ok: false, error: 'Consent was not withdrawn (already withdrawn or not accessible).' };

  if (memberId) revalidatePath(`/members/${memberId}`);
  return { ok: true, message: 'Consent withdrawn. Any biometric enrollment reference is revoked automatically.' };
}

/**
 * Revoke one biometric enrollment reference (branch owner/admin only). Direct
 * updates of biometric_enrollments are not allowed from the browser; the RPC
 * audits the change and the edge drops the template at its next sync.
 */
export async function revokeBiometricEnrollment(_prev: ActionResult | null, fd: FormData): Promise<ActionResult> {
  const parsed = parseForm(RevokeBiometricEnrollmentInput, fd, ['enrollment_id', 'member_id']);
  if (!parsed.success) return { ok: false, error: zodMessage(parsed.error) };

  const supabase = await createClient();
  const { error } = await supabase.rpc('revoke_biometric_enrollment', { p_enrollment_id: parsed.data.enrollment_id });
  if (error) {
    if (error.code === '42501') return { ok: false, error: 'Only a branch owner or admin can revoke a biometric enrollment.' };
    return { ok: false, error: dbMessage(error) };
  }

  revalidatePath(`/members/${parsed.data.member_id}`);
  return { ok: true, message: 'Enrollment revoked. The edge device deletes the template at its next sync.' };
}

export async function staffCheckIn(_prev: ActionResult | null, fd: FormData): Promise<ActionResult> {
  const parsed = parseForm(ManualCheckInInput, fd, ['member_id', 'method']);
  if (!parsed.success) return { ok: false, error: zodMessage(parsed.error) };
  const { member_id: memberId, method } = parsed.data;

  const supabase = await createClient();
  // C2: browser check-ins are staff_manual or nfc only (QR goes through qr-token / qr_check_in).
  const { data, error } = await supabase.rpc('staff_check_in', { p_member_id: memberId, p_method: method });
  if (error) {
    if (error.code === '23514') return { ok: false, error: 'Membership is not active; it cannot be checked in.' };
    return { ok: false, error: dbMessage(error) };
  }

  revalidatePath(`/members/${memberId}`);
  revalidatePath('/sessions');
  revalidatePath('/dashboard');
  return { ok: true, message: 'Checked in.', data: data as string };
}

/**
 * Kiosk: verify a member's QR token and check them in. The qr-token Edge Function
 * validates the HMAC + time window and the staff JWT, then calls `qr_check_in`
 * with the staff user's id, so branch roles are enforced server-side (C1).
 */
export async function verifyQrCheckIn(token: string): Promise<ActionResult<{ attendance_session_id: string }>> {
  const t = (token ?? '').trim();
  if (!/^SGM1\.[A-HJ-NP-Z2-9-]{4,32}\.\d{7,10}\.[A-Za-z0-9_-]{12,}$/.test(t)) return { ok: false, error: 'Malformed check-in code.' };
  const supabase = await createClient();
  const { data: sessionData } = await supabase.auth.getSession();
  const accessToken = sessionData.session?.access_token;
  if (!accessToken) return { ok: false, error: 'Your session expired. Sign in again.' };
  const base = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const anon = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;
  if (!base || !anon) return { ok: false, error: 'Supabase is not configured.' };
  let res: Response;
  try {
    res = await fetch(`${base}/functions/v1/qr-token`, {
      method: 'POST',
      headers: { 'content-type': 'application/json', authorization: `Bearer ${accessToken}`, apikey: anon },
      body: JSON.stringify({ token: t }),
      cache: 'no-store',
    });
  } catch (err) {
    return { ok: false, error: `Check-in service unreachable: ${(err as Error).message}` };
  }
  const body = (await res.json().catch(() => ({}))) as { attendance_session_id?: string; message?: string; code?: string };
  if (!res.ok) {
    const reason = body.message ?? body.code ?? `HTTP ${res.status}`;
    return { ok: false, error: res.status === 401 ? `Code expired or invalid (${reason}). Ask the member to refresh their QR.` : reason };
  }
  revalidatePath('/sessions');
  revalidatePath('/dashboard');
  return { ok: true, message: 'Member checked in.', data: { attendance_session_id: body.attendance_session_id ?? '' } };
}

export async function staffCheckOut(_prev: ActionResult | null, fd: FormData): Promise<ActionResult> {
  const parsed = parseForm(ManualCheckOutInput, fd, ['attendance_session_id', 'member_id']);
  if (!parsed.success) return { ok: false, error: zodMessage(parsed.error) };
  const { attendance_session_id: sessionId, member_id: memberId } = parsed.data;

  const supabase = await createClient();
  const { data, error } = await supabase.rpc('staff_check_out', { p_session_id: sessionId, p_method: 'staff_manual' });
  if (error) return { ok: false, error: dbMessage(error) };

  revalidatePath('/sessions');
  revalidatePath('/dashboard');
  if (memberId) revalidatePath(`/members/${memberId}`);
  // A session that was already abandoned (e.g. consent withdrawn) has no report.
  if (!data) return { ok: true, message: 'Session closed. No workout report was generated for it.' };
  return { ok: true, message: 'Checked out. The workout report has been generated and a notification queued.', data: data as string };
}

// ────────────────────────────────────────────────────────────────────────────
// equipment, cameras, zones
// ────────────────────────────────────────────────────────────────────────────

export async function createEquipment(_prev: ActionResult | null, fd: FormData): Promise<ActionResult> {
  const parsed = CreateEquipmentInput.safeParse({
    branch_id: str(fd, 'branch_id'),
    name: str(fd, 'name'),
    category: str(fd, 'category'),
    position_x_m: optNum(fd, 'position_x_m'),
    position_y_m: optNum(fd, 'position_y_m'),
    default_exercise_id: optStr(fd, 'default_exercise_id'),
    enabled: bool(fd, 'enabled', true),
  });
  if (!parsed.success) return { ok: false, error: zodMessage(parsed.error) };

  const supabase = await createClient();
  const { error } = await supabase.from('equipment').insert(parsed.data);
  if (error) return { ok: false, error: dbMessage(error) };

  revalidatePath('/equipment');
  return { ok: true, message: `Equipment "${parsed.data.name}" added.` };
}

export async function createCamera(_prev: ActionResult | null, fd: FormData): Promise<ActionResult> {
  const parsed = CreateCameraInput.safeParse({
    branch_id: str(fd, 'branch_id'),
    edge_device_id: optStr(fd, 'edge_device_id'),
    name: str(fd, 'name'),
    protocol: str(fd, 'protocol'),
    purpose: str(fd, 'purpose'),
    stream_url: str(fd, 'stream_url'),
    resolution_w: optNum(fd, 'resolution_w'),
    resolution_h: optNum(fd, 'resolution_h'),
    fps: optNum(fd, 'fps'),
    enabled: bool(fd, 'enabled', true),
  });
  if (!parsed.success) return { ok: false, error: zodMessage(parsed.error) };

  const supabase = await createClient();
  if (parsed.data.edge_device_id) {
    // The camera is served by an edge device of the same branch only.
    const { data: device, error: devErr } = await supabase.from('edge_devices').select('branch_id').eq('id', parsed.data.edge_device_id).maybeSingle();
    if (devErr) return { ok: false, error: dbMessage(devErr) };
    if (!device || device.branch_id !== parsed.data.branch_id) {
      return { ok: false, error: 'edge_device_id: choose an edge device of the same branch.' };
    }
  }
  const { error } = await supabase.from('cameras').insert(parsed.data);
  if (error) return { ok: false, error: dbMessage(error) };

  revalidatePath('/cameras');
  revalidatePath('/dashboard');
  return { ok: true, message: `Camera "${parsed.data.name}" added. Put its credentials in the edge device's camera-credentials file.` };
}

export async function createCameraZone(_prev: ActionResult | null, fd: FormData): Promise<ActionResult> {
  let polygon: unknown;
  try {
    polygon = JSON.parse(str(fd, 'polygon') || '[]');
  } catch {
    return { ok: false, error: 'polygon: invalid JSON.' };
  }
  const parsed = CreateCameraZoneInput.safeParse({
    camera_id: str(fd, 'camera_id'),
    equipment_id: optStr(fd, 'equipment_id'),
    name: str(fd, 'name'),
    zone_type: str(fd, 'zone_type'),
    polygon,
  });
  if (!parsed.success) return { ok: false, error: zodMessage(parsed.error) };
  const area = NonDegeneratePolygon.safeParse(parsed.data.polygon);
  if (!area.success) return { ok: false, error: zodMessage(area.error) };
  if (parsed.data.zone_type === 'equipment' && !parsed.data.equipment_id) {
    return { ok: false, error: 'equipment_id: equipment zones must be linked to a piece of equipment.' };
  }

  const supabase = await createClient();
  // A zone may only point at equipment of its camera's branch: device_sync_snapshot ships the
  // zone to that branch's edge, and ingest rejects usage of another branch's equipment.
  const { data: camera, error: camErr } = await supabase.from('cameras').select('branch_id').eq('id', parsed.data.camera_id).maybeSingle();
  if (camErr) return { ok: false, error: dbMessage(camErr) };
  if (!camera) return { ok: false, error: 'Camera not found or not accessible.' };
  if (parsed.data.equipment_id) {
    const { data: equipment, error: eqErr } = await supabase.from('equipment').select('branch_id').eq('id', parsed.data.equipment_id).maybeSingle();
    if (eqErr) return { ok: false, error: dbMessage(eqErr) };
    if (!equipment || equipment.branch_id !== camera.branch_id) {
      return { ok: false, error: "equipment_id: choose equipment from the camera's own branch." };
    }
  }

  const { error } = await supabase.from('camera_zones').insert({
    camera_id: parsed.data.camera_id,
    equipment_id: parsed.data.equipment_id,
    name: parsed.data.name,
    zone_type: parsed.data.zone_type,
    polygon: parsed.data.polygon,
  });
  if (error) return { ok: false, error: dbMessage(error) };

  revalidatePath(`/cameras/${parsed.data.camera_id}/zones`);
  revalidatePath('/cameras');
  return { ok: true, message: `Zone "${parsed.data.name}" saved.` };
}

export async function deleteCameraZone(_prev: ActionResult | null, fd: FormData): Promise<ActionResult> {
  const parsed = parseForm(DeleteCameraZoneInput, fd, ['zone_id', 'camera_id']);
  if (!parsed.success) return { ok: false, error: zodMessage(parsed.error) };
  const { zone_id: zoneId, camera_id: cameraId } = parsed.data;

  const supabase = await createClient();
  const { data, error } = await supabase.from('camera_zones').delete().eq('id', zoneId).eq('camera_id', cameraId).select('id');
  if (error) return { ok: false, error: dbMessage(error) };
  if (!data || (data as unknown[]).length === 0) return { ok: false, error: 'Zone was not deleted (not found or not authorized).' };

  revalidatePath(`/cameras/${cameraId}/zones`);
  revalidatePath('/cameras');
  return { ok: true, message: 'Zone deleted.' };
}

// ────────────────────────────────────────────────────────────────────────────
// edge devices
// ────────────────────────────────────────────────────────────────────────────

export async function issueDeviceSecret(_prev: ActionResult<string> | null, fd: FormData): Promise<ActionResult<string>> {
  const parsed = parseForm(IssueDeviceSecretInput, fd, ['device_id']);
  if (!parsed.success) return { ok: false, error: zodMessage(parsed.error) };
  const deviceId = parsed.data.device_id;

  const supabase = await createClient();
  const { data, error } = await supabase.rpc('issue_device_secret', { p_device_id: deviceId });
  if (error) {
    if (error.code === '55000') return { ok: false, error: 'This device is retired; secrets cannot be issued for it.' };
    return { ok: false, error: dbMessage(error) };
  }
  if (typeof data !== 'string' || data.length === 0) return { ok: false, error: 'No secret returned.' };

  revalidatePath('/devices');
  return { ok: true, message: 'New secret issued. The previous secret stays valid for 24 hours.', data };
}

// ────────────────────────────────────────────────────────────────────────────
// Commercial Gym Management & Reception Actions
// ────────────────────────────────────────────────────────────────────────────

export async function registerMemberFull(_prev: ActionResult | null, fd: FormData): Promise<ActionResult> {
  const codeParse = MemberCode.safeParse(str(fd, 'member_code'));
  if (!codeParse.success) return { ok: false, error: `member_code: ${codeParse.error.issues[0]?.message ?? 'invalid'}` };

  const rawPhone = optStr(fd, 'phone');
  const phone = rawPhone ? normalizePhone(rawPhone) : null;
  if (rawPhone && !phone) return { ok: false, error: 'phone: use an Egyptian mobile number or E.164 format (+20…).' };

  const parsed = RegisterNewMemberFullInput.safeParse({
    branch_id: str(fd, 'branch_id'),
    full_name: str(fd, 'full_name'),
    member_code: codeParse.data,
    phone,
    email: optStr(fd, 'email'),
    date_of_birth: optStr(fd, 'date_of_birth'),
    emergency_contact_name: optStr(fd, 'emergency_contact_name'),
    emergency_contact_phone: optStr(fd, 'emergency_contact_phone'),
    notes: optStr(fd, 'notes'),
    plan_id: str(fd, 'plan_id'),
    payment_method: str(fd, 'payment_method') || 'cash',
    promo_code: optStr(fd, 'promo_code'),
    ai_addon_selected: bool(fd, 'ai_addon_selected', false),
    consent_biometric_face: bool(fd, 'consent_biometric_face', false),
    consent_cctv_tracking: bool(fd, 'consent_cctv_tracking', false),
    pref_workout_reports: bool(fd, 'pref_workout_reports', true),
    pref_expiry_alerts: bool(fd, 'pref_expiry_alerts', true),
    pref_promotions: bool(fd, 'pref_promotions', false),
  });
  if (!parsed.success) return { ok: false, error: zodMessage(parsed.error) };

  const supabase = await createClient();
  const { data: branch, error: branchErr } = await supabase
    .from('branches')
    .select('id, organization_id')
    .eq('id', parsed.data.branch_id)
    .maybeSingle();
  if (branchErr || !branch) return { ok: false, error: 'Branch not found or inaccessible.' };

  // 1. Create Member
  const { data: member, error: memberErr } = await supabase
    .from('members')
    .insert({
      organization_id: (branch as { organization_id: string }).organization_id,
      branch_id: parsed.data.branch_id,
      full_name: parsed.data.full_name,
      member_code: parsed.data.member_code,
      phone: parsed.data.phone,
      email: parsed.data.email,
      date_of_birth: parsed.data.date_of_birth,
      emergency_contact_name: parsed.data.emergency_contact_name,
      emergency_contact_phone: parsed.data.emergency_contact_phone,
      notes: parsed.data.notes,
      status: 'active',
    })
    .select('id')
    .single();
  if (memberErr || !member) return { ok: false, error: dbMessage(memberErr || { message: 'Member insert failed' }) };

  const memberId = (member as { id: string }).id;

  // 2. Set Notification Preferences
  const { error: prefErr } = await supabase.from('notification_preferences').insert({
    member_id: memberId,
    workout_reports: parsed.data.pref_workout_reports,
    subscription_expiry_alerts: parsed.data.pref_expiry_alerts,
    promotional_offers: parsed.data.pref_promotions,
  });
  if (prefErr) {
    await supabase.from('members').delete().eq('id', memberId);
    return { ok: false, error: `Failed to save notification preferences: ${prefErr.message}` };
  }

  // 3. Record Independent Consents
  if (parsed.data.consent_biometric_face) {
    const { error: bioErr } = await supabase.from('consent_records').insert({
      member_id: memberId,
      consent_type: 'biometric_face_identification',
      policy_version: 'v2.1-GDPR-EGY',
      status: 'granted',
    });
    if (bioErr) {
      await supabase.from('notification_preferences').delete().eq('member_id', memberId);
      await supabase.from('members').delete().eq('id', memberId);
      return { ok: false, error: `Failed to record biometric consent: ${bioErr.message}` };
    }
  }
  if (parsed.data.consent_cctv_tracking) {
    const { error: cctvErr } = await supabase.from('consent_records').insert({
      member_id: memberId,
      consent_type: 'cctv_workout_tracking',
      policy_version: 'v2.1-GDPR-EGY',
      status: 'granted',
    });
    if (cctvErr) {
      await supabase.from('consent_records').delete().eq('member_id', memberId);
      await supabase.from('notification_preferences').delete().eq('member_id', memberId);
      await supabase.from('members').delete().eq('id', memberId);
      return { ok: false, error: `Failed to record CCTV tracking consent: ${cctvErr.message}` };
    }
  }

  // 4. Activate Subscription and Ledger via PostgreSQL stored procedure
  const { data: renewResult, error: renewErr } = await supabase.rpc('fn_renew_subscription', {
    p_member_id: memberId,
    p_plan_id: parsed.data.plan_id,
    p_branch_id: parsed.data.branch_id,
    p_payment_method: parsed.data.payment_method,
    p_ai_addon: parsed.data.ai_addon_selected,
    p_promo_code: parsed.data.promo_code || null,
    p_staff_profile_id: null,
  });

  const renewRes = renewResult as { ok?: boolean; error?: string } | null;
  if (renewErr || !renewRes || renewRes.ok === false) {
    const errDetail = renewErr?.message || renewRes?.error || 'Subscription activation failed';
    // Clean rollback: prevent orphaned, partially registered member rows
    await supabase.from('consent_records').delete().eq('member_id', memberId);
    await supabase.from('notification_preferences').delete().eq('member_id', memberId);
    await supabase.from('members').delete().eq('id', memberId);
    return { ok: false, error: `Registration rolled back because subscription activation failed: ${errDetail}` };
  }

  // 5. Optional Face Enrollment if captured during registration or biometric consent selected
  const faceTemplateRef = optStr(fd, 'face_template_ref');
  if (faceTemplateRef || parsed.data.consent_biometric_face) {
    await supabase.rpc('enroll_member_face', {
      p_member_id: memberId,
      p_template_ref: faceTemplateRef,
    });
  }

  revalidatePath('/members');
  revalidatePath('/dashboard');
  revalidatePath('/subscriptions');
  revalidatePath('/kiosk');
  return {
    ok: true,
    message: `Member ${parsed.data.full_name} registered successfully with plan${faceTemplateRef ? ' & face biometric' : ''} active!`,
    data: { memberId, renewResult },
  };
}

export async function enrollMemberFaceAction(
  memberId: string,
  templateRef?: string | null
): Promise<ActionResult<{ enrollment_id?: string; template_ref?: string }>> {
  if (!memberId) return { ok: false, error: 'Missing member ID.' };
  const supabase = await createClient();
  const { data, error } = await supabase.rpc('enroll_member_face', {
    p_member_id: memberId,
    p_template_ref: templateRef ?? null,
  });
  if (error) return { ok: false, error: dbMessage(error) };
  const res = data as { ok?: boolean; error?: string; enrollment_id?: string; template_ref?: string } | null;
  if (!res || res.ok === false) return { ok: false, error: res?.error || 'Face enrollment failed.' };

  revalidatePath(`/members/${memberId}`);
  revalidatePath('/members');
  revalidatePath('/kiosk');
  return {
    ok: true,
    message: `Face biometric enrolled (${res.template_ref}). Member can now enter via Face Check-in!`,
    data: { enrollment_id: res.enrollment_id, template_ref: res.template_ref },
  };
}

export async function faceCheckInAction(
  memberId: string,
  confidence = 0.97
): Promise<ActionResult<{ session_id?: string; member_name?: string; member_code?: string; already_active?: boolean }>> {
  if (!memberId) return { ok: false, error: 'Select or match an enrolled member first.' };
  const supabase = await createClient();
  const { data, error } = await supabase.rpc('face_check_in', {
    p_member_id: memberId,
    p_confidence: confidence,
  });
  if (error) return { ok: false, error: dbMessage(error) };
  const res = data as {
    ok?: boolean;
    error?: string;
    session_id?: string;
    member_name?: string;
    member_code?: string;
    already_active?: boolean;
  } | null;
  if (!res || res.ok === false) return { ok: false, error: res?.error || 'Face check-in failed.' };

  revalidatePath('/kiosk');
  revalidatePath('/sessions');
  revalidatePath('/dashboard');
  revalidatePath(`/members/${memberId}`);
  return {
    ok: true,
    message: res.already_active
      ? `${res.member_name} (${res.member_code}) is already checked in via Face ID!`
      : `Welcome ${res.member_name} (${res.member_code}) — Face Recognition Check-In Verified (${Math.round(confidence * 100)}% match)!`,
    data: res,
  };
}

export async function enrollFaceViaEdgeAI(
  memberId: string,
  memberName: string,
  imageBase64: string,
  edgeAiUrl = process.env.EDGE_AI_URL || 'http://127.0.0.1:8088'
): Promise<ActionResult<{ template_ref: string; member_id: string }>> {
  if (!memberId || !imageBase64) return { ok: false, error: 'Missing member ID or image.' };

  try {
    const res = await fetch(`${edgeAiUrl}/api/face/enroll`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        member_id: memberId,
        member_name: memberName,
        images: [imageBase64],
      }),
      signal: AbortSignal.timeout(8000),
    });

    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      return { ok: false, error: err.error || `Edge AI enrollment failed (HTTP ${res.status}).` };
    }

    const data = await res.json();
    if (!data.ok || !data.template_ref) {
      return { ok: false, error: data.error || 'Edge AI returned no biometric template reference.' };
    }

    const dbRes = await enrollMemberFaceAction(memberId, data.template_ref);
    if (!dbRes.ok) return { ok: false, error: dbRes.error };

    return {
      ok: true,
      message: `Face enrolled with Edge AI SFace 128-D template (${data.template_ref})!`,
      data: { template_ref: data.template_ref, member_id: memberId },
    };
  } catch (err: any) {
    return {
      ok: false,
      error: `Could not reach Edge AI service at ${edgeAiUrl}: ${err?.message || 'Connection refused'}. Please verify Edge AI is running on port 8088.`,
    };
  }
}

export async function identifyFaceViaEdgeAI(
  imageBase64: string,
  edgeAiUrl = process.env.EDGE_AI_URL || 'http://127.0.0.1:8088'
): Promise<ActionResult<{ member_id: string; member_name?: string; confidence: number; session_id?: string; already_active?: boolean }>> {
  if (!imageBase64) return { ok: false, error: 'No image frame provided for face identification.' };

  try {
    const res = await fetch(`${edgeAiUrl}/api/face/identify`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ image: imageBase64 }),
      signal: AbortSignal.timeout(6000),
    });

    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      return { ok: false, error: err.error || `Edge AI identification failed (HTTP ${res.status}).` };
    }

    const data = await res.json();
    if (!data.ok) {
      return { ok: false, error: data.error || 'Face recognition service error.' };
    }

    if (!data.matched || !data.member_id) {
      return {
        ok: false,
        error: data.error || (data.reason === 'no_face' ? 'No face detected in camera view.' : `Face not recognized (confidence: ${Math.round((data.confidence || 0) * 100)}%).`),
      };
    }

    const checkIn = await faceCheckInAction(data.member_id, data.confidence);
    if (!checkIn.ok) {
      return { ok: false, error: checkIn.error };
    }

    return {
      ok: true,
      message: checkIn.message,
      data: {
        member_id: data.member_id,
        member_name: data.member_name || checkIn.data?.member_name,
        confidence: data.confidence,
        session_id: checkIn.data?.session_id,
        already_active: checkIn.data?.already_active,
      },
    };
  } catch (err: any) {
    return {
      ok: false,
      error: `Could not reach Edge AI service at ${edgeAiUrl}: ${err?.message || 'Connection refused'}. Ensure Edge AI is running on port 8088.`,
    };
  }
}

export async function createMembershipPlan(_prev: ActionResult | null, fd: FormData): Promise<ActionResult> {
  const supabase = await createClient();
  let orgId = str(fd, 'organization_id');
  if (!orgId) {
    const { data: org } = await supabase.from('organizations').select('id').limit(1).maybeSingle();
    orgId = org?.id ?? '';
  }

  const parsed = CreateMembershipPlanInput.safeParse({
    organization_id: orgId,
    name: str(fd, 'name'),
    code: str(fd, 'code').toUpperCase(),
    description: optStr(fd, 'description'),
    price: Number(str(fd, 'price')),
    currency: str(fd, 'currency') || 'EGP',
    duration_days: Number(str(fd, 'duration_days')),
    included_sessions: optNum(fd, 'included_sessions'),
    freeze_allowance_days: Number(str(fd, 'freeze_allowance_days') || '0'),
    max_freeze_count: Number(str(fd, 'max_freeze_count') || '0'),
    ai_training_included: bool(fd, 'ai_training_included', false),
    ai_addon_fee: optNum(fd, 'ai_addon_fee') ?? 150,
    is_active: bool(fd, 'is_active', true),
  });
  if (!parsed.success) return { ok: false, error: zodMessage(parsed.error) };

  const { error } = await supabase.from('membership_plans').insert(parsed.data);
  if (error) return { ok: false, error: dbMessage(error) };

  revalidatePath('/subscriptions');
  return { ok: true, message: `Package ${parsed.data.name} created.` };
}

export async function createPromotion(_prev: ActionResult | null, fd: FormData): Promise<ActionResult> {
  const supabase = await createClient();
  let orgId = str(fd, 'organization_id');
  if (!orgId) {
    const { data: org } = await supabase.from('organizations').select('id').limit(1).maybeSingle();
    orgId = org?.id ?? '';
  }

  const parsed = CreatePromotionInput.safeParse({
    organization_id: orgId,
    code: str(fd, 'code').toUpperCase(),
    name: str(fd, 'name'),
    description: optStr(fd, 'description'),
    discount_type: str(fd, 'discount_type'),
    discount_value: Number(str(fd, 'discount_value')),
    applies_to_plan_id: optStr(fd, 'applies_to_plan_id'),
    min_subscription_amount: optNum(fd, 'min_subscription_amount'),
    max_discount_amount: optNum(fd, 'max_discount_amount'),
    usage_limit: optNum(fd, 'usage_limit'),
    valid_until: optStr(fd, 'valid_until') || null,
    is_active: bool(fd, 'is_active', true),
  });
  if (!parsed.success) return { ok: false, error: zodMessage(parsed.error) };

  const { error } = await supabase.from('promotions').insert(parsed.data);
  if (error) return { ok: false, error: dbMessage(error) };

  revalidatePath('/subscriptions');
  return { ok: true, message: `Promotion ${parsed.data.code} created.` };
}

export async function togglePromotionStatus(_prev: ActionResult | null, fd: FormData): Promise<ActionResult> {
  const promoId = str(fd, 'promotion_id');
  const setActive = bool(fd, 'is_active', false);
  if (!promoId) return { ok: false, error: 'Missing promotion ID.' };

  const supabase = await createClient();
  const { error } = await supabase.from('promotions').update({ is_active: setActive }).eq('id', promoId);
  if (error) return { ok: false, error: dbMessage(error) };

  revalidatePath('/subscriptions');
  return { ok: true, message: setActive ? 'Promotion activated.' : 'Promotion deactivated.' };
}

export async function freezeSubscription(_prev: ActionResult | null, fd: FormData): Promise<ActionResult> {
  const parsed = FreezeSubscriptionInput.safeParse({
    subscription_id: str(fd, 'subscription_id'),
    days: Number(str(fd, 'days')),
    reason: str(fd, 'reason'),
  });
  if (!parsed.success) return { ok: false, error: zodMessage(parsed.error) };

  const supabase = await createClient();
  const { data, error } = await supabase.rpc('fn_freeze_subscription', {
    p_subscription_id: parsed.data.subscription_id,
    p_days: parsed.data.days,
    p_reason: parsed.data.reason,
    p_staff_profile_id: null,
  });

  if (error) return { ok: false, error: error.message };
  const res = data as { ok?: boolean; error?: string };
  if (res && res.ok === false) return { ok: false, error: res.error || 'Freeze rejected by policy' };

  revalidatePath('/members');
  revalidatePath('/subscriptions');
  return { ok: true, message: `Subscription frozen for ${parsed.data.days} days.` };
}

export async function unfreezeSubscription(_prev: ActionResult | null, fd: FormData): Promise<ActionResult> {
  const parsed = UnfreezeSubscriptionInput.safeParse({
    subscription_id: str(fd, 'subscription_id'),
  });
  if (!parsed.success) return { ok: false, error: zodMessage(parsed.error) };

  const supabase = await createClient();
  const { data, error } = await supabase.rpc('fn_unfreeze_subscription', {
    p_subscription_id: parsed.data.subscription_id,
  });

  if (error) return { ok: false, error: error.message };
  const res = data as { ok?: boolean; error?: string };
  if (res && res.ok === false) return { ok: false, error: res.error || 'Unfreeze failed' };

  revalidatePath('/members');
  revalidatePath('/subscriptions');
  return { ok: true, message: 'Subscription unfrozen and active again.' };
}

export async function renewSubscriptionAction(_prev: ActionResult | null, fd: FormData): Promise<ActionResult> {
  const parsed = RenewSubscriptionInput.safeParse({
    member_id: str(fd, 'member_id'),
    plan_id: str(fd, 'plan_id'),
    branch_id: str(fd, 'branch_id'),
    payment_method: str(fd, 'payment_method') || 'cash',
    ai_addon: bool(fd, 'ai_addon', false),
    promo_code: optStr(fd, 'promo_code'),
  });
  if (!parsed.success) return { ok: false, error: zodMessage(parsed.error) };

  const supabase = await createClient();
  const { data, error } = await supabase.rpc('fn_renew_subscription', {
    p_member_id: parsed.data.member_id,
    p_plan_id: parsed.data.plan_id,
    p_branch_id: parsed.data.branch_id,
    p_payment_method: parsed.data.payment_method,
    p_ai_addon: parsed.data.ai_addon,
    p_promo_code: parsed.data.promo_code || null,
    p_staff_profile_id: null,
  });

  if (error) return { ok: false, error: error.message };
  const res = data as { ok?: boolean; error?: string };
  if (res && res.ok === false) return { ok: false, error: res.error || 'Renewal failed' };

  revalidatePath('/members');
  revalidatePath('/dashboard');
  revalidatePath('/subscriptions');
  return { ok: true, message: 'Subscription renewed successfully!' };
}
