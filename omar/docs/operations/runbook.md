# Operations runbook

## Dashboards and signals

| Signal | Source | Healthy |
| --- | --- | --- |
| `edge_camera_online{name}` | edge `/metrics` | 1 for every enabled camera |
| `edge_queue_depth` | edge `/metrics`, `device_health_logs.queue_depth` | < 100 steady state |
| `edge_p95_latency_ms` | edge `/metrics` | < 400 ms |
| `edge_reps_suppressed_total` vs `edge_reps_admitted_total` | edge | suppressed ≈ admitted × (overlapping cameras − 1) |
| `edge_identity_merges_refused_total` | edge | low and flat; spikes mean ReID thresholds or camera overlap need review |
| `edge_devices.last_seen_at` | admin → Devices | < 2 min old |
| `notification_jobs` status=dead | admin SQL | 0 |
| Supabase DB CPU / connections | Supabase dashboard | < 70 % |

## Alerts and responses

### Camera offline (`EdgeCameraOffline`)
1. `docker compose logs edge-ai | grep camera.status` → error text (timeout, auth, 404 path).
2. From the edge host: `ffprobe -rtsp_transport tcp rtsp://<cam>` with credentials from the secrets file. Auth failure → camera password changed: update `camera-credentials.yaml`, restart `edge-ai`. Network → check PoE switch / VLAN.
3. Members at that machine during the outage get `no_camera_coverage` uncertainty; nothing to repair in data.

### Sync backlog (`EdgeQueueBacklog`)
1. `curl localhost:8088/healthz | jq '.sync_last_error'`.
2. `401` → device secret or clock. Check `chronyc tracking`; if time is fine, rotate the secret (admin → Devices) and update `.env`.
3. `5xx` → check Supabase status/logs for `ingest_events` errors; the edge retries automatically.
4. Network → verify egress to `*.supabase.co:443`.
5. After recovery, `edge_queue_depth` falls at ≤ 500 events/2 s per device. Nothing to replay manually.

### Events rejected (`sync.event_rejected` in logs)
A rejection is a contract or business-rule violation (e.g. member without consent). Inspect `event_ingest_log where status='rejected'` for the reason. Consent problems → front desk records consent → future check-ins succeed; past rejected events are not re-created.

### Session never checked out
`abandon_stale_sessions(6)` runs every 15 min and finalises sessions older than 6 h with status `abandoned`; their reports mark open sets unverified. Front desk can check members out manually in admin → Live Sessions.

### Notification dead-lettered
`select * from notification_jobs where status='dead'`. Common causes: no push subscription (member never enabled push → fall back to email in Phase 7 when SMTP is configured) or expired endpoints (auto-disabled). Re-queue: `update notification_jobs set status='queued', attempts=0, next_attempt_at=now() where id=...`.

### Edge host rebuilt
Restore `/data` volume from the encrypted backup (templates + queue). If templates are lost, members fall back to QR until re-enrolled; cloud `biometric_enrollments` rows for that device should be revoked to keep the registry truthful.

### Database restore
Supabase PITR to the chosen timestamp. After restore, edge devices may hold events already applied before the restore point — they are re-sent and applied idempotently; events whose `batch_id` was already consumed return `replayed`. Run the RLS test suite against the restored instance.

### Member cannot be identified at the entrance
`curl localhost:8088/healthz | jq '.identification, .consent_cache_stale, .stages.face'`. `stages.face: null` → no face model/biometric key configured (QR only). `consent_cache_stale: true` → `edge-ai sync` failing (check `config_sync_last_error`); the gate fails closed by design. High `ambiguous` counts → two enrolled members look alike at the current threshold; re-enrol with better photos or raise `FaceMatcher.ambiguity_margin`. Members can always fall back to the kiosk QR (admin → Check-in Kiosk).

### Enrolling / revoking a member's face template (on the edge host)
```bash
edge-ai sync                                   # refresh consents from the cloud first (fails closed otherwise)
edge-ai enroll --member <uuid> --image a.jpg --image b.jpg --image c.jpg
edge-ai drain                                  # pushes biometric.enrolled so the cloud registry shows it
edge-ai revoke --member <uuid> --reason member_request
```
Photos are not stored; only the encrypted embedding is. Consent withdrawn in the member app → the next `sync` deletes the template automatically.

### Discovering cameras
`edge-ai discover --interface <camera-VLAN IP> --seconds 5` lists ONVIF devices (model, name, XAddrs). Resolve RTSP URIs with `get_stream_uri()` using the viewer account, then register the camera in the admin app **without** credentials and add the credentials to `camera-credentials.yaml`.

## Routine
- Weekly: review `audit_logs` for role grants and secret rotations; check `model_versions` benchmark freshness.
- Monthly: backup restore drill; dependency updates; rotate device secrets.
- Quarterly: re-run the camera probe and the rep-count evaluation on fresh labelled footage; recalibrate thresholds.
