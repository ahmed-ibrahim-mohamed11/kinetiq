/** Shared shape returned by every server action; safe to import from client components. */
export type ActionResult<T = unknown> =
  | { ok: true; message?: string; data?: T }
  | { ok: false; error: string };

export const initialActionState: ActionResult<never> | null = null;
