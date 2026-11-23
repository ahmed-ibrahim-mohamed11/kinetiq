/** supabase-js (service role) implementation of DeviceAuthDeps, shared by ingest-events and device-sync. */
import type { SupabaseClient } from 'npm:@supabase/supabase-js@2.47.10';
import type { DeviceAuthDeps } from './device_auth.ts';

export function supabaseDeviceAuth(supabase: SupabaseClient): Pick<DeviceAuthDeps, 'getDeviceSecrets' | 'getDeviceStatus'> {
  return {
    getDeviceSecrets: async (deviceId) => await supabase.rpc('get_device_secrets', { p_device_id: deviceId }),
    getDeviceStatus: async (deviceId) => {
      const { data, error } = await supabase.from('edge_devices').select('status').eq('id', deviceId).maybeSingle();
      return { data: (data?.status as string | undefined) ?? null, error };
    },
  };
}
