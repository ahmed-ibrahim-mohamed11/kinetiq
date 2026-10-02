# MASTER PROMPT — GYM AI PROJECT CONTINUATION

> Provenance: written by the project owner and pasted into the Claude Code session on 2026-10-02.
> This document is **the spec**. `docs/PROJECT_STATE.md` records what actually exists versus what this spec assumes.
> Companion operating instructions / phase table: `docs/PHASE_PLAN.md`.

You are taking over an existing graduation project as the primary coding/research partner. Act as a senior computer-vision/ML engineer, Python systems engineer, full-stack engineer, database architect, and deployment engineer. I want to continue this project by vibe coding, but I do not want uncontrolled rewrites or hidden data leakage. Read every uploaded file before changing code.

## 0. First response behavior
Before implementing anything:
1. Inspect the entire uploaded repository / all uploaded files.
2. Identify the current entry points, dependencies, model files, data files, evaluation scripts, notebooks, duplicated code, and missing pieces.
3. Confirm which file is the current baseline and which files are legacy.
4. Reconstruct the current pipeline from code, not only from this prompt.
5. Create/update these project documents in the repo:
   - docs/PROJECT_STATE.md
   - docs/ARCHITECTURE.md
   - docs/AI_EVALUATION.md
   - docs/CLOUD_MIGRATION.md
   - docs/HARDWARE_SETUP.md
   - CHANGELOG.md
   - TODO.md
6. Give me a concise audit + proposed Phase 0/1 plan, then begin the safest first implementation step unless a missing file genuinely blocks you.

Do not silently invent files, metrics, or APIs that are not present. If something is uncertain, mark it explicitly.

## 1. PROJECT VISION
Build a smart-gym computer-vision platform.
A physical camera watches an exercise station. A local AI service should:
- acquire the camera stream;
- detect/track the athlete;
- estimate body pose;
- derive 3D movement information;
- segment repetitions;
- later analyze exercise form;
- generate rep-level metrics and coach alerts;
- persist data to a cloud database;
- update a live web dashboard;
- optionally drive a later 3D/Unreal visualization.

Current MVP exercise: dual-handed seated row from a rear camera.
Current research priority: robust repetition segmentation.
Form classification is a later phase. Do not conflate rep segmentation with form classification.

## 2. CURRENT RESEARCH DATA
Current dataset contains six real videos:
- correct_01
- correct_02
- correct_03
- asymmetry_01
- asymmetry_02
- asymmetry_03

Each video currently has 5 manually annotated reps, 30 reps total.
Ground-truth file: `manual_rep_annotations.csv`
Expected columns: video, label, rep, start_frame, pull_frame, end_frame

The label column is coach/form ground truth (correct / asymmetry) but MUST NOT be used by the repetition-segmentation model.
The repetition task uses only START / PULL / END annotations.

Important limitation: this is currently leave-one-video-out data, largely one setup/athlete context. It does NOT prove unseen-person generalization. Future validation must become leave-one-person-out when multiple athletes are collected.

## 3. CURRENT AI PIPELINE
Current conceptual pipeline:
```
Video / Camera
  ↓ YOLO11n-Pose
  ↓ COCO-17 2D keypoints
  ↓ COCO-17 → H36M-17 conversion
  ↓ normalization
  ↓ MotionBERT monocular 3D skeleton
  ↓ torso-frame / body-scaled features
  ↓ small ridge phase model
  ↓ left/right phase + fused phase
  ↓ explicit-duration temporal decoder
  ↓ START / PULL / END
  ↓ per-arm timing + symmetry statistics
```
Current pose files are H36M-17 (T,17,3) .npy sequences.
The 3D depth is estimated monocular depth, not calibrated physical depth.

## 4. IMPORTANT DEVELOPMENT HISTORY
Do not repeat earlier failed approaches without a reason.

**4.1 2D segmentation.** An early signal used horizontal elbow span normalized by shoulder width. It worked on some correct videos but failed badly on asymmetry videos.

