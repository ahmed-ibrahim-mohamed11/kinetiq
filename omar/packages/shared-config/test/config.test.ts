import { describe, expect, it } from 'vitest';
import { ConfigError, loadPublicEnv, loadServerEnv } from '../src/index';

describe('shared-config', () => {
  it('loads a valid public env', () => {
    const env = loadPublicEnv({
      NEXT_PUBLIC_SUPABASE_URL: 'https://abc.supabase.co',
      NEXT_PUBLIC_SUPABASE_ANON_KEY: 'x'.repeat(40),
    });
    expect(env.NEXT_PUBLIC_APP_ENV).toBe('development');
  });

  it('fails loudly on missing values', () => {
    expect(() => loadPublicEnv({})).toThrow(ConfigError);
  });

  it('refuses service-role keys exposed under NEXT_PUBLIC_', () => {
    expect(() =>
      loadServerEnv({
        SUPABASE_URL: 'https://abc.supabase.co',
        SUPABASE_SERVICE_ROLE_KEY: 'y'.repeat(40),
        NEXT_PUBLIC_SUPABASE_SERVICE_ROLE_KEY: 'leak',
      }),
    ).toThrow(/never be public/);
  });

  it('applies worker defaults', () => {
    const env = loadServerEnv({ SUPABASE_URL: 'https://abc.supabase.co', SUPABASE_SERVICE_ROLE_KEY: 'y'.repeat(40) });
    expect(env.WORKER_POLL_INTERVAL_MS).toBe(2000);
    expect(env.WORKER_MAX_ATTEMPTS).toBe(6);
  });
});
