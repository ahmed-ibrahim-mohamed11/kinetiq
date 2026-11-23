/**
 * Notification worker entry point.
 *
 * Polls notification_jobs via SKIP LOCKED RPCs (claim_notification_jobs),
 * delivers Web Push (VAPID) and reports outcomes; the database owns retry
 * scheduling and dead-lettering. Runs as a long-lived container
 * (infrastructure/docker) or a cron-triggered process.
 */
import { createClient } from '@supabase/supabase-js';
import webpush from 'web-push';
import { loadServerEnv } from '@smartgym/shared-config';
import { ResendEmailSender } from './email';
import { tick, type Db, type EmailSender, type Job, type PushSender, type PushSub } from './worker';

const env = loadServerEnv();
const memberAppUrl = process.env.MEMBER_APP_URL ?? 'http://localhost:3001';
const workerId = `${process.env.HOSTNAME ?? 'worker'}-${process.pid}`;

if (!env.VAPID_PUBLIC_KEY || !env.VAPID_PRIVATE_KEY || !env.VAPID_SUBJECT) {
  console.error('VAPID_PUBLIC_KEY, VAPID_PRIVATE_KEY and VAPID_SUBJECT are required (npx web-push generate-vapid-keys)');
  process.exit(2);
}
webpush.setVapidDetails(env.VAPID_SUBJECT, env.VAPID_PUBLIC_KEY, env.VAPID_PRIVATE_KEY);

// Service role: this process is trusted and runs outside the browser. Never ship this key anywhere else.
const supabase = createClient(env.SUPABASE_URL, env.SUPABASE_SERVICE_ROLE_KEY, { auth: { persistSession: false } });

const db: Db = {
  async claim(worker, limit) {
    const { data, error } = await supabase.rpc('claim_notification_jobs', { p_worker: worker, p_limit: limit });
    if (error) throw error;
    return (data ?? []) as Job[];
  },
  async complete(jobId, ok, err) {
    const { error } = await supabase.rpc('complete_notification_job', { p_job_id: jobId, p_ok: ok, p_error: err ?? null });
    if (error) throw error;
  },
  async memberName(memberId) {
    const { data } = await supabase.from('members').select('full_name').eq('id', memberId).maybeSingle();
    return data?.full_name ?? 'Member';
  },
  async pushSubscriptions(memberId) {
    const { data, error } = await supabase.rpc('member_push_subscriptions', { p_member_id: memberId });
    if (error) throw error;
    return (data ?? []) as PushSub[];
  },
  async disablePushSubscription(id) {
    await supabase.rpc('disable_push_subscription', { p_id: id });
  },
};

const push: PushSender = {
  async send(sub, json) {
    await webpush.sendNotification({ endpoint: sub.endpoint, keys: { p256dh: sub.p256dh, auth: sub.auth } }, json, { TTL: 3600, urgency: 'normal' });
  },
};

// Email fallback (members without a push subscription). Optional: without RESEND_API_KEY the
// email channel fails explicitly and the job dead-letters after its retries.
let email: EmailSender | undefined;
if (process.env.RESEND_API_KEY && process.env.EMAIL_FROM) {
  email = new ResendEmailSender(
    {
      async memberEmail(memberId) {
        const { data } = await supabase.from('members').select('email').eq('id', memberId).maybeSingle();
        return (data?.email as string | null) ?? null;
      },
    },
    { apiKey: process.env.RESEND_API_KEY, from: process.env.EMAIL_FROM },
  );
}

const log = (msg: string, data?: Record<string, unknown>) => console.log(JSON.stringify({ ts: new Date().toISOString(), msg, ...data }));

let stopping = false;
process.on('SIGTERM', () => (stopping = true));
process.on('SIGINT', () => (stopping = true));

async function main() {
  log('worker.started', { workerId, pollMs: env.WORKER_POLL_INTERVAL_MS, email: Boolean(email) });
  while (!stopping) {
    try {
      const r = await tick({ db, push, email, memberAppUrl, workerId, log });
      if (r.claimed === 0) await new Promise((res) => setTimeout(res, env.WORKER_POLL_INTERVAL_MS));
    } catch (err) {
      log('worker.tick_error', { error: (err as Error).message });
      await new Promise((res) => setTimeout(res, Math.min(30_000, env.WORKER_POLL_INTERVAL_MS * 5)));
    }
  }
  log('worker.stopped');
}

void main();
