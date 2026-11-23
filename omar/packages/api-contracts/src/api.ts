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
  date_of_birth: z.string().regex(/^\d{4}-\d{2}-\d{2}$/).nullable().optional(),
  emergency_contact_name: z.string().max(100).nullable().optional(),
  emergency_contact_phone: z.string().regex(/^\+?[1-9]\d{7,14}$/).nullable().optional(),
  notes: z.string().max(500).nullable().optional(),
});
export type CreateMemberInput = z.infer<typeof CreateMemberInput>;

// ────────────────────────────────────────────────────────────────────────────
// Commercial Gym & Subscription Inputs
// ────────────────────────────────────────────────────────────────────────────

export const CreateMembershipPlanInput = z.object({
  organization_id: uuid,
  name: z.string().min(2).max(100),
  code: z.string().min(2).max(30).regex(/^[A-Z0-9_-]+$/, 'Uppercase letters, numbers, underscores and dashes'),
  description: z.string().max(500).nullable().optional(),
  price: z.number().nonnegative(),
  currency: z.string().min(3).max(4).default('EGP'),
  duration_days: z.number().int().positive(),
  included_sessions: z.number().int().positive().nullable().optional(),
  freeze_allowance_days: z.number().int().nonnegative().default(0),
  max_freeze_count: z.number().int().nonnegative().default(0),
  ai_training_included: z.boolean().default(false),
  ai_addon_fee: z.number().nonnegative().default(150),
  is_active: z.boolean().default(true),
});
export type CreateMembershipPlanInput = z.infer<typeof CreateMembershipPlanInput>;

export const CreatePromotionInput = z.object({
  organization_id: uuid,
  code: z.string().min(2).max(40).regex(/^[A-Z0-9_-]+$/),
  name: z.string().min(2).max(120),
  description: z.string().max(500).nullable().optional(),
  discount_type: z.enum(['percentage', 'fixed_amount', 'extra_days', 'ai_addon_free']),
  discount_value: z.number().nonnegative(),
  applies_to_plan_id: uuid.nullable().optional(),
  min_subscription_amount: z.number().nonnegative().nullable().optional(),
  max_discount_amount: z.number().nonnegative().nullable().optional(),
  usage_limit: z.number().int().positive().nullable().optional(),
  valid_from: z.string().datetime({ offset: true }).optional(),
  valid_until: z.string().datetime({ offset: true }).nullable().optional(),
  is_active: z.boolean().default(true),
});
export type CreatePromotionInput = z.infer<typeof CreatePromotionInput>;

export const RegisterNewMemberFullInput = z.object({
  branch_id: uuid,
  full_name: z.string().min(2).max(120),
  member_code: z.string().min(3).max(32).regex(/^[A-Z0-9-]+$/),
  phone: z.string().regex(/^\+?[1-9]\d{7,14}$/).nullable().optional(),
  email: z.string().email().nullable().optional(),
  date_of_birth: z.string().regex(/^\d{4}-\d{2}-\d{2}$/).nullable().optional(),
  emergency_contact_name: z.string().max(100).nullable().optional(),
  emergency_contact_phone: z.string().regex(/^\+?[1-9]\d{7,14}$/).nullable().optional(),
  notes: z.string().max(500).nullable().optional(),
  // Subscription selection
  plan_id: uuid,
  payment_method: z.enum(['cash', 'credit_card', 'bank_transfer', 'pos_terminal', 'online_gateway']).default('cash'),
  promo_code: z.string().max(40).nullable().optional(),
  ai_addon_selected: z.boolean().default(false),
  // Distinct Consents
  consent_biometric_face: z.boolean().default(false),
  consent_cctv_tracking: z.boolean().default(false),
  // Distinct Notification Preferences
  pref_workout_reports: z.boolean().default(true),
  pref_expiry_alerts: z.boolean().default(true),
  pref_promotions: z.boolean().default(false),
});
export type RegisterNewMemberFullInput = z.infer<typeof RegisterNewMemberFullInput>;

export const FreezeSubscriptionInput = z.object({
  subscription_id: uuid,
  days: z.number().int().min(1).max(90),
  reason: z.string().min(2).max(300),
});
export type FreezeSubscriptionInput = z.infer<typeof FreezeSubscriptionInput>;

export const UnfreezeSubscriptionInput = z.object({
  subscription_id: uuid,
});
export type UnfreezeSubscriptionInput = z.infer<typeof UnfreezeSubscriptionInput>;

export const RenewSubscriptionInput = z.object({
  member_id: uuid,
  plan_id: uuid,
  branch_id: uuid,
  payment_method: z.enum(['cash', 'credit_card', 'bank_transfer', 'pos_terminal', 'online_gateway']).default('cash'),
  ai_addon: z.boolean().default(false),
  promo_code: z.string().max(40).nullable().optional(),
});
export type RenewSubscriptionInput = z.infer<typeof RenewSubscriptionInput>;

