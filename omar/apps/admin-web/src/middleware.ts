import type { NextRequest } from 'next/server';
import { updateSession } from '@smartgym/supabase-client/middleware';

// The kiosk QR scanner needs the camera. Permissions-Policy applies per document and the
// dashboard navigates client-side, so the whole (staff-only) app allows it for its own origin.
const ADMIN_PERMISSIONS_POLICY = 'camera=(self), microphone=(), geolocation=()';

export async function middleware(request: NextRequest) {
  return updateSession(request, { publicPaths: ['/login', '/auth', '/api/health', '/_not-found'], loginPath: '/login', permissionsPolicy: ADMIN_PERMISSIONS_POLICY });
}

export const config = {
  matcher: ['/((?!_next/static|_next/image|favicon.ico|.*\\.(?:svg|png|jpg|jpeg|gif|webp|ico)$).*)'],
};
