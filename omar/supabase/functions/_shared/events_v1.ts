// VENDORED COPY of packages/api-contracts/src/events.ts for the Deno Edge Functions (which cannot import
// workspace packages). Do not edit by hand: copy the source file and change only the zod import below.
// tests/vendored_events.test.ts (Deno) and packages/api-contracts/test/vendored.test.ts fail on drift.
/**
 * events.v1 — the versioned contract between edge devices and the cloud.
 *
 * Every event carries:
 *  - event_id: UUID v4 generated on the edge. This is the idempotency key.
 *    Replaying the same event_id is a no-op server-side.
 *  - sequence: monotonic per device. Gaps are logged as potential data loss.
 *  - occurred_at: wall-clock time of the observation (edge NTP-synced).
 *  - schema_version: literal '1'. Breaking changes bump this and add a new file.
 *
 * The Python mirror is services/edge-ai/edge_ai/contracts/events_v1.py.
 * tests/contracts verifies both sides accept the same fixtures.
 */
import { z } from 'npm:zod@3.24.1';

export const EVENTS_SCHEMA_VERSION = '1' as const;

const uuid = z.string().uuid();
const isoDateTime = z.string().datetime({ offset: true });
const confidence = z.number().min(0).max(1);

// ────────────────────────────────────────────────────────────────────────────
// Payloads
// ────────────────────────────────────────────────────────────────────────────

export const DeviceHeartbeatPayload = z.object({
  software_version: z.string().min(1),
  uptime_seconds: z.number().int().nonnegative(),
  gpu_utilization: z.number().min(0).max(1).nullable(),
  gpu_memory_used_mb: z.number().nonnegative().nullable(),
  cpu_utilization: z.number().min(0).max(1).nullable(),
  queue_depth: z.number().int().nonnegative(),
  cameras_online: z.number().int().nonnegative(),
  cameras_total: z.number().int().nonnegative(),
  /** Measured, not estimated. */
  avg_pipeline_latency_ms: z.number().nonnegative().nullable(),
});

export const CameraStatusPayload = z.object({
  camera_id: uuid,
  status: z.enum(['online', 'reconnecting', 'offline', 'degraded']),
  fps_measured: z.number().nonnegative().nullable(),
  resolution_w: z.number().int().positive().nullable(),
  resolution_h: z.number().int().positive().nullable(),
  error: z.string().max(500).nullable(),
});

export const CheckInMethod = z.enum(['face', 'qr', 'nfc', 'staff_manual']);

export const IdentityCheckInPayload = z.object({
  member_id: uuid,
  method: CheckInMethod,
  /** Face match confidence; 1.0 for QR/NFC/manual. */
  confidence: confidence,
  camera_id: uuid.nullable(),
  /** Edge-side global track bound at the entrance, if any. */
  global_track_id: z.string().min(1).nullable(),
  /** Reference to the consent record the edge verified before matching. */
  consent_record_id: uuid.nullable(),
  presentation_attack_score: z.number().min(0).max(1).nullable(),
});

export const IdentityCheckOutPayload = z.object({
  member_id: uuid,
  method: CheckInMethod,
  confidence: confidence,
  camera_id: uuid.nullable(),
});

export const TrackStartedPayload = z.object({
  global_track_id: z.string().min(1),
  /** Null when the person is not (yet) bound to a checked-in member. */
  member_id: uuid.nullable(),
  identity_confidence: confidence,
  tracking_confidence: confidence,
  first_camera_id: uuid,
});

export const TrackEndedPayload = z.object({
  global_track_id: z.string().min(1),
  reason: z.enum(['left_view', 'checkout', 'merged', 'timeout', 'ambiguous_identity']),
  last_camera_id: uuid.nullable(),
});

export const TrackingUncertainPayload = z.object({
  global_track_id: z.string().min(1),
  member_id: uuid.nullable(),
  from: isoDateTime,
  to: isoDateTime,
  reason: z.enum([
    'occlusion',
    'no_camera_coverage',
    'identity_ambiguous',
    'camera_offline',
    'low_tracking_confidence',
  ]),
});

