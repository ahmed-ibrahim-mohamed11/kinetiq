/**
 * POST /functions/v1/ingest-events   (verify_jwt = false)
 *
 * Authenticated by a per-device HMAC-SHA256 signature (NOT a user JWT):
 *   X-Device-Id:        <uuid>
 *   X-Device-Signature: hex(HMAC_SHA256(secret, canonicalBatchString))
 *   canonicalBatchString = `${device_id}\n${batch_id}\n${sent_at}\n${sha256hex(JSON.stringify(events))}`
 * where `events` is the array exactly as received (parsed JSON, before any schema work).
 *
 * Replay protection: batch_id is single-use (DB PK) and sent_at must be within
 * INGEST_CLOCK_SKEW_SECONDS (1..3600, default 300) of server time. Multiple active secrets are
 * accepted during rotation. Authentication failures return a generic 401; a retired device gets
 * 403 {code:'device_retired'}; infrastructure failures 500 so the edge retries.
 *
 * See handler.ts for the order of checks and validation rules. All business validation happens in
 * SQL (public.ingest_events) so the same rules apply to every ingest path.
 */
import { createClient } from 'npm:@supabase/supabase-js@2.47.10';
import { parseClockSkew } from '../_shared/device_auth.ts';
import { supabaseDeviceAuth } from '../_shared/device_db.ts';
import { handle, type IngestDeps } from './handler.ts';

const supabase = createClient(Deno.env.get('SUPABASE_URL') ?? '', Deno.env.get('SUPABASE_SERVICE_ROLE_KEY') ?? '', {
  auth: { persistSession: false },
});

const deps: IngestDeps = {
  clockSkewSeconds: parseClockSkew(Deno.env.get('INGEST_CLOCK_SKEW_SECONDS')),
  ...supabaseDeviceAuth(supabase),
  ingestEvents: async (args) => await supabase.rpc('ingest_events', args),
};

Deno.serve((req) => handle(req, deps));
