'use client';

import { useActionState, useEffect, useRef } from 'react';
import { Button, Input, Label, Select } from '@smartgym/ui';
import { recordConsent, withdrawConsent } from '@/lib/actions';
import type { ActionResult } from '@/lib/action-result';
import { ActionFeedback } from '@/components/action-feedback';

const CONSENT_TYPES = [
  { value: 'cctv_workout_tracking', label: 'CCTV workout tracking', hint: 'Required for any automatic exercise tracking.' },
  { value: 'biometric_face_identification', label: 'Biometric face identification', hint: 'Optional. QR/NFC check-in is always available instead.' },
  { value: 'data_processing', label: 'Data processing', hint: 'Storage of workout records and reports.' },
  { value: 'marketing', label: 'Marketing communications', hint: 'Optional.' },
] as const;

export function ConsentForm({ memberId }: { memberId: string }) {
  const [state, action, pending] = useActionState<ActionResult | null, FormData>(recordConsent, null);
  const formRef = useRef<HTMLFormElement>(null);
  useEffect(() => {
    if (state?.ok) formRef.current?.reset();
  }, [state]);

  return (
    <form ref={formRef} action={action} className="space-y-4 rounded-md border p-4">
      <p className="text-sm font-medium">Record consent</p>
      <input type="hidden" name="member_id" value={memberId} />
      <div className="space-y-2">
        <Label htmlFor="consent_type">Type</Label>
        <Select id="consent_type" name="consent_type" required defaultValue="cctv_workout_tracking">
          {CONSENT_TYPES.map((t) => (
            <option key={t.value} value={t.value}>
              {t.label}
            </option>
          ))}
        </Select>
        <p className="text-xs text-muted-foreground">Biometric identification is optional and always has a non-biometric alternative.</p>
      </div>
      <div className="space-y-2">
        <Label htmlFor="policy_version">Privacy policy version</Label>
        <Input id="policy_version" name="policy_version" required defaultValue="privacy-v1" maxLength={32} />
      </div>
      <label className="flex items-start gap-2 text-sm">
        <input type="checkbox" name="informed_acknowledged" required className="mt-0.5 h-4 w-4 rounded border-input" />
        <span>
          The member was informed, in plain language, what is processed (camera tracking and, if selected, a facial template stored only on the gym&apos;s
          edge device), for what purpose, for how long, and how to withdraw. They explicitly agreed.
        </span>
      </label>
      <ActionFeedback state={state} />
      <Button type="submit" disabled={pending} className="w-full">
        {pending ? 'Saving…' : 'Record consent'}
      </Button>
    </form>
  );
}

export function WithdrawConsentButton({ consentId, memberId }: { consentId: string; memberId: string }) {
  const [state, action, pending] = useActionState<ActionResult | null, FormData>(withdrawConsent, null);
  return (
    <form action={action} className="inline-flex flex-col items-end gap-1">
      <input type="hidden" name="consent_record_id" value={consentId} />
      <input type="hidden" name="member_id" value={memberId} />
      <Button type="submit" size="sm" variant="ghost" disabled={pending} className="text-destructive hover:text-destructive">
        {pending ? 'Withdrawing…' : 'Withdraw'}
      </Button>
      {state && !state.ok && <span className="text-xs text-destructive">{state.error}</span>}
    </form>
  );
}
