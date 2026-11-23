/**
 * GET /functions/v1/device-sync   (verify_jwt = false)
 *
 * Device-authenticated snapshot for the edge (C6): cameras/zones/equipment/exercise catalogue of
 * its branch, granted consents (ids only) and enrollment references to keep or revoke locally.
 * Authentication and error mapping: see handler.ts.
 */
import { createClient } from 'npm:@supabase/supabase-js@2.47.10';
import { parseClockSkew } from '../_shared/device_auth.ts';
import { supabaseDeviceAuth } from '../_shared/device_db.ts';
import { type DeviceSyncDeps, handle } from './handler.ts';

const supabase = createClient(Deno.env.get('SUPABASE_URL') ?? '', Deno.env.get('SUPABASE_SERVICE_ROLE_KEY') ?? '', {
  auth: { persistSession: false },
});

const deps: DeviceSyncDeps = {
  clockSkewSeconds: parseClockSkew(Deno.env.get('INGEST_CLOCK_SKEW_SECONDS')),
  ...supabaseDeviceAuth(supabase),
  snapshot: async (deviceId) => await supabase.rpc('device_sync_snapshot', { p_device_id: deviceId }),
};

Deno.serve((req) => handle(req, deps));
