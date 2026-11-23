import { createServerClient, type CookieOptions } from '@supabase/ssr';
import { cookies } from 'next/headers';
import type { Database } from './types/database';

type CookieToSet = { name: string; value: string; options?: CookieOptions };

/**
 * Server Component / Server Action / Route Handler client bound to the caller's
 * cookies. Still the anon key: the user's JWT travels with the request and RLS
 * decides what they can see. Never use the service role here.
 */
export async function createClient() {
  const cookieStore = await cookies();
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const key = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;
  if (!url || !key) throw new Error('NEXT_PUBLIC_SUPABASE_URL / NEXT_PUBLIC_SUPABASE_ANON_KEY are not set');
  const cookieName = process.env.NEXT_PUBLIC_AUTH_COOKIE_NAME;
  return createServerClient<Database>(url, key, {
    cookieOptions: cookieName ? { name: cookieName } : undefined,
    cookies: {
      getAll() {
        return cookieStore.getAll();
      },
      setAll(cookiesToSet: CookieToSet[]) {
        try {
          cookiesToSet.forEach(({ name, value, options }) => cookieStore.set(name, value, options));
        } catch {
          // Called from a Server Component: middleware refreshes the session instead.
        }
      },
    },
  });
}

/** Returns the signed-in user or null. */
export async function getUser() {
  const supabase = await createClient();
  const { data } = await supabase.auth.getUser();
  return data.user ?? null;
}

/** Staff roles for the signed-in user (empty for members). */
export async function getStaffRoles() {
  const supabase = await createClient();
  const { data: userRes } = await supabase.auth.getUser();
  if (!userRes.user) return [];
  const { data } = await supabase.from('staff_roles').select('organization_id, branch_id, role').eq('user_id', userRes.user.id);
  return data ?? [];
}

/** Member row linked to the signed-in user, or null. */
export async function getCurrentMember() {
  const supabase = await createClient();
  const { data: userRes } = await supabase.auth.getUser();
  if (!userRes.user) return null;
  const { data } = await supabase.from('members').select('*').eq('user_id', userRes.user.id).maybeSingle();
  return data;
}
