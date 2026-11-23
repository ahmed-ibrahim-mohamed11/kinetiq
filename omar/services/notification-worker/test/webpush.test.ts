/**
 * Interoperability proof for the WebCrypto Web Push implementation used by the
 * notification-dispatch Edge Function (supabase/functions/_shared/notify/webpush.ts):
 *  - payloads it encrypts decrypt with the reference RFC 8188/8291 implementation
 *    (http_ece, the library npm web-push uses), and are byte-identical to http_ece's
 *    own output for the same salt and ephemeral key;
 *  - VAPID JWTs it signs verify as ES256 against the public key from `web-push generate-vapid-keys`.
 */
import { createECDH, randomBytes } from 'node:crypto';
import { createRequire } from 'node:module';
import webpush from 'web-push';
import { describe, expect, it } from 'vitest';
import { b64urlDecode, b64urlEncode } from '../../../supabase/functions/_shared/notify/base64url.ts';
import {
  MAX_PAYLOAD_BYTES,
  MAX_VAPID_EXPIRY_SECONDS,
  PushServiceError,
  WebPushSender,
  createVapidJwt,
  encryptPushPayload,
  importVapidSigningKey,
} from '../../../supabase/functions/_shared/notify/webpush.ts';
import { processJob, type Db, type PushSub } from '../src/worker';

const require = createRequire(import.meta.url);
const ece = require('http_ece') as {
  decrypt(buffer: Buffer, params: Record<string, unknown>): Buffer;
  encrypt(buffer: Buffer, params: Record<string, unknown>): Buffer;
};

const enc = new TextEncoder();

function subscriptionKeys() {
  const ua = createECDH('prime256v1');
  ua.generateKeys();
  return { ua, p256dh: ua.getPublicKey().toString('base64url'), auth: randomBytes(16).toString('base64url') };
}

describe('RFC 8291 aes128gcm encryption (WebCrypto) vs http_ece', () => {
  it('decrypts with the reference implementation', async () => {
    const { ua, p256dh, auth } = subscriptionKeys();
    const message = JSON.stringify({ title: 'Your workout report is ready', body: 'Omar, today’s session summary is available.', url: 'https://m.example/reports/1' });
    const body = await encryptPushPayload(enc.encode(message), p256dh, auth);

    // Header: salt(16) || rs(4096) || idlen(65) || keyid (ephemeral uncompressed P-256 point).
    const view = new DataView(body.buffer);
    expect(view.getUint32(16)).toBe(4096);
    expect(body[20]).toBe(65);
    expect(body[21]).toBe(0x04);
    expect(body.length).toBe(86 + enc.encode(message).length + 1 + 16);

    const plain = ece.decrypt(Buffer.from(body), { version: 'aes128gcm', privateKey: ua, authSecret: auth });
    expect(plain.toString('utf8')).toBe(message);
  });

  it('is byte-identical to http_ece for the same salt and ephemeral key', async () => {
    const { p256dh, auth } = subscriptionKeys();
    const server = createECDH('prime256v1');
    server.generateKeys();
    const pub = server.getPublicKey();
    const jwk = { kty: 'EC', crv: 'P-256', x: b64urlEncode(pub.subarray(1, 33)), y: b64urlEncode(pub.subarray(33, 65)) };
    const serverKeys: CryptoKeyPair = {
      privateKey: await crypto.subtle.importKey('jwk', { ...jwk, d: b64urlEncode(server.getPrivateKey()) }, { name: 'ECDH', namedCurve: 'P-256' }, false, ['deriveBits']),
      publicKey: await crypto.subtle.importKey('jwk', jwk, { name: 'ECDH', namedCurve: 'P-256' }, true, []),
    };
    const salt = randomBytes(16);
    const payload = Buffer.from('{"title":"t","body":"b"}');

    const ours = await encryptPushPayload(payload, p256dh, auth, { salt, serverKeys });
    const reference = ece.encrypt(payload, { version: 'aes128gcm', dh: p256dh, privateKey: server, salt: salt.toString('base64url'), authSecret: auth });
    expect(Buffer.from(ours).equals(reference)).toBe(true);
  });

  it('round-trips the largest payload push services accept and rejects bigger ones', async () => {
    const { ua, p256dh, auth } = subscriptionKeys();
    const big = Buffer.alloc(MAX_PAYLOAD_BYTES, 0x61);
    const body = await encryptPushPayload(big, p256dh, auth);
    expect(body.length).toBe(4096);
    expect(ece.decrypt(Buffer.from(body), { version: 'aes128gcm', privateKey: ua, authSecret: auth }).equals(big)).toBe(true);
    await expect(encryptPushPayload(Buffer.alloc(MAX_PAYLOAD_BYTES + 1), p256dh, auth)).rejects.toThrow(/too large/);
  });

  it('rejects malformed subscription keys', async () => {
    const { auth } = subscriptionKeys();
    await expect(encryptPushPayload(enc.encode('x'), b64urlEncode(new Uint8Array(33)), auth)).rejects.toThrow(/p256dh/);
    const { p256dh } = subscriptionKeys();
    await expect(encryptPushPayload(enc.encode('x'), p256dh, b64urlEncode(new Uint8Array(8)))).rejects.toThrow(/auth secret/);
  });
});

