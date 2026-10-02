# CLOUD_MIGRATION.md — edge → Supabase → Next.js/Vercel

_Status: planned (Phases 3–6). Nothing here exists yet._

## Decision
Heavy, continuous AI stays on the local edge computer. Cloud is the data/control/visualisation plane. No continuous raw video upload in the MVP; compact rep/session/heartbeat events only.

## Phase 3 — Supabase foundation
- SQL migrations committed under `supabase/migrations/` (never dashboard-only schema).
- Tables: `organizations, profiles, gyms, machines, devices, cameras, sessions, reps, form_results, alerts, device_heartbeats, model_versions, evidence_assets` (field lists in the master prompt; `reps` gets `UNIQUE(event_uuid)` and `UNIQUE(session_id, rep_index)`).
- RLS on all user-facing tables, scoped by organization/gym; RLS tests.
- Service-role key never in the browser; edge device gets its own identity or a secured ingestion endpoint.
- No RTSP credentials in plain columns; secrets only in env/secret store. `.env.example` committed, `.env` ignored.
- `device_heartbeats` retention/downsampling policy defined up front.
- Private Storage bucket + signed URLs for optional evidence.
- Generated TypeScript types; local dev docs.

## Phase 4 — Dashboard with simulated data
Next.js + TypeScript on Vercel. Pages: `/login /dashboard /sessions /sessions/[id] /analytics /devices /alerts /models /settings`.
A simulator emits realistic `rep_completed` / `session_*` / `device_heartbeat` events so the UI is testable without hardware.

## Phase 5 — Edge agent (`ai/edge_agent/`)
`CameraSource` abstraction (USB index now, RTSP later; reconnect, timestamps, measured FPS, bounded queue, health). Models loaded once with version exposed. Session lifecycle with stable IDs; each rep emitted exactly once. **Offline-first outbox** (SQLite): append → send when online → ack only on cloud success → retry with backoff → idempotent by `event_uuid`. Periodic heartbeat (device, camera_ok, capture/inference FPS, latency, queue depth, model/agent version, last error). Structured logging; graceful shutdown.

## Phase 6 — End-to-end
One device, one camera, one seated-row station. Rep reaches Supabase → dashboard updates within seconds; no duplicate reps after retries; camera/network failures visible.

## Event contract
`shared/event-schemas/` — versioned JSON schema shared by Python and TypeScript; see `docs/ARCHITECTURE.md §5`.

## Cloud quality gates
RLS isolation tests pass · browser never receives service-role credentials · rep visible on dashboard within a few seconds · model/device version attached to every result.
