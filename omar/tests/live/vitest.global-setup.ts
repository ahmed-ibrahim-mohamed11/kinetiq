/**
 * Vitest global setup for the live suite: picks one run id shared by every test file (provided to
 * the files through `inject('qaRunId')`) and, on teardown, sweeps any QA tenant of this run that a
 * crashed or aborted file did not remove. Each file still destroys its own tenant in afterAll.
 */
import type { GlobalSetupContext } from 'vitest/node';
import { liveEnv } from './lib/env.ts';
import { newRunId, sweepQaTenants } from './lib/qa-tenant.ts';

declare module 'vitest' {
  export interface ProvidedContext {
    qaRunId: string;
  }
}

export default function setup({ provide }: GlobalSetupContext): () => Promise<void> {
  liveEnv(); // fail fast (names only) when the root .env is incomplete
  const runId = process.env.QA_RUN_ID ?? newRunId('lv');
  provide('qaRunId', runId);
  console.log(`[live] QA run id ${runId} (tenants: qa-smart-gym-${runId}-*)`);
  return async () => {
    const reports = await sweepQaTenants(runId);
    if (reports.length > 0) console.warn(`[live] swept ${reports.length} leftover QA tenant(s) of run ${runId}`);
  };
}
