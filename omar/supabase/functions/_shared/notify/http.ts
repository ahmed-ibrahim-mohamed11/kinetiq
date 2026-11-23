/** Outbound HTTP helpers shared by the push and email senders. Runtime-neutral (global fetch only). */

export const DEFAULT_HTTP_TIMEOUT_MS = 10_000;

/**
 * fetch with a deadline (AbortSignal.timeout; Deno and Node >= 18). A hanging endpoint would
 * otherwise stall a dispatch batch past pg_net's 25 s timeout and leave its claimed jobs in
 * 'sending' until the stale-lock release frees them. The deadline also covers reading the body.
 */
export async function fetchWithTimeout(
  f: typeof fetch,
  url: string,
  init: RequestInit,
  timeoutMs: number,
  what: string,
): Promise<Response> {
  try {
    return await f(url, { ...init, signal: AbortSignal.timeout(timeoutMs) });
  } catch (err) {
    const name = (err as { name?: string } | null)?.name;
    if (name === 'TimeoutError' || name === 'AbortError') throw new Error(`${what} did not respond within ${timeoutMs} ms`);
    throw err;
  }
}
