/**
 * `Db` / `EmailLookup` adapters over a service-role supabase-js client, shared by the
 * Edge Function and the Node worker. Typed against a minimal structural interface so the
 * module does not import supabase-js itself (npm: specifiers differ between Deno and Node);
 * callers pass their client cast to `SupabaseLike`.
 */
import type { EmailLookup } from './email.ts';
import type { Db, Job, PushSub } from './worker.ts';

interface PgError {
  message: string;
  code?: string;
}
type Result = PromiseLike<{ data: unknown; error: PgError | null }>;

export interface SupabaseLike {
  rpc(fn: string, args?: Record<string, unknown>): Result;
  from(table: string): {
    select(columns: string): { eq(column: string, value: string): { maybeSingle(): Result } };
  };
}

function fail(what: string, error: PgError): never {
  throw Object.assign(new Error(`${what} failed: ${error.message}`), { code: error.code });
}

export function supabaseDb(client: SupabaseLike): Db {
  return {
    async claim(worker, limit) {
      const { data, error } = await client.rpc('claim_notification_jobs', { p_worker: worker, p_limit: limit });
      if (error) fail('claim_notification_jobs', error);
      return (data ?? []) as Job[];
    },
    async complete(jobId, ok, err) {
      const { error } = await client.rpc('complete_notification_job', { p_job_id: jobId, p_ok: ok, p_error: err ?? null });
      if (error) fail('complete_notification_job', error);
    },
    async memberName(memberId) {
      const { data } = await client.from('members').select('full_name').eq('id', memberId).maybeSingle();
      return (data as { full_name?: string | null } | null)?.full_name ?? 'Member';
    },
    async pushSubscriptions(memberId) {
      const { data, error } = await client.rpc('member_push_subscriptions', { p_member_id: memberId });
      if (error) fail('member_push_subscriptions', error);
      return (data ?? []) as PushSub[];
    },
    async disablePushSubscription(id) {
      // Best effort: a failure here must not fail a job another subscription already delivered.
      await client.rpc('disable_push_subscription', { p_id: id });
    },
  };
}

export function supabaseEmailLookup(client: SupabaseLike): EmailLookup {
  return {
    async memberEmail(memberId) {
      const { data } = await client.from('members').select('email').eq('id', memberId).maybeSingle();
      return (data as { email?: string | null } | null)?.email ?? null;
    },
  };
}
