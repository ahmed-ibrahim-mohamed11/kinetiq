# PROJECT_STATE.md — GYM-AI

_Last update: 2026-10-08 (technical-lead mode). Live roadmap: PROJECT_ROADMAP.md · decisions:
DECISION_LOG.md · changes: CHANGELOG.md · full audits: TECHNICAL_MASTER_REPORT.md +
CURRENT_HANDOFF_REPORT.md. Historical Phase-0 state preserved in git history._

## Dual status (never conflated)
- **GRADUATION DEMO READY: NOT YET** — AI core near freeze (segmentation validated, form v1
  directional, external stress test done), but no integrated demo (backend undeployed, no
  dashboard, no end-to-end flow) and realtime feasibility unmeasured.
- **PRODUCTION READY: NO** — fresh unseen-athlete validation BLOCKED (owner recording),
  realtime unmeasured, seated-row form thresholds provisional, YOLO AGPL decision pending.

## Status labels
DONE+VALIDATED: rep segmentation in-domain (Phases 0–2.8), result-schema fix, dataset gate
tooling, external wild stress test (Domain B, PARTIAL EXTERNAL TRANSFER). PARTIAL: form v1
(rom_proxy directional; 3 indicators partial; seated-row thresholds provisional), seated-row
generalization (zero-shot + 3 wild athletes; no fresh-athlete evidence), realtime (causal
streaming design VALIDATED on dev videos — V2 F1 0.806 vs offline 0.841, rep events ~5 s after
rep end — but live operation NOT FEASIBLE on this CPU: 0.26–0.66× realtime, GPU or budget mode
needed; docs/REALTIME_FEASIBILITY.md). BLOCKED: unseen-athlete validation (owner recording),
Supabase deploy (keys), Vercel (account), live-demo hardware decision (owner). NOT STARTED:
dashboard UI, edge agent, Unreal.

## Current milestone
**Realtime/causal feasibility DONE.** Causal audit (offline system hides ±5 s detrend look-ahead,
whole-video tempo/Viterbi, bidirectional lifting) + streaming prototype with commit-once
semantics: V2 (5.4 s look-ahead) reproduces 44/47 offline rep events, F1 0.806 vs 0.841, rep
emitted ~5 s after rep end; compute on this CPU laptop is 0.26–0.66× realtime (YOLO 88 ms/frame
is the bottleneck) → live demo needs a GPU or the 15 fps/no-TTA budget mode (fast-tempo weakness
measured); after-set processing already feasible today. Before that, same day: external stress
test (bc3472e, PARTIAL EXTERNAL TRANSFER) + docs hygiene (ea71ccd).
**Next: fresh-dataset gate on recording arrival; integration spine after keys (owner).**

## Primary model + current metrics (frozen evidence)
| what | result |
|---|---|
| PRIMARY (product / seated row) | **MotionBERT-Lite** (zero-shot seated row F1 0.841/0.853) |
| PRIMARY (in-domain benchmark) | PoseFormerV2 (unseen-subject back F1 0.939; brittle cross-exercise/zero-shot) |
| Unseen-subject TEST (back, 5 subjects, protocol v2) | PFv2 **0.939** (side 0.924, bad 0.925) · MB 0.825 · MB-Lite 0.812 (pull MAE **0.094 s**) |
| Seated-row zero-shot (Domain A, 30 reps) | MB-Lite F1 0.841 IoU / 0.853 pull, R 0.97, pull MAE 0.128 s; PFv2 collapsed 0.303 |
| External wild (Domain B, 10 reps, Instagram) | MB-Lite pull F1 0.842 / IoU 0.632, pull MAE 0.282 s → **PARTIAL EXTERNAL TRANSFER** |
| Form v1 | rom_proxy AUC 0.778 (VALIDATED-directional); lr_amp 0.634, lr_lag, trunk PARTIAL; lr_onset REMOVED |
| Dataset | 165 videos / 2,472 reps / 24 subjects, frozen split 14/5/5 (Phase 2.6) |
| Speed/size (offline lifting, NOT realtime) | MB-Lite 87.9 fps CPU / 64 MB; fps robustness ≤1 native frame @7.5/30 |

## What is COMPLETE
- Reproducible pipelines: YOLO top-8 cache → deterministic tracker → {PFv2, MB, MB-Lite} lifting
  → masked-supervision phase model + duration HSMM → don't-care evaluation v1/v2 (10 guard
  tests) → canonical result schema (6 tests).
- Datasets/annotations/splits (frozen, hashed); unseen-athlete dataset gate (7 tests); form v1
  indicators (9 tests); external stress-test track incl. GT, metrics, taxonomy (8 tests).
- **98-test suite** (3 slow regression tests included).
- Supabase schema + RLS + seed + tests; shared event contract v1.0 (undeployed, code-complete).

## What is PARTIAL / UNVALIDATED
- Form analysis v1: directional evidence only; seated-row thresholds PROVISIONAL (15 reps,
  1 athlete); per-deviation accuracy unclaimable until fresh dataset.
- Rep-window extent under paused tempo (wild finding: holds absorbed into windows); rear/side
  depth-dominant views for the MB family (Mendeley side 0.383; 1 wild rear miss).
- True metric 3D accuracy (proxies only — no mocap). Realtime path (nothing measured).

## What is BLOCKED (owner actions)
- Fresh ≥10-athlete recordings (CRITICAL; spec UNSEEN_ATHLETE_DATASET_SPEC.md — add one
  slow/paused-tempo set + rear view per stress-test findings). Supabase keys + Vercel account
  (integration phase). Realtime target-hardware decision. YOLO AGPL-3.0 product decision.

## What is OBSOLETE
- Legacy 2D elbow-span pipeline (reference only), MediaPipe/VideoPose3D paths (eliminated 2.5C),
  original PHASE_PLAN.md ordering (superseded), ai/models/form_thresholds_v1.json (deleted
  2026-10-08; superseded by exercise-scoped _back/_seated_row files).

## Active blocker & next action
Realtime/causal feasibility prototype (causal audit → streaming variant → latency benchmark →
accuracy-vs-offline comparison) on this laptop; offline system stays the reference. Then: fresh
dataset gate on arrival of recordings; integration spine after ML sign-off + owner keys.

## Known limitations (carry into every report)
One public 15 fps dataset dominates training; seated-row evidence is same-athlete zero-shot +
10 wild reps; 3D is estimated monocular (no medical/injury claims — "coach-attention
indicators" only); external wild n is small and never substitutes Domain C validation.
