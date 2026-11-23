import { defineConfig } from '@playwright/test';

/**
 * Browser check of the deployed apps (admin kiosk + member app) on a temporary QA tenant.
 * Uses the locally installed Google Chrome (channel 'chrome'): no browser download.
 *
 * Playwright empties `outputDir` at the start of every run, so it is a sub-folder of
 * tests/live/artifacts; the vitest evidence (e2e-session.json, functions.json) next to it survives.
 */
export default defineConfig({
  testDir: '.',
  testMatch: /.*\.spec\.ts$/,
  testIgnore: ['**/node_modules/**', '**/artifacts/**'],
  outputDir: './artifacts/ui',
  globalSetup: './ui.global-setup.ts',
  globalTeardown: './ui.global-teardown.ts',
  workers: 1,
  fullyParallel: false,
  retries: 0,
  timeout: 300_000,
  expect: { timeout: 30_000 },
  reporter: [
    ['list'],
    ['json', { outputFile: './artifacts/ui-results.json' }],
    ['html', { outputFolder: './artifacts/ui-report', open: 'never' }],
  ],
  use: {
    channel: 'chrome',
    headless: true,
    screenshot: 'on',
    // A trace records fill() arguments and network bodies (the temporary QA password, auth JWTs)
    // and does not pass through redact(), so it is opt-in: QA_TRACE=1.
    trace: process.env.QA_TRACE === '1' ? 'retain-on-failure' : 'off',
    viewport: { width: 1280, height: 900 },
    actionTimeout: 30_000,
    navigationTimeout: 60_000,
  },
});
