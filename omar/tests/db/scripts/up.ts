/**
 * Start an embedded PostgreSQL with the full schema + seed and keep it running
 * for manual exploration (psql / supabase gen types). Ctrl+C stops it.
 *
 *   npm run db:up -w tests/db
 */
import { startHarness } from '../harness';

const h = await startHarness({ log: (m) => console.log(`[db] ${m}`) });
console.log(`\nPostgreSQL ready: ${h.connectionString}\n`);
console.log(`Generate types:  npx supabase gen types typescript --db-url "${h.connectionString}" > packages/supabase-client/src/types/database.ts`);
process.on('SIGINT', async () => {
  await h.stop();
  process.exit(0);
});
setInterval(() => undefined, 60_000);
