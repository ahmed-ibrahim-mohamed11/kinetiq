-- ============================================================================
-- Exercise catalog v1 (migration 0007)
-- ----------------------------------------------------------------------------
-- Reference data, not seed data: every environment (including production, which never runs
-- supabase/seed.sql) needs the catalog because the edge rep state machine and the
-- device-sync snapshot are driven by rep_profile. Idempotent on slug so re-running is safe.
--   signal: joint-angle or normalized displacement the state machine watches
--   top/bottom thresholds: hysteresis bands in the signal's unit
--   min_rep_seconds / max_rep_seconds: plausibility window for one repetition
-- rep_profile fields are consumed by services/edge-ai/edge_ai/reps/profiles.py.
-- ============================================================================
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
