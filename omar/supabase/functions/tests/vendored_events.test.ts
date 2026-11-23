/**
 * _shared/events_v1.ts must stay an exact copy of packages/api-contracts/src/events.ts (the single
 * source of truth for events.v1), differing only in the vendoring notice and the zod import.
 * Fix a failure by re-copying the source file.
 *
 *   cd supabase/functions
 *   deno test --config deno.json --allow-env --allow-read tests/vendored_events.test.ts
 */
import assert from 'node:assert/strict';
import { EdgeEvent, EventBatch } from '../_shared/events_v1.ts';

const normalize = (s: string) => s.replace(/\r\n/g, '\n');

Deno.test('vendored events_v1.ts matches packages/api-contracts/src/events.ts', async () => {
  const source = normalize(await Deno.readTextFile(new URL('../../../packages/api-contracts/src/events.ts', import.meta.url)));
  const vendored = normalize(await Deno.readTextFile(new URL('../_shared/events_v1.ts', import.meta.url)));
  const body = vendored.replace(/^(\/\/[^\n]*\n)+/, '');
  assert.equal(body, source.replace("import { z } from 'zod';", "import { z } from 'npm:zod@3.24.1';"));
});

Deno.test('vendored schema accepts every shared fixture', async () => {
  const fixtures = JSON.parse(
    await Deno.readTextFile(new URL('../../../packages/api-contracts/fixtures/events_v1_valid.json', import.meta.url)),
  ) as unknown[];
  for (const ev of fixtures) assert.equal(EdgeEvent.safeParse(ev).success, true, JSON.stringify(ev).slice(0, 200));
  const batch = {
    schema_version: '1',
    batch_id: crypto.randomUUID(),
    device_id: crypto.randomUUID(),
    sent_at: new Date().toISOString(),
    events: fixtures,
  };
  assert.equal(EventBatch.safeParse(batch).success, true);
});
