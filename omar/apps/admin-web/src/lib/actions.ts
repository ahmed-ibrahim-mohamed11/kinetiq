'use server';

import 'server-only';
import { revalidatePath } from 'next/cache';
import type { ZodError } from 'zod';
import {
  CreateCameraInput,
  CreateCameraZoneInput,
  CreateEquipmentInput,
  CreateMemberInput,
  RecordConsentInput,
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

async function currentUserId(): Promise<string | null> {
  const supabase = await createClient();
  const { data } = await supabase.auth.getUser();
  return data.user?.id ?? null;
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
  const staffId = await currentUserId();
  const { error } = await supabase.from('consent_records').insert({
    member_id: parsed.data.member_id,
    consent_type: parsed.data.consent_type,
    policy_version: parsed.data.policy_version,
    captured_by_staff_id: staffId,
  });
  if (error) return { ok: false, error: dbMessage(error) };

  revalidatePath(`/members/${parsed.data.member_id}`);
  return { ok: true, message: 'Consent recorded.' };
}

export async function withdrawConsent(_prev: ActionResult | null, fd: FormData): Promise<ActionResult> {
  const consentId = str(fd, 'consent_record_id');
  const memberId = str(fd, 'member_id');
  if (!consentId) return { ok: false, error: 'Missing consent record id.' };

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

export async function staffCheckIn(_prev: ActionResult | null, fd: FormData): Promise<ActionResult> {
  const memberId = str(fd, 'member_id');
  if (!memberId) return { ok: false, error: 'Missing member id.' };

  const supabase = await createClient();
  const { data, error } = await supabase.rpc('staff_check_in', { p_member_id: memberId, p_method: 'staff_manual' });
  if (error) return { ok: false, error: dbMessage(error) };

  revalidatePath(`/members/${memberId}`);
  revalidatePath('/sessions');
  revalidatePath('/dashboard');
  return { ok: true, message: 'Checked in.', data: data as string };
}

/**
 * Kiosk: verify a member's QR token and check them in. The qr-token Edge Function
 * validates the HMAC + time window and calls `staff_check_in` under the STAFF
 * session, so branch roles are enforced server-side.
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
  const sessionId = str(fd, 'attendance_session_id');
  const memberId = optStr(fd, 'member_id');
  if (!sessionId) return { ok: false, error: 'Missing session id.' };

  const supabase = await createClient();
  const { data, error } = await supabase.rpc('staff_check_out', { p_session_id: sessionId, p_method: 'staff_manual' });
  if (error) return { ok: false, error: dbMessage(error) };

  revalidatePath('/sessions');
  revalidatePath('/dashboard');
  if (memberId) revalidatePath(`/members/${memberId}`);
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
  const zoneId = str(fd, 'zone_id');
  const cameraId = str(fd, 'camera_id');
  if (!zoneId) return { ok: false, error: 'Missing zone id.' };

  const supabase = await createClient();
  const { data, error } = await supabase.from('camera_zones').delete().eq('id', zoneId).select('id');
  if (error) return { ok: false, error: dbMessage(error) };
  if (!data || (data as unknown[]).length === 0) return { ok: false, error: 'Zone was not deleted (not found or not authorized).' };

  if (cameraId) revalidatePath(`/cameras/${cameraId}/zones`);
  revalidatePath('/cameras');
  return { ok: true, message: 'Zone deleted.' };
}

// ────────────────────────────────────────────────────────────────────────────
// edge devices
// ────────────────────────────────────────────────────────────────────────────

export async function issueDeviceSecret(_prev: ActionResult<string> | null, fd: FormData): Promise<ActionResult<string>> {
  const deviceId = str(fd, 'device_id');
  if (!deviceId) return { ok: false, error: 'Missing device id.' };

  const supabase = await createClient();
  const { data, error } = await supabase.rpc('issue_device_secret', { p_device_id: deviceId });
  if (error) return { ok: false, error: dbMessage(error) };
  if (typeof data !== 'string' || data.length === 0) return { ok: false, error: 'No secret returned.' };

  revalidatePath('/devices');
  return { ok: true, message: 'New secret issued. The previous secret stays valid for 24 hours.', data };
}
