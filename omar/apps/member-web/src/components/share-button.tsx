'use client';

import { useEffect, useState, useTransition } from 'react';
import { Check, Copy, Link2, Link2Off, Printer } from 'lucide-react';
import { Alert, Button, Input, Select, formatDateTime } from '@smartgym/ui';
import { createReportShare, revokeReportShare } from '@/lib/actions';

const LIFETIMES = [
  { days: 1, label: '1 day' },
  { days: 7, label: '7 days' },
  { days: 30, label: '30 days' },
] as const;

/**
 * Report sharing on demand (C3): no link exists until the member creates one.
 * The member picks a lifetime (1-30 days), can copy the link, and can revoke it;
 * creating a new link replaces the previous one.
 */
export function ShareButton({ reportId, shareToken, expiresAt }: { reportId: string; shareToken: string | null; expiresAt: string | null }) {
  const [share, setShare] = useState<{ token: string; expiresAt: string } | null>(shareToken && expiresAt ? { token: shareToken, expiresAt } : null);
  const [days, setDays] = useState<number>(7);
  const [origin, setOrigin] = useState('');
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pending, startTransition] = useTransition();

  useEffect(() => setOrigin(window.location.origin), []);

  const active = share !== null && new Date(share.expiresAt).getTime() > Date.now();
  const url = share ? `${origin}/r/${share.token}` : '';

  function create() {
    setError(null);
    startTransition(async () => {
      const result = await createReportShare({ reportId, days });
      if (!result.ok || !result.data) {
        setError(result.error ?? 'Could not create the share link.');
        return;
      }
      setShare({ token: result.data.token, expiresAt: result.data.expires_at });
    });
  }

  function revoke() {
    if (!window.confirm('Stop sharing this report? Anyone with the link will no longer be able to open it.')) return;
    setError(null);
    startTransition(async () => {
      const result = await revokeReportShare({ reportId });
      if (!result.ok) {
        setError(result.error ?? 'Could not revoke the share link.');
        return;
      }
      setShare(null);
      setCopied(false);
    });
  }

  async function copy() {
    try {
      await navigator.clipboard.writeText(url);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      window.prompt('Copy this link', url);
    }
  }

  return (
    <div className="flex w-full flex-col items-end gap-2 print:hidden sm:w-auto">
      <div className="flex flex-wrap items-center justify-end gap-2">
        {active ? (
          <>
            <Button variant="outline" size="sm" onClick={() => void copy()} disabled={pending || !origin}>
              {copied ? <Check className="h-4 w-4" aria-hidden /> : <Copy className="h-4 w-4" aria-hidden />}
              {copied ? 'Link copied' : 'Copy link'}
            </Button>
            <Button variant="ghost" size="sm" onClick={revoke} disabled={pending} data-testid="share-revoke" className="text-destructive hover:text-destructive">
              <Link2Off className="h-4 w-4" aria-hidden />
              {pending ? 'Working…' : 'Stop sharing'}
            </Button>
          </>
        ) : (
          <>
            <label htmlFor="share-days" className="sr-only">
              Link valid for
            </label>
            <Select id="share-days" value={days} onChange={(e) => setDays(Number(e.target.value))} className="h-9 w-auto" disabled={pending}>
              {LIFETIMES.map((l) => (
                <option key={l.days} value={l.days}>
                  {l.label}
                </option>
              ))}
            </Select>
            <Button variant="outline" size="sm" onClick={create} disabled={pending} data-testid="share-create">
              <Link2 className="h-4 w-4" aria-hidden />
              {pending ? 'Creating…' : 'Create share link'}
            </Button>
          </>
        )}
        <Button variant="ghost" size="sm" onClick={() => window.print()}>
          <Printer className="h-4 w-4" aria-hidden />
          Print
        </Button>
      </div>
      {active && share && (
        <div className="w-full space-y-1 sm:w-96">
          <label htmlFor="share-url" className="sr-only">
            Share link
          </label>
          <Input id="share-url" data-testid="share-url" readOnly value={url} onFocus={(e) => e.currentTarget.select()} className="h-9 font-mono text-xs" />
          <p className="text-right text-xs text-muted-foreground">
            Anyone with this link can see this report and your first name until {formatDateTime(share.expiresAt)}.
          </p>
        </div>
      )}
      {!active && share && <p className="text-xs text-muted-foreground">The previous share link has expired.</p>}
      {error && <Alert variant="error">{error}</Alert>}
    </div>
  );
}
