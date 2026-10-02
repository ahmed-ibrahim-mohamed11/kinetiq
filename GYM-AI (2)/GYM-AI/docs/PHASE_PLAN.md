# PHASE_PLAN.md — operating plan

> Provenance: project owner's operating prompt, pasted 2026-10-02. Spec = `docs/MASTER_PROMPT.md`. Live status = `docs/PROJECT_STATE.md` + `TODO.md`.

| Stage | What we build | Done when |
|---|---|---|
| 0. Recover or rebuild the AI baseline | Either restore the v2 HSMM files, or rebuild the 3D pipeline (MotionBERT → phase model → HSMM). Set up git and one evaluation script. | One command gives honest rep-detection scores |
| 1. Clean up the code | Turn the scattered scripts into a proper package, with tests | Scores are identical to stage 0 |
| 2. Fix the remaining failure | Find out why asymmetry_01's first reps are missed, without cheating on that video | Full validation run is rerun |
| 3. Online database | Supabase tables, login, per-organization data security | Security tests pass |
| 4. Website | Next.js dashboard driven by fake data, deployed to Vercel | Live dashboard updates |
| 5. Edge agent | Python service that reads the camera, counts reps and sends events, with an offline queue | Runs for a full session |
| 6. End-to-end | A video file acts as the camera; reps appear live on the website | No duplicate reps after a network cut |
| 7. Real-time mode | Streaming version of the AI, with speed measured | FPS and latency are reported |
| 8. Form analysis | Left/right symmetry measures plus rule-based "coach review" alerts. No classifier yet, because 6 videos of one person isn't enough. | Alerts show on the dashboard |
| 9. Graduation deliverables | README, results with their limitations, architecture diagram, demo | Ready to present |

## Step 0: AI baseline path (asked once)
Question: "Do you have the v2 HSMM files (scripts/notebook, MotionBERT checkpoint, .npy pose files) somewhere?"
- **Path A, files provided**: copy into `ai/research/`, reproduce the claimed v2 nested LOVO result (GT 30 / Pred 28 / TP 27 / FP 1 / FN 3 / F1 0.93 / MAE 3.5, 3.3, 6.6). If it does not reproduce, report the real numbers. Never fake a match.
- **Path B, files lost**: rebuild the research pipeline from MASTER_PROMPT §3–5: YOLO11n-pose → COCO-17→H36M-17 → normalization → MotionBERT (official repo + pretrained checkpoint, estimated monocular 3D) → torso-frame features → compact ridge phase model (left/right + fused) → explicit-duration HSMM decoder (PULL/RET duration distributions, direct RET→PULL, censored first/last segments, per-arm timing). Nested LOVO with train-only hyperparameter selection. Whatever numbers come out become the new honest baseline. Don't try to "hit 0.93".
- No answer → Path B, stated at the top of the report.

## Phase definitions (each ends with tests passing + docs/CHANGELOG updated + a short report)
- **Phase 0 — Freeze.** git init; .gitignore (.venv, *.mp4, *.pt, *.pth, *.bin, large *.npy); commit current state. requirements.txt with exact versions. `ai/research/eval_rep_segmentation.py` as the single evaluator: TP/FP/FN, P/R/F1, START/PULL/END MAE, per video. Default matching = one-to-one PULL within ±15 frames; rule recorded in `docs/AI_EVALUATION.md`. Run on the legacy 2D baseline first (sanity), then on the Path A/B pipeline. Save `ai/outputs/baselines/*.json`. Done when ONE documented command reproduces the baseline.
- **Phase 1 — Clean package, no behaviour change.** Move legacy 2D scripts to `ai/research/legacy_2d/`. Extract `ai/core/` (pose, lifting, features, phase model, decoder, evaluation), config, logging, pytest unit tests. Metrics byte-identical to Phase 0.
- **Phase 2 — Diagnose the remaining failure.** Score-decomposition tooling: emission, duration, transition, peak term, smoothing. Changes selected on training folds only, followed by a full nested LOVO rerun. Never tune on the held-out video.
- **Phase 3 — Supabase.** Migrations in `supabase/migrations/` for MASTER_PROMPT §13 tables. RLS on all user-facing tables, scoped by organization. RLS tests, seed.sql, .env.example. Shared versioned event schema in `shared/event-schemas/` (JSON Schema + generated Python pydantic + TypeScript types).
- **Phase 4 — Dashboard on simulated data.** Next.js + TypeScript in `web/`. Pages: /login, /dashboard (live), /sessions, /sessions/[id], /analytics, /devices, /alerts, /models, /settings. Realtime via Supabase. Event simulator. Vercel-ready. Service-role key never in browser code.
- **Phase 5 — Edge agent** (`ai/edge_agent/`, long-running). CameraSource (USB index, file, RTSP) with reconnect, timestamps, measured FPS, bounded queue. Models loaded once. Session lifecycle. Each rep emitted exactly once. SQLite outbox with stable event_uuid, retry/backoff, ack only on cloud success. Heartbeat. Graceful shutdown.
- **Phase 6 — End-to-end.** Video file as fake camera. Rep events reach Supabase, dashboard updates live, no duplicates after a simulated outage, camera failures visible. Integration test.
- **Phase 7 — REALTIME_EDGE mode.** Causal filters, sliding buffer, chunked 3D, delayed rep finalisation or bounded re-decode. OFFLINE_EVAL untouched. Measure capture FPS, inference FPS, end-to-end latency, rep difference vs offline.
- **Phase 8 — Form analysis, honest scope.** Per-rep symmetry features (lag_pull, lag_onset, amp_ratio, trunk lean, ROM) inside decoded rep windows. Rule-based "potential deviation, coach review" alerts with thresholds documented as provisional. NO classifier on 6 videos from one athlete; document that multi-athlete LOPO data is required.
- **Phase 9 — Graduation deliverables.** README with setup/run commands, `docs/FINAL_RESULTS.md` (current vs target, LOVO = same athlete ≠ LOPO, estimated 3D ≠ measured depth, no medical claims), architecture diagram, demo script (video file → edge agent → live dashboard).

## Non-negotiable rules
- Never hardcode the rep count to 5. Never feed the correct/asymmetry label to rep segmentation.
- Never pick thresholds or hyperparameters from the outer held-out video. Never report a model fitted on all 6 videos as validation.
- Rep segmentation stays independent from form analysis. Keep per-arm timing.
- No invented files, metrics or APIs. Mark uncertainty explicitly.
- Every behaviour-changing change goes in CHANGELOG.md. Keep TODO.md current.
- Secrets go in env files only. Never commit keys.

## Working style
For each step: state the goal, name the files, smallest coherent change, run tests/eval, show the result, update docs, recommend the next step. Commit after each green step. Stop and ask only for: (a) Step 0, (b) things only the owner can provide (Supabase URL/keys, Vercel account, camera/hardware details, MotionBERT checkpoint if download is blocked), (c) irreversible actions.

## Owner provides along the way
Answer on the v2 files (Step 0) · a free Supabase project (URL + keys) at Phase 3 · a Vercel account at Phase 4 · camera and edge-computer details before Phase 5.