export const EquipmentUsageStartedPayload = z.object({
  usage_local_id: z.string().min(1),
  global_track_id: z.string().min(1),
  member_id: uuid.nullable(),
  equipment_id: uuid,
  zone_id: uuid,
  camera_id: uuid,
  association_confidence: confidence,
});

export const EquipmentUsageEndedPayload = z.object({
  usage_local_id: z.string().min(1),
  reason: z.enum(['left_zone', 'track_lost', 'checkout', 'timeout']),
});

export const ExerciseActivityStartedPayload = z.object({
  activity_local_id: z.string().min(1),
  usage_local_id: z.string().min(1),
  exercise_id: uuid,
  classification_confidence: confidence,
  classifier_model_version_id: uuid.nullable(),
});

export const ExerciseActivityEndedPayload = z.object({
  activity_local_id: z.string().min(1),
  active_seconds: z.number().nonnegative(),
});

export const SetStartedPayload = z.object({
  set_local_id: z.string().min(1),
  activity_local_id: z.string().min(1),
  set_index: z.number().int().positive(),
  rest_before_seconds: z.number().nonnegative().nullable(),
});

export const RepCountedPayload = z.object({
  set_local_id: z.string().min(1),
  /** 1-based index within the set. */
  rep_index: z.number().int().positive(),
  camera_id: uuid,
  confidence: confidence,
  /** Reserved for Phase 9; MUST be null in v1 producers. */
  quality_label: z.null(),
});

export const SetCompletedPayload = z.object({
  set_local_id: z.string().min(1),
  rep_count: z.number().int().nonnegative(),
  confidence: confidence,
  /** False when any rep in the set was inferred during an uncertain segment. */
  verified: z.boolean(),
});

/**
 * Biometric lifecycle. The TEMPLATE never leaves the edge; these events carry
 * only the opaque reference so the cloud registry (biometric_enrollments) stays
 * truthful for audits and consent withdrawal.
 */
export const BiometricEnrolledPayload = z.object({
  member_id: uuid,
  consent_record_id: uuid,
  template_ref: z.string().min(8).max(64),
  key_id: z.string().min(4).max(64),
  model_name: z.string().min(1).max(100),
  /** Quality score of the enrollment image(s) in [0,1], null when the model does not report one. */
  quality: confidence.nullable(),
});

export const BiometricRevokedPayload = z.object({
  member_id: uuid,
  template_ref: z.string().min(8).max(64),
  reason: z.enum(['consent_withdrawn', 'member_request', 'staff_request', 'key_rotation', 'retired_device']),
});

// ────────────────────────────────────────────────────────────────────────────
// Discriminated union
// ────────────────────────────────────────────────────────────────────────────

const envelopeBase = {
  schema_version: z.literal(EVENTS_SCHEMA_VERSION),
  event_id: uuid,
  device_id: uuid,
  branch_id: uuid,
  sequence: z.number().int().nonnegative(),
  occurred_at: isoDateTime,
  sent_at: isoDateTime,
};

