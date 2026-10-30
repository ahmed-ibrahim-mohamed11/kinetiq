'use client';

import { Alert } from '@smartgym/ui';
import type { ActionResult } from '@/lib/action-result';

/** Renders the outcome of a server action (success or error) under a form. */
export function ActionFeedback({ state }: { state: ActionResult<unknown> | null }) {
  if (!state) return null;
  if (state.ok) return state.message ? <Alert variant="success">{state.message}</Alert> : null;
  return <Alert variant="error">{state.error}</Alert>;
}
