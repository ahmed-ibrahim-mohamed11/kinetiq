'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { QRCodeSVG } from 'qrcode.react';
import { createClient } from '@smartgym/supabase-client/browser';
import { Alert, Button } from '@smartgym/ui';

const REFRESH_MS = 30_000;

interface TokenResponse {
  token: string;
  expires_in_seconds: number;
}

export function CheckInQr({ memberCode }: { memberCode: string }) {
  const [token, setToken] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [secondsLeft, setSecondsLeft] = useState(REFRESH_MS / 1000);
  const [loading, setLoading] = useState(true);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);

  const fetchToken = useCallback(async () => {
    setLoading(true);
    try {
      const supabase = createClient();
      const { data } = await supabase.auth.getSession();
      const accessToken = data.session?.access_token;
      if (!accessToken) throw new Error('not signed in');
      const base = process.env.NEXT_PUBLIC_SUPABASE_URL;
      if (!base) throw new Error('Supabase URL not configured');
      const res = await fetch(`${base}/functions/v1/qr-token`, {
        method: 'GET',
        headers: { Authorization: `Bearer ${accessToken}`, apikey: process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY ?? '' },
        cache: 'no-store',
      });
      if (!res.ok) throw new Error(`QR service responded ${res.status}`);
      const body = (await res.json()) as TokenResponse;
      if (!body.token) throw new Error('empty token');
      setToken(body.token);
      setError(null);
      setSecondsLeft(REFRESH_MS / 1000);
    } catch (err) {
      setToken(null);
      setError((err as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void fetchToken();
    timer.current = setInterval(() => {
      setSecondsLeft((s) => {
        if (s <= 1) {
          void fetchToken();
          return REFRESH_MS / 1000;
        }
        return s - 1;
      });
    }, 1000);
    return () => {
      if (timer.current) clearInterval(timer.current);
    };
  }, [fetchToken]);

  return (
    <div className="flex flex-col items-center gap-4">
      {token ? (
        <div className="rounded-xl border bg-white p-4 shadow-sm">
          <QRCodeSVG value={token} size={220} level="M" marginSize={1} aria-label="Check-in QR code" />
        </div>
      ) : (
        <div className="flex h-[252px] w-[252px] items-center justify-center rounded-xl border border-dashed text-sm text-muted-foreground">
          {loading ? 'Generating codeâ€¦' : 'No code available'}
        </div>
      )}
      {error ? (
        <Alert variant="warning" title="QR service unavailable">
          Show your member code at the desk instead. <span className="text-xs text-muted-foreground">({error})</span>
        </Alert>
      ) : (
        <p className="text-xs text-muted-foreground">
          Scan at the entrance kiosk or show it to the front desk. {token && <span className="tabular-nums">Refreshes in {secondsLeft}s.</span>}
        </p>
      )}
      <div className="w-full rounded-md bg-muted px-4 py-3 text-center">
        <p className="text-xs uppercase tracking-wide text-muted-foreground">Member code</p>
        <p className="font-mono text-xl font-semibold tracking-widest">{memberCode}</p>
      </div>
      <Button variant="outline" size="sm" onClick={() => void fetchToken()} disabled={loading}>
        {loading ? 'Refreshingâ€¦' : 'Refresh code'}
      </Button>
    </div>
  );
}
