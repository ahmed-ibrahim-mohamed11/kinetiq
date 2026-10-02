You are my senior engineering partner for my graduation project GYM-AI, a smart-gym computer-vision platform. You work inside this repository on my Windows machine (Python 3.10 venv at .venv). Finish the project end to end, in phases, keeping the repo runnable after every phase.

## Read first (mandatory, before any change)
1. docs/MASTER_PROMPT.md: full vision, rules, history, target architecture. It is the spec.
2. docs/PROJECT_STATE.md, docs/AI_EVALUATION.md, TODO.md: verified audit of what actually exists.
3. Every file in ai/src/ and ai/outputs/*.csv.

Key fact from the audit: the folder contains only a 2D prototype (YOLO11n-pose, elbow-span signal, percentile FSM). The v2 HSMM, v1 FSM, ridge phase model, MotionBERT code/weights and H36M .npy files described in the master prompt are NOT in the repo. motionbert/ and ai/models/ are empty.

## Step 0: Decide the AI baseline path (ask me once, then proceed)
Ask me: "Do you have the v2 HSMM files (scripts/notebook, MotionBERT checkpoint, .npy pose files) somewhere?"
- **Path A, files provided:** copy them into ai/research/, reproduce the claimed v2 nested LOVO result (GT 30 / Pred 28 / TP 27 / FP 1 / FN 3 / F1 0.93 / MAE 3.5, 3.3, 6.6). If it does not reproduce, report the real numbers. Never fake a match.
- **Path B, files lost:** rebuild the research pipeline from the master prompt sections 3–5. Steps: YOLO11n-pose, COCO-17 to H36M-17, normalization, MotionBERT (official repo + pretrained checkpoint, estimated monocular 3D), torso-frame features, compact ridge phase model (left/right + fused), explicit-duration HSMM decoder (PULL/RET duration distributions, direct RET to PULL, censored first/last segments, per-arm timing). Use nested LOVO with train-only hyperparameter selection. Whatever numbers come out become the new honest baseline. Don't try to "hit 0.93".

If I don't answer, do Path B, and say so at the top of your report.

## Phases (each ends with tests passing + docs/CHANGELOG updated + a short report to me)
**Phase 0: Freeze.** `git init`, a .gitignore (.venv, *.mp4, *.pt, *.pth, *.bin, large *.npy), commit the current state. requirements.txt with exact versions. Write ai/research/eval_rep_segmentation.py as the single evaluator: TP/FP/FN, P/R/F1, START/PULL/END MAE, per video. Default matching = one-to-one PULL within ±15 frames; record the rule in docs/AI_EVALUATION.md. Run it on the legacy 2D baseline first (sanity), then on the Path A/B pipeline. Save ai/outputs/baselines/*.json. Done when ONE documented command reproduces the baseline.

**Phase 1: Clean package, no behavior change.** Move the legacy 2D scripts to ai/research/legacy_2d/. Extract ai/core/ (pose, lifting, features, phase model, decoder, evaluation), config, logging, pytest unit tests. Prove the metrics are byte-identical to Phase 0.

**Phase 2: Diagnose the remaining failure** (e.g. asymmetry_01 early reps). Build score-decomposition tooling: emission, duration, transition, peak term, smoothing. Any change is selected on training folds only and followed by a full nested LOVO rerun. Never tune on the held-out video.

**Phase 3: Supabase.** SQL migrations in supabase/migrations/ for the tables in master prompt section 13. RLS on all user-facing tables, scoped by organization. RLS tests, seed.sql, .env.example. Shared versioned event schema in shared/event-schemas/ (JSON Schema, plus generated Python pydantic and TypeScript types).

**Phase 4: Dashboard on simulated data.** Next.js + TypeScript in web/. Pages: /login, /dashboard (live), /sessions, /sessions/[id], /analytics, /devices, /alerts, /models, /settings. Realtime updates via Supabase. Event simulator script that emits realistic sessions/reps/heartbeats. Vercel-ready. Service-role key never in browser code.

**Phase 5: Edge agent** (ai/edge_agent/, long-running service). CameraSource (USB index, file, RTSP) with reconnect, timestamps, measured FPS and a bounded queue. Models loaded once. Session lifecycle. Each rep emitted exactly once. SQLite outbox with stable event_uuid, retry/backoff, ack only on cloud success. Heartbeat. Graceful shutdown.

**Phase 6: End-to-end.** Use a video file as a fake camera. Rep events must reach Supabase, the dashboard must update live, there must be no duplicates after a simulated network outage, and camera failures must be visible. Write an integration test.

**Phase 7: REALTIME_EDGE mode.** Causal filters, sliding buffer, chunked 3D, delayed rep finalization or bounded re-decode. OFFLINE_EVAL stays untouched. Measure capture FPS, inference FPS, end-to-end latency, and the rep difference vs offline.

**Phase 8: Form analysis, honest scope.** Compute per-rep symmetry features (lag_pull, lag_onset, amp_ratio, trunk lean, ROM) inside decoded rep windows. Add rule-based "potential deviation, coach review" alerts with thresholds documented as provisional. Do NOT train a classifier on 6 videos from one athlete. Document that LOPO data with multiple athletes is required.

**Phase 9: Graduation deliverables.** README with setup/run commands, docs/FINAL_RESULTS.md (current vs target results, LOVO = same athlete ≠ LOPO, estimated 3D ≠ measured depth, no medical claims), an architecture diagram, and a demo script (video file → edge agent → live dashboard).

## Non-negotiable rules
- Never hardcode the rep count to 5. Never feed the correct/asymmetry label to rep segmentation.
- Never pick thresholds or hyperparameters from the outer held-out video. Never report a model fitted on all 6 videos as validation.
- Rep segmentation stays independent from form analysis. Keep per-arm timing.
- No invented files, metrics or APIs. Mark uncertainty explicitly.
- Every behavior-changing change goes in CHANGELOG.md. Keep TODO.md current.
- Secrets go in env files only. Never commit keys.

## Working style
For each step: state the goal, name the files you'll touch, make the smallest coherent change, run the tests/eval, show the result, update the docs, recommend the next step. Commit after each green step with a clear message. Stop and ask me only for:
(a) Step 0,
(b) things only I can provide (Supabase project URL/keys, Vercel account, camera hardware details, MotionBERT checkpoint download if blocked),
(c) irreversible actions.
Otherwise keep going phase by phase.

Start now: read the files listed above, give me a 10-line audit confirmation, then ask the Step 0 question.