**4.2 Simple MotionBERT 3D elbow-depth signal.** A relative elbow-depth/state-machine approach produced roughly: correct_01: 5, correct_02: 5, correct_03: 8, asymmetry_01: 4, asymmetry_02: 4, asymmetry_03: 7. So 3D helped but simple percentile/state thresholds were not robust.

**4.3 Left/right separately.** Separate arm counts also disagreed significantly. Averaging was not the only problem.

**4.4 3D elbow angle.** Shoulder-elbow-wrist angle performed worse for timing because wrist pose quality is weak in the rear view. Do not make wrist-dependent elbow angle the primary segmentation feature unless pose quality changes materially.

**4.5 Ridge phase model + old FSM.** A compact ridge model was trained to predict a normalized cycle phase from body-normalized features. The original v1 decoder was a fused-phase FSM with return-to-rest logic.
Nested leave-one-video-out result: GT = 30, Pred = 25, TP = 24, FP = 1, FN = 6, Precision = 0.96, Recall = 0.80, F1 = 0.87.
Timing on matched reps: Start MAE = 9.1 frames, Pull MAE = 3.5 frames, End MAE = 6.5 frames.

**4.6 Peak/prominence experiments.** Local peak detection improved asymmetry_01 but over-segmented correct_03.
Critical evidence: a REAL rep around frame 93 in asymmetry_01 had prominence only 0.0258, while a FALSE movement around frame 406 in correct_03 had stronger prominence (0.1234 left, 0.0589 right). Therefore prominence thresholding cannot be the robust solution.

**4.7 HMM / notebook HSMM / logistic-emission experiments.** A basic HMM improved some cases but merged fast reps and allowed degenerate state durations. A notebook explicit-duration HSMM fixed correct_03 but still merged asymmetry_01 early reps. A logistic-emission HSMM improved PULLING-vs-RETURNING discrimination but did not solve the first fast cycles in asymmetry_01. These experiments are diagnostic history, not the production baseline.

## 5. CURRENT BEST BASELINE: V2 EXPLICIT-DURATION HSMM
The uploaded current best file should be something equivalent to: `rep_segmentation_v2_hsmm.py`
Legacy baseline: `rep_segmentation_v1_fsm.py`
Do not delete either baseline.

The current v2 architecture includes:
- phase model preserved from the research pipeline;
- cross-fitted phase predictions to fit decoder emissions more honestly;
- label-free tempo estimation from phase velocity;
- observations based on phase + velocity;
- explicit PULL and RET duration distributions;
- direct RET -> PULL transition to permit no-pause consecutive reps;
- learned peak-height likelihood;
- censored first/last segments;
- per-arm timing features inside decoded rep windows;
- nested train-only hyperparameter selection.

Current real nested LOVO result with v2: GT = 30, Pred = 28, TP = 27, FP = 1, FN = 3, Precision = 0.96, Recall = 0.90, F1 = 0.93.
Timing: Start MAE = 3.5 frames, Pull MAE = 3.3 frames, End MAE = 6.6 frames.
Per video:
- asymmetry_01: GT 5 / Pred 3 / TP 2 / FP 1 / FN 3 / F1 0.50
- asymmetry_02: 5/5 / F1 1.00
- asymmetry_03: 5/5 / F1 1.00
- correct_01: 5/5 / F1 1.00
- correct_02: 5/5 / F1 1.00
- correct_03: 5/5 / F1 1.00

This is the benchmark you must reproduce before refactoring.

## 6. CURRENT REMAINING AI FAILURE
asymmetry_01 GT:
- Rep1: START=22 PULL=60 END=77
- Rep2: START=81 PULL=93 END=107
- Rep3: START=111 PULL=126 END=140
- Rep4: START=144 PULL=159 END=173
- Rep5: START=184 PULL=193 END=214

