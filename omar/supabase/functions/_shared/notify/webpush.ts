/**
 * Web Push sender built on WebCrypto only (no npm web-push), so it runs on the
 * Supabase Edge Runtime (Deno) as well as on Node >= 20.
 *
 *  - VAPID (RFC 8292): ES256 JWT { aud: origin of the endpoint, exp <= 12h, sub: VAPID_SUBJECT },
 *    signed with the application server key imported as a JWK (d from VAPID_PRIVATE_KEY,
 *    x/y from VAPID_PUBLIC_KEY; both in the web-push base64url format).
 *    Header: Authorization: vapid t=<jwt>, k=<VAPID_PUBLIC_KEY>.
 *  - Message encryption (RFC 8291) with the aes128gcm content coding (RFC 8188):
 *    ephemeral P-256 ECDH with the subscription key, IKM = HKDF(auth, ecdh,
 *    "WebPush: info\0" || ua_public || as_public, 32), CEK = HKDF(salt, IKM,
 *    "Content-Encoding: aes128gcm\0", 16), NONCE = HKDF(salt, IKM, "Content-Encoding: nonce\0", 12),
 *    a single record terminated by the 0x02 padding delimiter, record size 4096 and the
 *    header salt(16) || rs(uint32) || idlen(1) || keyid(as_public, 65).
 */
import { b64urlDecode, b64urlEncode, concatBytes } from './base64url.ts';
import { DEFAULT_HTTP_TIMEOUT_MS, fetchWithTimeout } from './http.ts';
import type { PushSender, PushSub } from './worker.ts';

const enc = new TextEncoder();

export const RECORD_SIZE = 4096;
/** Push services accept at most 4096 bytes of encrypted body: 86 header + payload + 1 delimiter + 16 tag. */
export const MAX_PAYLOAD_BYTES = RECORD_SIZE - 86 - 1 - 16;
export const MAX_VAPID_EXPIRY_SECONDS = 12 * 60 * 60;

export interface VapidConfig {
  /** base64url uncompressed P-256 public key (65 bytes), as printed by `web-push generate-vapid-keys`. */
  publicKey: string;
  /** base64url P-256 private scalar d (32 bytes). */
  privateKey: string;
  /** mailto: or https: contact URI. */
  subject: string;
}

function decodePublicPoint(b64: string, what: string): Uint8Array<ArrayBuffer> {
  const bytes = b64urlDecode(b64);
  if (bytes.length !== 65 || bytes[0] !== 0x04) throw new Error(`${what} must be an uncompressed P-256 point (65 bytes, base64url)`);
  return bytes;
}

// ── VAPID ────────────────────────────────────────────────────────────────────

export function validateVapidSubject(subject: string): void {
  if (!/^mailto:.+@.+/.test(subject) && !/^https:\/\/.+/.test(subject)) {
    throw new Error('VAPID_SUBJECT must be a mailto: or https: URI');
  }
}

/** Imports the VAPID signing key (ECDSA P-256) from the web-push base64url key pair. */
export async function importVapidSigningKey(publicKey: string, privateKey: string): Promise<CryptoKey> {
  const pub = decodePublicPoint(publicKey, 'VAPID_PUBLIC_KEY');
  const d = b64urlDecode(privateKey);
  if (d.length !== 32) throw new Error('VAPID_PRIVATE_KEY must be a 32-byte P-256 private key (base64url)');
  const jwk: JsonWebKey = {
    kty: 'EC',
    crv: 'P-256',
    x: b64urlEncode(pub.subarray(1, 33)),
    y: b64urlEncode(pub.subarray(33, 65)),
    d: b64urlEncode(d),
    ext: false,
  };
  return await crypto.subtle.importKey('jwk', jwk, { name: 'ECDSA', namedCurve: 'P-256' }, false, ['sign']);
}

/** The JWT audience is the origin of the push service endpoint. */
export function vapidAudience(endpoint: string): string {
  return new URL(endpoint).origin;
}

