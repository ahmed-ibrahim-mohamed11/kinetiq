// Minimal typings for http_ece 1.2.0 (CommonJS, ships no types). Only what lib/autopush.ts uses.
declare module 'http_ece' {
  import type { ECDH } from 'node:crypto';

  export interface EceParams {
    version?: 'aes128gcm' | 'aesgcm';
    /** Receiver key pair (decrypt) or sender key pair (encrypt). */
    privateKey?: ECDH;
    /** Sender public key for 'aesgcm' (base64url string or raw bytes). */
    dh?: string | Buffer;
    /** Web Push auth secret (base64url string or raw 16 bytes). */
    authSecret?: string | Buffer;
    /** 'aesgcm' only: salt from the Encryption header. */
    salt?: string | Buffer;
    rs?: number | string;
    keyid?: string | Buffer;
    key?: string | Buffer;
    pad?: number;
  }

  const ece: {
    decrypt(buffer: Buffer, params: EceParams): Buffer;
    encrypt(buffer: Buffer, params: EceParams): Buffer;
  };
  export default ece;
}
