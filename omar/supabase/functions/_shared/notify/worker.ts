/**
 * Notification job processing — single source of truth for the Supabase Edge Function
 * (notification-dispatch) and the self-hosted Node worker (services/notification-worker).
 *
 * The database owns scheduling: jobs are claimed with claim_notification_jobs (SKIP LOCKED)
 * and every claimed job is reported back with complete_notification_job, which applies
 * exponential backoff and dead-lettering. Runtime-neutral: no Node or Deno APIs.
 */
import { render } from './templates.ts';

export interface Job {
  id: string;
  member_id: string;
  channel: 'web_push' | 'email' | 'sms';
  template: string;
  payload: unknown;
  attempts: number;
  max_attempts: number;
}

export interface PushSub {
  id: string;
  endpoint: string;
  p256dh: string;
  auth: string;
}

export interface Db {
  claim(worker: string, limit: number): Promise<Job[]>;
  complete(jobId: string, ok: boolean, error?: string): Promise<void>;
  memberName(memberId: string): Promise<string>;
  pushSubscriptions(memberId: string): Promise<PushSub[]>;
  disablePushSubscription(id: string): Promise<void>;
}

export interface PushSender {
  /** Resolves on success; rejects with { statusCode } on failure (web-push semantics). */
  send(sub: PushSub, json: string): Promise<void>;
  /**
   * Optional configuration check (e.g. importing the VAPID key) that the dispatcher awaits
   * before claiming jobs; rejects when the sender cannot work, so jobs stay queued.
   */
  ready?(): Promise<void>;
}

export interface EmailSender {
  send(memberId: string, subject: string, body: string, url: string): Promise<void>;
}

export interface WorkerDeps {
  db: Db;
  push: PushSender;
  email?: EmailSender;
  memberAppUrl: string;
  workerId: string;
  log?: (msg: string, data?: Record<string, unknown>) => void;
}

export interface TickResult {
  claimed: number;
  delivered: number;
  failed: number;
}

export async function processJob(job: Job, deps: WorkerDeps): Promise<{ ok: boolean; error?: string }> {
  const name = await deps.db.memberName(job.member_id);
  const rendered = render(job.template, job.payload, name, deps.memberAppUrl);

  if (job.channel === 'web_push') {
    const subs = await deps.db.pushSubscriptions(job.member_id);
    if (subs.length === 0) return { ok: false, error: 'no active push subscriptions' };
    const body = JSON.stringify({ title: rendered.title, body: rendered.body, url: rendered.url, tag: rendered.tag });
    let delivered = 0;
    let lastError = '';
    for (const sub of subs) {
      try {
        await deps.push.send(sub, body);
        delivered++;
      } catch (err) {
        const status = (err as { statusCode?: number }).statusCode;
        lastError = `push ${status ?? 'error'}: ${(err as Error).message ?? ''}`.trim();
        // 404/410 = subscription gone → disable so we stop retrying it.
        if (status === 404 || status === 410) await deps.db.disablePushSubscription(sub.id);
      }
    }
    return delivered > 0 ? { ok: true } : { ok: false, error: lastError || 'all push deliveries failed' };
  }

  if (job.channel === 'email') {
    if (!deps.email) return { ok: false, error: 'email channel not configured' };
    await deps.email.send(job.member_id, rendered.title, rendered.body, rendered.url);
    return { ok: true };
  }

  return { ok: false, error: `channel ${job.channel} not supported` };
}

export async function tick(deps: WorkerDeps, limit = 20): Promise<TickResult> {
  const jobs = await deps.db.claim(deps.workerId, limit);
  let delivered = 0;
  let failed = 0;
  for (const job of jobs) {
    let result: { ok: boolean; error?: string };
    try {
      result = await processJob(job, deps);
    } catch (err) {
      result = { ok: false, error: (err as Error).message };
    }
    await deps.db.complete(job.id, result.ok, result.error);
    if (result.ok) delivered++;
    else failed++;
    deps.log?.(result.ok ? 'notification.delivered' : 'notification.failed', { job: job.id, template: job.template, error: result.error, attempt: job.attempts });
  }
  return { claimed: jobs.length, delivered, failed };
}
