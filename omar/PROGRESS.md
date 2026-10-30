# Implementation progress

Last updated: 2026-10-30. This file is the source of truth for what exists, what has been **verified by execution**, and what is blocked. Nothing below is marked complete because files exist; every "verified" row names the command that proved it.

Legend: ✅ verified by execution · 🟡 implemented, not fully verifiable here · ⛔ blocked on an external resource · ⬜ not started

## Phase status

| Phase | Status | Gate result |
| --- | --- | --- |
| 0 — Feasibility & discovery | 🟡 tooling complete; on-site data missing | Camera probe + ONVIF discovery tools, feasibility checklist, hardware/network assumptions, risk register, model-selection protocol. Gate **not** passed: needs real camera probes and the floor survey (external). |
| 1 — Project foundation | ✅ | Monorepo, shared packages, schema + RLS + engine SQL, Edge Functions (Deno type-checked), both web apps (built), worker, Docker, CI, docs. **All six migrations and the seed execute on a real PostgreSQL**; RLS, ingest, notification and retention RPCs verified by tests. Gate passed. |
| 2 — Camera integration | 🟡 | RTSP reader with reconnection verified on a real decode path (synthetic video), probe tool, ONVIF WS-Discovery (parser tested), ONNX detector adapter verified through ONNX Runtime with a synthetic model, persistent queue + signed sync verified end-to-end into PostgreSQL. **Missing**: a real camera stream and the GPU benchmark on chosen weights (external). |
| 3 — Member identification | 🟡 | Consent model + gate (fails closed), encrypted template store, matcher with ambiguity rejection, Haar/YuNet face detectors, ArcFace-style ONNX embedder, enrollment & revocation CLI with `biometric.enrolled/revoked` events, cloud `device-sync` + `device_sync_snapshot`, entrance identification stage wired into the pipeline (tested), QR issue/verify function + **front-desk kiosk** page, consent-withdrawal → template deletion both ways. **Missing**: chosen face/PAD model weights and threshold calibration on real enrollments. |
| 4 — Multi-camera tracking | 🟡 | Per-camera tracker, global identity registry (ReID + spatial/temporal gating, refuse-ambiguous), ReID ONNX embedder wired into the worker (first sighting + periodic), uncertainty events, cross-camera handoff — all unit-tested on synthetic data. **Missing**: ReID weights and crowded-scene footage. |
| 5 — Exercise & equipment recognition | 🟡 | Equipment registry, zone editor, floor map, zone-dwell association, v1 exercise classification (equipment default), catalogue/zone config synced from the cloud. **Missing**: temporal exercise classifier (needs labelled footage). |
| 6 — Rep & set intelligence | 🟡 | Time-aware rep state machine, set segmentation, rest detection, cross-camera dedup, confidence/unverified handling, pose adapter → joint angles verified; evaluation harness ready. **Missing**: labelled-footage precision/recall. |
| 7 — Reporting & member experience | ✅ (software) | Member app (QR, live workout, history, report, share link, consents, push), admin app (dashboard, live sessions, members, consents, equipment, cameras/zones, devices, audit, kiosk), worker with Web Push + Resend email fallback. Report generation verified on a real database from real pipeline events. **Not** exercised against Supabase Auth/PostgREST (needs a project). |
| 8 — Production hardening | 🟡 | RLS + authorization tests executed; ingest idempotency/replay verified; abandon-stale-sessions, retention, notification backoff/dead-letter verified; backup/restore drill implemented (skipped locally — needs `pg_dump` 18, runs in CI); monitoring alerts written. **Missing**: 24 h soak on real cameras, internet-outage drill on the real stack, third-party review. |
| 9 — Form analysis roadmap | 🟡 design only | Schema hooks (`form_rules`, `quality_label` constrained NULL), reserved event field, ADR. Intentionally not implemented. |

## Verification evidence (executed on the dev machine: Windows 11, Node 24.20, npm 11.19, Python 3.10.11, Deno 2.9.8, embedded PostgreSQL 18.4)

| Check | Command | Result |
| --- | --- | --- |
| TS typecheck, all 11 workspaces (with **generated** Database types) | `npm run typecheck` | exit 0 |
| TS unit tests | `npm run test:unit` | api-contracts 11 ✅, validation 9 ✅, shared-config 4 ✅, notification-worker 9 ✅ (incl. email sender) |
| Static security tests | `npm run test -w tests/security` | secrets-scan 6 ✅ (RLS-via-PostgREST suite correctly skipped without a Supabase project) |
| **Migrations + seed on real PostgreSQL** | `npm run test -w tests/db` → `migrations.test.ts` | 5 ✅ — all tables present with RLS, catalogue seeded, privileged functions not executable by anon/authenticated, audit trigger fires |
| **Ingest engine on real PostgreSQL** | `ingest.test.ts` | 8 ✅ — fixture workout → completed session, sets/reps, report (total_reps 10, uncertain segment), notification queued, device status; idempotent replay; batch replay rejected; consent gate (face w/o consent rejected, QR accepted); cross-branch equipment rejected; stale-session abandonment; notification claim/backoff/dead-letter; retention over 8 tables |
| **RLS on real PostgreSQL** (PostgREST-equivalent role + JWT claims) | `rls.test.ts` | 13 ✅ — member isolation (rows, consents, no infra visibility, no writes, consent withdrawal only, self checkout); staff org isolation, cross-org insert denied, RPC check-in/out, secret rotation, owner-only owner grants; anon catalogue-only, shared report token, privileged RPCs denied |
| **Edge → cloud end-to-end** | `e2e-edge.test.ts` | 1 ✅ — real `edge-ai simulate` (two cameras, one member, two sets) → 29 HMAC-signed events over HTTP → `ingest_events` → completed session, 2 sets, 15 deduplicated reps = `repetition_events` count, report with `leg-press` and uncertain segment, zero rejections |
| Backup/restore drill | `backup-restore.test.ts` | ⚠ skipped locally (needs `pg_dump`/`psql` 18; the sandbox refused the binary download). Runs in CI job `database-embedded` with `postgresql-client-18` |
| Generated types match migrations | `npm run gen:types -w tests/db` | wrote `database.ts` (54 KB) from the live schema via Supabase CLI |
| Edge Functions | `deno check --config deno.json ingest-events qr-token device-sync` | exit 0 |
| admin-web / member-web production builds | `npm run build -w apps/…` | exit 0, 15 + 13 routes (admin now includes `/kiosk`) |
| Python lint / strict types | `ruff check .` · `mypy edge_ai` | clean / clean (52+ files) |
| Python tests | `pytest -q` (services/edge-ai) | **79 passed**: contracts (shared fixtures incl. biometric events), rep counter, set segmenter, dedup, queue+sync (signature vectors, backoff, poison handling), geometry+tracker, global identity, zones, face store, device sync + enrollment + entrance identifier, ONVIF parser, **ONNX adapters through ONNX Runtime**, **real video decode**, pipeline simulation, pipeline entrance + config sync |

