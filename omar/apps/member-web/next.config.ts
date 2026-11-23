import type { NextConfig } from 'next';

const nextConfig: NextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  allowedDevOrigins: ['192.168.1.3', '127.0.0.1', 'localhost', '*.local'],
  env: {
    NEXT_PUBLIC_AUTH_COOKIE_NAME: 'sb-kinetiq-member-auth',
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

  async headers() {
    return [
      {
        source: '/(.*)',
        headers: [
          { key: 'X-Frame-Options', value: 'DENY' },
          { key: 'X-Content-Type-Options', value: 'nosniff' },
          { key: 'Referrer-Policy', value: 'strict-origin-when-cross-origin' },
          { key: 'Strict-Transport-Security', value: 'max-age=63072000; includeSubDomains; preload' },
          { key: 'Permissions-Policy', value: 'camera=(self), microphone=(), geolocation=()' },
          // Content-Security-Policy is set per request (nonce) by the middleware.
        ],
      },
      // Service worker must be served from the origin root with no caching surprises.
      { source: '/sw.js', headers: [{ key: 'Cache-Control', value: 'no-cache' }, { key: 'Service-Worker-Allowed', value: '/' }] },
    ];
  },
};

export default nextConfig;
