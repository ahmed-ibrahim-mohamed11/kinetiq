'use client';

import { useState, useTransition } from 'react';
import { useRouter } from 'next/navigation';
import { Alert, Button } from '@smartgym/ui';
import { memberCheckOut } from '@/lib/actions';

export function CheckOutButton() {
  const router = useRouter();
  const [pending, start] = useTransition();
  const [error, setError] = useState<string | null>(null);

  function onClick() {
    if (!window.confirm('Check out now? Your session will end and your workout report will be generated.')) return;
    start(async () => {
      const result = await memberCheckOut();
      if (!result.ok || !result.data) {
        setError(result.error ?? 'Check-out failed.');
        return;
      }
      router.push(`/reports/${result.data.reportId}`);
    });
  }

  return (
    <div className="space-y-2">
      {error && <Alert variant="error">{error}</Alert>}
      <Button className="w-full" variant="default" onClick={onClick} disabled={pending}>
        {pending ? 'Finishing your session…' : 'Check out'}
      </Button>
    </div>
  );
}
