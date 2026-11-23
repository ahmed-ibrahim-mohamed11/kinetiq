/**
 * Web Push on the Deno runtime (WebCrypto): an independent RFC 8291 receiver written with
 * WebCrypto decrypts what the sender produces, and VAPID JWTs verify as ES256.
 * (Byte-exact interoperability with the reference http_ece library is proven in
 * services/notification-worker/test/webpush.test.ts.)
 *
 *   cd supabase/functions
 *   deno test --config deno.json --allow-env --allow-read tests/notification_webpush.test.ts
 */
import assert from 'node:assert/strict';
import { b64urlDecode, b64urlEncode, concatBytes } from '../_shared/notify/base64url.ts';
import { ResendEmailSender } from '../_shared/notify/email.ts';
import { createVapidJwt, encryptPushPayload, importVapidSigningKey, PushServiceError, WebPushSender } from '../_shared/notify/webpush.ts';

const enc = new TextEncoder();
const dec = new TextDecoder();

async function hkdf(salt: Uint8Array<ArrayBuffer>, ikm: Uint8Array<ArrayBuffer>, info: Uint8Array<ArrayBuffer>, len: number) {
  const key = await crypto.subtle.importKey('raw', ikm, 'HKDF', false, ['deriveBits']);
  return new Uint8Array(await crypto.subtle.deriveBits({ name: 'HKDF', hash: 'SHA-256', salt, info }, key, len * 8));
}

/** A user agent: subscription key pair + auth secret, and the RFC 8291 receiver side. */
async function userAgent() {
  const keys = await crypto.subtle.generateKey({ name: 'ECDH', namedCurve: 'P-256' }, true, ['deriveBits']) as CryptoKeyPair;
  const uaPublic = new Uint8Array(await crypto.subtle.exportKey('raw', keys.publicKey));
  const auth = crypto.getRandomValues(new Uint8Array(16));
  return {
    p256dh: b64urlEncode(uaPublic),
    auth: b64urlEncode(auth),
    async decrypt(body: Uint8Array<ArrayBuffer>): Promise<string> {
      const salt = body.slice(0, 16);
      const rs = new DataView(body.buffer).getUint32(16);
      assert.equal(rs, 4096);
      const idlen = body[20]!;
      const asPublic = body.slice(21, 21 + idlen);
      const asKey = await crypto.subtle.importKey('raw', asPublic, { name: 'ECDH', namedCurve: 'P-256' }, false, []);
      const ecdh = new Uint8Array(await crypto.subtle.deriveBits({ name: 'ECDH', public: asKey }, keys.privateKey, 256));
      const ikm = await hkdf(auth, ecdh, concatBytes(enc.encode('WebPush: info\x00'), uaPublic, asPublic), 32);
      const cek = await hkdf(salt, ikm, enc.encode('Content-Encoding: aes128gcm\x00'), 16);
      const nonce = await hkdf(salt, ikm, enc.encode('Content-Encoding: nonce\x00'), 12);
      const aes = await crypto.subtle.importKey('raw', cek, 'AES-GCM', false, ['decrypt']);
      const record = new Uint8Array(await crypto.subtle.decrypt({ name: 'AES-GCM', iv: nonce }, aes, body.slice(21 + idlen)));
      let end = record.length - 1;
      while (end >= 0 && record[end] === 0) end--;
      assert.equal(record[end], 0x02, 'last record must end with the 0x02 delimiter');
      return dec.decode(record.slice(0, end));
    },
  };
}

/** VAPID key pair in the web-push base64url format (65-byte public point, 32-byte d). */
async function vapidKeys() {
  const kp = await crypto.subtle.generateKey({ name: 'ECDSA', namedCurve: 'P-256' }, true, ['sign', 'verify']) as CryptoKeyPair;
  const jwk = await crypto.subtle.exportKey('jwk', kp.privateKey);
  const publicKey = b64urlEncode(concatBytes(new Uint8Array([4]), b64urlDecode(jwk.x!), b64urlDecode(jwk.y!)));
  return { publicKey, privateKey: jwk.d!, verifyKey: kp.publicKey };
}

Deno.test('aes128gcm payload decrypts with an independent WebCrypto receiver', async () => {
  const ua = await userAgent();
  const message = JSON.stringify({ title: 'Your workout report is ready', body: 'عمر، ملخص جلستك جاهز' });
  const body = await encryptPushPayload(enc.encode(message), ua.p256dh, ua.auth);
  assert.equal(body.length, 86 + enc.encode(message).length + 17);
  assert.equal(await ua.decrypt(body), message);
  // Fresh salt and ephemeral key per message.
  const again = await encryptPushPayload(enc.encode(message), ua.p256dh, ua.auth);
  assert.notDeepEqual(again.slice(0, 86), body.slice(0, 86));
});

