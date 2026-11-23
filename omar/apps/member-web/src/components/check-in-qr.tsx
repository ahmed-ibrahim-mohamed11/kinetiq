'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { QRCodeSVG } from 'qrcode.react';
import { Alert, Button } from '@smartgym/ui';
import { getQrTokenAction } from '@/lib/actions';

const REFRESH_MS = 30_000;

export function CheckInQr({ memberCode }: { memberCode: string }) {
  const [token, setToken] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [secondsLeft, setSecondsLeft] = useState(REFRESH_MS / 1000);
  const [loading, setLoading] = useState(true);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);

  const fetchToken = useCallback(async () => {
    setLoading(true);
    try {
      const res = await getQrTokenAction();
      if (!res.ok || !res.data?.token) {
        throw new Error(res.error || 'Failed to generate token');
      }
      setToken(res.data.token);
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
        <div className="flex flex-col items-center gap-2">
          <div className="rounded-xl border bg-white p-4 shadow-sm">
            <QRCodeSVG value={token} size={220} level="M" marginSize={1} aria-label="Check-in QR code" />
          </div>
          {/* The same code as text, so the front desk can type it when the kiosk camera is unavailable. */}
          <p className="max-w-[18rem] select-all break-all text-center font-mono text-xs text-muted-foreground" data-testid="qr-token-text" aria-label="Check-in code as text">
            {token}
          </p>
        </div>
      ) : (
        <div className="flex h-[252px] w-[252px] items-center justify-center rounded-xl border border-dashed text-sm text-muted-foreground">
          {loading ? 'Generating code…' : 'No code available'}
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
        {loading ? 'Refreshing…' : 'Refresh code'}
      </Button>
    </div>
  );
}