export const EdgeEvent = z.discriminatedUnion('event_type', [
  z.object({ ...envelopeBase, event_type: z.literal('device.heartbeat'), payload: DeviceHeartbeatPayload }),
  z.object({ ...envelopeBase, event_type: z.literal('camera.status'), payload: CameraStatusPayload }),
  z.object({ ...envelopeBase, event_type: z.literal('identity.checkin'), payload: IdentityCheckInPayload }),
  z.object({ ...envelopeBase, event_type: z.literal('identity.checkout'), payload: IdentityCheckOutPayload }),
  z.object({ ...envelopeBase, event_type: z.literal('track.started'), payload: TrackStartedPayload }),
  z.object({ ...envelopeBase, event_type: z.literal('track.ended'), payload: TrackEndedPayload }),
  z.object({ ...envelopeBase, event_type: z.literal('tracking.uncertain'), payload: TrackingUncertainPayload }),
  z.object({ ...envelopeBase, event_type: z.literal('equipment.usage.started'), payload: EquipmentUsageStartedPayload }),
  z.object({ ...envelopeBase, event_type: z.literal('equipment.usage.ended'), payload: EquipmentUsageEndedPayload }),
  z.object({ ...envelopeBase, event_type: z.literal('exercise.activity.started'), payload: ExerciseActivityStartedPayload }),
  z.object({ ...envelopeBase, event_type: z.literal('exercise.activity.ended'), payload: ExerciseActivityEndedPayload }),
  z.object({ ...envelopeBase, event_type: z.literal('set.started'), payload: SetStartedPayload }),
  z.object({ ...envelopeBase, event_type: z.literal('rep.counted'), payload: RepCountedPayload }),
  z.object({ ...envelopeBase, event_type: z.literal('set.completed'), payload: SetCompletedPayload }),
  z.object({ ...envelopeBase, event_type: z.literal('biometric.enrolled'), payload: BiometricEnrolledPayload }),
  z.object({ ...envelopeBase, event_type: z.literal('biometric.revoked'), payload: BiometricRevokedPayload }),
]);

export type EdgeEvent = z.infer<typeof EdgeEvent>;
export type EdgeEventType = EdgeEvent['event_type'];

export const EDGE_EVENT_TYPES = [
  'device.heartbeat',
  'camera.status',
  'identity.checkin',
  'identity.checkout',
  'track.started',
  'track.ended',
  'tracking.uncertain',
  'equipment.usage.started',
  'equipment.usage.ended',
  'exercise.activity.started',
  'exercise.activity.ended',
  'set.started',
  'rep.counted',
  'set.completed',
  'biometric.enrolled',
  'biometric.revoked',
] as const satisfies readonly EdgeEventType[];

// ────────────────────────────────────────────────────────────────────────────
// Batch envelope sent to /functions/v1/ingest-events
// ────────────────────────────────────────────────────────────────────────────

/**
 * Signature: HMAC-SHA256 over the canonical string
 *   `${device_id}\n${batch_id}\n${sent_at}\n${sha256(JSON.stringify(events))}`
 * using the device secret. Header: X-Device-Signature. Replay protection:
 * batch_id is unique per device and sent_at must be within clock skew.
 */
export const EventBatch = z.object({
  schema_version: z.literal(EVENTS_SCHEMA_VERSION),
  batch_id: uuid,
  device_id: uuid,
  sent_at: isoDateTime,
  events: z.array(EdgeEvent).min(1).max(500),
});
export type EventBatch = z.infer<typeof EventBatch>;

/** Machine-readable reason a single event was rejected (`reason` is a fixed human-readable sentence). */
export const IngestRejectionCode = z.enum(['branch_mismatch', 'no_consent', 'unknown_reference', 'invalid_value', 'invalid_state', 'internal']);
export type IngestRejectionCode = z.infer<typeof IngestRejectionCode>;

export const IngestResult = z.object({
  batch_id: uuid,
  accepted: z.number().int().nonnegative(),
  duplicates: z.number().int().nonnegative(),
  rejected: z.array(z.object({ event_id: uuid, reason: z.string(), code: IngestRejectionCode.optional() })),
  server_time: isoDateTime,
  /** True when the batch_id was already processed (the whole batch is an idempotent no-op). */
  replayed: z.boolean().optional(),
});
export type IngestResult = z.infer<typeof IngestResult>;

export function canonicalBatchString(batch: Pick<EventBatch, 'device_id' | 'batch_id' | 'sent_at'>, eventsSha256Hex: string): string {
  return `${batch.device_id}\n${batch.batch_id}\n${batch.sent_at}\n${eventsSha256Hex}`;
}
