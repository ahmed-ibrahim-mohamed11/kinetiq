'use server';

import 'server-only';
import { revalidatePath } from 'next/cache';
import { z } from 'zod';
import {
  CreateReportShareInput,
  RegisterPushSubscriptionInput,
  ReportShareResult,
  RevokeReportShareInput,
} from '@smartgym/api-contracts';
import { createClient, getCurrentMember } from '@smartgym/supabase-client/server';

export interface ActionResult<T = undefined> {
  ok: boolean;
  error?: string;
  data?: T;
}

const uuid = z.string().uuid();

const PUSH_UNSUPPORTED =
  "This browser's push service is not supported. Your workout reports will be sent by email instead.";

/**
 * Member self check-out. The RPC finalizes the session (closing open sets,
 * generating the report, queuing the notification) and returns the report id.
 */
export async function memberCheckOut(): Promise<ActionResult<{ reportId: string }>> {
  const supabase = await createClient();
  const { data, error } = await supabase.rpc('member_self_check_out');
  if (error) {
    const msg = error.code === 'P0002' ? 'You are not currently checked in.' : error.message;
    return { ok: false, error: msg };
  }
  revalidatePath('/dashboard');
  revalidatePath('/history');
  return { ok: true, data: { reportId: data } };
}

/** Withdraw one of the member's own consents. History is never edited, only status + withdrawn_at. */
export async function withdrawConsent(input: { consentRecordId: string }): Promise<ActionResult> {
  const parsed = z.object({ consentRecordId: uuid }).safeParse(input);
  if (!parsed.success) return { ok: false, error: 'Invalid consent reference.' };
  const supabase = await createClient();
  const { data, error } = await supabase
    .from('consent_records')
    .update({ status: 'withdrawn', withdrawn_at: new Date().toISOString() })
    .eq('id', parsed.data.consentRecordId)
    .eq('status', 'granted')
    .select('id');
  if (error) return { ok: false, error: error.message };
  if (!data || data.length === 0) return { ok: false, error: 'Consent not found or already withdrawn.' };
  revalidatePath('/settings');
  return { ok: true };
}

/** Save (or refresh) the browser's Web Push subscription for the signed-in user. */
export async function registerPushSubscription(input: unknown): Promise<ActionResult> {
  const parsed = RegisterPushSubscriptionInput.safeParse(input);
  if (!parsed.success) {
    const badEndpoint = parsed.error.issues.some((i) => i.path[0] === 'endpoint');
    return { ok: false, error: badEndpoint ? PUSH_UNSUPPORTED : 'Invalid push subscription.' };
  }
  const supabase = await createClient();
  const { data: userRes } = await supabase.auth.getUser();
  if (!userRes.user) return { ok: false, error: 'Not signed in.' };
  const { error } = await supabase.from('push_subscriptions').upsert(
    {
      user_id: userRes.user.id,
      endpoint: parsed.data.endpoint,
      p256dh: parsed.data.keys.p256dh,
      auth: parsed.data.keys.auth,
      user_agent: parsed.data.user_agent,
      disabled_at: null,
      last_used_at: null,
    },
    { onConflict: 'endpoint' },
  );
  // 22023: the database refused the endpoint (not a known Web Push service).
  if (error) return { ok: false, error: error.code === '22023' ? PUSH_UNSUPPORTED : error.message };
  revalidatePath('/settings');
  return { ok: true };
}

/** Disable a push subscription by endpoint (the browser side unsubscribes separately). */
export async function disablePushSubscription(input: { endpoint: string }): Promise<ActionResult> {
  // Any URL is fine here: disabling never sends anything to the endpoint.
  const parsed = z.object({ endpoint: z.string().url().max(2048) }).safeParse(input);
  if (!parsed.success) return { ok: false, error: 'Invalid endpoint.' };
  const supabase = await createClient();
  const { error } = await supabase.from('push_subscriptions').update({ disabled_at: new Date().toISOString() }).eq('endpoint', parsed.data.endpoint);
  if (error) return { ok: false, error: error.message };
  revalidatePath('/settings');
  return { ok: true };
}

/** Record the first time the member opened a report (RLS: member may update own reports only). */
export async function markReportViewed(input: { reportId: string }): Promise<ActionResult> {
  const parsed = z.object({ reportId: uuid }).safeParse(input);
  if (!parsed.success) return { ok: false, error: 'Invalid report.' };
  const supabase = await createClient();
  const { error } = await supabase.from('workout_reports').update({ viewed_at: new Date().toISOString() }).eq('id', parsed.data.reportId).is('viewed_at', null);
  if (error) return { ok: false, error: error.message };
  return { ok: true };
}

function shareError(error: { code?: string; message: string }): string {
  switch (error.code) {
    case 'P0002':
      return 'Report not found.';
    case '22023':
      return 'Choose a link lifetime between 1 and 30 days.';
    case '42501':
      return 'Only the member who owns this report can share it.';
    default:
      return 'Could not update the share link. Please try again.';
  }
}

