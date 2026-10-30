import { createServerClient, type CookieOptions } from '@supabase/ssr';
import { NextResponse, type NextRequest } from 'next/server';
import type { Database } from './types/database';

type CookieToSet = { name: string; value: string; options?: CookieOptions };

export interface SessionGuardOptions {
  /** Paths that do not require a session (prefix match). */
  publicPaths: string[];
  /** Where to send anonymous users. */
  loginPath: string;
}

/**
 * Refreshes the Supabase session cookie on every request and redirects
 * anonymous users away from protected routes. Use from each app's middleware.ts.
 */
export async function updateSession(request: NextRequest, opts: SessionGuardOptions) {
  let response = NextResponse.next({ request });
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const key = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;
  if (!url || !key) return response;

  const supabase = createServerClient<Database>(url, key, {
    cookies: {
      getAll() {
        return request.cookies.getAll();
      },
      setAll(cookiesToSet: CookieToSet[]) {
        cookiesToSet.forEach(({ name, value }) => request.cookies.set(name, value));
        response = NextResponse.next({ request });
        cookiesToSet.forEach(({ name, value, options }) => response.cookies.set(name, value, options));
      },
    },
  });

  const {
    data: { user },
  } = await supabase.auth.getUser();

  const path = request.nextUrl.pathname;
  const isPublic = opts.publicPaths.some((p) => path === p || path.startsWith(p.endsWith('/') ? p : `${p}/`));
  if (!user && !isPublic) {
    const redirect = request.nextUrl.clone();
    redirect.pathname = opts.loginPath;
    redirect.searchParams.set('next', path);
    return NextResponse.redirect(redirect);
  }
  // Security headers for every response.
  response.headers.set('X-Frame-Options', 'DENY');
  response.headers.set('X-Content-Type-Options', 'nosniff');
  response.headers.set('Referrer-Policy', 'strict-origin-when-cross-origin');
  response.headers.set('Permissions-Policy', 'camera=(), microphone=(), geolocation=()');
  return response;
}