export interface VapidJwtOptions {
  /** Milliseconds since epoch; defaults to Date.now(). */
  now?: number;
  /** Lifetime in seconds, capped at 12h (RFC 8292 recommends <= 24h; we stay well inside). */
  expiresInSeconds?: number;
}

export async function createVapidJwt(signingKey: CryptoKey, audience: string, subject: string, opts: VapidJwtOptions = {}): Promise<string> {
  const nowSec = Math.floor((opts.now ?? Date.now()) / 1000);
  const ttl = Math.max(60, Math.min(opts.expiresInSeconds ?? MAX_VAPID_EXPIRY_SECONDS, MAX_VAPID_EXPIRY_SECONDS));
  const header = b64urlEncode(enc.encode(JSON.stringify({ typ: 'JWT', alg: 'ES256' })));
  const claims = b64urlEncode(enc.encode(JSON.stringify({ aud: audience, exp: nowSec + ttl, sub: subject })));
  const signingInput = `${header}.${claims}`;
  // WebCrypto ECDSA returns the IEEE P1363 r||s form (64 bytes), which is exactly what JWS ES256 expects.
  const sig = new Uint8Array(await crypto.subtle.sign({ name: 'ECDSA', hash: 'SHA-256' }, signingKey, enc.encode(signingInput)));
  return `${signingInput}.${b64urlEncode(sig)}`;
}

export function vapidAuthorization(jwt: string, publicKey: string): string {
  return `vapid t=${jwt}, k=${publicKey}`;
}

// ── RFC 8291 / RFC 8188 aes128gcm ────────────────────────────────────────────

async function hkdf(salt: Uint8Array<ArrayBuffer>, ikm: Uint8Array<ArrayBuffer>, info: Uint8Array<ArrayBuffer>, length: number): Promise<Uint8Array<ArrayBuffer>> {
  const key = await crypto.subtle.importKey('raw', ikm, 'HKDF', false, ['deriveBits']);
  return new Uint8Array(await crypto.subtle.deriveBits({ name: 'HKDF', hash: 'SHA-256', salt, info }, key, length * 8));
}

export interface EncryptOptions {
  /** 16 random bytes; generated when omitted (tests pass a fixed salt). */
  salt?: Uint8Array;
  /** Ephemeral application-server ECDH key pair; generated when omitted. */
  serverKeys?: CryptoKeyPair;
}

/**
 * Encrypts a push message for a subscription (p256dh / auth in base64url).
 * Returns the complete aes128gcm body: header || single encrypted record.
 */
export async function encryptPushPayload(
  plaintext: Uint8Array,
  p256dh: string,
  authSecret: string,
  opts: EncryptOptions = {},
): Promise<Uint8Array<ArrayBuffer>> {
  const uaPublic = decodePublicPoint(p256dh, 'subscription p256dh');
  const auth = b64urlDecode(authSecret);
  if (auth.length < 16) throw new Error('subscription auth secret must be at least 16 bytes');
  if (plaintext.length > MAX_PAYLOAD_BYTES) throw new Error(`push payload too large (${plaintext.length} > ${MAX_PAYLOAD_BYTES} bytes)`);

  const salt = opts.salt ? Uint8Array.from(opts.salt) : crypto.getRandomValues(new Uint8Array(16));
  if (salt.length !== 16) throw new Error('salt must be 16 bytes');

  const serverKeys = opts.serverKeys ??
    (await crypto.subtle.generateKey({ name: 'ECDH', namedCurve: 'P-256' }, true, ['deriveBits']) as CryptoKeyPair);
  const asPublic = new Uint8Array(await crypto.subtle.exportKey('raw', serverKeys.publicKey));

  const uaKey = await crypto.subtle.importKey('raw', uaPublic, { name: 'ECDH', namedCurve: 'P-256' }, false, []);
  const ecdhSecret = new Uint8Array(await crypto.subtle.deriveBits({ name: 'ECDH', public: uaKey }, serverKeys.privateKey, 256));

  const keyInfo = concatBytes(enc.encode('WebPush: info\x00'), uaPublic, asPublic);
  const ikm = await hkdf(auth, ecdhSecret, keyInfo, 32);
  const cek = await hkdf(salt, ikm, enc.encode('Content-Encoding: aes128gcm\x00'), 16);
  const nonce = await hkdf(salt, ikm, enc.encode('Content-Encoding: nonce\x00'), 12);

  // Single (last) record: plaintext followed by the 0x02 padding delimiter, no extra padding.
  const record = concatBytes(plaintext, new Uint8Array([0x02]));
  const aesKey = await crypto.subtle.importKey('raw', cek, 'AES-GCM', false, ['encrypt']);
  const ciphertext = new Uint8Array(await crypto.subtle.encrypt({ name: 'AES-GCM', iv: nonce, tagLength: 128 }, aesKey, record));

  const header = new Uint8Array(16 + 4 + 1 + asPublic.length);
  header.set(salt, 0);
  new DataView(header.buffer).setUint32(16, RECORD_SIZE, false);
  header[20] = asPublic.length;
  header.set(asPublic, 21);
  return concatBytes(header, ciphertext);
}

