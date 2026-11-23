'use client';

import { useState } from 'react';
import { Check, Copy, Printer } from 'lucide-react';
import { Button, formatDateTime } from '@smartgym/ui';

export function ShareButton({ shareToken, expiresAt }: { shareToken: string; expiresAt: string }) {
  const [copied, setCopied] = useState(false);
  const expired = new Date(expiresAt).getTime() < Date.now();

  async function copy() {
    const url = `${window.location.origin}/r/${shareToken}`;
    try {
      await navigator.clipboard.writeText(url);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      window.prompt('Copy this link', url);
    }
  }

  return (
    <div className="flex flex-wrap items-center gap-2 print:hidden">
      <Button variant="outline" size="sm" onClick={() => void copy()} disabled={expired}>
        {copied ? <Check className="h-4 w-4" aria-hidden /> : <Copy className="h-4 w-4" aria-hidden />}
        {copied ? 'Link copied' : 'Share link'}
      </Button>
      <Button variant="ghost" size="sm" onClick={() => window.print()}>
        <Printer className="h-4 w-4" aria-hidden />
        Print
      </Button>
      <span className="text-xs text-muted-foreground">{expired ? 'Share link expired' : `Link valid until ${formatDateTime(expiresAt)}`}</span>
    </div>
  );
}