Current v2 predictions:
- Pred1: START=51 PULL=66 END=89
- Pred2: START=143 PULL=156 END=174
- Pred3: START=182 PULL=196 END=215

Pred2 and Pred3 match GT Rep4 and Rep5 well. The first three cycles are not correctly segmented. Pred1 is a malformed window spanning part of Rep1 and movement toward Rep2.

Tempo is NOT currently the primary real-data failure. For the held-out asymmetry_01 fold: label-free estimated tempo ≈ 1.17 s/cycle; GT pull spacing is roughly 33, 33, 33, 34 frames; at ~30 fps that is ≈1.1 s/cycle. So the real-data tempo estimate is reasonable. Do not spend the next iteration blindly changing tempo estimation.

**Next AI research task.** Instrument the HSMM for frames around 15–145. For chosen and plausible candidate segmentations, decompose total score into: REST / PULL / RET emission score; duration likelihood; RET -> REST vs RET -> PULL transition contribution; peak-height contribution; smoothing/detrending effect; fused phase and velocity. Compare the chosen wrong segmentation with candidate boundaries around GT pulls 60, 93, 126.
This is a diagnostic failure case only. Do not tune directly on asymmetry_01 after inspecting it. Any new hyperparameter or decoder change must be selected only from training folds and evaluated again with full nested LOVO.

## 7. NON-NEGOTIABLE AI RULES
1. Never hardcode the rep count to 5.
2. Never use correct/asymmetry label in the rep-segmentation model.
3. Never choose thresholds/hyperparameters from the outer held-out video.
4. Do not report metrics from a final model fitted on all six videos as validation.
5. Preserve nested validation.
6. When we get multiple athletes, use leave-one-person-out.
7. Keep rep segmentation independent from form classification.
8. Keep per-arm timing so left/right differences can later become form features.
9. Do not overfit to the current six videos.
10. Prefer compact models while data is small.
11. Record every behavior-changing change in CHANGELOG.md.
12. If a change improves one failure case, rerun all folds before calling it an improvement.

## 8. FORM ANALYSIS — FUTURE PHASE
Do not train this yet unless the uploaded repo already contains a validated implementation.
Future form-analysis plan: segment reps first; calculate per-rep features; classify coach-defined errors only after rep boundaries are stable; collect multiple athletes; make correct/incorrect labels balanced enough to avoid shortcuts; explicitly control for tempo so the classifier does not learn "fast = asymmetry"; use leave-one-person-out evaluation; report macro-F1, class recall, confusion matrix, and confidence calibration where appropriate.
Potential later features: left/right pull timing lag; onset lag; amplitude ratio; elbow trajectory asymmetry; trunk motion / lean; range-of-motion features; velocity consistency; rep duration / pull-return ratio as context, not as a shortcut label.
Do not claim medical diagnosis or guaranteed injury prevention. Use language such as "movement/form deviation" or "potential biomechanical concern requiring coach review."

## 9. CRITICAL ARCHITECTURE DECISION: EDGE AI + CLOUD DATA PLANE
I want a website and online database, but the heavy continuous AI should NOT be moved into the Vercel website. The physical camera is local. The recommended production architecture is:
```
USB / RTSP / IP Camera
        ↓
LOCAL EDGE AI SERVICE (Python): capture, YOLO, MotionBERT / 3D, rep segmentation, later form analysis
        ↓ structured events over HTTPS
SUPABASE: Postgres, Auth, Realtime, Storage (optional evidence)
        ↓
NEXT.JS DASHBOARD
        ↓
VERCEL
```
The website/cloud is the control/data/visualization plane. The edge computer is the camera/inference plane. Do not upload continuous raw video to the cloud for the MVP unless explicitly required. Prefer compact rep/session/heartbeat events.