// ── Sender ───────────────────────────────────────────────────────────────────

export interface WebPushOptions {
  vapid: VapidConfig;
  /** Seconds the push service keeps an undelivered message (default 3600, same as the Node worker). */
  ttlSeconds?: number;
  urgency?: 'very-low' | 'low' | 'normal' | 'high';
  /** Abort a push-service request after this many milliseconds (default 10 000). */
  timeoutMs?: number;
  fetchImpl?: typeof fetch;
}

/** Error thrown for non-2xx push service responses; `statusCode` drives 404/410 handling in processJob. */
export class PushServiceError extends Error {
  constructor(
    message: string,
    readonly statusCode: number,
  ) {
    super(message);
    this.name = 'PushServiceError';
  }
}

export class WebPushSender implements PushSender {
  private signingKey?: Promise<CryptoKey>;

  constructor(private readonly opts: WebPushOptions) {
    validateVapidSubject(opts.vapid.subject);
  }

  private key(): Promise<CryptoKey> {
    this.signingKey ??= importVapidSigningKey(this.opts.vapid.publicKey, this.opts.vapid.privateKey);
    return this.signingKey;
  }

  /**
   * Imports the VAPID key once; rejects when the key material is malformed. The dispatcher calls
   * this before claiming jobs, so a configuration mistake leaves them queued instead of failing
   * (and eventually dead-lettering) every claimed web_push job.
   */
  async ready(): Promise<void> {
    await this.key();
  }

  /** Builds the HTTP request for one subscription (exposed for tests). */
  async buildRequest(sub: PushSub, json: string): Promise<{ url: string; init: RequestInit }> {
    const endpoint = new URL(sub.endpoint);
    // Endpoints are member-supplied: only talk HTTPS to them (no plain-HTTP/SSRF to internal hosts).
    if (endpoint.protocol !== 'https:') throw new Error('push endpoint must use https');
    const body = await encryptPushPayload(enc.encode(json), sub.p256dh, sub.auth);
    const jwt = await createVapidJwt(await this.key(), endpoint.origin, this.opts.vapid.subject);
    return {
      url: endpoint.href,
      init: {
        method: 'POST',
        headers: {
          'content-type': 'application/octet-stream',
          'content-encoding': 'aes128gcm',
          ttl: String(this.opts.ttlSeconds ?? 3600),
          urgency: this.opts.urgency ?? 'normal',
          authorization: vapidAuthorization(jwt, this.opts.vapid.publicKey),
        },
        body,
      },
    };
  }

  async send(sub: PushSub, json: string): Promise<void> {
    const { url, init } = await this.buildRequest(sub, json);
    const f = this.opts.fetchImpl ?? fetch;
    const res = await fetchWithTimeout(f, url, init, this.opts.timeoutMs ?? DEFAULT_HTTP_TIMEOUT_MS, 'push service');
    if (!res.ok) {
      const text = await res.text().catch(() => '');
      throw new PushServiceError(`push service rejected the message${text ? `: ${text.slice(0, 200)}` : ''}`, res.status);
    }
    await res.body?.cancel().catch(() => undefined);
  }
}
