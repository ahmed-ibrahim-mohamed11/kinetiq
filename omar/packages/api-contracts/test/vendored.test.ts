/**
 * The Deno Edge Functions validate events with a vendored copy of src/events.ts
 * (supabase/functions/_shared/events_v1.ts). This fails when the two drift: re-copy the file and
 * change only the zod import to `npm:zod@3.24.1` (keep the leading `//` vendoring notice).
 */
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import { IngestResult } from '../src/index';

const here = dirname(fileURLToPath(import.meta.url));
const read = (p: string) => readFileSync(p, 'utf8').replace(/\r\n/g, '\n');

describe('vendored events.v1 copy for the Edge Functions', () => {
  it('is identical to src/events.ts apart from the notice and the zod import', () => {
    const source = read(join(here, '..', 'src', 'events.ts'));
    const vendored = read(join(here, '..', '..', '..', 'supabase', 'functions', '_shared', 'events_v1.ts'));
    expect(vendored.replace(/^(\/\/[^\n]*\n)+/, '')).toBe(source.replace("import { z } from 'zod';", "import { z } from 'npm:zod@3.24.1';"));
  });
});

describe('IngestResult (C4)', () => {
  const base = { batch_id: '99999999-9999-4999-8999-999999999999', accepted: 1, duplicates: 0, server_time: '2026-11-02T10:00:00.000Z' };
  const rejection = { event_id: '6f1d2c3e-4b5a-4c6d-8e7f-901234567801', reason: 'The event belongs to another branch.' };

  it('accepts rejections with a C4 code', () => {
    expect(IngestResult.safeParse({ ...base, rejected: [{ ...rejection, code: 'branch_mismatch' }] }).success).toBe(true);
  });
  it('rejects codes outside the fixed set', () => {
    expect(IngestResult.safeParse({ ...base, rejected: [{ ...rejection, code: 'oops' }] }).success).toBe(false);
  });
  it('accepts a replayed batch response', () => {
    expect(IngestResult.safeParse({ ...base, accepted: 0, rejected: [], replayed: true }).success).toBe(true);
  });
});
