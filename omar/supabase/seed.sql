-- ============================================================================
-- Seed data
-- ----------------------------------------------------------------------------
-- 1. Exercise catalog: real reference data used by the edge rep state machine.
--    rep_profile fields are consumed by services/edge-ai/edge_ai/reps/profiles.py.
-- 2. A clearly-labelled DEMO organization for local development ONLY.
--    Never run this file against a production project.
-- ============================================================================

-- ----------------------------------------------------------------------------
-- Exercise catalog (v1 rep profiles)
--   signal: joint-angle or normalized displacement the state machine watches
--   down/up thresholds: hysteresis bands in the signal's unit
--   min_rep_seconds / max_rep_seconds: plausibility window for one repetition
-- ----------------------------------------------------------------------------
insert into public.exercise_catalog (slug, name, movement_pattern, primary_signal, rep_profile) values
('lat-pulldown', 'Lat Pulldown', 'pull_vertical', 'elbow_angle_mean',
 '{"version":1,"signal":"elbow_angle_mean","unit":"deg","top_threshold":150,"bottom_threshold":75,"min_rep_seconds":0.8,"max_rep_seconds":6,"start_state":"top"}'),
('seated-row', 'Seated Cable Row', 'pull_horizontal', 'elbow_angle_mean',
 '{"version":1,"signal":"elbow_angle_mean","unit":"deg","top_threshold":155,"bottom_threshold":70,"min_rep_seconds":0.8,"max_rep_seconds":6,"start_state":"top"}'),
('chest-press-machine', 'Chest Press (Machine)', 'push_horizontal', 'elbow_angle_mean',
 '{"version":1,"signal":"elbow_angle_mean","unit":"deg","top_threshold":155,"bottom_threshold":80,"min_rep_seconds":0.8,"max_rep_seconds":6,"start_state":"bottom"}'),
('shoulder-press-machine', 'Shoulder Press (Machine)', 'push_vertical', 'elbow_angle_mean',
 '{"version":1,"signal":"elbow_angle_mean","unit":"deg","top_threshold":160,"bottom_threshold":85,"min_rep_seconds":0.8,"max_rep_seconds":6,"start_state":"bottom"}'),
('leg-press', 'Leg Press', 'squat', 'knee_angle_mean',
 '{"version":1,"signal":"knee_angle_mean","unit":"deg","top_threshold":160,"bottom_threshold":95,"min_rep_seconds":1.0,"max_rep_seconds":8,"start_state":"top"}'),
('leg-extension', 'Leg Extension', 'extension', 'knee_angle_mean',
 '{"version":1,"signal":"knee_angle_mean","unit":"deg","top_threshold":160,"bottom_threshold":95,"min_rep_seconds":0.8,"max_rep_seconds":6,"start_state":"bottom"}'),
('leg-curl', 'Leg Curl', 'curl', 'knee_angle_mean',
 '{"version":1,"signal":"knee_angle_mean","unit":"deg","top_threshold":160,"bottom_threshold":70,"min_rep_seconds":0.8,"max_rep_seconds":6,"start_state":"top"}'),
('biceps-curl', 'Biceps Curl', 'curl', 'elbow_angle_mean',
 '{"version":1,"signal":"elbow_angle_mean","unit":"deg","top_threshold":150,"bottom_threshold":60,"min_rep_seconds":0.8,"max_rep_seconds":6,"start_state":"top"}'),
('triceps-pushdown', 'Triceps Pushdown', 'extension', 'elbow_angle_mean',
 '{"version":1,"signal":"elbow_angle_mean","unit":"deg","top_threshold":160,"bottom_threshold":80,"min_rep_seconds":0.6,"max_rep_seconds":5,"start_state":"bottom"}'),
('squat', 'Squat', 'squat', 'knee_angle_mean',
 '{"version":1,"signal":"knee_angle_mean","unit":"deg","top_threshold":165,"bottom_threshold":100,"min_rep_seconds":1.0,"max_rep_seconds":8,"start_state":"top"}'),
