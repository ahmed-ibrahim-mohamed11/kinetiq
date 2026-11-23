# PROJECT_STATE.md — GYM-AI

_Last update: 2026-10-10 (technical-lead mode, new owner master-prompt scope: Phases 0–7).
Live roadmap: PROJECT_ROADMAP.md · decisions: DECISION_LOG.md · changes: CHANGELOG.md · risks:
RISK_REGISTER.md · baseline: AI_CORE_BASELINE_MANIFEST.md · full audits: TECHNICAL_MASTER_REPORT.md
+ CURRENT_HANDOFF_REPORT.md. Historical Phase-0 state preserved in git history._

## 2026-10-10 update (Phases 4–5)
**Phase 4 DONE**: torch CUDA build adopted with all frozen numbers byte-identical. Production live
components (`ai/core/{tracking,live}.py`, `lifting.MotionBertLite`, `pose.YoloTopK`) are each
proven equal to their research references (`6955cf5`). Measured on AC power (all runs VALID,
docs/LIVE_MODE.md):
- GPU (RTX 5050 Laptop): `cuda_V2` is realtime at 1.31× (148 s stream: 1.17× last quarter,
  latency p95 0.24 s), IoU F1 0.783.
- CPU only (i7-13650HX): `cpu_V3` (15 fps budget) is realtime at 1.07× (1.02× last quarter,
  narrow), IoU F1 0.8125.
- V2 quality on CPU is 0.55×, so not realtime.
- fp16, no-TTA and 320 px were rejected by the accuracy gate.
- Limit: decode cost grows with stream length (R-17), so run one stream per set.

**Phase 5 DONE** (Docker build excepted: Docker is not installed):
- The fitted system is a persisted, fingerprint-verified artifact (R-01 closed in substance).
- `python -m ai.scripts.analyze_set VIDEO` is Mode A in one command. From raw video it reproduces
  the frozen Phase 2.8 result (IoU F1 0.8406, pull F1 0.8529), with poses bit-identical to the
  research cache.
- Results follow the strict `AnalysisResult` v1.0 schema (`d91d6a4`).
- Local FastAPI service (`python -m ai.service`) with privacy-preserving upload handling, plus
  PRIVACY.md requirements (`a72719e`).
- Ultralytics usage telemetry is switched off in the product detector (found during P5.5).
- Fresh clone + fresh venv following docs/REPRODUCIBILITY.md reproduced all 8 headline metrics
  and the fingerprint exactly, and Mode A output identically. Two defects it found were fixed
  (torch install instruction; tests that errored without git-ignored caches) (`9e91249`).
- Only gap: the Dockerfile is unbuilt (Docker not installed: owner action).

## 2026-10-09 update (new scope: Phases 0–5 current, 6–7 after owner recordings, DB/UI/deploy DEFERRED)
**Phase 0 DONE** (commit `35eccfb`): repo moved to a new machine (RTX 5050 GPU laptop, was
CPU-only); every documented headline metric reproduced exactly (Mendeley VAL IoU F1 0.9137,
internal seated-row IoU/pull F1 0.8406/0.8529, external IoU/pull F1 0.6316/0.8421, causal
V1/V2/V3 F1 0.7397/0.806/0.8125) — see AI_CORE_BASELINE_MANIFEST.md. New:
`ai/evaluation/{protocol,harness}.py` (shared, tested BEFORE/AFTER harness for all future Phase
2–4 experiments) and the owner-required governance docs (RISK_REGISTER.md,
AI_CORE_BASELINE_MANIFEST.md, EXPERIMENT_REGISTRY.md — none existed before). Audit found and fixed
(comment/metadata-only, zero behaviour change, re-verified against the frozen hash manifest): a
`.gitignore` gap that let two large benchmark-only checkpoints (151 MB) into git despite policy;
a stale tracker docstring. Audit found and NOT yet fixed (logged, scheduled): `ai/core` does not
match the validated product pipeline and has no persisted model artifact (R-01, CRITICAL,
scheduled for Phase 5); `device="cpu"` hardcoded in 4 scripts blocks using this machine's new GPU
(R-03, scheduled for Phase 4). See RISK_REGISTER.md for the full ranked list (15 items).