export const RecordPaymentInput = z.object({
  branch_id: uuid,
  member_id: uuid,
  subscription_id: uuid.nullable().optional(),
  amount: z.number().positive(),
  currency: z.string().default('EGP'),
  method: z.enum(['cash', 'credit_card', 'bank_transfer', 'pos_terminal', 'online_gateway']),
  reference_number: z.string().max(100).nullable().optional(),
  notes: z.string().max(500).nullable().optional(),
});
export type RecordPaymentInput = z.infer<typeof RecordPaymentInput>;

export const UpdateNotificationPreferencesInput = z.object({
  member_id: uuid,
  workout_reports: z.boolean(),
  subscription_expiry_alerts: z.boolean(),
  promotional_offers: z.boolean(),
});
export type UpdateNotificationPreferencesInput = z.infer<typeof UpdateNotificationPreferencesInput>;

export const RecordConsentInput = z.object({
  member_id: uuid,
  consent_type: z.enum(['biometric_face_identification', 'cctv_workout_tracking', 'data_processing', 'marketing']),
  policy_version: z.string().min(1).max(32),
  /** Must be true — UI cannot submit consent without the explicit checkbox. */
  informed_acknowledged: z.literal(true),
});
export type RecordConsentInput = z.infer<typeof RecordConsentInput>;

/** member_id is optional and used only to refresh the member page after the action. */
export const WithdrawConsentInput = z.object({ consent_record_id: uuid, member_id: uuid.optional() });

/**
 * Staff check-in from the browser (public.staff_check_in, contract C2): only
 * 'staff_manual' and 'nfc'. QR check-ins go through the qr-token Edge Function
 * (public.qr_check_in) and face check-ins come from the edge only.
 */
export const StaffCheckInMethod = z.enum(['staff_manual', 'nfc']);
export const ManualCheckInInput = z.object({
  member_id: uuid,
  method: StaffCheckInMethod.default('staff_manual'),
});
export type ManualCheckInInput = z.infer<typeof ManualCheckInInput>;

export const ManualCheckOutInput = z.object({ attendance_session_id: uuid, member_id: uuid.optional() });

export const DeleteCameraZoneInput = z.object({ zone_id: uuid, camera_id: uuid });
export const IssueDeviceSecretInput = z.object({ device_id: uuid });
export const RevokeBiometricEnrollmentInput = z.object({ enrollment_id: uuid, member_id: uuid });

/** Member report sharing (public.create_report_share / revoke_report_share, contract C3). */
export const CreateReportShareInput = z.object({ report_id: uuid, days: z.number().int().min(1).max(30).default(7) });
export type CreateReportShareInput = z.infer<typeof CreateReportShareInput>;
export const RevokeReportShareInput = z.object({ report_id: uuid });
export const ReportShareResult = z.object({ token: z.string().regex(/^[0-9a-f]{48}$/), expires_at: z.string() });
export type ReportShareResult = z.infer<typeof ReportShareResult>;

/**
 * Web Push endpoints: https URLs of a known push service only. Members supply the
 * endpoint and the notification sender POSTs to it, so anything else would be SSRF.
 * Mirrors public.is_allowed_push_endpoint (migration 20261102000010): FCM, Mozilla
 * autopush, Apple and Windows (WNS), host followed directly by '/', at most 2048 chars.
 */
const PUSH_ENDPOINT_RE =
  /^https:\/\/(fcm\.googleapis\.com|android\.googleapis\.com|updates\.push\.services\.mozilla\.com|web\.push\.apple\.com|[a-z0-9-]+\.notify\.windows\.com)\/\S*$/i;
export function isAllowedPushEndpoint(endpoint: string): boolean {
  return endpoint.length <= 2048 && PUSH_ENDPOINT_RE.test(endpoint);
}
export const PushEndpoint = z
  .string()
  .url()
  .max(2048)
  .refine(isAllowedPushEndpoint, 'Push endpoint must be an https URL of a known Web Push service');

export const RegisterPushSubscriptionInput = z.object({
  endpoint: PushEndpoint,
  keys: z.object({ p256dh: z.string().min(1).max(200), auth: z.string().min(1).max(100) }),
  user_agent: z.string().max(300).nullable(),
});

export const ApiError = z.object({
  code: z.enum(['validation_error', 'unauthorized', 'forbidden', 'not_found', 'conflict', 'internal']),
  message: z.string(),
  details: z.unknown().optional(),
});
export type ApiError = z.infer<typeof ApiError>;
