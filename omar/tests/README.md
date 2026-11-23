# Tests

| Suite | Location | Runs where | Needs |
| --- | --- | --- | --- |
| Contract & validation unit tests | `packages/*/test` | CI `web` job, `npm run test:unit` | nothing |
| Notification worker unit tests | `services/notification-worker/test` | CI `web` | nothing |
| Static security checks (secret scan, RLS coverage, revokes) | `tests/security/secrets-scan.test.ts` | CI `web` | nothing |
| **Database on embedded PostgreSQL — no Docker** (all migrations + seed; ingest engine; RLS via role switching; notification/retention RPCs; **edge→cloud e2e** with the real Python pipeline; backup/restore drill) | `tests/db/*.test.ts` | CI `database-embedded` job, `npm run test -w tests/db` | nothing (binaries downloaded by `embedded-postgres`); the drill needs `pg_dump`/`psql` 18 (`PG_TOOLS_BIN` or `tests/db/.pgtools/bin`), otherwise it is reported as skipped |
| **RLS / authorization through PostgREST + Auth** | `tests/security/rls.test.ts` | CI `database` job | local Supabase (`supabase start`) |
| Ingest smoke (signature, idempotency, replay, finalisation, report) | `tests/integration/ingest-smoke.mjs` | CI `database` | local Supabase + `functions serve` |
| Edge unit + simulation tests | `services/edge-ai/tests` | CI `edge`, `pytest` | Python 3.10+ |
| Computer-vision evaluation | `services/edge-ai/edge_ai/tools/evaluate_reps.py`, protocol below | manual, Phase 4/6 gates | labelled footage |
| Performance benchmark | `services/edge-ai/edge_ai/tools/benchmark.py` | manual, Phase 2 gate | GPU host, model |
| E2E (Playwright) | `tests/e2e` | Phase 7 | deployed preview |

## Regenerating database types

`npm run gen:types -w tests/db` starts the embedded PostgreSQL, applies the migrations and runs `supabase gen types typescript --db-url …` into `packages/supabase-client/src/types/database.ts`. `-- --check` fails when the committed file drifts from the migrations.

## Computer-vision evaluation protocol (`tests/computer-vision`)

1. Record authorised footage with consent from everyone visible; keep it on the edge host only.
2. Export per-track joint-angle signals from the pipeline (debug flag to be added in Phase 4) to CSV: `t,value,confidence`.
3. Two labellers mark `label_rep_end=1` at each completed rep (video scrubbing); adjudicate disagreements.
4. Run `python -m edge_ai.tools.evaluate_reps --profile <slug> --csv <file>` per exercise and camera; record precision/recall/F1/count error.
5. Acceptance (Phase 6 gate, per exercise): precision ≥ 0.97, recall ≥ 0.95, |count error| ≤ 1 per 20 reps, measured on ≥ 100 labelled reps across ≥ 5 members. Numbers below the bar block the exercise from the pilot catalogue — they do not get rounded up.
6. Cross-camera identity: zero wrong-member attributions on the labelled set; report recall separately.

## Performance protocol (`tests/performance`)

`python -m edge_ai.tools.benchmark --detector models/<chosen>.onnx --streams N --seconds 60` for N = 2, 4, 6, 8 on the target host. Acceptance (Phase 2 gate): per-stream inference ≥ 10 fps and p95 latency ≤ 400 ms at the planned camera count, GPU memory ≤ 80 %. Also run a 10-minute real-RTSP soak with one forced camera disconnect (pull the cable) and one forced internet outage (block 443) — the edge must reconnect and drain the queue with zero duplicate reps (`select count(*) from repetition_events` equals the admitted count in `/healthz`).
