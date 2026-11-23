/**
 * Web Push RECEIVER on Mozilla's public push service (autopush), so delivery is proven end to
 * end: our dispatcher → VAPID-signed request → push service → this WebSocket → decrypted payload.
 *
 * Protocol (https://mozilla-push-service.readthedocs.io/en/latest/design/#webpush-protocol):
 *   connect wss://push.services.mozilla.com/  (subprotocol "push-notification"; retried without it,
 *                                               because the server no longer echoes it back)
 *   → {"messageType":"hello","use_webpush":true}                 ← {"messageType":"hello","uaid":…,"status":200}
 *   → {"messageType":"register","channelID":<uuid>,"key":<VAPID public key>}
 *                                                                ← {"messageType":"register","status":200,"pushEndpoint":…}
 *   ← {"messageType":"notification","channelID":…,"version":…,"data":<base64url>,"headers":{"encoding":"aes128gcm"}}
 *   → {"messageType":"ack","updates":[{"channelID":…,"version":…,"code":100}]}
 *   keepalive: "{}" every 30 s (the server answers "{}").
 *
 * The subscription key pair (P-256) and the 16-byte auth secret are generated locally; the push
 * service never sees them, so a payload we can decrypt was encrypted for this subscription only.
 */
import { createECDH, randomBytes, randomUUID, type ECDH } from 'node:crypto';
import ece from 'http_ece';

export const MOZILLA_AUTOPUSH_URL = 'wss://push.services.mozilla.com/';

export interface PushSubscriptionKeys {
  endpoint: string;
  p256dh: string;
  auth: string;
}

export interface ReceivedPush {
  receivedAt: string;
  channelID: string;
  version: string;
  encoding: string | null;
  /** Decrypted body (UTF-8). Empty string when the push carried no payload. */
  text: string;
  /** Parsed JSON body when the payload is JSON, else null. */
  json: unknown;
}

interface AutopushMessage {
  messageType?: string;
  status?: number;
  uaid?: string;
  channelID?: string;
  pushEndpoint?: string;
  version?: string;
  data?: string;
  headers?: Record<string, string>;
}

function parseHeaderParams(value: string | undefined): Record<string, string> {
  const out: Record<string, string> = {};
  for (const part of (value ?? '').split(/[;,]/)) {
    const [k, ...rest] = part.trim().split('=');
    if (k && rest.length) out[k.trim()] = rest.join('=').trim().replace(/^"|"$/g, '');
  }
  return out;
}

export class AutopushReceiver implements PushSubscriptionKeys {
  readonly endpoint: string;
  readonly p256dh: string;
  readonly auth: string;
  readonly channelID: string;

  private readonly ws: WebSocket;
  private readonly ecdh: ECDH;
  private readonly authSecret: Buffer;
  private readonly received: ReceivedPush[] = [];
  private readonly waiters: Array<(p: ReceivedPush) => void> = [];
  private readonly keepalive: ReturnType<typeof setInterval>;
  private closed = false;
  readonly errors: string[] = [];

  private constructor(ws: WebSocket, endpoint: string, channelID: string, ecdh: ECDH, authSecret: Buffer) {
    this.ws = ws;
    this.endpoint = endpoint;
    this.channelID = channelID;
    this.ecdh = ecdh;
    this.authSecret = authSecret;
    this.p256dh = ecdh.getPublicKey().toString('base64url');
    this.auth = authSecret.toString('base64url');
    this.keepalive = setInterval(() => {
      if (this.ws.readyState === WebSocket.OPEN) this.ws.send('{}');
    }, 30_000);
    this.keepalive.unref?.();
    ws.addEventListener('message', (ev) => this.onMessage(ev));
    ws.addEventListener('close', () => {
      this.closed = true;
    });
  }

  /** Open a socket, say hello and register one channel for the given VAPID application server key. */
  static async connect(vapidPublicKey: string, opts: { url?: string; timeoutMs?: number } = {}): Promise<AutopushReceiver> {
    const url = opts.url ?? MOZILLA_AUTOPUSH_URL;
    const timeoutMs = opts.timeoutMs ?? 20_000;
    const key = Buffer.from(vapidPublicKey, 'base64url');
    if (key.length !== 65 || key[0] !== 0x04) throw new Error('VAPID public key must be a base64url uncompressed P-256 point (65 bytes)');

    const ws = await AutopushReceiver.open(url, timeoutMs);
    const pending: AutopushMessage[] = [];
    let notify: (() => void) | null = null;
    const onMsg = (ev: MessageEvent) => {
      try {
        pending.push(JSON.parse(String(ev.data)) as AutopushMessage);
      } catch {
        /* ignore non-JSON frames */
      }
      notify?.();
    };
    ws.addEventListener('message', onMsg);

    const deadline = Date.now() + timeoutMs;
    const next = async (type: string): Promise<AutopushMessage> => {
      for (;;) {
        const i = pending.findIndex((m) => m.messageType === type);
        if (i >= 0) return pending.splice(i, 1)[0]!;
        const left = deadline - Date.now();
        if (left <= 0) throw new Error(`autopush: timed out waiting for "${type}"`);
        await new Promise<void>((resolve) => {
          const t = setTimeout(resolve, left);
          notify = () => {
            clearTimeout(t);
            resolve();
          };
        });
        notify = null;
      }
    };

    try {
      ws.send(JSON.stringify({ messageType: 'hello', use_webpush: true }));
      const hello = await next('hello');
      if (hello.status !== 200 || !hello.uaid) throw new Error(`autopush: hello failed (status ${hello.status ?? '?'})`);

      const channelID = randomUUID();
      ws.send(JSON.stringify({ messageType: 'register', channelID, key: vapidPublicKey }));
      const reg = await next('register');
      if (reg.status !== 200 || !reg.pushEndpoint) throw new Error(`autopush: register failed (status ${reg.status ?? '?'})`);

      ws.removeEventListener('message', onMsg);
      const ecdh = createECDH('prime256v1');
      ecdh.generateKeys();
      const receiver = new AutopushReceiver(ws, reg.pushEndpoint, channelID, ecdh, randomBytes(16));
      // Anything that arrived between register and construction (unlikely) is replayed.
      for (const m of pending) receiver.handle(m);
      return receiver;
    } catch (err) {
      try {
        ws.close();
      } catch {
        /* ignore */
      }
      throw err;
    }
  }

