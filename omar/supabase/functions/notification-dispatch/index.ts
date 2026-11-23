/**
 * POST /functions/v1/notification-dispatch   (verify_jwt = false)
 *
 * Delivers due notification_jobs without a separately hosted worker. pg_cron calls it every
 * minute through pg_net (migration 20261102000011) with header
 *   x-dispatch-secret: <NOTIFICATION_DISPATCH_SECRET>
 * compared in constant time; anything else gets 401.
 *
 * It claims due jobs with claim_notification_jobs (SKIP LOCKED, so it can run next to the
 * self-hosted Node worker), sends Web Push (VAPID + RFC 8291 aes128gcm, WebCrypto only) or
 * email (Resend, optional), and reports every job with complete_notification_job, which owns
 * backoff and dead-lettering. 404/410 from a push service disables that subscription.
 * Responds {claimed, delivered, failed}.
 *
 * Function secrets: NOTIFICATION_DISPATCH_SECRET, VAPID_PUBLIC_KEY, VAPID_PRIVATE_KEY, VAPID_SUBJECT,
 * MEMBER_APP_URL; optional RESEND_API_KEY + EMAIL_FROM. SUPABASE_URL and
 * SUPABASE_SERVICE_ROLE_KEY are provided by the platform.
 */
import { createClient } from 'npm:@supabase/supabase-js@2.47.10';
import { createDispatchHandler } from '../_shared/notify/dispatch.ts';
import { ResendEmailSender } from '../_shared/notify/email.ts';
import { supabaseDb, supabaseEmailLookup, type SupabaseLike } from '../_shared/notify/supabase-db.ts';
import { WebPushSender } from '../_shared/notify/webpush.ts';
import type { EmailSender, WorkerDeps } from '../_shared/notify/worker.ts';

const env = (name: string): string => (Deno.env.get(name) ?? '').trim();

const required = ['SUPABASE_URL', 'SUPABASE_SERVICE_ROLE_KEY', 'VAPID_PUBLIC_KEY', 'VAPID_PRIVATE_KEY', 'VAPID_SUBJECT', 'MEMBER_APP_URL'];
const missingConfig = required.filter((name) => !env(name));

const log = (msg: string, data?: Record<string, unknown>) => console.log(JSON.stringify({ ts: new Date().toISOString(), msg, ...data }));

let cached: WorkerDeps | undefined;

function buildDeps(): WorkerDeps {
  if (cached) return cached;
  const client = createClient(env('SUPABASE_URL'), env('SUPABASE_SERVICE_ROLE_KEY'), { auth: { persistSession: false } });
  const sb = client as unknown as SupabaseLike;

  let email: EmailSender | undefined;
  if (env('RESEND_API_KEY') && env('EMAIL_FROM')) {
    email = new ResendEmailSender(supabaseEmailLookup(sb), { apiKey: env('RESEND_API_KEY'), from: env('EMAIL_FROM') });
  }

  cached = {
    db: supabaseDb(sb),
    push: new WebPushSender({ vapid: { publicKey: env('VAPID_PUBLIC_KEY'), privateKey: env('VAPID_PRIVATE_KEY'), subject: env('VAPID_SUBJECT') } }),
    email,
    memberAppUrl: env('MEMBER_APP_URL'),
    workerId: `edge-fn-${crypto.randomUUID().slice(0, 8)}`,
    log,
  };
  return cached;
}

Deno.serve(
  createDispatchHandler({
    secret: env('NOTIFICATION_DISPATCH_SECRET'),
    deps: buildDeps,
    missingConfig,
    log,
  }),
);
