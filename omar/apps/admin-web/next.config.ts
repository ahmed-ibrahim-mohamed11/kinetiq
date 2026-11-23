import type { NextConfig } from 'next';

const nextConfig: NextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  env: {
    NEXT_PUBLIC_AUTH_COOKIE_NAME: 'sb-kinetiq-admin-auth',
  },
  transpilePackages: [
    '@smartgym/ui',
    '@smartgym/supabase-client',
    '@smartgym/api-contracts',
    '@smartgym/validation',
    '@smartgym/shared-config',
    '@smartgym/shared-types',
  ],
  serverExternalPackages: ['@supabase/supabase-js', '@supabase/ssr'],
  eslint: { ignoreDuringBuilds: true },
  experimental: {
    serverActions: { bodySizeLimit: '1mb' },
  },
  async headers() {
    return [
      {
        source: '/(.*)',
        headers: [
          { key: 'X-Frame-Options', value: 'DENY' },
          { key: 'X-Content-Type-Options', value: 'nosniff' },
          { key: 'Referrer-Policy', value: 'strict-origin-when-cross-origin' },
          { key: 'Strict-Transport-Security', value: 'max-age=63072000; includeSubDomains; preload' },
          // Same value as src/middleware.ts: the kiosk QR scanner uses the camera (own origin only).
          // Content-Security-Policy is set per request (nonce) by the middleware.
          { key: 'Permissions-Policy', value: 'camera=(self), microphone=(), geolocation=()' },
        ],
      },
    ];
  },
};

export default nextConfig;
