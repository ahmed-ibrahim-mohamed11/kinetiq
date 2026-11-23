/**
 * @smartgym/shared-types
 *
 * Plain TypeScript domain types mirrored from the PostgreSQL schema in
 * supabase/migrations. These are *structural* types only; runtime validation
 * lives in @smartgym/api-contracts (Zod) and the Python edge service (Pydantic).
 *
 * Keep these in sync with the migrations — CI runs a drift check in
 * tests/security against the generated Supabase types.
 */

export type UUID = string;
/** ISO-8601 timestamp with timezone, e.g. 2026-10-27T10:15:30.123Z */
export type ISODateTime = string;

// ────────────────────────────────────────────────────────────────────────────
// Tenancy & people
// ────────────────────────────────────────────────────────────────────────────

export type StaffRole = 'owner' | 'admin' | 'manager' | 'trainer' | 'front_desk' | 'auditor';

export type MembershipStatus = 'active' | 'frozen' | 'expired' | 'cancelled';

export type ConsentType =
  | 'biometric_face_identification'
  | 'cctv_workout_tracking'
  | 'data_processing'
  | 'marketing';

export type ConsentStatus = 'granted' | 'withdrawn' | 'expired';

export interface Organization {
  id: UUID;
  name: string;
  slug: string;
  country_code: string;
  created_at: ISODateTime;
}

export interface Branch {
  id: UUID;
  organization_id: UUID;
  name: string;
  timezone: string;
  address: string | null;
  floor_plan: FloorPlan | null;
  created_at: ISODateTime;
}

export interface FloorPlan {
  /** Plan units are metres relative to the origin corner. */
  width_m: number;
  height_m: number;
  /** Optional background image stored in the private `floor-plans` bucket. */
  image_path: string | null;
}

export interface Member {
  id: UUID;
  organization_id: UUID;
  branch_id: UUID;
  /** auth.users.id — null until the member activates their account. */
  user_id: UUID | null;
  member_code: string;
  full_name: string;
  phone: string | null;
  email: string | null;
  status: MembershipStatus;
  created_at: ISODateTime;
}

export interface ConsentRecord {
  id: UUID;
  member_id: UUID;
  consent_type: ConsentType;
  status: ConsentStatus;
  policy_version: string;
  granted_at: ISODateTime;
  withdrawn_at: ISODateTime | null;
  captured_by_staff_id: UUID | null;
}

/**
 * Reference to a biometric template that lives ONLY on the edge device.
 * The cloud stores no template bytes — only an opaque reference, the device
 * that holds it, and the encryption key identifier for rotation audits.
 */
export interface BiometricEnrollment {
  id: UUID;
  member_id: UUID;
  edge_device_id: UUID;
  template_ref: string;
  key_id: string;
  model_version_id: UUID;
  enrolled_at: ISODateTime;
  revoked_at: ISODateTime | null;
}

// ────────────────────────────────────────────────────────────────────────────
// Devices, cameras, zones, equipment
// ────────────────────────────────────────────────────────────────────────────

export type DeviceStatus = 'provisioning' | 'active' | 'degraded' | 'offline' | 'retired';

export interface EdgeDevice {
  id: UUID;
  branch_id: UUID;
  name: string;
  status: DeviceStatus;
  /** Public half of the device key; the private half never leaves the device. */
  public_key: string | null;
  software_version: string | null;
  last_seen_at: ISODateTime | null;
}

export type CameraProtocol = 'rtsp' | 'onvif' | 'nvr_channel';
export type CameraPurpose = 'entrance' | 'floor' | 'exit' | 'mixed';

export interface Camera {
  id: UUID;
  branch_id: UUID;
  edge_device_id: UUID | null;
  name: string;
  protocol: CameraProtocol;
  purpose: CameraPurpose;
  /** URL WITHOUT credentials. Credentials live in the edge secrets file. */
  stream_url: string;
  resolution_w: number | null;
  resolution_h: number | null;
  fps: number | null;
  /** 3x3 homography (row-major) mapping image pixels → floor plan metres. */
  homography: number[] | null;
  enabled: boolean;
}

/** Polygon in image pixel coordinates for one camera. */
export interface CameraZone {
  id: UUID;
  camera_id: UUID;
  equipment_id: UUID | null;
  name: string;
  polygon: Array<[number, number]>;
  zone_type: 'equipment' | 'entrance' | 'exit' | 'exclusion' | 'free_weights';
}

export type EquipmentCategory =
  | 'machine'
  | 'cable'
  | 'free_weight_station'
  | 'cardio'
  | 'bodyweight_station';