/** Create (or replace) the public share link of one of the member's own reports (C3). */
export async function createReportShare(input: { reportId: string; days: number }): Promise<ActionResult<ReportShareResult>> {
  const parsed = CreateReportShareInput.safeParse({ report_id: input.reportId, days: input.days });
  if (!parsed.success) return { ok: false, error: 'Choose a link lifetime between 1 and 30 days.' };
  const supabase = await createClient();
  const { data, error } = await supabase.rpc('create_report_share', { p_report_id: parsed.data.report_id, p_days: parsed.data.days });
  if (error) return { ok: false, error: shareError(error) };
  const result = ReportShareResult.safeParse(data);
  if (!result.success) return { ok: false, error: 'Could not update the share link. Please try again.' };
  revalidatePath(`/reports/${parsed.data.report_id}`);
  return { ok: true, data: result.data };
}

export async function updateNotificationPreferences(input: {
  workout_reports?: boolean;
  subscription_expiry_alerts?: boolean;
  promotional_offers?: boolean;
}): Promise<ActionResult> {
  const member = await getCurrentMember();
  if (!member) return { ok: false, error: 'Not authenticated as a member.' };
  const supabase = await createClient();
  const { error } = await supabase.from('notification_preferences').upsert(
    {
      member_id: member.id,
      workout_reports: input.workout_reports ?? true,
      subscription_expiry_alerts: input.subscription_expiry_alerts ?? true,
      promotional_offers: input.promotional_offers ?? false,
      updated_at: new Date().toISOString(),
    },
    { onConflict: 'member_id' },
  );
  if (error) return { ok: false, error: error.message };
  revalidatePath('/settings');
  return { ok: true };
}

export async function savePhoneCameraWorkoutSession(input: {
  reps: number;
  validReps?: number;
  imperfectReps?: number;
  exercise: string;
  durationSeconds: number;
  formScore: number;
  repTelemetry?: Array<{
    rep_number: number;
    verdict: 'correct' | 'incorrect';
    min_knee_angle_deg: number;
    max_trunk_lean_deg: number;
    detected_errors: string[];
    confidence: number;
  }>;
}): Promise<ActionResult<{ sessionId: string; reportId?: string }>> {
  const member = await getCurrentMember();
  if (!member) return { ok: false, error: 'Not authenticated as a member.' };
  const supabase = await createClient();

  const { data, error } = await supabase.rpc('save_ai_workout_session', {
    p_member_id: member.id,
    p_reps: input.reps,
    p_valid_reps: input.validReps ?? input.reps,
    p_imperfect_reps: input.imperfectReps ?? 0,
    p_exercise: input.exercise.toLowerCase(),
    p_duration_seconds: Math.max(1, input.durationSeconds),
    p_form_score: input.formScore,
    p_rep_telemetry: input.repTelemetry ?? [],
    p_camera_id: null,
  });

  if (error) return { ok: false, error: error.message };
  const payload = data as { ok?: boolean; error?: string; session_id?: string; report_id?: string } | null;
  if (!payload?.ok || !payload.session_id) {
    return { ok: false, error: payload?.error ?? 'Failed to save AI workout session.' };
  }

  revalidatePath('/dashboard');
  revalidatePath('/history');
  return { ok: true, data: { sessionId: payload.session_id, reportId: payload.report_id } };
}

/** Revoke the share link of one of the member's own reports; the old link stops working at once. */
export async function revokeReportShare(input: { reportId: string }): Promise<ActionResult> {
  const parsed = RevokeReportShareInput.safeParse({ report_id: input.reportId });
  if (!parsed.success) return { ok: false, error: 'Invalid report.' };
  const supabase = await createClient();
  const { error } = await supabase.rpc('revoke_report_share', { p_report_id: parsed.data.report_id });
  if (error) return { ok: false, error: shareError(error) };
  revalidatePath(`/reports/${parsed.data.report_id}`);
  return { ok: true };
}

/**
 * Server-side proxy for the qr-token edge function.
 * Eliminates client-side CORS errors and preflight failures on localhost/PWA.
 */
export async function getQrTokenAction(): Promise<ActionResult<{ token: string; expires_in_seconds: number }>> {
  const supabase = await createClient();
  const { data: sessionData } = await supabase.auth.getSession();
  const accessToken = sessionData.session?.access_token;
  if (!accessToken) return { ok: false, error: 'Not signed in.' };

  const base = process.env.NEXT_PUBLIC_SUPABASE_URL || 'https://vpwudyxfsbyrbpwqdmew.supabase.co';
  try {
    const res = await fetch(`${base}/functions/v1/qr-token`, {
      method: 'GET',
      headers: {
        Authorization: `Bearer ${accessToken}`,
        apikey: process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY ?? '',
      },
      cache: 'no-store',
    });
    if (!res.ok) {
      return { ok: false, error: `QR service responded ${res.status}` };
    }
    const body = (await res.json()) as { token: string; expires_in_seconds: number };
    return { ok: true, data: body };
  } catch (err) {
    return { ok: false, error: (err as Error).message };
  }
}


