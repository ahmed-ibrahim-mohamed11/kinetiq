# PROJECT_STATE.md — GYM-AI

_Last update: 2026-10-05 (technical-lead mode). Live roadmap: PROJECT_ROADMAP.md · decisions:
DECISION_LOG.md · changes: CHANGELOG.md. Historical Phase-0 state preserved in git history._

## Current milestone
**Phase 2.8 DONE — seated-row zero-shot transfer.** MB-Lite F1 0.85 (R 0.97, pull MAE 0.13 s);
PFv2 collapsed (0.30, fps ruled out) -> **MB-Lite is the seated-row product primary**. Next:
form-analysis v1 on MB-Lite rep windows; integration restart queued.

## Primary model + current metrics (frozen evidence)
| what | result |
|---|---|
| PRIMARY (product / seated row) | **MotionBERT-Lite** (zero-shot seated row F1 0.85) |
| PRIMARY (in-domain benchmark) | PoseFormerV2 (unseen-subject back F1 0.939; brittle cross-exercise) |
| FALLBACK / speed / timing | MotionBERT-Lite (87.9 fps lift CPU, 64 MB, pull MAE 0.094 s) |
| Unseen-subject TEST (back, 5 subjects, IoU) | PFv2 **F1 0.939** (side 0.924, bad-form 0.925) · MB 0.825 · MB-Lite 0.812 |
| Same-athlete seated-row baseline (tag `baseline-v0.2.0-frozen`) | F1 0.93 / 0.97 (LOVO, trained on those videos — reference only) |
| Dataset | 165 videos / 2,472 reps / 24 subjects, frozen subject split 14/5/5 (Phase 2.6) |
| fps robustness | verified: ≤1 native frame pull shift across 7.5/15/30 fps |

## What is COMPLETE
- Reproducible pipelines: YOLO top-8 cache → deterministic tracker → {PFv2, MB, MB-Lite} lifting →
  masked-supervision phase model + duration HSMM → don't-care evaluation (10 guard tests).
- Datasets/annotations/split (frozen, hashed), benchmark + training artifacts, 65-test suite.
- Supabase schema + RLS + seed + tests; shared event contract v1.0 (undeployed, code-complete).

## What is PARTIAL / UNVALIDATED
- Seated-row transfer (2.8 in flight). True metric 3D accuracy (proxies only — no mocap).
- Form analysis: features exist per rep (lag/amp/trunk) but no validated indicator thresholds yet.

## What is BLOCKED (owner actions)
- Fresh unseen-athlete recordings (CRITICAL; spec in 2.6/2.7 docs). Supabase project keys +
  Vercel account (for integration restart). Camera/edge hardware details (realtime phase).

## What is OBSOLETE
- Legacy 2D elbow-span pipeline (reference only), MediaPipe/VideoPose3D paths (eliminated 2.5C),
  original PHASE_PLAN.md ordering (superseded by PROJECT_ROADMAP.md).

## Active blocker & next action
Transfer result → then: form-analysis v1 (rule-based coach-attention indicators) and integration
restart (Supabase deploy → dashboard → edge agent → end-to-end demo), per roadmap.

## Known limitations (carry into every report)
One public 15 fps dataset dominates training; seated-row evidence is same-athlete or zero-shot;
3D is estimated monocular (no medical/injury claims — "coach-attention indicators" only).
