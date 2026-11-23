"""Project-wide constants. Mirror of packages/shared-config CONSTANTS — keep in sync."""

EVENTS_SCHEMA_VERSION = "1"

# Seconds of no rep movement before an active set is closed.
SET_END_IDLE_SECONDS = 4.0
# Minimum gap between sets to count as a rest interval.
MIN_REST_SECONDS = 4.0
# Rep events for the same identity + equipment within this window are duplicates.
REP_DEDUP_WINDOW_MS = 700
# Below this identity confidence a track is reported but never bound to a member.
IDENTITY_BIND_THRESHOLD = 0.85
# Below this tracking confidence reps are counted but flagged unverified.
TRACKING_VERIFIED_THRESHOLD = 0.6
# Dwell time inside an equipment zone before usage starts (filters walk-throughs).
ZONE_ENTER_DWELL_SECONDS = 2.0
# Time outside a zone before usage ends (filters brief occlusions / jitter).
ZONE_EXIT_GRACE_SECONDS = 5.0
# Max gap during which a lost local track may be re-associated to the same global id.
TRACK_REASSOCIATE_SECONDS = 30.0

# Central Camera Manager & Face Recognition Configuration
FACE_MATCH_THRESHOLD = 0.70
FACE_DETECTION_INTERVAL_MS = 250
RECOGNITION_COOLDOWN_MS = 5000
MAX_FRAME_QUEUE_SIZE = 5
CAMERA_RECONNECT_MAX_ATTEMPTS = 5
CONSUMER_LEASE_TIMEOUT_SECONDS = 30.0
CAMERA_HEARTBEAT_TIMEOUT_SECONDS = 30.0