## 10. REAL-TIME MIGRATION WARNING
The current research path is not automatically a true live streaming implementation. Inspect for these issues explicitly:
- zero-phase sosfiltfilt / non-causal filtering uses future frames;
- MotionBERT overlapping windows may require future context;
- segmental Viterbi / HSMM may decode an entire sequence instead of incrementally;
- research scripts assume complete video arrays.

Therefore create TWO MODES:
- **A. OFFLINE_EVAL** — Preserve the exact scientific evaluation pipeline. Used for nested LOVO/LOPO, reproducible metrics, research comparisons. Never silently change it to make real-time engineering easier.
- **B. REALTIME_EDGE** — Streaming production implementation. May use causal filtering; sliding buffers; incremental or chunked 3D inference; delayed rep finalization; online decoder / periodic re-decode of a bounded buffer. Benchmark accuracy and latency separately from OFFLINE_EVAL.

## 11. EDGE AGENT REQUIREMENTS
Create a long-running Python edge service, not a notebook. It should eventually contain:
- **CameraSource abstraction** — webcam / USB camera index; RTSP/IP URL later. Required: reconnect on failure; timestamps; measured FPS; bounded queue/buffer; health status.
- **Model lifecycle** — load YOLO/MotionBERT/models once; do not reload per frame; expose active model version; warm up if useful; fail clearly if model artifact is missing.
- **Session lifecycle** — start session; end session; assign machine/device/session IDs; maintain rep index; emit each completed rep exactly once.
- **Offline-first cloud queue** — Internet loss must not stop local inference. On-disk queue / SQLite outbox: append event locally; send when online; mark acknowledged only after cloud success; retry with backoff; idempotency key / stable event UUID to prevent duplicates.
- **Heartbeat** — periodically send device ID; camera OK; capture FPS; inference FPS; end-to-end latency; queue depth; model version; agent version; last error.

## 12. WEB APPLICATION TARGET
Modern stable Next.js + TypeScript, deployed to Vercel, clean responsive dashboard. Required pages:
- `/login` — coach/admin authentication.
- `/dashboard` — live current station/session: device online/offline; camera online/offline; machine; current exercise; session elapsed time; live rep count; current movement state; last completed rep; pull/return/total duration; left/right pull lag; onset lag; amplitude ratio; latest form result later; latest alert; edge FPS / latency; model version.
- `/sessions` — historical session list; filters: date; machine; device; exercise; athlete/session reference.
- `/sessions/[id]` — rep-by-rep detail; charts/tables: rep index; start/pull/end; duration; pull duration; return duration; tempo; lag_pull; lag_onset; amp_ratio; peak; peak_z; later form class/confidence; alert flag.
- `/analytics` — total reps; average durations; symmetry metrics; alert rate; model version comparisons; per-session distributions.
- `/devices` — online/offline; last_seen; camera status; edge version; model version; FPS; latency; queue depth.
- `/alerts` — coach review queue.
- `/models` — model version registry and evaluation metrics.
- `/settings` — gym/machine/device mappings and retention/configuration.

## 13. SUPABASE REQUIREMENTS
Use Supabase for Postgres; Auth; Realtime; Storage for optional private evidence assets. Use SQL migrations committed in the repository. Do not create an undocumented schema only by clicking the dashboard.

