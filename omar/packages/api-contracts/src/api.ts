/**
 * Request/response contracts for the web apps' server actions and route
 * handlers. All writes from the browser go through these schemas before
 * touching Supabase, and RLS enforces authorization a second time.
 */
import { z } from 'zod';

const uuid = z.string().uuid();

/** RTSP/HTTP URL that MUST NOT embed credentials (user:pass@host). */
export const StreamUrlWithoutCredentials = z
  .string()
  .url()
  .refine((u) => /^(rtsp|rtsps|http|https):\/\//i.test(u), 'Only rtsp(s):// or http(s):// URLs are allowed')
  .refine((u) => !/^[a-z]+:\/\/[^/@]+@/i.test(u), 'Do not embed credentials in the stream URL; store them in the edge secrets file');

export const CreateCameraInput = z.object({
  branch_id: uuid,
  edge_device_id: uuid.nullable(),
  name: z.string().min(1).max(100),
  protocol: z.enum(['rtsp', 'onvif', 'nvr_channel']),
  purpose: z.enum(['entrance', 'floor', 'exit', 'mixed']),
  stream_url: StreamUrlWithoutCredentials,
  resolution_w: z.number().int().positive().nullable(),
  resolution_h: z.number().int().positive().nullable(),
  fps: z.number().positive().max(120).nullable(),
  enabled: z.boolean().default(true),
});
export type CreateCameraInput = z.infer<typeof CreateCameraInput>;

export const UpdateCameraInput = CreateCameraInput.partial().extend({ id: uuid });

export const Point2D = z.tuple([z.number().nonnegative(), z.number().nonnegative()]);

export const CreateCameraZoneInput = z.object({
  camera_id: uuid,
  equipment_id: uuid.nullable(),
  name: z.string().min(1).max(100),
  zone_type: z.enum(['equipment', 'entrance', 'exit', 'exclusion', 'free_weights']),
  polygon: z.array(Point2D).min(3).max(64),
});
export type CreateCameraZoneInput = z.infer<typeof CreateCameraZoneInput>;

export const CreateEquipmentInput = z.object({
  branch_id: uuid,
  name: z.string().min(1).max(100),
  category: z.enum(['machine', 'cable', 'free_weight_station', 'cardio', 'bodyweight_station']),
  position_x_m: z.number().nonnegative().nullable(),
  position_y_m: z.number().nonnegative().nullable(),
  default_exercise_id: uuid.nullable(),
  enabled: z.boolean().default(true),
});
export type CreateEquipmentInput = z.infer<typeof CreateEquipmentInput>;

export const CreateMemberInput = z.object({
  branch_id: uuid,
  full_name: z.string().min(2).max(120),
  member_code: z.string().min(3).max(32).regex(/^[A-Z0-9-]+$/, 'Uppercase letters, digits and dashes only'),
  phone: z.string().regex(/^\+?[1-9]\d{7,14}$/, 'Use E.164 format, e.g. +201001234567').nullable(),
  email: z.string().email().nullable(),
});
export type CreateMemberInput = z.infer<typeof CreateMemberInput>;

export const RecordConsentInput = z.object({
  member_id: uuid,
  consent_type: z.enum(['biometric_face_identification', 'cctv_workout_tracking', 'data_processing', 'marketing']),
  policy_version: z.string().min(1).max(32),
  /** Must be true — UI cannot submit consent without the explicit checkbox. */
  informed_acknowledged: z.literal(true),
});
export type RecordConsentInput = z.infer<typeof RecordConsentInput>;

export const WithdrawConsentInput = z.object({ consent_record_id: uuid });

export const ManualCheckInInput = z.object({
  member_id: uuid,
  branch_id: uuid,
  method: z.enum(['qr', 'nfc', 'staff_manual']),
});

export const ManualCheckOutInput = z.object({ attendance_session_id: uuid });

export const RegisterPushSubscriptionInput = z.object({
  endpoint: z.string().url(),
  keys: z.object({ p256dh: z.string().min(1), auth: z.string().min(1) }),
  user_agent: z.string().max(300).nullable(),
});

export const ApiError = z.object({
  code: z.enum(['validation_error', 'unauthorized', 'forbidden', 'not_found', 'conflict', 'internal']),
  message: z.string(),
  details: z.unknown().optional(),
});
export type ApiError = z.infer<typeof ApiError>;
