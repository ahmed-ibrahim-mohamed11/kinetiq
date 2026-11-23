/** Playwright global teardown: always removes the UI run's QA tenant and sweeps leftovers of the run. */
import { destroyQaTenant, sweepQaTenants, type QaTenant } from './lib/qa-tenant.ts';
import { writeJsonArtifact } from './lib/artifacts.ts';
import { UI_RUN_ENV, UI_TENANT_ENV } from './ui.global-setup.ts';

export default async function globalTeardown(): Promise<void> {
  const raw = process.env[UI_TENANT_ENV];
  const runId = process.env[UI_RUN_ENV];
  const errors: string[] = [];
  let report: unknown = null;
  if (raw) {
    try {
      report = await destroyQaTenant(JSON.parse(raw) as QaTenant);
    } catch (err) {
      errors.push((err as Error).message);
    }
  }
  if (runId) {
    try {
      await sweepQaTenants(runId);
    } catch (err) {
      errors.push((err as Error).message);
    }
  }
  writeJsonArtifact('ui-teardown.json', { ok: errors.length === 0, runId, report, errors });
  if (errors.length > 0) throw new Error(`UI QA tenant teardown failed: ${errors.join('; ')}`);
  console.log('[ui] QA tenant removed');
}