Suggested tables:
- **organizations**: id, name, created_at
- **profiles**: user_id, organization_id, display_name, role
- **gyms**: id, organization_id, name, timezone
- **machines**: id, gym_id, name, exercise_type, location_label
- **devices**: id, gym_id, name, status, last_seen_at, agent_version, model_version_id
- **cameras**: id, device_id, machine_id, source_type, source_label, enabled, metadata_json — do NOT store RTSP credentials in plain columns.
- **sessions**: id, machine_id, device_id, athlete_ref nullable, started_at, ended_at, total_reps, status
- **reps**: id, event_uuid unique, session_id, rep_index, start_frame, pull_frame, end_frame, started_at / pull_at / ended_at as useful, duration_ms, pull_duration_ms, return_duration_ms, peak, peak_z, pull_left_frame, pull_right_frame, lag_pull_ms, lag_onset_ms, amp_ratio, model_version_id, created_at. Unique (session_id, rep_index) and/or event UUID for idempotency.
- **form_results**: rep_id, model_version_id, class, score/confidence, feature_json, coach_review fields
- **alerts**: session_id, rep_id nullable, alert_type, severity, message, status, created_at, reviewed_at
- **device_heartbeats**: device_id, ts, camera_ok, capture_fps, inference_fps, latency_ms, queue_depth, error_code. Plan retention/downsampling so this table does not grow without limit.
- **model_versions**: id, name, semantic_version, task, git_commit, metrics_json, artifact_uri, active, created_at
- **evidence_assets**: rep_id / alert_id, storage_path, kind, created_at, retention_until

## 14. SUPABASE SECURITY
Non-negotiable:
- Enable Row Level Security on user-facing tables.
- Never put the Supabase service-role key in the browser.
- Use Auth for dashboard users.
- Scope data by organization/gym.
- Edge device should have its own authenticated identity or call a secured ingestion endpoint.
- Secrets belong in environment variables/secret stores, not Git.
- Storage evidence bucket should be private.
- Use signed URLs / authorized access for evidence media.
- Add RLS tests.
For live UI, prefer a design where durable DB writes are the source of truth and Supabase Realtime/Broadcast updates the browser.

## 15. EVENT CONTRACT
Create a versioned schema shared between Python and TypeScript. Example:
```json
{
  "schema_version": "1.0",
  "event_type": "rep_completed",
  "event_uuid": "uuid",
  "device_id": "uuid",
  "session_id": "uuid",
  "machine_id": "uuid",
  "exercise": "seated_row",
  "rep_index": 4,
  "frames": { "start": 143, "pull": 156, "end": 174 },
  "metrics": {
    "duration_ms": 0, "pull_duration_ms": 0, "return_duration_ms": 0,
    "pull_left_frame": 158, "pull_right_frame": 157,
    "lag_pull_ms": -33.5, "lag_onset_ms": 0, "amp_ratio": 0.63,
    "peak": 0.57, "peak_z": -1.34
  },
  "model_version": "rep-seg-v2-hsmm",
  "agent_version": "edge-agent-0.1.0"
}
```
Other event types: session_started, session_ended, device_heartbeat, camera_error, alert_created, model_loaded. Use schema versioning.

## 16. REPOSITORY TARGET STRUCTURE
```
gym-ai/
├─ ai/ { research/, core/, edge_agent/, models/README.md, tests/ }
├─ web/
├─ supabase/ { migrations/, seed.sql, functions/ }
├─ shared/event-schemas/
├─ docs/ { PROJECT_STATE.md, ARCHITECTURE.md, AI_EVALUATION.md, CLOUD_MIGRATION.md, HARDWARE_SETUP.md }
├─ .env.example, CHANGELOG.md, TODO.md, README.md
```
Do not force this exact tree if the existing repository already has a good structure. Adapt rather than destroy. Before moving code, add tests / a reproducible command that preserves current v2 metrics.

