# events.v1 — edge → cloud event contract

Source of truth: `packages/api-contracts/src/events.ts` (Zod) and `services/edge-ai/edge_ai/contracts/events_v1.py` (Pydantic). Shared fixtures: `packages/api-contracts/fixtures/events_v1_valid.json` — both implementations must accept every fixture (tested in CI).

## Envelope

| Field | Type | Meaning |
| --- | --- | --- |
| `schema_version` | `"1"` | Contract version |
| `event_id` | uuid | Idempotency key, generated on the edge |
| `device_id` | uuid | `edge_devices.id` — must match the authenticated device |
| `branch_id` | uuid | Must match the device's branch |
| `sequence` | int ≥ 0 | Monotonic per device, assigned when persisted to the local queue |
| `occurred_at` | ISO-8601 with offset | Observation time (edge clock, NTP-synced) |
| `sent_at` | ISO-8601 with offset | Time the envelope was built |
| `event_type` | enum | See below |
| `payload` | object | Type-specific |

## Event types and payloads

| `event_type` | Payload | Server effect |
| --- | --- | --- |
| `device.heartbeat` | software_version, uptime_seconds, gpu_utilization?, gpu_memory_used_mb?, cpu_utilization?, queue_depth, cameras_online, cameras_total, avg_pipeline_latency_ms? | `device_health_logs` row; `edge_devices.last_seen_at`, status → active |
| `camera.status` | camera_id, status ∈ online/reconnecting/offline/degraded, fps_measured?, resolution_w?, resolution_h?, error? | `camera_status_logs`; `cameras.last_status` |
| `identity.checkin` | member_id, method ∈ face/qr/nfc/staff_manual, confidence, camera_id?, global_track_id?, consent_record_id?, presentation_attack_score? | Requires active member in branch, `cctv_workout_tracking` consent, and `biometric_face_identification` consent when method=face. Creates/reuses the active `attendance_sessions` row; binds the entrance track |
| `identity.checkout` | member_id, method, confidence, camera_id? | `finalize_attendance_session` on the active session (no-op if none) |
| `track.started` | global_track_id, member_id?, identity_confidence, tracking_confidence, first_camera_id | Upsert `person_tracks`; binds to the member's active session only when identity_confidence ≥ 0.85 |
| `track.ended` | global_track_id, reason ∈ left_view/checkout/merged/timeout/ambiguous_identity, last_camera_id? | Closes the track |
| `tracking.uncertain` | global_track_id, member_id?, from, to, reason ∈ occlusion/no_camera_coverage/identity_ambiguous/camera_offline/low_tracking_confidence | `tracking_uncertain_segments` (surfaced in reports) |
| `equipment.usage.started` | usage_local_id, global_track_id, member_id?, equipment_id, zone_id, camera_id, association_confidence | Insert `equipment_usage_sessions` (idempotent on device+usage_local_id); equipment must belong to the branch |
| `equipment.usage.ended` | usage_local_id, reason ∈ left_zone/track_lost/checkout/timeout | Sets `ended_at` |
| `exercise.activity.started` | activity_local_id, usage_local_id, exercise_id, classification_confidence, classifier_model_version_id? | Insert `exercise_activity_sessions` |
| `exercise.activity.ended` | activity_local_id, active_seconds | Sets `ended_at`, `active_seconds` |
| `set.started` | set_local_id, activity_local_id, set_index, rest_before_seconds? | Insert `exercise_sets` |
| `rep.counted` | set_local_id, rep_index, camera_id, confidence, quality_label = **null** | Insert `repetition_events` (id = event_id); live `rep_count` = max(rep_index) |
| `set.completed` | set_local_id, rep_count, confidence, verified | Authoritative `rep_count`, `confidence`, `verified`, `ended_at` |
| `biometric.enrolled` | member_id, consent_record_id, template_ref, key_id, model_name, quality? | Insert `biometric_enrollments` reference; requires a granted biometric consent record belonging to the member. **No template data.** |
| `biometric.revoked` | member_id, template_ref, reason ∈ consent_withdrawn/member_request/staff_request/key_rotation/retired_device | Sets `revoked_at` on the reference |

All confidences are in [0, 1]. `quality_label` is reserved for Phase 9 and must be `null`; the database rejects anything else.

## Batch

```json
{
  "schema_version": "1",
  "batch_id": "<uuid, single-use>",
  "device_id": "<uuid>",
  "sent_at": "<ISO-8601>",
  "events": [ /* 1..500 envelopes, ascending sequence */ ]
}
```

Response (`IngestResult`): `{ batch_id, accepted, duplicates, rejected: [{event_id, reason}], server_time }`. A replayed `batch_id` returns 200 with `accepted: 0` and `replayed: true`.

## Ordering and gaps

Events in a batch are applied in array order. Child events referencing an unknown parent local id (e.g. `set.started` before `exercise.activity.started`) are rejected individually and reported; the edge logs them as poison and does not retry. The edge therefore always flushes in sequence order and never reorders across batches.

## Versioning

Additive optional fields do not bump the version. Removing/renaming fields or changing semantics creates `events.v2` (new Zod/Pydantic files, new `_apply_edge_event_v2`), and the ingest RPC dispatches on `schema_version`. Devices announce their contract version in `device.heartbeat.software_version` conventions (`<app>-<contract>`).
