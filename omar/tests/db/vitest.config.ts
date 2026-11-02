import { fileURLToPath } from 'node:url';
import { defineConfig } from 'vitest/config';

// Root is pinned to this directory so an invocation from the repository root
// (`npx vitest run --config tests/db/vitest.config.ts`) can never pick up other
// workspaces' tests — tests/live talks to the hosted project and must only run on purpose.
const here = fileURLToPath(new URL('.', import.meta.url));

export default defineConfig({
  test: {
    root: here,
    include: ['*.test.ts'],
    testTimeout: 180_000,
    hookTimeout: 300_000,
    pool: 'forks',
    poolOptions: { forks: { singleFork: true } },
  },
});
