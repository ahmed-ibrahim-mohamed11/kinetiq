import { defineConfig } from 'vitest/config';

/**
 * Live suite: runs against the hosted Supabase project, one file at a time, one test at a time.
 * Every file creates its own QA tenant in beforeAll and removes it in afterAll; the global setup's
 * teardown sweeps anything this run left behind (crash safety net).
 */
export default defineConfig({
  test: {
    include: ['*.test.ts'],
    globalSetup: ['./vitest.global-setup.ts'],
    testTimeout: 300_000,
    hookTimeout: 300_000,
    teardownTimeout: 300_000,
    pool: 'forks',
    poolOptions: { forks: { singleFork: true } },
    fileParallelism: false,
    sequence: { concurrent: false, shuffle: false },
    retry: 0,
    reporters: ['verbose'],
  },
});
