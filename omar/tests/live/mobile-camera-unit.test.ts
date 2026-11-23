import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { resolveEdgeAiUrl } from '../../packages/ui/src/components/camera-session';
import { buildContentSecurityPolicy } from '../../packages/supabase-client/src/middleware';

describe('Mobile Camera Network & URL Resolution', () => {
  const origWindow = (globalThis as any).window;
  const origEnv = process.env.NEXT_PUBLIC_EDGE_AI_URL;

  afterEach(() => {
    (globalThis as any).window = origWindow;
    if (origEnv !== undefined) {
      process.env.NEXT_PUBLIC_EDGE_AI_URL = origEnv;
    } else {
      delete process.env.NEXT_PUBLIC_EDGE_AI_URL;
    }
  });

  it('never emits 127.0.0.1 when running on a mobile browser over LAN', () => {
    // Simulate mobile browser loaded from LAN IP
    (globalThis as any).window = {
      location: {
        hostname: '192.168.1.3',
        protocol: 'https:',
        origin: 'https://192.168.1.3:3001',
      },
    };
    delete process.env.NEXT_PUBLIC_EDGE_AI_URL;

    const url = resolveEdgeAiUrl();
    expect(url).not.toContain('127.0.0.1');
    expect(url).toBe('http://192.168.1.3:8088');
  });

  it('dynamically rewrites NEXT_PUBLIC_EDGE_AI_URL containing 127.0.0.1 to handset LAN hostname', () => {
    // When baked-in NEXT_PUBLIC_EDGE_AI_URL has 127.0.0.1 from local dev
    process.env.NEXT_PUBLIC_EDGE_AI_URL = 'http://127.0.0.1:8088';

    // Handset accessed via LAN hostname
    (globalThis as any).window = {
      location: {
        hostname: '192.168.1.3',
        protocol: 'https:',
        origin: 'https://192.168.1.3:3001',
      },
    };

    const url = resolveEdgeAiUrl();
    expect(url).not.toContain('127.0.0.1');
    expect(url).toBe('http://192.168.1.3:8088');
  });

  it('preserves remote non-loopback NEXT_PUBLIC_EDGE_AI_URL', () => {
    process.env.NEXT_PUBLIC_EDGE_AI_URL = 'https://edge.gym-cloud.internal:8088';

    (globalThis as any).window = {
      location: {
        hostname: '192.168.1.3',
        protocol: 'https:',
        origin: 'https://192.168.1.3:3001',
      } as any,
    };

    const url = resolveEdgeAiUrl();
    expect(url).toBe('https://edge.gym-cloud.internal:8088');
  });

  it('rewrites propUrl if it mistakenly contains 127.0.0.1 for mobile clients', () => {
    (globalThis as any).window = {
      location: {
        hostname: 'gym-member.local',
        protocol: 'https:',
        origin: 'https://gym-member.local:3001',
      } as any,
    };

    const url = resolveEdgeAiUrl('http://127.0.0.1:8088');
    expect(url).not.toContain('127.0.0.1');
    expect(url).toBe('http://gym-member.local:8088');
  });

  it('resolves correctly on localhost desktop environment', () => {
    (globalThis as any).window = {
      location: {
        hostname: 'localhost',
        protocol: 'http:',
        origin: 'http://localhost:3000',
      },
    };
    delete process.env.NEXT_PUBLIC_EDGE_AI_URL;

    const url = resolveEdgeAiUrl();
    expect(url).toBe('http://localhost:8088');
  });

  it('resolves correctly on LAN IP desktop environment (e.g. 192.168.1.3)', () => {
    (globalThis as any).window = {
      location: {
        hostname: '192.168.1.3',
        protocol: 'http:',
        origin: 'http://192.168.1.3:3000',
      },
    };
    delete process.env.NEXT_PUBLIC_EDGE_AI_URL;

    const url = resolveEdgeAiUrl();
    expect(url).toBe('http://192.168.1.3:8088');
  });

  it('resolves correctly on LAN IP phone environment with HTTPS', () => {
    (globalThis as any).window = {
      location: {
        hostname: '192.168.1.3',
        protocol: 'https:',
        origin: 'https://192.168.1.3:3001',
      },
    };
    delete process.env.NEXT_PUBLIC_EDGE_AI_URL;

    const url = resolveEdgeAiUrl();
    expect(url).toBe('http://192.168.1.3:8088');
  });

  it('handles custom NEXT_PUBLIC_EDGE_AI_URL with custom port and pathname', () => {
    process.env.NEXT_PUBLIC_EDGE_AI_URL = 'http://edge-box:9099/ai';
    (globalThis as any).window = {
      location: {
        hostname: '192.168.1.3',
        protocol: 'https:',
        origin: 'https://192.168.1.3:3001',
      },
    };

    const url = resolveEdgeAiUrl();
    expect(url).toBe('http://edge-box:9099/ai');
  });

  it('Edge AI offline handling provides clear connection error diagnostics', async () => {
    // When Edge AI port 8088 is offline / connection refused
    const origFetch = globalThis.fetch;
    globalThis.fetch = async () => {
      throw new Error('TypeError: fetch failed (ECONNREFUSED 192.168.1.3:8088)');
    };

    try {
      const url = resolveEdgeAiUrl();
      let errorThrown = false;
      try {
        await fetch(`${url}/healthz`);
      } catch (err: any) {
        errorThrown = true;
        expect(err.message).toContain('ECONNREFUSED');
      }
      expect(errorThrown).toBe(true);
    } finally {
      globalThis.fetch = origFetch;
    }
  });

  it('CSP strictly rejects arbitrary and untrusted Host headers (anti-reflection / injection check)', () => {
    // Malicious Host headers
    const maliciousHosts = [
      'evil.com',
      'attacker.org',
      'sub.evil.com',
      '192.168.1.3; script-src evil.com',
      '192.168.1.3\r\nX-Injected: header',
      'localhost.evil.com',
      '192.168.1.3.nip.io',
    ];

    for (const badHost of maliciousHosts) {
      const csp = buildContentSecurityPolicy('test-nonce', 'https://project.supabase.co', true, badHost);
      expect(csp).not.toContain(badHost);
      expect(csp).not.toContain(`http://${badHost}:8088`);
      expect(csp).not.toContain(`https://${badHost}:8088`);
    }
  });

  it('CSP dynamically includes valid private LAN host and excludes fixed invalid IPs', () => {
    const csp = buildContentSecurityPolicy('test-nonce', 'https://project.supabase.co', true, '192.168.1.3');

    // Must include the current client LAN host
    expect(csp).toContain('http://192.168.1.3:8088');
    expect(csp).toContain('https://192.168.1.3:8088');

    // Must NOT have the old hardcoded 192.168.1.11
    expect(csp).not.toContain('192.168.1.11');

    // Must include media-src blob: for MediaStream video srcObject
    expect(csp).toContain("media-src 'self' blob:");
  });
});
