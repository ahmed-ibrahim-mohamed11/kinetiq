/**
 * Playwright global setup: creates the UI run's own QA tenant. The tenant (including the temporary
 * passwords) is handed to the workers through this process's environment only — never written to
 * disk. ui.global-teardown.ts destroys it.
 */
import { liveEnv } from './lib/env.ts';
import { createQaTenant, newRunId } from './lib/qa-tenant.ts';

export const UI_TENANT_ENV = 'QA_UI_TENANT';
export const UI_RUN_ENV = 'QA_UI_RUN_ID';

export default async function globalSetup(): Promise<void> {
  liveEnv();
  const runId = process.env.QA_RUN_ID ? `${process.env.QA_RUN_ID}-ui` : newRunId('ui');
  process.env[UI_RUN_ENV] = runId;
  const tenant = await createQaTenant(runId);
  process.env[UI_TENANT_ENV] = JSON.stringify(tenant);
  console.log(`[ui] QA tenant ${tenant.orgSlug} created`);
}