  /**
   * Open the socket. The protocol names the "push-notification" subprotocol, but the current
   * autopush server does not echo it back and a WHATWG WebSocket then fails the handshake, so a
   * second attempt without a subprotocol follows.
   */
  private static async open(url: string, timeoutMs: number): Promise<WebSocket> {
    const attempt = (protocols: string[]) =>
      new Promise<WebSocket>((resolve, reject) => {
        const ws = protocols.length ? new WebSocket(url, protocols) : new WebSocket(url);
        const t = setTimeout(() => {
          try {
            ws.close();
          } catch {
            /* ignore */
          }
          reject(new Error('autopush: WebSocket connect timed out'));
        }, timeoutMs);
        ws.addEventListener('open', () => {
          clearTimeout(t);
          resolve(ws);
        }, { once: true });
        ws.addEventListener('error', () => {
          clearTimeout(t);
          reject(new Error(`autopush: WebSocket handshake failed${protocols.length ? ` (subprotocol ${protocols.join(',')})` : ''}`));
        }, { once: true });
      });
    try {
      return await attempt(['push-notification']);
    } catch {
      return attempt([]);
    }
  }

  get subscription(): PushSubscriptionKeys {
    return { endpoint: this.endpoint, p256dh: this.p256dh, auth: this.auth };
  }

  private onMessage(ev: MessageEvent): void {
    let msg: AutopushMessage;
    try {
      msg = JSON.parse(String(ev.data)) as AutopushMessage;
    } catch {
      return;
    }
    this.handle(msg);
  }

  private handle(msg: AutopushMessage): void {
    if (msg.messageType !== 'notification') return;
    const channelID = msg.channelID ?? '';
    const version = msg.version ?? '';
    let push: ReceivedPush | null = null;
    let code = 100;
    try {
      push = this.decrypt(msg);
    } catch (err) {
      code = 101;
      this.errors.push(`decrypt failed: ${(err as Error).message}`);
    }
    try {
      this.ws.send(JSON.stringify({ messageType: 'ack', updates: [{ channelID, version, code }] }));
    } catch {
      /* socket closing */
    }
    if (!push) return;
    const waiter = this.waiters.shift();
    if (waiter) waiter(push);
    else this.received.push(push);
  }

  private decrypt(msg: AutopushMessage): ReceivedPush {
    const headers = Object.fromEntries(Object.entries(msg.headers ?? {}).map(([k, v]) => [k.toLowerCase(), v]));
    const encoding = headers['encoding'] ?? (msg.data ? 'aes128gcm' : null);
    let text = '';
    if (msg.data) {
      const body = Buffer.from(msg.data, 'base64url');
      if (encoding === 'aesgcm') {
        // Legacy draft: salt in Encryption, sender key in Crypto-Key (dh=…).
        const salt = parseHeaderParams(headers['encryption'])['salt'];
        const dh = parseHeaderParams(headers['crypto_key'] ?? headers['crypto-key'])['dh'];
        if (!salt || !dh) throw new Error('aesgcm push without salt/dh headers');
        text = ece.decrypt(body, { version: 'aesgcm', privateKey: this.ecdh, dh, salt, authSecret: this.authSecret }).toString('utf8');
      } else {
        text = ece.decrypt(body, { version: 'aes128gcm', privateKey: this.ecdh, authSecret: this.authSecret }).toString('utf8');
      }
    }
    let json: unknown = null;
    try {
      json = text ? JSON.parse(text) : null;
    } catch {
      json = null;
    }
    return { receivedAt: new Date().toISOString(), channelID: msg.channelID ?? '', version: msg.version ?? '', encoding, text, json };
  }

  /** Resolves with the next (or an already buffered) notification; rejects after timeoutMs. */
  waitForNotification(timeoutMs: number): Promise<ReceivedPush> {
    const buffered = this.received.shift();
    if (buffered) return Promise.resolve(buffered);
    if (this.closed) return Promise.reject(new Error('autopush: socket is closed'));
    return new Promise<ReceivedPush>((resolve, reject) => {
      const waiter = (p: ReceivedPush) => {
        clearTimeout(timer);
        resolve(p);
      };
      const timer = setTimeout(() => {
        const i = this.waiters.indexOf(waiter);
        if (i >= 0) this.waiters.splice(i, 1);
        const why = this.errors.length ? ` (${this.errors.join('; ')})` : '';
        reject(new Error(`autopush: no notification within ${Math.round(timeoutMs / 1000)} s${why}`));
      }, timeoutMs);
      this.waiters.push(waiter);
    });
  }

  close(): void {
    clearInterval(this.keepalive);
    if (this.closed) return;
    this.closed = true;
    try {
      // Best effort: drop the channel so the endpoint stops accepting messages.
      if (this.ws.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify({ messageType: 'unregister', channelID: this.channelID }));
      this.ws.close();
    } catch {
      /* ignore */
    }
  }
}