describe('VAPID (RFC 8292) ES256 JWT', () => {
  it('signs a JWT that verifies against the web-push public key', async () => {
    const keys = webpush.generateVAPIDKeys();
    const signingKey = await importVapidSigningKey(keys.publicKey, keys.privateKey);
    const now = Date.UTC(2026, 10, 2, 12, 0, 0);
    const jwt = await createVapidJwt(signingKey, 'https://fcm.googleapis.com', 'mailto:ops@example.com', { now });

    const [h, c, s] = jwt.split('.') as [string, string, string];
    expect(JSON.parse(Buffer.from(h, 'base64url').toString())).toEqual({ typ: 'JWT', alg: 'ES256' });
    const claims = JSON.parse(Buffer.from(c, 'base64url').toString());
    expect(claims.aud).toBe('https://fcm.googleapis.com');
    expect(claims.sub).toBe('mailto:ops@example.com');
    expect(claims.exp - now / 1000).toBeLessThanOrEqual(MAX_VAPID_EXPIRY_SECONDS);
    expect(claims.exp).toBeGreaterThan(now / 1000);

    const verifyKey = await crypto.subtle.importKey('raw', b64urlDecode(keys.publicKey), { name: 'ECDSA', namedCurve: 'P-256' }, false, ['verify']);
    const sig = b64urlDecode(s);
    expect(sig.length).toBe(64);
    const ok = await crypto.subtle.verify({ name: 'ECDSA', hash: 'SHA-256' }, verifyKey, sig, enc.encode(`${h}.${c}`));
    expect(ok).toBe(true);

    // A different key must not verify it.
    const other = webpush.generateVAPIDKeys();
    const otherKey = await crypto.subtle.importKey('raw', b64urlDecode(other.publicKey), { name: 'ECDSA', namedCurve: 'P-256' }, false, ['verify']);
    expect(await crypto.subtle.verify({ name: 'ECDSA', hash: 'SHA-256' }, otherKey, sig, enc.encode(`${h}.${c}`))).toBe(false);
  });

  it('caps the expiry at 12 hours', async () => {
    const keys = webpush.generateVAPIDKeys();
    const key = await importVapidSigningKey(keys.publicKey, keys.privateKey);
    const now = Date.now();
    const jwt = await createVapidJwt(key, 'https://updates.push.services.mozilla.com', 'mailto:a@b.c', { now, expiresInSeconds: 48 * 3600 });
    const claims = JSON.parse(Buffer.from(jwt.split('.')[1]!, 'base64url').toString());
    expect(claims.exp).toBe(Math.floor(now / 1000) + 12 * 3600);
  });

  it('rejects malformed VAPID keys', async () => {
    const keys = webpush.generateVAPIDKeys();
    await expect(importVapidSigningKey(keys.publicKey, b64urlEncode(new Uint8Array(16)))).rejects.toThrow(/VAPID_PRIVATE_KEY/);
    await expect(importVapidSigningKey('abc', keys.privateKey)).rejects.toThrow(/VAPID_PUBLIC_KEY/);
    expect(() => new WebPushSender({ vapid: { ...keys, subject: 'ops@example.com' } })).toThrow(/VAPID_SUBJECT/);
  });
});

