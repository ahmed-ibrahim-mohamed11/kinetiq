# PROJECT_ROADMAP.md — evidence-driven roadmap (owned by the technical lead; updated when results demand)

_Last update: 2026-10-10. History of superseded plans: PHASE_PLAN.md (original), DECISION_LOG.md.
Current scope is the owner's 2026-10-09 master prompt: Phases 0–5 now, 6 after fresh-athlete
recordings arrive, 7 (defense package) after that; DB/UI/deployment DEFERRED to project closing._

## Current phase status (2026-10-09 master-prompt scope)
| phase | status |
|---|---|
| 0 — Audit & lock baseline | **DONE** (`35eccfb`) |
| 1 — Data acquisition | **DONE** (`13a8034`) |
| 2 — Model training/improvement | **DONE** (P2.1–P2.5 all executed and decided; see EXPERIMENT_REGISTRY.md) |
| 3 — Robustness & safety of output | **DONE** (P3.0–P3.7; see docs/ROBUSTNESS_REPORT.md) |
| 4 — Live/realtime mode | **DONE**: components (`6955cf5`); measured on AC: GPU `cuda_V2` 1.31× (IoU F1 0.783), CPU-only `cpu_V3` 1.07× (0.8125), both for 148 s streams; session-length limit R-17 (LIVE_MODE.md) |
| 5 — AI service (local) | **DONE** except the Docker build, **DEFERRED to the closing phase** (owner decision 2026-10-10: Docker is installed then): artifact + Mode A + schema (`d91d6a4`), service + privacy (`a72719e`), reproducibility + regression tests + boundaries (`a73cc8f`), fresh-clone verification (`9e91249`) |
| 6 — Final unseen-athlete validation | **Pre-registered and executable** (docs/PHASE6_PREREGISTRATION.md; one command `python -m ai.scripts.phase6_evaluate`; candidate frozen at tag `ai-core-candidate-v1`); intake/annotation tooling: next step, before any recording; BLOCKED on the owner's recordings |
| 7 — Defense package | NOT STARTED |
| DB / UI / deployment | **DEFERRED** (project closing, per owner instruction) |

## What the closing phases will need (prepared, not started)
Closing phases start only on the owner's "START CLOSING PHASE". The AI side exposes stable
interfaces for them; this list is what they must supply or decide.
- **Database (Supabase)**: owner-provided project and keys (service-role key server-side only);
  apply and review the existing migrations + RLS (`supabase/`) and run its RLS tests against the
  live project; store results as `AnalysisResult` v1.0 (JSON Schema
  `ai/schemas/analysis_result.v1.json`, keyed by `schema_version`) and/or `rep_completed` edge
  events (`ai.core.schema.to_edge_events`, contract `shared/event-schemas` v1.0); implement the
  retention, erasure, export and consent requirements in docs/PRIVACY.md.
- **UI (Next.js on Vercel)**: generate types from the JSON Schema. Show abstention statuses as
  answers, not errors. Label form indicators as coach-attention prompts with a HEURISTIC
  confidence (not a probability). Display the standing warnings every result carries.
- **Deployment**: authentication in front of the AI service (it has none, localhost only); hardware
  sizing from Phase 4 measurements (docs/LIVE_MODE.md); install Docker, then build, run and
  digest-pin the image (deferred here by owner decision 2026-10-10); YOLO is licensed for
  **academic / non-commercial use only** (owner decision 2026-10-10) — a public or commercial
  deployment needs a new decision (Enterprise licence or detector swap) first; keep Ultralytics
  telemetry off (`YOLO_OFFLINE=1`; the product detector already sets it); upload
  limits, HTTPS, encryption at rest, log policy (docs/AI_SERVICE.md, PRIVACY.md).
- **Live mode placement**: in-process on an edge device next to the camera
  (`ai.core.live.LiveStreamer`) vs a server streaming endpoint. Decide after P4.5 (DECISION_LOG
  2026-10-10).

## Done (validated, committed, frozen)
- **AI foundation**: honest baselines (Phase 0–2, tag `baseline-v0.2.0-frozen`), dataset audit +
  443-rep verified benchmark (2.5A/B), 6-pipeline pose benchmark (2.5C), 2,472-rep / 24-subject
  dataset + frozen subject split (2.6), frozen-backbone training with locked unseen-subject TEST
  (2.7), seated-row zero-shot transfer (2.8): **MB-Lite product primary / PFv2 in-domain
  benchmark primary**.
- **Phase A–C**: canonical result schema (root-cause fix), unseen-athlete dataset gate,
  form-analysis v1 (interpretable indicators; rom_proxy directional AUC 0.778; exercise-scoped
  thresholds; seated-row set PROVISIONAL).
- **External wild stress test (Domain B, commit bc3472e)**: frozen MB-Lite zero-shot on curated
  Instagram units → **PARTIAL EXTERNAL TRANSFER** (pull F1 0.842 / IoU 0.632 over 10 certain
  reps; window-extent degradation on paused tempo; one rear-view miss; editing/cuts are the
  dominant wild obstacle). docs/EXTERNAL_REAL_WORLD_STRESS_TEST.md.
