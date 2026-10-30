/**
 * Public type surface of @smartgym/supabase-client/types.
 *
 * `database.ts` is GENERATED from the migrations (npm run gen:types -w tests/db);
 * this file adds the hand-written conveniences the apps import, derived from the
 * generated enums so they can never drift from the schema.
 */
export type { Database, Json, Tables, Enums, TablesInsert, TablesUpdate, CompositeTypes } from './database';
import type { Database } from './database';

type E = Database['public']['Enums'];

export type StaffRole = E['staff_role'];
export type MembershipStatus = E['membership_status'];
export type ConsentType = E['consent_type'];
export type ConsentStatus = E['consent_status'];
export type DeviceStatus = E['device_status'];
export type CameraProtocol = E['camera_protocol'];
export type CameraPurpose = E['camera_purpose'];
export type ZoneType = E['zone_type'];
export type EquipmentCategory = E['equipment_category'];
export type MovementPattern = E['movement_pattern'];
export type ModelComponent = E['model_component'];
export type ModelRuntime = E['model_runtime'];
export type CheckinMethod = E['checkin_method'];
export type SessionStatus = E['session_status'];
export type NotificationChannel = E['notification_channel'];
export type NotificationStatus = E['notification_status'];
export type IngestStatus = E['ingest_status'];

type T = Database['public']['Tables'];

export type OrganizationRow = T['organizations']['Row'];
export type BranchRow = T['branches']['Row'];
export type ProfileRow = T['profiles']['Row'];
export type StaffRoleRow = T['staff_roles']['Row'];
export type MemberRow = T['members']['Row'];
export type ConsentRecordRow = T['consent_records']['Row'];
export type EdgeDeviceRow = T['edge_devices']['Row'];
export type ModelVersionRow = T['model_versions']['Row'];
export type CameraRow = T['cameras']['Row'];
export type ExerciseCatalogRow = T['exercise_catalog']['Row'];
export type EquipmentRow = T['equipment']['Row'];
export type CameraZoneRow = T['camera_zones']['Row'];
export type BiometricEnrollmentRow = T['biometric_enrollments']['Row'];
export type AttendanceSessionRow = T['attendance_sessions']['Row'];
export type PersonTrackRow = T['person_tracks']['Row'];
export type TrackingUncertainSegmentRow = T['tracking_uncertain_segments']['Row'];
export type EquipmentUsageSessionRow = T['equipment_usage_sessions']['Row'];
export type ExerciseActivitySessionRow = T['exercise_activity_sessions']['Row'];
export type ExerciseSetRow = T['exercise_sets']['Row'];
export type RepetitionEventRow = T['repetition_events']['Row'];
export type WorkoutReportRow = T['workout_reports']['Row'];
export type PushSubscriptionRow = T['push_subscriptions']['Row'];
export type NotificationJobRow = T['notification_jobs']['Row'];
export type EventIngestLogRow = T['event_ingest_log']['Row'];
export type DeviceHealthLogRow = T['device_health_logs']['Row'];
export type CameraStatusLogRow = T['camera_status_logs']['Row'];
export type AuditLogRow = T['audit_logs']['Row'];