**Phase 1 DONE**: public dataset survey (DATASET_VALIDATION.md + LICENSING.md +
`ai/data/dataset_registry.json`, primary-source-verified licenses) — conclusion: no public
dataset of seated/cable row with per-rep annotations exists; MM-Fit (CC BY 4.0, dumbbell rows,
own-recorded) approved for download; Fit3D and QEVD gated on owner EULA approval; RepCount-A
excluded (YouTube-scraped, owner hard rule). Synthetic-perturbation generator
`ai/augmentation/perturb.py` (time-warp, confidence dropout/noise, frame-drop, fps-downsample,
resolution-jitter proxy, bounded yaw rotation) with 24 tests, 2 of which run every transform
against the real frozen decoder — this caught two wrong initial assumptions before they became
unverified doc claims (EXPERIMENT_REGISTRY E-01): wrist-joint dropout has zero effect (wrist is
never read by `ai/core/features.py` — now documented in `config.py`), and yaw rotation is a
decoder no-op only for small angles (large rotations flip the fixed-constant backward-axis sign).
Recording spec + ingestion/validation tool for the final dataset already existed
(UNSEEN_ATHLETE_DATASET_SPEC.md + `validate_unseen_dataset.py`) — confirmed still current, not
redone. Full suite: **136/136 passed** (98 existing + 14 protocol-equivalence + 24 augmentation).

**Phase 2 in progress**: P2.1 (orientation/depth-sign) done — a per-video auto-sign fix was
tested and REJECTED (regressed `mendeley_val` pooled and `external`), but a follow-up using REAL
CMU Panoptic Studio multi-view footage (free, no-account, licensed; Fit3D DROPPED per owner
directive — no access available) gave the project's **first real 3D-ground-truth accuracy
number ever** (PA-MPJPE 4.2/10.1/12.6 cm front/back/side) and independently confirmed the
depth-sign convention is view-dependent (correct for rear cameras, inverted for front/side) —
see EXPERIMENT_REGISTRY.md P2.1-panoptic, RISK_REGISTER R-07/R-11. P2.2 (paused-tempo window
extent, R-08) done — reproduced the real external degradation synthetically (a targeted END-hold
insertion is the closest match) and tried a minimal post-hoc fix (trim absorbed holds via a
near-REST plateau search); REJECTED (mixed/negative results, slight regression on untouched
normal-tempo data). Found and logged a new MEDIUM bug (R-16): `estimate_tempo` can crash under
extreme slow tempo — not fixed yet (Phase 3 item). P2.3 (form indicators) done — per-slice
sensitivity/specificity breakdown, no indicator dropped, 1 correction + 1 confidence upgrade.
P2.4 (confidence calibration) done — extended, still-explicitly-heuristic confidence combining
more signals; true calibration stays BLOCKED on Domain C, stated not hidden. P2.5 (annotation
noise + Domain C gate review) done — F1-based rankings robust to annotation noise, a close
pull-MAE ranking (MB-Lite vs PFv2) is NOT distinguishable from noise; 4/5 Domain C gates
CONFIRMED appropriate, the per-view gate flagged AT ELEVATED RISK on side/oblique (not changed).

**Phase 2 (model training/improvement) DONE.** All 5 pre-registered experiments executed and
decided: P2.1 REJECTED (sign fix) + real-multiview evidence added, P2.2 REJECTED (extent fix)
+ a new bug found, P2.3/P2.4/P2.5 measurement deliverables ACCEPTED. 144/144 tests pass
throughout; zero regressions to the frozen baseline (headline metrics/fitted-system fingerprint
unchanged across every commit in this phase).

**Phase 3 (robustness & safety of output) DONE.** docs/ROBUSTNESS_REPORT.md consolidates all 7
items: P3.0 fixed the R-16 crash; P3.1–P3.2 found wrist dropout is a true no-op at any severity,
shoulder/elbow dropout are each independently sufficient to disable an arm (previously
unquantified), resolution/compression jitter is the single most damaging perturbation and
sharply worse out-of-distribution, and tempo INCONSISTENCY is a distinct stressor from pure
slowdown; P3.3–P3.4 consolidated existing view/tracking evidence (one acknowledged gap: no
dedicated multi-person-crossing probe); P3.5–P3.6 added `ai/core/guards.py` (abstention +
exercise guard, 0 false abstentions on 45 real videos, additive/zero-regression by
construction); P3.7 fixed 3 silent-output gaps (bad fps, invalid Cfg, empty annotations) and
confirmed 2 already-correct (missing checkpoint, broken video). 186/186 tests pass; every fix
verified a true no-op on all real data (byte-identical headline metrics/fingerprint throughout).

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
