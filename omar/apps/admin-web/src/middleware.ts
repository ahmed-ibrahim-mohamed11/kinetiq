import type { NextRequest } from 'next/server';
import { updateSession } from '@smartgym/supabase-client/middleware';

export async function middleware(request: NextRequest) {
  return updateSession(request, { publicPaths: ['/login', '/auth', '/api/health'], loginPath: '/login' });
}

export const config = {
  matcher: ['/((?!_next/static|_next/image|favicon.ico|.*\\.(?:svg|png|jpg|jpeg|gif|webp|ico)$).*)'],
};