Deno.test('VAPID JWT verifies as ES256 and carries aud/exp/sub', async () => {
  const keys = await vapidKeys();
  const signingKey = await importVapidSigningKey(keys.publicKey, keys.privateKey);
  const now = Date.now();
  const jwt = await createVapidJwt(signingKey, 'https://web.push.apple.com', 'mailto:ops@example.com', { now });
  const [h, c, s] = jwt.split('.') as [string, string, string];
  const claims = JSON.parse(dec.decode(b64urlDecode(c)));
  assert.equal(claims.aud, 'https://web.push.apple.com');
  assert.equal(claims.sub, 'mailto:ops@example.com');
  assert.ok(claims.exp > now / 1000 && claims.exp - now / 1000 <= 12 * 3600);
  const pub = await crypto.subtle.importKey('raw', b64urlDecode(keys.publicKey), { name: 'ECDSA', namedCurve: 'P-256' }, false, ['verify']);
  assert.ok(await crypto.subtle.verify({ name: 'ECDSA', hash: 'SHA-256' }, pub, b64urlDecode(s), enc.encode(`${h}.${c}`)));
  assert.ok(await crypto.subtle.verify({ name: 'ECDSA', hash: 'SHA-256' }, keys.verifyKey, b64urlDecode(s), enc.encode(`${h}.${c}`)));
});

Deno.test('WebPushSender posts the RFC 8030 request and surfaces push-service status codes', async () => {
  const keys = await vapidKeys();
  const ua = await userAgent();
  let captured: { url: string; init: RequestInit } | undefined;
  let status = 201;
  const fetchImpl = ((url: string, init: RequestInit) => {
    captured = { url, init };
    return Promise.resolve(new Response(status === 201 ? null : 'gone', { status }));
  }) as unknown as typeof fetch;
  const sender = new WebPushSender({ vapid: { publicKey: keys.publicKey, privateKey: keys.privateKey, subject: 'mailto:ops@example.com' }, fetchImpl });
  const sub = { id: 's', endpoint: 'https://fcm.googleapis.com/fcm/send/abc', p256dh: ua.p256dh, auth: ua.auth };

  await sender.send(sub, '{"title":"t"}');
  const headers = captured!.init.headers as Record<string, string>;
  assert.equal(captured!.url, sub.endpoint);
  assert.equal(headers['content-encoding'], 'aes128gcm');
  assert.equal(headers.ttl, '3600');
  assert.equal(headers.urgency, 'normal');
  assert.match(headers.authorization!, new RegExp(`^vapid t=[\\w-]+\\.[\\w-]+\\.[\\w-]+, k=${keys.publicKey}$`));
  assert.equal(await ua.decrypt(captured!.init.body as Uint8Array<ArrayBuffer>), '{"title":"t"}');

  status = 410;
  await assert.rejects(sender.send(sub, '{}'), (err: unknown) => err instanceof PushServiceError && err.statusCode === 410);
});

/** fetch that never answers until its signal aborts (a hanging push endpoint / email provider). */
const hangingFetch = ((_url: string, init: RequestInit) =>
  new Promise<Response>((_resolve, reject) => {
    init.signal?.addEventListener('abort', () => reject(init.signal!.reason));
  })) as unknown as typeof fetch;

Deno.test('WebPushSender aborts a push service that does not answer within timeoutMs', async () => {
  const keys = await vapidKeys();
  const ua = await userAgent();
  const sender = new WebPushSender({
    vapid: { publicKey: keys.publicKey, privateKey: keys.privateKey, subject: 'mailto:ops@example.com' },
    fetchImpl: hangingFetch,
    timeoutMs: 50,
  });
  const started = Date.now();
  await assert.rejects(
    sender.send({ id: 's', endpoint: 'https://push.example/hang', p256dh: ua.p256dh, auth: ua.auth }, '{}'),
    /push service did not respond within 50 ms/,
  );
  assert.ok(Date.now() - started < 5_000);
});

Deno.test('ResendEmailSender aborts a provider that does not answer within timeoutMs', async () => {
  const sender = new ResendEmailSender({ memberEmail: () => Promise.resolve('member@example.test') }, {
    apiKey: 'k',
    from: 'Gym <r@example.test>',
    fetchImpl: hangingFetch,
    timeoutMs: 50,
  });
  await assert.rejects(sender.send('m1', 's', 'b', 'https://m.example/r'), /email provider did not respond within 50 ms/);
});

Deno.test('WebPushSender.ready() resolves for a valid key pair and rejects malformed key material', async () => {
  const keys = await vapidKeys();
  await new WebPushSender({ vapid: { publicKey: keys.publicKey, privateKey: keys.privateKey, subject: 'mailto:ops@example.com' } }).ready();
  const bad = new WebPushSender({ vapid: { publicKey: keys.publicKey, privateKey: b64urlEncode(new Uint8Array(16)), subject: 'mailto:ops@example.com' } });
  await assert.rejects(bad.ready(), /VAPID_PRIVATE_KEY/);
  const badPub = new WebPushSender({ vapid: { publicKey: 'abc', privateKey: keys.privateKey, subject: 'mailto:ops@example.com' } });
  await assert.rejects(badPub.ready(), /VAPID_PUBLIC_KEY/);
});
