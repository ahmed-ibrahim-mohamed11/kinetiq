'use client';

import { createBrowserClient } from '@supabase/ssr';
import type { Database } from './types/database';

let client: ReturnType<typeof createBrowserClient<Database>> | undefined;

/** Browser client. Uses ONLY the anon key; RLS enforces every read/write. */
export function createClient() {
  if (client) return client;
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const key = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;
  if (!url || !key) throw new Error('NEXT_PUBLIC_SUPABASE_URL / NEXT_PUBLIC_SUPABASE_ANON_KEY are not set');
  client = createBrowserClient<Database>(url, key);
  return client;
}
