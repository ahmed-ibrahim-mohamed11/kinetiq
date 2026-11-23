/**
 * base64url helpers (RFC 4648 §5) shared by Web Push and VAPID.
 * Runtime-neutral: uses only atob/btoa, available in Deno and Node >= 16.
 */

/** Decodes base64url (or plain base64, padded or not) into bytes. */
export function b64urlDecode(input: string): Uint8Array<ArrayBuffer> {
  const clean = input.trim().replace(/-/g, '+').replace(/_/g, '/').replace(/=+$/, '');
  if (!/^[A-Za-z0-9+/]*$/.test(clean)) throw new Error('invalid base64url string');
  const padded = clean + '='.repeat((4 - (clean.length % 4)) % 4);
  const bin = atob(padded);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

/** Encodes bytes as unpadded base64url. */
export function b64urlEncode(bytes: Uint8Array): string {
  let bin = '';
  for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i] as number);
  return btoa(bin).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

/** Concatenates byte arrays into a fresh ArrayBuffer-backed Uint8Array. */
export function concatBytes(...parts: Uint8Array[]): Uint8Array<ArrayBuffer> {
  const out = new Uint8Array(parts.reduce((n, p) => n + p.length, 0));
  let offset = 0;
  for (const p of parts) {
    out.set(p, offset);
    offset += p.length;
  }
  return out;
}