## 17. IMPLEMENTATION ROADMAP
Proceed in phases and keep the repo runnable after each phase.
- **Phase 0 — Audit + freeze baseline**: read everything; reproduce current v2 nested LOVO; save baseline metrics; identify exact dependency versions; preserve v1/v2 scripts; add project-state documentation. Done when a single documented command reproduces the baseline.
- **Phase 1 — Clean AI package without behavior changes**: extract reusable pose / phase / decoder / evaluation modules; add config/logging; add unit tests; prove metrics unchanged. No algorithmic improvement yet unless needed to fix a refactor regression.
- **Phase 2 — Diagnose remaining asymmetry_01 failure**: add score decomposition tooling; compare chosen vs candidate segmentation; determine whether failure comes from emissions, duration, transition prior, peak term, smoothing, or another term; any proposed change must be trained/selected on training folds only; rerun full nested LOVO. Do not optimize for "6/6 videos exactly 5 reps."
- **Phase 3 — Supabase foundation**: migrations; Auth; RLS; schema; seed data; model/device/session/reps tables; generated TypeScript types if appropriate; local dev documentation.
- **Phase 4 — Website with simulated data**: build the dashboard before hardware integration; simulator that emits realistic rep/session/heartbeat events; deploy to Vercel.
- **Phase 5 — Edge agent**: camera adapter; inference loop; session lifecycle; local outbox queue; event uploader; heartbeat; structured logging; graceful shutdown/restart.
- **Phase 6 — End-to-end cloud connection**: one device; one camera; one seated-row station; rep event reaches Supabase; dashboard updates live; no duplicate reps after retries; camera/network failures are visible.
- **Phase 7 — Real-time AI production path**: causal/streaming equivalents where needed; measure capture FPS, inference FPS, end-to-end event latency, rep segmentation difference vs offline evaluator.
- **Phase 8 — Multi-athlete dataset + form analysis**: only after rep segmentation + platform are stable.
- **Phase 9 — Multi-machine / multi-person**: later.
- **Phase 10 — Unreal / richer 3D visualization**: later and independent of core AI correctness.

## 18. PROPOSED QUALITY GATES
These are engineering goals, not current claims.
- **Rep segmentation** (initial future LOPO gate): pooled F1 ≥ 0.90 on held-out people; stretch ≥ 0.95; pull MAE ≤ 5 frames around 30 fps; report per-person results; test clips with rep counts not equal to 5.
- **Real-time edge**: stable full-session run; no unbounded memory growth; bounded queue; recovers from camera disconnect; local inference continues during internet outage; queued events replay without duplication.
- **Cloud**: RLS isolation tests pass; browser never receives service-role credentials; rep visible on dashboard within a few seconds under normal network; model/device version is attached to results.

## 19. DEVELOPMENT STYLE I WANT FROM YOU
I am continuing this by vibe coding with you, so make the project easy to resume. For each meaningful change:
1. State the goal. 2. Name the exact files you will change. 3. Make the smallest coherent change. 4. Run relevant tests/evaluation. 5. Show the result. 6. Update docs / changelog. 7. Tell me the next recommended step.
Do not dump dozens of unrelated changes into one step. Do not rewrite working modules merely for style. Prefer explicit, maintainable code over cleverness. Keep configuration out of magic constants when it is truly deployment configuration. But do not turn research hyperparameters into user-editable production knobs unless scientifically justified.

## 20. WHAT I EXPECT YOU TO DELIVER THROUGHOUT THE PROJECT
Eventually the repository should contain: reproducible offline AI evaluation; production edge service; hardware/camera setup docs; Supabase migrations/RLS; Vercel-ready web dashboard; simulated event generator; end-to-end integration tests; model version registry; monitoring/heartbeat UI; project architecture docs; final graduation-project metrics and limitations.

## 21. IMPORTANT LANGUAGE / CLAIMS
When documenting the project:
- say "estimated monocular 3D pose," not true measured depth;
- say "movement/form deviation" or "potential biomechanical concern," not medical diagnosis;
- do not claim injury prevention unless evidence supports it;
- distinguish CURRENT RESULT from TARGET RESULT;
- distinguish offline research accuracy from real-time production behavior;
- explicitly state when evaluation is same-athlete LOVO vs new-person LOPO.

## 22. START NOW
Read all uploaded files first. Then respond with: 1. Repository audit — what is actually present. 2. Current pipeline reconstructed from code. 3. Baseline reproduction command and expected metrics. 4. Any contradictions between this prompt and the files. 5. Phase 0 implementation plan. 6. Then begin the first safe change, preserving the current baseline.
Do not skip the audit and do not start by creating a brand-new project disconnected from the existing AI code.
