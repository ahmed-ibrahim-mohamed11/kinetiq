import type { NextRequest } from 'next/server';
import { updateSession } from '@smartgym/supabase-client/middleware';

export async function middleware(request: NextRequest) {
  // /r/<token> is the public shared-report link; everything else needs a session.
  return updateSession(request, { publicPaths: ['/login', '/auth', '/r', '/api/health', '/sw.js', '/manifest.webmanifest'], loginPath: '/login' });
}

export const config = {
  matcher: ['/((?!_next/static|_next/image|favicon.ico|sw.js|manifest.webmanifest|.*\\.(?:svg|png|jpg|jpeg|gif|webp|ico)$).*)'],
};