('bench-press', 'Bench Press', 'push_horizontal', 'elbow_angle_mean',
 '{"version":1,"signal":"elbow_angle_mean","unit":"deg","top_threshold":160,"bottom_threshold":85,"min_rep_seconds":0.8,"max_rep_seconds":7,"start_state":"top"}'),
('push-up', 'Push-up', 'push_horizontal', 'elbow_angle_mean',
 '{"version":1,"signal":"elbow_angle_mean","unit":"deg","top_threshold":160,"bottom_threshold":90,"min_rep_seconds":0.6,"max_rep_seconds":5,"start_state":"top"}'),
('treadmill-walk-run', 'Treadmill', 'cardio_cyclic', 'none',
 '{"version":1,"signal":"none","unit":"","count_reps":false,"occupancy_only":true}')
on conflict (slug) do nothing;

-- ----------------------------------------------------------------------------
-- Model registry placeholders: rows describe CANDIDATES to benchmark in Phase 2/4.
-- benchmark is NULL until measured on real gym footage. Do not treat as chosen.
-- ----------------------------------------------------------------------------
insert into public.model_versions (component, name, version, runtime, license, checksum_sha256, benchmark) values
('detector', 'rtdetr-l', 'candidate', 'onnxruntime', 'Apache-2.0', null, null),
('detector', 'yolov8n', 'candidate', 'onnxruntime', 'AGPL-3.0 (commercial license required)', null, null),
('pose', 'rtmpose-m', 'candidate', 'onnxruntime', 'Apache-2.0', null, null),
('pose', 'yolov8n-pose', 'candidate', 'onnxruntime', 'AGPL-3.0 (commercial license required)', null, null),
('reid', 'osnet_x1_0', 'candidate', 'onnxruntime', 'MIT', null, null),
('face', 'insightface-buffalo_l', 'candidate', 'onnxruntime', 'MIT (model weights: non-commercial research — verify before use)', null, null),
('tracker', 'bytetrack', 'candidate', 'pytorch', 'MIT', null, null)
on conflict (component, name, version) do nothing;

-- ----------------------------------------------------------------------------
-- DEMO TENANT — LOCAL DEVELOPMENT ONLY. All ids are fixed so tests can reference them.
-- ----------------------------------------------------------------------------
insert into public.organizations (id, name, slug, country_code)
values ('a0000000-0000-4000-8000-000000000001', 'DEMO Smart Gym (test data)', 'demo-smart-gym', 'EG')
on conflict (id) do nothing;

insert into public.branches (id, organization_id, name, timezone, address, floor_plan)
values ('b0000000-0000-4000-8000-000000000001', 'a0000000-0000-4000-8000-000000000001', 'DEMO Branch — Nasr City', 'Africa/Cairo', 'TEST DATA', '{"width_m": 30, "height_m": 20, "image_path": null}')
on conflict (id) do nothing;

insert into public.edge_devices (id, branch_id, name, status, software_version)
values ('d0000000-0000-4000-8000-000000000001', 'b0000000-0000-4000-8000-000000000001', 'edge-01 (dev laptop)', 'provisioning', '0.1.0')
on conflict (id) do nothing;

-- Dev-only device secret. Matches EDGE_DEVICE_SECRET in services/edge-ai/.env.example.
insert into public.edge_device_secrets (edge_device_id, secret_hash, secret)
values ('d0000000-0000-4000-8000-000000000001', encode(extensions.digest('dev-only-device-secret-change-me', 'sha256'), 'hex'), 'dev-only-device-secret-change-me')
on conflict do nothing;

