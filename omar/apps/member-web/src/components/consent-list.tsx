'use client';

import { useState, useTransition } from 'react';
import type { Tables } from '@smartgym/supabase-client/types';
import { Alert, Badge, Button, formatDateTime, statusVariant } from '@smartgym/ui';
import { withdrawConsent } from '@/lib/actions';
import { CONSENT_LABELS } from '@/lib/labels';

export function ConsentList({ consents }: { consents: Tables<'consent_records'>[] }) {
  const [pending, start] = useTransition();
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  function withdraw(c: Tables<'consent_records'>) {
    const label = CONSENT_LABELS[c.consent_type]?.title ?? c.consent_type;
    const warning =
      c.consent_type === 'biometric_face_identification'
        ? `Withdraw "${label}"?\n\nYour face template will be deleted from the gym's edge device and you will check in with the QR code instead. This cannot be undone without enrolling again.`
        : c.consent_type === 'cctv_workout_tracking'
          ? `Withdraw "${label}"?\n\nThe cameras will stop counting your workouts and no further reports will be generated.`
          : `Withdraw "${label}"?`;
    if (!window.confirm(warning)) return;
    setError(null);
    setBusyId(c.id);
    start(async () => {
      const result = await withdrawConsent({ consentRecordId: c.id });
      if (!result.ok) setError(result.error ?? 'Could not withdraw consent.');
      setBusyId(null);
    });
  }

  if (consents.length === 0) {
    return <p className="rounded-md border border-dashed p-4 text-sm text-muted-foreground">No consent records on file. Ask the front desk if you expected to see something here.</p>;
  }

  return (
    <div className="space-y-3">
      {error && <Alert variant="error">{error}</Alert>}
      <ul className="divide-y rounded-lg border">
        {consents.map((c) => {
          const meta = CONSENT_LABELS[c.consent_type];
          return (
            <li key={c.id} className="flex flex-col gap-2 px-4 py-3 sm:flex-row sm:items-start sm:justify-between">
              <div className="min-w-0 space-y-1">
                <div className="flex flex-wrap items-center gap-2">
                  <p className="font-medium">{meta?.title ?? c.consent_type}</p>
                  <Badge variant={statusVariant(c.status)}>{c.status}</Badge>
                </div>
                {meta && <p className="text-sm text-muted-foreground">{meta.description}</p>}
                <p className="text-xs text-muted-foreground">
                  Policy {c.policy_version} Â· granted {formatDateTime(c.granted_at)}
                  {c.withdrawn_at && ` Â· withdrawn ${formatDateTime(c.withdrawn_at)}`}
                </p>
              </div>
              {c.status === 'granted' && (
                <Button variant="outline" size="sm" className="shrink-0" onClick={() => withdraw(c)} disabled={pending && busyId === c.id}>
                  {pending && busyId === c.id ? 'Withdrawingâ€¦' : 'Withdraw'}
                </Button>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