describe('WebPushSender over HTTP', () => {
  it('sends an encrypted, VAPID-authorized request the subscriber can decrypt', async () => {
    const keys = webpush.generateVAPIDKeys();
    const { ua, p256dh, auth } = subscriptionKeys();
    const calls: Array<{ url: string; init: RequestInit }> = [];
    const fetchImpl = (async (url: string, init: RequestInit) => {
      calls.push({ url, init });
      return new Response(null, { status: 201 });
    }) as unknown as typeof fetch;
    const sender = new WebPushSender({ vapid: { ...keys, subject: 'mailto:ops@example.com' }, fetchImpl });
    await sender.send({ id: 's1', endpoint: 'https://push.example.net/wpush/v2/abc', p256dh, auth }, '{"title":"hi"}');

    expect(calls).toHaveLength(1);
    const { url, init } = calls[0]!;
    expect(url).toBe('https://push.example.net/wpush/v2/abc');
    const headers = init.headers as Record<string, string>;
    expect(headers['content-encoding']).toBe('aes128gcm');
    expect(headers.ttl).toBe('3600');
    expect(headers.urgency).toBe('normal');
    const m = /^vapid t=([^,]+), k=(.+)$/.exec(headers.authorization!);
    expect(m?.[2]).toBe(keys.publicKey);
    const claims = JSON.parse(Buffer.from(m![1]!.split('.')[1]!, 'base64url').toString());
    expect(claims.aud).toBe('https://push.example.net');
    const plain = ece.decrypt(Buffer.from(init.body as Uint8Array), { version: 'aes128gcm', privateKey: ua, authSecret: auth });
    expect(plain.toString()).toBe('{"title":"hi"}');
  });

  it('maps 410 to PushServiceError so processJob disables the subscription', async () => {
    const keys = webpush.generateVAPIDKeys();
    const { p256dh, auth } = subscriptionKeys();
    const fetchImpl = (async () => new Response('subscription expired', { status: 410 })) as unknown as typeof fetch;
    const sender = new WebPushSender({ vapid: { ...keys, subject: 'https://gym.example' }, fetchImpl });
    const sub: PushSub = { id: 'gone', endpoint: 'https://push.example.net/x', p256dh, auth };
    await expect(sender.send(sub, '{}')).rejects.toBeInstanceOf(PushServiceError);
    await expect(sender.send(sub, '{}')).rejects.toMatchObject({ statusCode: 410 });

    const disabled: string[] = [];
    const db: Db = {
      async claim() { return []; },
      async complete() {},
      async memberName() { return 'Test Member'; },
      async pushSubscriptions() { return [sub]; },
      async disablePushSubscription(id) { disabled.push(id); },
    };
    const job = { id: 'j', member_id: 'm', channel: 'web_push' as const, template: 'workout_report_ready', attempts: 1, max_attempts: 6,
      payload: { report_id: '6f1d2c3e-4b5a-4c6d-8e7f-901234567801', attendance_session_id: '6f1d2c3e-4b5a-4c6d-8e7f-901234567802' } };
    const r = await processJob(job, { db, push: sender, memberAppUrl: 'https://m', workerId: 'w' });
    expect(r.ok).toBe(false);
    expect(r.error).toMatch(/push 410/);
    expect(disabled).toEqual(['gone']);
  });

  it('refuses non-https endpoints', async () => {
    const keys = webpush.generateVAPIDKeys();
    const { p256dh, auth } = subscriptionKeys();
    const sender = new WebPushSender({ vapid: { ...keys, subject: 'mailto:ops@example.com' }, fetchImpl: (async () => new Response(null)) as unknown as typeof fetch });
    await expect(sender.send({ id: 'x', endpoint: 'http://169.254.169.254/latest', p256dh, auth }, '{}')).rejects.toThrow(/https/);
  });
});
