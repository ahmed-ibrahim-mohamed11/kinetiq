/**
 * HTTP handler of the notification-dispatch Edge Function, kept free of Deno globals so it
 * can be unit-tested with fake DB / push dependencies.
 *
 *   POST  x-dispatch-secret: <NOTIFICATION_DISPATCH_SECRET>   body: ignored ({} from pg_cron)
 *   200   {claimed, delivered, failed}
 *   401   missing or wrong secret (constant-time comparison)
 *   405   any other method
 *   500   {code:'not_configured', missing:[...]} before claiming anything (missing settings, or
 *         push key material that push.ready() rejects), or {code:'internal'}
 */
import { tick, type TickResult, type WorkerDeps } from './worker.ts';

export interface DispatchHandlerOptions {
  /** Expected shared secret. Empty/undefined → every request is rejected with 401. */
  secret: string | undefined;
  /** Builds the worker dependencies; called only for authorized, fully configured requests. */
  deps: () => WorkerDeps;
  /** Names of required settings that are missing. Non-empty → 500 without claiming jobs (they stay queued). */
  missingConfig?: string[];
  /** Jobs claimed per batch (claim_notification_jobs p_limit). */
  batchSize?: number;
  /** Upper bound on batches per invocation. */
  maxBatches?: number;
  /** Stop starting new batches after this many milliseconds (pg_net waits 25 s). */
  timeBudgetMs?: number;
  now?: () => number;
  log?: (msg: string, data?: Record<string, unknown>) => void;
}

const enc = new TextEncoder();

/** `missing` entry reported when the push sender's ready() check fails. */
export const INVALID_VAPID = 'VAPID_PUBLIC_KEY/VAPID_PRIVATE_KEY (invalid)';

/** Constant-time string comparison: compares SHA-256 digests so neither content nor length leaks through timing. */
export async function secretsEqual(presented: string, expected: string): Promise<boolean> {
  const [a, b] = await Promise.all([
    crypto.subtle.digest('SHA-256', enc.encode(presented)),
    crypto.subtle.digest('SHA-256', enc.encode(expected)),
  ]);
  const x = new Uint8Array(a);
  const y = new Uint8Array(b);
  let diff = 0;
  for (let i = 0; i < x.length; i++) diff |= (x[i] as number) ^ (y[i] as number);
  return diff === 0;
}

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });
}

export function createDispatchHandler(opts: DispatchHandlerOptions): (req: Request) => Promise<Response> {
  const batchSize = opts.batchSize ?? 20;
  const maxBatches = opts.maxBatches ?? 5;
  const timeBudgetMs = opts.timeBudgetMs ?? 15_000;
  const now = opts.now ?? Date.now;

  return async (req) => {
    if (req.method !== 'POST') return json(405, { code: 'method_not_allowed' });

    const presented = req.headers.get('x-dispatch-secret') ?? '';
    const expected = opts.secret ?? '';
    if (!expected) opts.log?.('dispatch.secret_not_configured');
    if (!expected || !presented || !(await secretsEqual(presented, expected))) {
      return json(401, { code: 'unauthorized' });
    }

    if (opts.missingConfig && opts.missingConfig.length > 0) {
      opts.log?.('dispatch.not_configured', { missing: opts.missingConfig });
      return json(500, { code: 'not_configured', missing: opts.missingConfig });
    }

    const total: TickResult = { claimed: 0, delivered: 0, failed: 0 };
    const started = now();
    let deps: WorkerDeps;
    try {
      deps = opts.deps();
    } catch (err) {
      opts.log?.('dispatch.error', { error: (err as Error).message });
      return json(500, { code: 'internal', message: 'dispatch failed' });
    }
    // Validate the push key material before claiming: a malformed VAPID key would otherwise fail
    // every claimed web_push job and burn its retries. Nothing is claimed, so jobs stay queued.
    if (deps.push.ready) {
      try {
        await deps.push.ready();
      } catch (err) {
        opts.log?.('dispatch.push_not_ready', { error: (err as Error).message });
        return json(500, { code: 'not_configured', missing: [INVALID_VAPID] });
      }
    }
    try {
      for (let i = 0; i < maxBatches; i++) {
        const r = await tick(deps, batchSize);
        total.claimed += r.claimed;
        total.delivered += r.delivered;
        total.failed += r.failed;
        if (r.claimed < batchSize || now() - started >= timeBudgetMs) break;
      }
    } catch (err) {
      opts.log?.('dispatch.error', { error: (err as Error).message, ...total });
      return json(500, { code: 'internal', message: 'dispatch failed' });
    }
    opts.log?.('dispatch.done', { ...total, ms: now() - started });
    return json(200, total);
  };
}
