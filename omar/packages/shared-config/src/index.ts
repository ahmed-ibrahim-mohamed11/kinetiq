/**
 * @smartgym/shared-config — typed environment loading.
 *
 * Two separate schemas enforce the browser/server boundary:
 *  - PublicEnv: only NEXT_PUBLIC_* values. Safe to ship to the client.
 *  - ServerEnv: service-role and worker secrets. Importing `loadServerEnv`
 *    from a client component is blocked by the `server-only` guard in apps.
 */
import { z } from 'zod';

export const PublicEnvSchema = z.object({
  NEXT_PUBLIC_SUPABASE_URL: z.string().url(),
  NEXT_PUBLIC_SUPABASE_ANON_KEY: z.string().min(20),
  NEXT_PUBLIC_VAPID_PUBLIC_KEY: z.string().optional().default(''),
  NEXT_PUBLIC_APP_ENV: z.enum(['development', 'preview', 'production']).default('development'),
});
export type PublicEnv = z.infer<typeof PublicEnvSchema>;

export const ServerEnvSchema = z.object({
  SUPABASE_URL: z.string().url(),
  SUPABASE_SERVICE_ROLE_KEY: z.string().min(20),
  SUPABASE_DB_URL: z.string().url().optional(),
  VAPID_PUBLIC_KEY: z.string().optional(),
  VAPID_PRIVATE_KEY: z.string().optional(),
  VAPID_SUBJECT: z.string().regex(/^(mailto:|https:)/).optional(),
  WORKER_POLL_INTERVAL_MS: z.coerce.number().int().min(250).max(60_000).default(2000),
  WORKER_MAX_ATTEMPTS: z.coerce.number().int().min(1).max(20).default(6),
});
export type ServerEnv = z.infer<typeof ServerEnvSchema>;

export class ConfigError extends Error {
  constructor(public readonly issues: string[]) {
    super(`Invalid configuration:\n${issues.map((i) => `  - ${i}`).join('\n')}`);
    this.name = 'ConfigError';
  }
}

function formatIssues(error: z.ZodError): string[] {
  return error.issues.map((i) => `${i.path.join('.') || '(root)'}: ${i.message}`);
}

export function loadPublicEnv(source: Record<string, string | undefined> = process.env): PublicEnv {
  const parsed = PublicEnvSchema.safeParse(source);
  if (!parsed.success) throw new ConfigError(formatIssues(parsed.error));
  return parsed.data;
}

export function loadServerEnv(source: Record<string, string | undefined> = process.env): ServerEnv {
  const parsed = ServerEnvSchema.safeParse(source);
  if (!parsed.success) throw new ConfigError(formatIssues(parsed.error));
  // Defensive: a service-role key must never be present under a NEXT_PUBLIC_ name.
  for (const key of Object.keys(source)) {
    if (key.startsWith('NEXT_PUBLIC_') && /SERVICE_ROLE/i.test(key)) {
      throw new ConfigError([`${key}: service-role credentials must never be public`]);
    }
  }
  return parsed.data;
}

/** Project-wide constants that both TS and Python read (Python copy: edge_ai/constants.py). */
export const CONSTANTS = {
  EVENTS_SCHEMA_VERSION: '1',
  /** Seconds of no movement before an active set is closed. */
  SET_END_IDLE_SECONDS: 4,
  /** Minimum seconds between sets to count as a rest interval (shorter = same set). */
  MIN_REST_SECONDS: 4,
  /** Two rep events from different cameras within this window for the same identity+equipment are duplicates. */
  REP_DEDUP_WINDOW_MS: 700,
  /** Below this identity confidence, a track is reported but never bound to a member. */
  IDENTITY_BIND_THRESHOLD: 0.85,
  /** Below this tracking confidence, reps are counted but flagged unverified. */
  TRACKING_VERIFIED_THRESHOLD: 0.6,
  /** Attendance sessions without checkout are auto-abandoned after this many hours. */
  SESSION_ABANDON_HOURS: 6,
  /** Report share links expire after this many days. */
  REPORT_LINK_TTL_DAYS: 30,
  /** Raw video is never stored by this platform; derived events retained this long by default. */
  EVENT_RETENTION_DAYS: 365,
  /** Central Camera Broker & Face Recognition Configuration */
  FACE_MATCH_THRESHOLD: 0.70,
  FACE_DETECTION_INTERVAL_MS: 250,
  RECOGNITION_COOLDOWN_MS: 5000,
  MAX_FRAME_QUEUE_SIZE: 5,
  CAMERA_RECONNECT_MAX_ATTEMPTS: 5,
  CONSUMER_LEASE_TIMEOUT_SECONDS: 30,
  CAMERA_HEARTBEAT_TIMEOUT_SECONDS: 30,
} as const;