Fixes made during this round of verification: time-aware rep smoothing; `interface`→`type` Rows then full type generation; `@supabase/ssr` upgraded 0.5→0.12 (its generics lagged supabase-js 2.118 and typed every query as `never`), after which all app-side Supabase type wrappers were deleted; four SQL defects found statically (`base64url`, forced RLS on secrets, pgcrypto search_path, ambiguous seed code) all confirmed fixed by execution; JS/Python JSON number normalisation (integral-after-rounding); detector layout heuristic for small anchor counts; OpenCV pinned `<5` (Haar cascades removed in 5.0); e2e shim must not block the event loop.

## Not verified (honest list)

- **No RTSP camera has been opened**; the decode path was exercised on a locally generated video file only.
- **No model weights, benchmark numbers or accuracy figures exist.** Adapters are verified with synthetic ONNX graphs, which proves plumbing, not recognition quality.
- **Supabase Auth / PostgREST / Edge Runtime have not executed** (no Supabase project or Docker). RLS was verified with role switching on PostgreSQL, which is what PostgREST does, but the HTTP layer itself (JWT verification, `qr-token`, the Deno functions at runtime) is only type-checked.
- **Web apps have not been clicked through against a live backend**; they build, type-check against the generated schema types, and their server actions call the verified RPCs.
- **No deployment** to Vercel or Supabase cloud; no Docker image built here (no Docker).
- Backup/restore drill skipped locally (see above).

## Blocked work

| Blocker | Needed for | Owner action |
| --- | --- | --- |
| Supabase project (region per legal) **or** Docker Desktop for `supabase start` | Auth/PostgREST/Edge Runtime execution, `tests/security/rls.test.ts`, `tests/integration/ingest-smoke.mjs`, clicking through the apps | Create project / install Docker; `supabase db push`; set function secrets |
| Vercel account linked to the GitHub repo | Deploy admin-web / member-web | Connect repo per `docs/deployment/vercel.md` |
| GitHub remote | CI execution (incl. the embedded-DB job and backup drill), gitleaks | `git remote add origin … && git push` (local repo initialised with the full history) |
| Authorised camera access (RTSP URLs, credentials, positions) | Phase 0 probe evidence, Phase 2 soak + GPU benchmark | Gym IT / owner |
| Floor plan, equipment inventory, occupancy data | Phase 0/5 configuration | Gym management |
| Model weight licences (detector/pose/ReID/face/PAD) | Phase 2/3/4 model wiring + thresholds | Product owner after benchmarks |
| Legal review (PDPL biometric licence, transfer, consent text, signage) | Pilot go-live | Counsel; see `docs/security/privacy-egypt.md` |
| `pg_dump`/`psql` 18 on the dev machine | Local backup drill | Install PostgreSQL 18 client tools and set `PG_TOOLS_BIN` |

## Current work

Nothing in flight. Everything that can run without external resources runs and passes.

## Next engineering tasks (in order)

1. Push to GitHub; confirm all CI jobs green (including `database-embedded` with the backup drill and the Deno check).
2. Create the Supabase project; `supabase db push`; deploy the three functions; run `tests/security/rls.test.ts` and `tests/integration/ingest-smoke.mjs` against it; sign in and click through both apps.
3. Deploy both apps to Vercel (staging first).
4. On the GPU edge node: `pip install -e ".[cv]"`, `edge-ai discover` + `edge-ai probe` on the first authorised camera; commit the JSON under `docs/phase0/`.
5. Phase 2: choose and benchmark the detector (`edge_ai.tools.benchmark`), 10-minute RTSP soak with forced disconnect.
6. Phase 3: choose face embedding + PAD models under acceptable licences; enrol staff volunteers; calibrate `FaceMatcher` thresholds; decide Haar vs YuNet for the entrance camera.
7. Phase 4: ReID weights; homographies; crowded-scene footage.
8. Phase 6: label footage; `evaluate_reps` per exercise; publish precision/recall; tune profiles.
9. Phase 8: 24 h soak with internet cut; third-party review of RLS + ingest; CSP headers; Supabase Vault for device secrets; Playwright E2E against staging.
