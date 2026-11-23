import { createServerClient, type CookieOptions } from '@supabase/ssr';
import { NextResponse, type NextRequest } from 'next/server';
import type { Database } from './types/database';

type CookieToSet = { name: string; value: string; options?: CookieOptions };

export interface SessionGuardOptions {
  /** Paths that do not require a session (prefix match). */
  publicPaths: string[];
  /** Where to send anonymous users. */
  loginPath: string;
  /**
   * Permissions-Policy for every response. Default denies camera, microphone and
   * geolocation. The admin app allows `camera=(self)` for the kiosk QR scanner.
   * Keep it equal to the value in the app's next.config.ts.
   */
  permissionsPolicy?: string;
}

export const DEFAULT_PERMISSIONS_POLICY = 'camera=(), microphone=(), geolocation=()';

/** 128-bit random nonce, base64 (Edge runtime: Web Crypto + btoa). */
function makeNonce(): string {
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  let bin = '';
  for (const b of bytes) bin += String.fromCharCode(b);
  return btoa(bin);
}

/** Origins the browser may talk to: the app itself plus the Supabase project (REST, Auth, Functions, Realtime). */
function supabaseConnectSources(supabaseUrl: string | undefined): string[] {
  if (!supabaseUrl) return [];
  try {
    const u = new URL(supabaseUrl);
    const ws = `${u.protocol === 'https:' ? 'wss:' : 'ws:'}//${u.host}`;
    return [u.origin, ws];
  } catch {
    return [];
  }
}

/**
 * Validates a host string against a strict allowlist.
 * Ensures arbitrary or attacker-controlled Host headers are never reflected into CSP.
 */
export function isAllowedCspHost(host: string | undefined): boolean {
  if (!host || typeof host !== 'string') return false;
  const trimmed = host.trim().toLowerCase();

  // Strict format check: must be a valid hostname or IP address without scheme, path, ports, or special chars
  if (!/^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)*$/.test(trimmed)) {
    return false;
  }

  // 1. Safe local identifiers
  if (['localhost', '127.0.0.1', '::1', 'host.docker.internal', 'kinetiq.local'].includes(trimmed)) {
    return true;
  }

  // 2. mDNS local domain (*.local)
  if (/^[a-z0-9-]+\.local$/.test(trimmed)) {
    return true;
  }

  // 3. RFC 1918 Private IPv4 address ranges:
  // 10.0.0.0/8
  if (/^10\.(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\.(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\.(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)$/.test(trimmed)) {
    return true;
  }
  // 172.16.0.0/12
  if (/^172\.(?:1[6-9]|2\d|3[01])\.(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\.(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)$/.test(trimmed)) {
    return true;
  }
  // 192.168.0.0/16
  if (/^192\.168\.(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\.(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)$/.test(trimmed)) {
    return true;
  }

  // 4. Explicitly allowed hosts from environment
  const envAllowed = (process.env.ALLOWED_HOSTS || '')
    .split(',')
    .map((h) => h.trim().toLowerCase())
    .filter(Boolean);
  if (envAllowed.includes(trimmed)) {
    return true;
  }

  return false;
}

/**
 * Nonce-based Content-Security-Policy (Next.js 15 pattern). Next reads the nonce
 * from the request's CSP header and puts it on its own scripts; 'strict-dynamic'
 * lets those scripts load the rest of the bundle. Pages must render dynamically
 * (the root layouts call connection()), otherwise prerendered HTML has no nonce.
 */
export function buildContentSecurityPolicy(
  nonce: string,
  supabaseUrl: string | undefined,
  dev = process.env.NODE_ENV === 'development',
  requestHost?: string,
): string {
  const dynamicEdgeSources: string[] = [];
  if (requestHost && isAllowedCspHost(requestHost) && requestHost !== 'localhost' && requestHost !== '127.0.0.1') {
    dynamicEdgeSources.push(`http://${requestHost}:8088`, `https://${requestHost}:8088`);
  }
  const edgeAiEnv = process.env.NEXT_PUBLIC_EDGE_AI_URL;
  if (edgeAiEnv) {
    try {
      const u = new URL(edgeAiEnv);
      dynamicEdgeSources.push(u.origin);
    } catch {
      // ignore invalid env URL
    }
  }

  const connect = [
    "'self'",
    ...supabaseConnectSources(supabaseUrl),
    'https://cdn.jsdelivr.net',
    'http://127.0.0.1:8088',
    'http://localhost:8088',
    ...dynamicEdgeSources,
  ];
  if (dev) connect.push('ws:', 'wss:'); // Next dev HMR socket
  const imgSources = [
    "'self'",
    'data:',
    'blob:',
    'http://127.0.0.1:8088',
    'http://localhost:8088',
    ...dynamicEdgeSources,
  ];
  return [
    "default-src 'self'",
    `script-src 'self' 'nonce-${nonce}' 'strict-dynamic' 'wasm-unsafe-eval' 'unsafe-eval' https://cdn.jsdelivr.net`,
    "style-src 'self' 'unsafe-inline'",
    `img-src ${imgSources.join(' ')}`,
    "font-src 'self' data:",
    `connect-src ${connect.join(' ')}`,
    "worker-src 'self' blob:",
    "manifest-src 'self'",
    "media-src 'self' blob:",
    "frame-src 'none'",
    "frame-ancestors 'none'",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
  ].join('; ');
}

function applySecurityHeaders(response: NextResponse, csp: string, permissionsPolicy: string): NextResponse {
  response.headers.set('Content-Security-Policy', csp);
  response.headers.set('X-Frame-Options', 'DENY');
  response.headers.set('X-Content-Type-Options', 'nosniff');
  response.headers.set('Referrer-Policy', 'strict-origin-when-cross-origin');
  response.headers.set('Permissions-Policy', permissionsPolicy);
  return response;
}

/**
 * Refreshes the Supabase session cookie on every request, redirects anonymous
 * users away from protected routes and sets the security headers (CSP with a
 * fresh nonce per request). Use from each app's middleware.ts.
 */
export async function updateSession(request: NextRequest, opts: SessionGuardOptions) {
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const key = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;
  const permissionsPolicy = opts.permissionsPolicy ?? DEFAULT_PERMISSIONS_POLICY;
  const nonce = makeNonce();
  const rawHost = request.headers.get('x-forwarded-host') || request.headers.get('host') || request.nextUrl.hostname;
  const host = (rawHost && !rawHost.startsWith('0.0.0.0')) ? rawHost.split(':')[0] : (request.nextUrl.hostname !== '0.0.0.0' ? request.nextUrl.hostname : undefined);
  const csp = buildContentSecurityPolicy(nonce, url, process.env.NODE_ENV === 'development', host);
  // The renderer reads the nonce from the request's CSP header.
  request.headers.set('x-nonce', nonce);
  request.headers.set('Content-Security-Policy', csp);

  let response = NextResponse.next({ request });
  if (!url || !key) return applySecurityHeaders(response, csp, permissionsPolicy);

  const cookieName = process.env.NEXT_PUBLIC_AUTH_COOKIE_NAME;
  const supabase = createServerClient<Database>(url, key, {
    cookieOptions: cookieName ? { name: cookieName } : undefined,
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
    redirect.search = '';
    redirect.searchParams.set('next', `${path}${request.nextUrl.search}`);
    return applySecurityHeaders(NextResponse.redirect(redirect), csp, permissionsPolicy);
  }
  return applySecurityHeaders(response, csp, permissionsPolicy);
}
