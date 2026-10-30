# AI Smart Gym

Hybrid edge-AI + cloud platform that turns a gym's existing IP cameras into an automatic workout tracker: consent-based member identification at the entrance (QR/NFC alternative), multi-camera tracking, equipment association, exercise recognition, repetition and set counting with cross-camera deduplication, and a personalised daily report delivered to the member's phone.

> **Status:** all software layers are built and verified by execution where no external resource is required — the migrations, ingest engine and RLS run on a real PostgreSQL in `tests/db`, and the real Python edge pipeline has been proven end-to-end into that database. What remains is external: a Supabase project, Vercel, authorised cameras, model weights and legal review. See [PROGRESS.md](PROGRESS.md) for exactly what is verified and what is blocked. Nothing has been validated on real gym footage yet; recognition accuracy claims are deliberately absent until that happens.

## Architecture in one picture

```
 Gym cameras (RTSP/ONVIF, camera VLAN)
        │
        ▼
 ┌──────────────────────────── Edge server (NVIDIA GPU, Docker) ────────────────────────────┐
 │ camera-gateway ─► detection ─► per-camera tracking ─► global identity fusion (ReID)      │
 │        │                                │                       │                         │
 │   health/metrics        zone/equipment association ─► exercise profile ─► rep state      │
 │                                                         machine ─► set segmenter         │
 │   identity-service (face templates encrypted, edge-only; consent-gated) ─► check-in      │
 │                                                                                           │
 │   rep dedup across cameras ─► events.v1 ─► SQLite WAL queue ─► HMAC-signed sync (HTTPS)  │
 └───────────────────────────────────────────────────────────────────────────────────────────┘
        │
        ▼
 Supabase: ingest-events Edge Function ─► ingest_events() RPC (idempotent) ─► PostgreSQL (RLS)
        │                                                                     │
        │                    finalize_attendance_session() ─► workout_reports ─► notification_jobs
        ▼                                                                     │
 Vercel: admin-web (staff) · member-web (members, QR, live sets, reports)     ▼
                                                              notification-worker (Web Push)
```

Full description: [docs/architecture/overview.md](docs/architecture/overview.md).

## Repository layout

| Path | What it is |
| --- | --- |
| `apps/admin-web` | Next.js staff console: members, consents, cameras & zones, equipment, devices, live sessions, audit |
| `apps/member-web` | Next.js member app: QR check-in, live workout, history, reports, push notifications, consent management |
| `packages/api-contracts` | **events.v1** (Zod) — the edge↔cloud contract, plus web API input schemas |
| `packages/shared-types` | Domain TypeScript types mirrored from the schema |
| `packages/validation` | Geometry, phone, member code, QR token validators (Python mirror in edge-ai) |
| `packages/shared-config` | Typed env loading; public/server boundary enforcement; shared constants |
| `packages/ui` | shadcn-style component kit + Tailwind preset |
| `packages/supabase-client` | Browser/server/middleware Supabase clients and `Database` types |
| `services/edge-ai` | Python edge pipeline: RTSP ingest, detection, tracking, fusion, reps/sets, encrypted biometrics, durable queue, signed sync |
| `services/notification-worker` | Node worker delivering Web Push with DB-driven retries |
| `supabase/migrations` | Schema, RLS, workout engine, ingest RPC, retention & jobs |
| `supabase/functions` | `ingest-events` (device HMAC auth), `device-sync` (cloud→edge config/consents), `qr-token` (member QR issue/verify) |
| `infrastructure/` | Dockerfiles, edge docker-compose, Prometheus/Grafana monitoring |
| `tests/db` | **Embedded PostgreSQL suite**: executes migrations + seed, ingest engine, RLS (role switching), edge→cloud e2e with the real Python pipeline, backup drill; also generates the Database types |
| `tests/` | security (static secret scan, RLS via PostgREST), integration (ingest smoke), computer-vision & performance protocols |
| `docs/` | architecture, security & privacy, API, deployment, operations |

## Quick start (local)

Prerequisites: Node 22, Python 3.10+, Docker (for Supabase local), Supabase CLI.

```bash
npm ci
npm run typecheck && npm run test:unit            # TS packages + worker
npm run test -w tests/db                          # real PostgreSQL (embedded, no Docker): migrations, ingest, RLS, e2e
supabase start                                    # full Supabase stack (needs Docker): Auth, PostgREST, Edge Runtime
supabase status -o env                            # copy ANON_KEY / SERVICE_ROLE_KEY
cp apps/admin-web/.env.local.example apps/admin-web/.env.local   # fill keys
cp apps/member-web/.env.local.example apps/member-web/.env.local
npm run dev:admin                                 # http://localhost:3000
npm run dev:member                                # http://localhost:3001

# Edge service
cd services/edge-ai && python -m venv .venv && .venv/Scripts/pip install -e ".[dev]"   # or bin/ on Linux
.venv/Scripts/pytest -q
cp .env.example .env && cp config/cameras.example.yaml config/cameras.yaml
.venv/Scripts/python -m edge_ai simulate          # synthetic workout → events → local Supabase
.venv/Scripts/python -m edge_ai probe rtsp://<camera>/...   # Phase 0 camera probe
```

## Security posture (summary)

- Browser only ever holds the anon key; every table has RLS with default deny; staff scoped by organization and branch; members see only their own rows.
- Edge devices authenticate with per-device HMAC secrets (rotatable), batches are replay-protected, every event is idempotent by `event_id`.
- Biometric templates are AES-256-GCM encrypted and **never leave the edge**; the cloud stores references only. Face identification requires a granted consent record, enforced on the edge *and* in the ingest RPC. Consent withdrawal revokes templates.
- No raw video is stored anywhere. Camera credentials live in an edge secrets file, never in the database or repo (enforced by a DB check constraint and a CI scan).
- Audit triggers on members, consents, biometrics, staff roles, cameras, devices and secrets.

Details: [docs/security/threat-model.md](docs/security/threat-model.md), [docs/security/privacy-egypt.md](docs/security/privacy-egypt.md).

## Licensing note on models

No model weights are bundled. Candidate detectors/pose/ReID/face models are registered in `model_versions` with their licenses; several popular options (YOLOv8 family, InsightFace weights) carry AGPL or non-commercial terms and need a commercial license before production use. Selection happens in Phase 2/4 on measured benchmarks — see [docs/architecture/model-selection.md](docs/architecture/model-selection.md).