export interface Equipment {
  id: UUID;
  branch_id: UUID;
  name: string;
  category: EquipmentCategory;
  /** Position on the floor plan in metres. */
  position_x_m: number | null;
  position_y_m: number | null;
  default_exercise_id: UUID | null;
  enabled: boolean;
}

export type MovementPattern =
  | 'push_horizontal'
  | 'push_vertical'
  | 'pull_horizontal'
  | 'pull_vertical'
  | 'squat'
  | 'hinge'
  | 'lunge'
  | 'curl'
  | 'extension'
  | 'core'
  | 'cardio_cyclic';

export interface Exercise {
  id: UUID;
  slug: string;
  name: string;
  movement_pattern: MovementPattern;
  /** Primary joint angle the rep state machine tracks (e.g. 'left_elbow'). */
  primary_signal: string;
  /** Reserved for Phase 9 form analysis; null in the current release. */
  form_rules: Record<string, unknown> | null;
}

export interface ModelVersion {
  id: UUID;
  component: 'detector' | 'tracker' | 'reid' | 'pose' | 'exercise_classifier' | 'face';
  name: string;
  version: string;
  runtime: 'onnxruntime' | 'tensorrt' | 'pytorch' | 'deepstream';
  license: string;
  checksum_sha256: string | null;
}

// ────────────────────────────────────────────────────────────────────────────
// Sessions, tracks, workouts
// ────────────────────────────────────────────────────────────────────────────

export type CheckInMethod = 'face' | 'qr' | 'nfc' | 'staff_manual';
export type SessionStatus = 'active' | 'finalizing' | 'completed' | 'abandoned';

export interface AttendanceSession {
  id: UUID;
  member_id: UUID;
  branch_id: UUID;
  check_in_at: ISODateTime;
  check_in_method: CheckInMethod;
  check_in_confidence: number | null;
  check_out_at: ISODateTime | null;
  check_out_method: CheckInMethod | null;
  status: SessionStatus;
}

export interface PersonTrack {
  id: UUID;
  attendance_session_id: UUID | null;
  edge_device_id: UUID;
  global_track_id: string;
  started_at: ISODateTime;
  ended_at: ISODateTime | null;
  identity_confidence: number;
  tracking_confidence: number;
}

export interface EquipmentUsageSession {
  id: UUID;
  attendance_session_id: UUID;
  equipment_id: UUID;
  started_at: ISODateTime;
  ended_at: ISODateTime | null;
  association_confidence: number;
}

export interface ExerciseActivitySession {
  id: UUID;
  equipment_usage_session_id: UUID;
  exercise_id: UUID;
  started_at: ISODateTime;
  ended_at: ISODateTime | null;
  classification_confidence: number;
  active_seconds: number;
}

export interface ExerciseSet {
  id: UUID;
  exercise_activity_session_id: UUID;
  set_index: number;
  started_at: ISODateTime;
  ended_at: ISODateTime | null;
  rep_count: number;
  rest_before_seconds: number | null;
  confidence: number;
  verified: boolean;
}

export interface RepetitionEvent {
  id: UUID;
  exercise_set_id: UUID;
  occurred_at: ISODateTime;
  camera_id: UUID;
  confidence: number;
  /** Reserved for Phase 9: null now; later 'correct' | 'incorrect' | 'uncertain'. */
  quality_label: string | null;
}

export interface WorkoutReport {
  id: UUID;
  attendance_session_id: UUID;
  member_id: UUID;
  generated_at: ISODateTime;
  summary: WorkoutReportSummary;
  /** Opaque, unguessable token for the mobile report link. */
  share_token: string;
  share_expires_at: ISODateTime;
}

export interface WorkoutReportSummary {
  schema_version: 1;
  check_in_at: ISODateTime;
  check_out_at: ISODateTime;
  visit_seconds: number;
  active_exercise_seconds: number;
  total_sets: number;
  total_reps: number;
  exercises: Array<{
    exercise_slug: string;
    exercise_name: string;
    equipment_name: string | null;
    equipment_seconds: number;
    active_seconds: number;
    sets: Array<{ set_index: number; reps: number; rest_before_seconds: number | null; confidence: number }>;
    total_reps: number;
  }>;
  /** Periods where tracking was lost or identity was uncertain. Never hidden. */
  uncertain_segments: Array<{ from: ISODateTime; to: ISODateTime; reason: string }>;
}

export type NotificationChannel = 'web_push' | 'email' | 'sms';
export type NotificationStatus = 'queued' | 'sending' | 'delivered' | 'failed' | 'dead';

export interface NotificationJob {
  id: UUID;
  member_id: UUID;
  channel: NotificationChannel;
  template: string;
  payload: Record<string, unknown>;
  status: NotificationStatus;
  attempts: number;
  next_attempt_at: ISODateTime;
  last_error: string | null;
}
