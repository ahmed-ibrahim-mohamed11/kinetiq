'use client';

import { useEffect, useState } from 'react';
import { formatDuration } from '@smartgym/ui';

/** Live-ticking duration since `sinceIso`. */
export function Elapsed({ sinceIso, className }: { sinceIso: string; className?: string }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);
  const seconds = Math.max(0, (now - new Date(sinceIso).getTime()) / 1000);
  return (
    <span className={className} aria-live="off" suppressHydrationWarning>
      {formatDuration(seconds)}
    </span>
  );
}
