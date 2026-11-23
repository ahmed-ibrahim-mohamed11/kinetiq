import { fileURLToPath } from 'node:url';
import { defineConfig } from 'vitest/config';

// Temporary, isolated runner for a migration under development (deleted after use).
export default defineConfig({
  test: {
    root: fileURLToPath(new URL('../../..', import.meta.url)),
    include: ['tests/integration/.scratch/*.test.ts'],
    testTimeout: 180_000,
    hookTimeout: 300_000,
    pool: 'forks',
    poolOptions: { forks: { singleFork: true } },
  },
});
