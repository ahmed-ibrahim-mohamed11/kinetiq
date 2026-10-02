-- GYM-AI development seed (Phase 3). Two organizations so RLS isolation is testable.
-- Run AFTER the migrations. Dashboard users are created via Supabase Auth; attach them to an
-- organization by inserting a row in profiles (see supabase/README.md).

insert into organizations (id, name) values
    ('11111111-1111-1111-1111-111111111111', 'Demo Gym Co'),
    ('22222222-2222-2222-2222-222222222222', 'Other Org (isolation check)');

insert into gyms (id, organization_id, name, timezone) values
    ('31111111-1111-1111-1111-111111111111', '11111111-1111-1111-1111-111111111111', 'Main Facility', 'Africa/Cairo'),
    ('32222222-2222-2222-2222-222222222222', '22222222-2222-2222-2222-222222222222', 'Other Facility', 'UTC');

insert into machines (id, gym_id, name, exercise_type, location_label) values
    ('41111111-1111-1111-1111-111111111111', '31111111-1111-1111-1111-111111111111', 'Seated Row 1', 'seated_row', 'Row A'),
    ('42222222-2222-2222-2222-222222222222', '32222222-2222-2222-2222-222222222222', 'Seated Row X', 'seated_row', 'Elsewhere');

insert into model_versions (id, name, semantic_version, task, git_commit, metrics_json, active) values
    ('51111111-1111-1111-1111-111111111111', 'rep-seg-core-hsmm', '0.2.0', 'rep_segmentation', '60074c4',
     '{"validation": "nested LOVO, same athlete", "f1_pull_tol15": 0.93, "f1_iou_0.50": 0.97}', true);

insert into devices (id, gym_id, name, status, agent_version, model_version_id) values
    ('61111111-1111-1111-1111-111111111111', '31111111-1111-1111-1111-111111111111', 'edge-station-01', 'offline',
     'edge-agent-0.1.0', '51111111-1111-1111-1111-111111111111'),
    ('62222222-2222-2222-2222-222222222222', '32222222-2222-2222-2222-222222222222', 'edge-station-x', 'offline',
     'edge-agent-0.1.0', '51111111-1111-1111-1111-111111111111');

insert into cameras (device_id, machine_id, source_type, source_label) values
    ('61111111-1111-1111-1111-111111111111', '41111111-1111-1111-1111-111111111111', 'file', 'correct_01.mp4'),
    ('62222222-2222-2222-2222-222222222222', '42222222-2222-2222-2222-222222222222', 'usb', '0');

-- one historical session with two reps for each org, so list pages render immediately
insert into sessions (id, machine_id, device_id, athlete_ref, started_at, ended_at, total_reps, status) values
    ('71111111-1111-1111-1111-111111111111', '41111111-1111-1111-1111-111111111111',
     '61111111-1111-1111-1111-111111111111', 'athlete-001', now() - interval '1 day',
     now() - interval '1 day' + interval '5 minutes', 2, 'ended'),
    ('72222222-2222-2222-2222-222222222222', '42222222-2222-2222-2222-222222222222',
     '62222222-2222-2222-2222-222222222222', 'athlete-x', now() - interval '2 days',
     now() - interval '2 days' + interval '4 minutes', 2, 'ended');

insert into reps (event_uuid, session_id, rep_index, start_frame, pull_frame, end_frame,
                  duration_ms, pull_duration_ms, return_duration_ms, peak, peak_z,
                  pull_left_frame, pull_right_frame, lag_pull_ms, lag_onset_ms, amp_ratio, model_version_id) values
    (gen_random_uuid(), '71111111-1111-1111-1111-111111111111', 1, 96, 126, 178,
     2733, 1000, 1733, 0.81, 0.4, 125, 127, 66.7, 33.3, 0.93, '51111111-1111-1111-1111-111111111111'),
    (gen_random_uuid(), '71111111-1111-1111-1111-111111111111', 2, 189, 221, 264,
     2500, 1066, 1434, 0.78, 0.2, 220, 222, 66.7, 0.0, 0.95, '51111111-1111-1111-1111-111111111111'),
    (gen_random_uuid(), '72222222-2222-2222-2222-222222222222', 1, 10, 40, 70,
     2000, 1000, 1000, 0.70, 0.0, 39, 41, 66.7, 0.0, 0.90, '51111111-1111-1111-1111-111111111111'),
    (gen_random_uuid(), '72222222-2222-2222-2222-222222222222', 2, 80, 110, 140,
     2000, 1000, 1000, 0.72, 0.1, 109, 111, 66.7, 0.0, 0.91, '51111111-1111-1111-1111-111111111111');

insert into alerts (session_id, alert_type, severity, message, status) values
    ('71111111-1111-1111-1111-111111111111', 'asymmetry_review', 'warning',
     'Potential movement deviation (amp_ratio below provisional threshold) - coach review', 'open');