insert into public.cameras (id, branch_id, edge_device_id, name, protocol, purpose, stream_url, resolution_w, resolution_h, fps) values
('c0000000-0000-4000-8000-000000000001', 'b0000000-0000-4000-8000-000000000001', 'd0000000-0000-4000-8000-000000000001', 'CAM-ENTRANCE (test)', 'rtsp', 'entrance', 'rtsp://192.0.2.10:554/Streaming/Channels/101', 1920, 1080, 20),
('c0000000-0000-4000-8000-000000000002', 'b0000000-0000-4000-8000-000000000001', 'd0000000-0000-4000-8000-000000000001', 'CAM-FLOOR-A (test)', 'rtsp', 'floor', 'rtsp://192.0.2.11:554/Streaming/Channels/101', 1920, 1080, 20),
('c0000000-0000-4000-8000-000000000003', 'b0000000-0000-4000-8000-000000000001', 'd0000000-0000-4000-8000-000000000001', 'CAM-FLOOR-B (test)', 'rtsp', 'floor', 'rtsp://192.0.2.12:554/Streaming/Channels/101', 1920, 1080, 20)
on conflict (id) do nothing;

insert into public.equipment (id, branch_id, name, category, position_x_m, position_y_m, default_exercise_id) values
('e0000000-0000-4000-8000-000000000001', 'b0000000-0000-4000-8000-000000000001', 'Lat Pulldown #1', 'machine', 5.0, 4.0, (select id from public.exercise_catalog where slug = 'lat-pulldown')),
('e0000000-0000-4000-8000-000000000002', 'b0000000-0000-4000-8000-000000000001', 'Leg Press #1', 'machine', 9.0, 4.0, (select id from public.exercise_catalog where slug = 'leg-press')),
('e0000000-0000-4000-8000-000000000003', 'b0000000-0000-4000-8000-000000000001', 'Chest Press #1', 'machine', 13.0, 4.0, (select id from public.exercise_catalog where slug = 'chest-press-machine'))
on conflict (id) do nothing;

insert into public.camera_zones (id, camera_id, equipment_id, name, zone_type, polygon) values
('f0000000-0000-4000-8000-000000000001', 'c0000000-0000-4000-8000-000000000001', null, 'entrance-door', 'entrance', '[[600,200],[1300,200],[1300,1000],[600,1000]]'),
('f0000000-0000-4000-8000-000000000002', 'c0000000-0000-4000-8000-000000000002', 'e0000000-0000-4000-8000-000000000001', 'lat-pulldown-1', 'equipment', '[[100,300],[700,300],[700,1000],[100,1000]]'),
('f0000000-0000-4000-8000-000000000003', 'c0000000-0000-4000-8000-000000000002', 'e0000000-0000-4000-8000-000000000002', 'leg-press-1', 'equipment', '[[800,300],[1500,300],[1500,1000],[800,1000]]'),
('f0000000-0000-4000-8000-000000000004', 'c0000000-0000-4000-8000-000000000003', 'e0000000-0000-4000-8000-000000000002', 'leg-press-1-alt', 'equipment', '[[200,250],[900,250],[900,1050],[200,1050]]'),
('f0000000-0000-4000-8000-000000000005', 'c0000000-0000-4000-8000-000000000003', 'e0000000-0000-4000-8000-000000000003', 'chest-press-1', 'equipment', '[[1000,250],[1800,250],[1800,1050],[1000,1050]]')
on conflict (id) do nothing;

-- Demo member (no auth user yet; staff invite links it later).
insert into public.members (id, organization_id, branch_id, member_code, full_name, phone, status)
-- member_code must avoid ambiguous characters (0/O, 1/I) per the check constraint.
values ('33333333-3333-4333-8333-333333333333', 'a0000000-0000-4000-8000-000000000001', 'b0000000-0000-4000-8000-000000000001', 'GYM-TEST-M2A7', 'Test Member (not a real person)', '+201000000000', 'active')
on conflict (id) do nothing;

insert into public.consent_records (id, member_id, consent_type, status, policy_version) values
('55555555-5555-4555-8555-555555555555', '33333333-3333-4333-8333-333333333333', 'biometric_face_identification', 'granted', 'privacy-v1'),
('55555555-5555-4555-8555-555555555556', '33333333-3333-4333-8333-333333333333', 'cctv_workout_tracking', 'granted', 'privacy-v1'),
('55555555-5555-4555-8555-555555555557', '33333333-3333-4333-8333-333333333333', 'data_processing', 'granted', 'privacy-v1')
on conflict (id) do nothing;
