/**
 * TypeScript mirror of shared/event-schemas/v1/events.schema.json (schema_version 1.0).
 * Used by the Next.js dashboard (Phase 4). Keep in sync with the JSON Schema and the
 * pydantic models in shared/event-schemas/python/gym_ai_events/models.py.
 */

export const SCHEMA_VERSION = "1.0" as const;

export type EventType =
  | "rep_completed"
  | "session_started"
  | "session_ended"
  | "device_heartbeat"
  | "camera_error"
  | "alert_created"
  | "model_loaded";

export interface Frames {
  start: number;
  pull: number;
  end: number;
}

export interface RepMetrics {
  duration_ms?: number;
  pull_duration_ms?: number;
  return_duration_ms?: number;
  pull_left_frame?: number;
  pull_right_frame?: number;
  lag_pull_ms?: number;
  lag_onset_ms?: number;
  amp_ratio?: number;
  peak?: number;
  peak_z?: number;
}

export interface HeartbeatPayload {
  camera_ok: boolean;
  capture_fps?: number;
  inference_fps?: number;
  latency_ms?: number;
  queue_depth?: number;
  error_code?: string | null;
}

export interface AlertPayload {
  alert_type: string;
  severity: "info" | "warning" | "critical";
  message: string;
  rep_event_uuid?: string;
}

export interface CameraErrorPayload {
  code: string;
  message: string;
}

interface EdgeEventBase {
  schema_version: typeof SCHEMA_VERSION;
  event_type: EventType;
  event_uuid: string;
  device_id: string;
  ts?: string;
  agent_version: string;
  model_version?: string;
  session_id?: string;
  machine_id?: string;
  exercise?: string;
  rep_index?: number;
  frames?: Frames;
  metrics?: RepMetrics;
  heartbeat?: HeartbeatPayload;
  alert?: AlertPayload;
  error?: CameraErrorPayload;
}

export interface RepCompletedEvent extends EdgeEventBase {
  event_type: "rep_completed";
  session_id: string;
  machine_id: string;
  rep_index: number;
  frames: Frames;
  metrics: RepMetrics;
  model_version: string;
}

export interface SessionStartedEvent extends EdgeEventBase {
  event_type: "session_started";
  session_id: string;
  machine_id: string;
}

export interface SessionEndedEvent extends EdgeEventBase {
  event_type: "session_ended";
  session_id: string;
}

export interface DeviceHeartbeatEvent extends EdgeEventBase {
  event_type: "device_heartbeat";
  heartbeat: HeartbeatPayload;
}

export interface CameraErrorEvent extends EdgeEventBase {
  event_type: "camera_error";
  error: CameraErrorPayload;
}

export interface AlertCreatedEvent extends EdgeEventBase {
  event_type: "alert_created";
  session_id: string;
  alert: AlertPayload;
}

export interface ModelLoadedEvent extends EdgeEventBase {
  event_type: "model_loaded";
  model_version: string;
}

export type EdgeEvent =
  | RepCompletedEvent
  | SessionStartedEvent
  | SessionEndedEvent
  | DeviceHeartbeatEvent
  | CameraErrorEvent
  | AlertCreatedEvent
  | ModelLoadedEvent;