- **Backend groundwork** (code-complete, undeployed): Supabase schema + RLS + seed + RLS tests;
  shared event contract v1.0 (JSON Schema + pydantic + TS, sync-tested).

## Now (2026-10-10)
- Phases 0–5 are at DoD as far as possible without owner input (Docker build excepted).
- **Owner inputs pending**:
  - fresh unseen-athlete recordings: CRITICAL for any production claim (spec
    UNSEEN_ATHLETE_DATASET_SPEC.md, incl. ≥1 slow/paused-tempo set and a rear view);
  - ~~YOLO AGPL decision~~ — decided 2026-10-10: academic / non-commercial only;
  - ~~Docker install~~ — deferred to the closing phase (owner decision 2026-10-10).

  Supabase/Vercel credentials are NOT requested: DB/UI/deploy are deferred by scope.
- Optional engineering while waiting (no owner input needed): sliding decode buffer for long live
  streams (R-17), under the P4.3 accuracy gate.
- _Superseded 2026-10-10_: the earlier "Now" asked for Supabase keys + a Vercel account; that is
  closing-phase work under the current scope.

## Done 2026-10-08 (was "Now")
- **Realtime/causal feasibility**: causal audit + streaming prototype (V1/V2/V3). V2 (5.4 s
  look-ahead): F1 0.806 vs offline 0.841, 44/47 offline reps reproduced, rep events ~5 s after
  rep end. Compute on CPU laptop: 0.26–0.66× realtime (YOLO 88 ms/frame dominant) → live demo
  needs GPU or budget mode; after-set processing feasible today. docs/REALTIME_FEASIBILITY.md.

## Next (ordered; reorder only with evidence)
1. **Fresh-athlete validation gate** the moment recordings arrive: validate dataset → freeze →
   single locked evaluation against pre-registered criteria (either outcome reported).
2. **Integration restart (graduation spine)**: deploy Supabase (needs owner keys) → minimal coach
   dashboard (Next.js) on simulated events → edge agent (video-file camera, MB-Lite offline
   path, outbox) → end-to-end demo: video → reps+indicators → DB → dashboard.
3. **Live-demo engineering** per owner hardware decision: GPU port of YOLO+MB-Lite, incremental
   tracker/decode, sliding decode buffer (design already validated; accuracy gates = match V2
   numbers on dev videos).
4. **Exercise library step** (after 1–3): bent-over row + seated row + bicep curl as the
   3-exercise demo set (curl needs per-exercise phase model — evidence: transfer F1 ≤ 0.65);
   exercise recognition gate only after modules are reliable.
5. **Final validation on fresh athletes** + graduation deliverables (FINAL_RESULTS,
   architecture, demo script).
- **Unreal visualization**: optional after 2; consumes stored 3D sequences; never a dependency.

## Deferred / rejected for now
- Pose-backbone fine-tuning (frozen representations suffice; revisit only if form-analysis
  features prove pose-limited). Multi-person production tracking (single-athlete first; event
  schema already carries session/device IDs for later). Injury prediction (never claimed).
- VideoPose3D/MediaPipe paths (eliminated 2.5C; VideoPose3D also CC BY-NC).

## Risk register
_The authoritative ranked list is docs/RISK_REGISTER.md (R-01…R-16); rows below are the original
roadmap view, updated 2026-10-10 where newer evidence exists._

| risk | rank | status / evidence | mitigation / next experiment |
|---|---|---|---|
| Seated-row domain gap | MEDIUM (was HIGH) | MB-Lite 0.85 zero-shot + wild pull F1 0.842 (Domain B) | new-athlete seated-row proof still needs fresh recordings |
| No fresh unseen-athlete data | CRITICAL | spec written; needs owner | record per spec (+ paused-tempo set, rear view); single locked evaluation |
| MB-family side/rear (depth-dominant) views | HIGH | TEST side 0.383; 1 wild rear-view FN | per-view routing ablation if needed; Domain C includes rear view |
| Window extent under paused tempo | MEDIUM (new, Domain B) | wild IoU 0.632 vs 0.841: holds absorbed into windows, pulls still correct | any fix developed on TRAIN/VAL only, validated on Mendeley + Domain C, never on the reels |
| True 3D accuracy unproven | HIGH | first real 3D GT (CMU Panoptic, P2.1): PA-MPJPE 4.2 / 10.1 / 12.6 cm front/back/side; output scale not metric; one subject only | label limitations in all outputs (done: standing warnings); more GT only if a claim needs it |
| Realtime compute gap | RESOLVED (measured) | RTX 5050 laptop GPU: `cuda_V2` 1.31× (148 s stream, latency p95 0.24 s); CPU-only i7-13650HX: `cpu_V3` (15 fps) 1.07×; V2 on CPU 0.55× | residual R-17 (decode grows with stream length: one stream per set; sliding buffer later) |
| Cross-exercise transfer weak | MEDIUM | best 0.650 (curl) | per-exercise modules (roadmap 4) |
| Annotation noise ceiling | LOW | tier recalls consistent | verify uncertain sample if needed |
| Supabase/web undeployed | MEDIUM | code-complete | roadmap 2; needs owner keys |
