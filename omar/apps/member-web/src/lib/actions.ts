'use server';

import 'server-only';
import { revalidatePath } from 'next/cache';
import { z } from 'zod';
import { RegisterPushSubscriptionInput } from '@smartgym/api-contracts';
import { createClient } from '@smartgym/supabase-client/server';

export interface ActionResult<T = undefined> {
  ok: boolean;
  error?: string;
  data?: T;
}

const uuid = z.string().uuid();

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
  if (!parsed.success) return { ok: false, error: 'Invalid push subscription.' };
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
  if (error) return { ok: false, error: error.message };
  revalidatePath('/settings');
  return { ok: true };
}

/** Disable a push subscription by endpoint (the browser side unsubscribes separately). */
export async function disablePushSubscription(input: { endpoint: string }): Promise<ActionResult> {
  const parsed = z.object({ endpoint: z.string().url() }).safeParse(input);
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
