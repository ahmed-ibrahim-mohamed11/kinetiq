/**
 * @smartgym/validation — small, dependency-light validators shared by the web
 * apps, Edge Functions and workers. Geometry helpers here are the reference
 * implementation; the Python edge mirrors them in edge_ai/geometry.py and
 * tests/contracts cross-checks fixtures.
 */
import { z } from 'zod';

export type Point = readonly [number, number];

/** Ray-casting point-in-polygon. Polygon must have >= 3 vertices. */
export function pointInPolygon(point: Point, polygon: readonly Point[]): boolean {
  if (polygon.length < 3) return false;
  const [x, y] = point;
  let inside = false;
  for (let i = 0, j = polygon.length - 1; i < polygon.length; j = i++) {
    const [xi, yi] = polygon[i]!;
    const [xj, yj] = polygon[j]!;
    const intersects = yi > y !== yj > y && x < ((xj - xi) * (y - yi)) / (yj - yi + Number.EPSILON) + xi;
    if (intersects) inside = !inside;
  }
  return inside;
}

/** Shoelace area; used to reject degenerate zone polygons. */
export function polygonArea(polygon: readonly Point[]): number {
  let area = 0;
  for (let i = 0, j = polygon.length - 1; i < polygon.length; j = i++) {
    const [xi, yi] = polygon[i]!;
    const [xj, yj] = polygon[j]!;
    area += xj * yi - xi * yj;
  }
  return Math.abs(area) / 2;
}

export const NonDegeneratePolygon = z
  .array(z.tuple([z.number(), z.number()]))
  .min(3)
  .refine((p) => polygonArea(p) > 1, 'Polygon area must exceed 1 square pixel');

/** E.164 with an Egypt-friendly default: accepts +20..., 010..., 011..., 012..., 015... */
export function normalizePhone(input: string, defaultCountry: 'EG' = 'EG'): string | null {
  const digits = input.replace(/[\s()-]/g, '');
  if (/^\+[1-9]\d{7,14}$/.test(digits)) return digits;
  if (defaultCountry === 'EG') {
    if (/^0(10|11|12|15)\d{8}$/.test(digits)) return `+2${digits}`;
    if (/^20(10|11|12|15)\d{8}$/.test(digits)) return `+${digits}`;
  }
  return null;
}

/** Member codes are printed on cards and encoded in QR; keep them unambiguous. */
export const MemberCode = z
  .string()
  .trim()
  .toUpperCase()
  .regex(/^[A-HJ-NP-Z2-9-]{4,32}$/, 'Member code must avoid ambiguous characters (0/O, 1/I)');

/** Slug for organizations, exercises etc. */
export const Slug = z.string().regex(/^[a-z0-9]+(?:-[a-z0-9]+)*$/, 'lowercase-kebab-case only').min(2).max(64);

/** IANA timezone sanity check without a full tz database. */
export function isValidTimeZone(tz: string): boolean {
  try {
    new Intl.DateTimeFormat('en-US', { timeZone: tz });
    return true;
  } catch {
    return false;
  }
}

/** Clamp a confidence to [0,1] and round to 4 dp for storage. */
export function normalizeConfidence(value: number): number {
  if (Number.isNaN(value)) return 0;
  return Math.round(Math.min(1, Math.max(0, value)) * 10_000) / 10_000;
}

const SAFE_NEXT_BASE = 'http://safe-next.invalid';

/**
 * Post-login redirect target (`?next=`). Returns a same-origin path (with query
 * and hash) or `fallback`. Rejects everything a browser could resolve to another
 * origin: absolute URLs, protocol-relative `//host`, backslashes (`/\host` is
 * `//host` to the WHATWG URL parser), control characters (tab/newline are
 * stripped by the parser, so `/<tab>/host` is `//host`), and paths that
 * normalise to `//host` (`/.//host`).
 */
export function safeNextPath(next: string | null | undefined, fallback = '/dashboard'): string {
  if (typeof next !== 'string' || next.length === 0 || next.length > 2048) return fallback;
  if (!next.startsWith('/') || next.startsWith('//')) return fallback;
  // eslint-disable-next-line no-control-regex
  if (next.includes('\\') || /[\u0000-\u001f\u007f]/.test(next)) return fallback;
  let url: URL;
  try {
    url = new URL(next, SAFE_NEXT_BASE);
  } catch {
    return fallback;
  }
  if (url.origin !== SAFE_NEXT_BASE) return fallback;
  const path = `${url.pathname}${url.search}${url.hash}`;
  if (!path.startsWith('/') || path.startsWith('//')) return fallback;
  return path;
}

/**
 * Validate a QR check-in token: `SGM1.<member_code>.<unix_minute>.<hmac12>`.
 * Verification itself needs the secret and lives server-side; this only
 * parses the shape so the UI can reject garbage before a network call.
 */
export const QrCheckInToken = z.string().regex(/^SGM1\.[A-HJ-NP-Z2-9-]{4,32}\.\d{7,10}\.[A-Za-z0-9_-]{12,}$/);

export function parseQrCheckInToken(token: string): { memberCode: string; minute: number; mac: string } | null {
  if (!QrCheckInToken.safeParse(token).success) return null;
  const [, memberCode, minute, mac] = token.split('.') as [string, string, string, string];
  return { memberCode, minute: Number(minute), mac };
}
