'use client';

import { useActionState } from 'react';
import { Button } from '@smartgym/ui';
import { staffCheckIn, staffCheckOut } from '@/lib/actions';
import type { ActionResult } from '@/lib/action-result';

export function CheckInButton({ memberId, activeSessionId, disabled }: { memberId: string; activeSessionId: string | null; disabled: boolean }) {
  const [inState, checkIn, inPending] = useActionState<ActionResult | null, FormData>(staffCheckIn, null);
  const [outState, checkOut, outPending] = useActionState<ActionResult | null, FormData>(staffCheckOut, null);
  const state = activeSessionId ? outState : inState;

  return (
    <div className="flex flex-col items-end gap-1">
      {activeSessionId ? (
        <form action={checkOut}>
          <input type="hidden" name="attendance_session_id" value={activeSessionId} />
          <input type="hidden" name="member_id" value={memberId} />
          <Button type="submit" variant="outline" disabled={outPending}>
            {outPending ? 'Checking out…' : 'Check out'}
          </Button>
        </form>
      ) : (
        <form action={checkIn}>
          <input type="hidden" name="member_id" value={memberId} />
          <Button type="submit" disabled={disabled || inPending} title={disabled ? 'Membership is not active' : undefined}>
            {inPending ? 'Checking in…' : 'Check in (manual)'}
          </Button>
        </form>
      )}
      {state && !state.ok && <span className="max-w-xs text-right text-xs text-destructive">{state.error}</span>}
      {state && state.ok && state.message && <span className="max-w-xs text-right text-xs text-success">{state.message}</span>}
    </div>
  );
}
