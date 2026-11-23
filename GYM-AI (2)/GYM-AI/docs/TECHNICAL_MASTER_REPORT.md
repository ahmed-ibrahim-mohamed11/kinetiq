# TECHNICAL_MASTER_REPORT.md — GYM-AI complete verified engineering audit

## UPDATE 2026-10-10 (read first; the 2026-10-08 audit below is kept unchanged as a record)

Since that audit, master-prompt Phases 0–5 were completed (Phase 6 waits for the owner's
recordings; Phase 7, the full defense package, comes after). Superseded statements below:
- Section 30 "realtime NOT TESTED": now measured. GPU `cuda_V2` 1.31×, CPU-only `cpu_V3` 1.07×
  (LIVE_MODE.md).
- Section 20 "no mocap": CMU Panoptic real 3D GT now gives PA-MPJPE 4.2 / 10.1 / 12.6 cm
  (front/back/side).
- Section 28 reproducibility: verified from a fresh clone (REPRODUCIBILITY.md).
- Section 27 test count: now 618.
- "production entry point / persisted model" gaps: closed (R-01).

Evidence index: EXPERIMENT_REGISTRY.md (every experiment with before/after numbers),
RISK_REGISTER.md, DECISION_LOG.md, AI_CORE_BASELINE_MANIFEST.{md,json}.

**Current scorecard** (vocabulary per global rule 9: VALIDATED / STRONG / PARTIAL / WEAK /
FAILED / NOT TESTED):

| area | status | evidence | biggest gap |
|---|---|---|---|
| Rep segmentation, unseen Mendeley subjects | STRONG | locked TEST IoU F1 0.812 (MB-Lite); LOSO over 15 dev components 0.878 (median subject 0.875, min 0.615) | side view |
| Seated-row rep segmentation (product domain) | PARTIAL | internal 0.841 / pull 0.853 (1 athlete, contaminated); external 0.632 / pull 0.842 (3 athletes) | Domain C not run |
| Fresh unseen-athlete generalization | NOT TESTED | blocked on owner recordings; evaluation pre-registered and executable (PHASE6_PREREGISTRATION.md), candidate frozen at `ai-core-candidate-v1` | recordings |
| Cross-view | PARTIAL | front/diagonal ≈ 0.97 (TEST, LOSO); side 0.383 TEST / 0.654 LOSO; main cause identified (camera-derived sign); the anatomical-sign fix raised LOSO side to 0.853 (about 2/3 of the gap) but cut the rear-view seated row 0.841 → 0.558, so it was rejected (P2.7) | side view (R-07): needs multi-view seated-row training data |
| Slow/paused-tempo window extent | WEAK | external IoU 0.632 vs pull 0.842; synthetic reproduction; fixes rejected (P2.2, P2.7 secondary) | known limitation; measured in Phase 6 |
| Cross-exercise transfer | WEAK | curl 0.650 | per-exercise models |
| Form indicators | PARTIAL | best AUC 0.778 (rom_proxy); per-slice blind spots (P2.3); seated-row thresholds PROVISIONAL | per-deviation ground truth |
| Confidence calibration | NOT TESTED | heuristic score, monotonicity-tested; not a probability | Domain C labels |
| 3D accuracy | PARTIAL | PA-MPJPE 4.2 / 10.1 / 12.6 cm (one Panoptic subject); scale not metric | more GT only if a claim needs it |
| Robustness + abstention | STRONG | failure boundaries quantified (ROBUSTNESS_REPORT.md); guards 0/45 false abstentions | multi-person crossing probe |
| Live mode (Mode B) | STRONG | realtime measured on GPU and CPU-only for 148 s streams; accuracy within the pre-registered band | decode growth with stream length (R-17) |
| Mode A pipeline + reproducibility | VALIDATED | raw video gives poses bit-identical to the research cache; fresh clone reproduces all 8 headline metrics exactly | Docker build (deferred to the closing phase) |
| Result schema + local service | VALIDATED | strict schema tests; 16 service tests; real server run | auth/deploy deferred by scope |
| Multi-person | NOT TESTED | single-athlete tracker by design | — |
| Licensing | PARTIAL | academic / non-commercial use only (owner decision 2026-10-10: YOLO AGPL-3.0 kept, no Enterprise licence); lifter weights carry non-commercial dataset terms | commercial use needs a new decision |
| Evaluation methodology + leakage protection | STRONG | pre-registered gates, locked TEST, subject-level splits, frozen-model rule for external | — |

Written for: a senior ML/CV engineer or graduation reviewer with no prior project exposure.
Date: 2026-10-08 · Repo state: commit `41976e6` (19 commits, tag `baseline-v0.2.0-frozen`) ·
Full test suite at writing time: **90 passed, 0 failed** (includes 3 slow byte-identical
baseline-regression tests). Every number in this report was re-read from committed artifacts
during this audit; where documentation and artifacts disagree the artifact wins and the
contradiction is flagged (Section 16 contains one such flag).

> **Addendum 2026-10-08 (post-audit, commit `bc3472e`)** — two changes after this report was
> frozen: (1) the EXTERNAL_WILD_STRESS_TEST completed: frozen MB-Lite zero-shot on curated
> Instagram units scored pull-rule F1 0.842 / IoU F1 0.632 over 10 certain GT reps → verdict
> **PARTIAL EXTERNAL TRANSFER** (full report: docs/EXTERNAL_REAL_WORLD_STRESS_TEST.md; suite now
> **98 passed** with its 8 integrity tests). (2) The Section-16 contradiction was resolved in
> docs/SEATED_ROW_TRANSFER_2_8.md per the artifact (the 2.8 FN is in asymmetry_01; asymmetry_02
> is clean). Body text below is preserved as written at audit time.

---

## SECTION 1 — EXECUTIVE TECHNICAL STATUS

**Overall AI/ML core: PARTIAL→STRONG** — strong on evidence discipline, reproducibility and
cross-subject rep segmentation; PARTIAL because the two claims that matter most for the product
(fresh-athlete generalization; validated form-error detection) are respectively BLOCKED on data
collection and only directionally validated.

| Component | Status | one-line evidence |
|---|---|---|
| Rep segmentation | **STRONG** | locked unseen-subject TEST F1 0.939 (PFv2) / zero-shot target-exercise F1 0.841–0.853 (MB-Lite) |
| Pose pipeline | STRONG | 6 pipelines benchmarked under one protocol; caches validated 3×165 videos, 0 integrity errors |
| Cross-subject generalization | **VALIDATED (in-domain)** | frozen subject split 14/5/5; TEST touched once |
| Cross-view generalization | PARTIAL | PFv2 side 0.924; MB-family side 0.38–0.47 (TEST) |
| Cross-exercise generalization | PARTIAL/WEAK | best transfer: MB-Lite curl 0.650; PFv2 0.418 |
| Seated-row transfer | **VALIDATED (zero-shot, reference set)** | MB-Lite 29/30 reps, F1 0.841 IoU, pull MAE 0.128 s @30 fps |
| Form analysis | PARTIAL | rom_proxy AUC 0.778 (directional); 3 indicators partial; thresholds exercise-scoped, seated-row set provisional |
| Tracking | VALIDATED (benchmark-grade) | 1,359/74,047 frames lost (1.8%), 195 jumps over 165 videos; exonerated as error source by failure attribution |
| 3D stability | PARTIAL (proxies only) | PFv2 bone-CV 0.066 vs MB-family ~0.12; no mocap → metric accuracy unprovable |
| Realtime readiness | **NOT TESTED** | all decoding offline/non-causal; only lifter FPS measured |
| Fresh unseen-athlete validation | **BLOCKED** | requires owner recording; spec + automated gate + pre-registered criteria exist |
| Production robustness | NOT TESTED | single public dataset + 6 dev videos; nothing deployed |

## SECTION 2 — COMPLETE PROJECT HISTORY (chronological, per commit)

**Phase 0 (`3ccdc20`,`2ea145b`,`8d80fce`)** — Goal: freeze an honest baseline. Found: claimed v2
F1 0.93 was unreproducible (MotionBERT pose files lost). Built: single evaluator (two matching
rules), legacy-2D sanity baseline (F1 0.62/0.69 — measured), then Path-B rebuild: vendored
official MotionBERT (commit `705d3a9`), official checkpoint, in-repo extractor → measured v2
baseline **F1 0.87** on rebuilt poses. The claimed 0.93 was retired as never reproduced. Valid
today: yes, as frozen history (`scripts/eval_v2.ps1` still reproduces 0.87).

**Phase 1 (`bb7caae`)** — research scripts refactored verbatim into `ai/core/` with a
byte-identical (md5) regression test against the Phase-0 prediction CSV. 23 unit tests. Still the
production package today.

**Phase 2 (`60074c4`)** — asymmetry_01 decoded as one giant rep. Score decomposition proved the
cause: a constant −30 log-survival floor beyond dmax let a 231-frame censored RET beat five real
reps' duration costs. Fix: survival consistent with the truncated duration law (−inf beyond dmax).
Full nested-LOVO rerun: **0.87 → 0.93 (pull ±0.5 s) / 0.97 (IoU)** — same-athlete LOVO on the 6
seated-row videos; frozen as tag `baseline-v0.2.0-frozen`. Caveat recorded then and still true:
same-athlete ≠ new-person. Phase 3 backend (`0bf7007`) was built code-complete then paused.

**Phase 2.5A (`0763215`)** — Mendeley dataset audit (doi 10.17632/kgbb3yn47p.3): 1,042 videos,
26 subjects, 7 exercises, 3 views, ALL 15 fps, zero annotations shipped; archive SHA-verified.

**Phase 2.5B (`bfea11c`)** — 443 human-verified START/PULL/END reps over 31 videos (every video
frame-inspected). Two dataset-integrity discoveries that shaped everything after: (a) the
production "highest-confidence box" person selection switches to background people on 15/34
videos (up to 110 identity jumps) — replaced for all benchmarks by a deterministic tracker;
(b) MD5 scan: **33 duplicate-file groups / 67 videos**, 21 spanning good↔bad labels and several
spanning *subjects* (005↔006, 010↔013, 014↔020, 015↔026, 019↔020) → form labels are noisy and
subject-wise splits must merge linked subjects.

**Phase 2.5C (`a5af4cd`)** — 6 pretrained pose pipelines under one frozen protocol (same tracked
2D, same downstream phase+HSMM, leave-one-subject-out on back): MB-Lite 0.864, MotionBERT 0.861,
PFv2 0.823, MotionAGFormer-B 0.807, MediaPipe 0.695, VideoPose3D 0.670. Side view universal
bottleneck. MediaPipe/VideoPose3D/AGFormer eliminated (quality; VideoPose3D additionally CC
BY-NC). Valid today as pretrained-zero-shot evidence; superseded for trained systems by 2.7.

**Phase 2.6 (`03d2a5e`)** — expanded to **165 videos / 2,472 reps / 24 subjects** with per-rep
quality tiers (verified 443 / auto 1,412 / uncertain 617 = don't-care) and the **frozen subject
split** train 14 / val 5 / test 5 honoring duplicate-linked components. Still the active dataset.

**Phase 2.7 (`d1bbef4`,`a41d759`)** — frozen backbones (MB-Lite, MB, PFv2) + masked-supervision
phase/HSMM, VAL-selected configs, single locked TEST: **PFv2 0.939 / MB 0.825 / MB-Lite 0.812**;
PFv2 side 0.924. Diagnostics: 106/166 baseline FPs were annotation-policy artifacts → evaluation
protocol v2 (edge don't-care + relaxed uncertain absorption; recall untouched; 10 guard tests);
fps robustness verified (≤1 native frame across 7.5/15/30); tracking exonerated; supervision
ablation (verified-only 0.910 vs 0.914) justified verified+auto. Rejected with evidence:
conf_thr 0.5 (side 0.760→0.690). Two implementation bugs hit and fixed (result-dict collision;
ablation initially demoting VAL GT — run discarded).

**Phase 2.8 (`13310a7`)** — zero-shot seated-row transfer (reference set): **MB-Lite 0.841
IoU / 0.853 pull, recall 0.97**; **PFv2 collapsed to 0.303**, and the controlled 15-fps-downsample
experiment (0.338) ruled out frame-rate as the cause → exercise/viewpoint brittleness. DECISION
REVERSAL: MB-Lite = product primary. The result that justifies the whole phase ordering.

**Post-2.8 (`c2702ba`,`41976e6`)** — Phase A: canonical result schema (the collision bug class
removed structurally; original failure preserved as a regression fixture; reruns content-identical).
Phase B: unseen-athlete dataset spec v1.0 with pre-registered acceptance criteria + automated
PASS/FAIL validator (7 fixture tests). Phase C: Form Analysis v1 (Section 21).

## SECTION 3 — CURRENT SYSTEM ARCHITECTURE (as implemented)

```
Video (any fps; tested 7.5/15/30)
 → YOLO11n-pose (COCO-17 2D + conf; top-8 candidates by box area cached)        [ai/research/mendeley_annotation/extract_2d_cache.py]
 → Deterministic subject tracker (largest-box init, IoU/center continuity,
   re-acquisition after 15 lost frames; NaN on loss)                            [ai/research/pose_benchmark/tracking.py]
 → COCO→H36M-17 mapping + per-pipeline normalization                            [ai/core/pose.py, lifters.py]
 → MotionBERT-Lite DSTformer (dim 256, 243-frame clips, flip-TTA) → (T,17,3)
   pelvis-zeroed estimated-monocular 3D + (T,17) conf                           [ai/core/lifting.py / pose_benchmark/lifters.py]
 → Torso-frame features: per-arm elbow(±shoulder) coords /torso-length,
   trunk-backward channel; conf-gated validity; (T,7) per arm                   [ai/core/features.py]
 → Ridge phase model (shared L/R weights, masked supervision) → fused phase     [ai/core/phase_model.py; phase27/train_eval.py]
 → Label-free tempo estimate + explicit-duration HSMM (REST/PULL/RET,
   log-normal durations in tempo units, censored edges, peak-height law)        [ai/core/decoder.py]
 → START/PULL/END per rep + per-arm timing
 → Form indicators v1 (4 interpretable, exercise-scoped thresholds)             [ai/core/form_indicators.py]
 → (designed, undeployed) event contract v1.0 → Supabase                        [shared/event-schemas/]
```
Key assumptions/weaknesses per stage: YOLO rear/side occlusion hallucinates far-arm joints
(dominant residual failure source); tracker is benchmark-grade (single prominent subject);
lifting is non-causal (243-frame context) → realtime path unproven; HSMM decodes whole sequences
(offline); 3D is relative/monocular (no metric claims).

## SECTION 4 — POSE MODEL HISTORY (all verified from registry + artifacts)

| Model | Role | ckpt (size) | 2.5C F1* | 2.7 TEST F1† | lift fps (CPU, current artifact) | verdict |
|---|---|---|---|---|---|---|
| MotionBERT | 2D→3D lifter | FT_MB_release (170 MB) | 0.861 | 0.825 | 24.4 | retired to reference (dominated) |
| **MotionBERT-Lite** | 2D→3D lifter | FT_MB_lite_global (64 MB) | 0.864 | 0.812 | **87.9** | **PRODUCT PRIMARY** (Section 6) |
| PoseFormerV2 | 2D→3D (freq-domain, 243f/27 coeff) | 27_243_45.2 (173 MB) | 0.823 | **0.939** | 17.9 | in-domain benchmark primary; failed target domain |
| MotionAGFormer-B | 2D→3D | b-h36m (142 MB) | 0.807 (best pull MAE 1.31f) | — | 39.3 | rejected: side collapse 0.573, worst jitter of lifters |
| MediaPipe Landmarker | end-to-end | full.task (9.4 MB) | 0.695 (side 0.428) | — | 28.4 e2e | rejected for counting; fastest complete CPU path |
| VideoPose3D | 2D→3D TCN | detectron_coco (68 MB) | 0.670 | — | 1380 | rejected (quality + **CC BY-NC**) |
\* pretrained zero-shot downstream, LOSO back. † trained downstream, locked TEST.
All licenses: Apache-2.0/MIT except VideoPose3D (non-commercial) and dataset (Section 29).

## SECTION 5 — MODEL-SELECTION STORY (what each step actually proved)

1. **MotionBERT (Phases 0–2)**: inherited choice; proved only same-athlete feasibility.
2. **2.5C → MB-Lite preferred**: equal F1 to MB at 2.1× speed — proved *pretrained-representation*
   quality under a fixed downstream, not trained-system superiority.
3. **2.7 → PFv2 wins unseen subjects (0.939, side 0.924)**: proved that with a TRAINED downstream
   and subject-wise splits, representation smoothness dominates in-domain generalization — and
   that 2.5C's ranking did not survive proper training (lesson: benchmark ranking ≠ trained
   ranking).
4. **2.8 → reversal to MB-Lite**: PFv2's in-domain win did not transfer to the target exercise
   (0.303; fps controlled out). Proved the project's most important selection principle:
   **in-domain unseen-subject F1 does not predict target-domain transfer; every representation
   decision must be validated on the deployment domain.** Decision log entries 2026-10-05/05b.

## SECTION 6 — CURRENT PRIMARY MODEL (verified from `ai/outputs/phase28/` this audit)

**MotionBERT-Lite** (frozen backbone) + Mendeley-back-trained phase/HSMM, zero-shot on seated row
@30 fps (no seated-row training anywhere in the system):
IoU rule: TP 29 FP 10 FN 1 → P 0.74 R **0.97** F1 **0.841**; pull rule: TP 29 FP 9 FN 1 →
P 0.76 R 0.97 F1 **0.853**. GT 30 reps. MAE start/pull/end = 0.27/**0.128**/0.36 s (pull-rule
row: 0.25/0.13/0.37). Per-video: correct_02/03 + asymmetry_02/03 perfect precision; correct_01
11 preds (4 FP), asymmetry_01 15 preds (6 FP, 1 FN). Model 64.1 MB, 87.9 fps lifting (CPU,
60,322-frame measurement).

## SECTION 7 — POSEFORMERV2 CONTRAST

Verified: 2.7 TEST 0.939 (P .937/R .940; side 0.924; worst subject 0.877) — the strongest
in-domain cross-subject result in the project. Verified: 2.8 seated row 0.303 IoU / 0.394 pull,
over-segmentation (correct_03: 18 preds/5 reps) AND boundary misplacement (correct_01: 5 preds,
0 IoU-TP). Controlled 15-fps input: 0.338 → NOT an fps artifact. Interpretation: PFv2 compresses
motion into 27 low-frequency DCT coefficients of a 243-frame window — an aggressive temporal
prior that fits the training exercise's motion manifold superbly (hence 0.939 and the best
geometric-stability proxies) but distorts out-of-manifold motion (rear-view seated pulling;
also the weakest curl transfer, 0.418). This is the textbook distinction between **in-domain
generalization** (new people, same movement distribution — PFv2 excels) and **cross-domain
generalization** (new movement/view distribution — PFv2 fails, MB-Lite's less-entangled attention
representation survives).

## SECTION 8 — DATASET INVENTORY (complete; nothing else is used)

| Dataset | Size | Subjects | Role | Status |
|---|---|---|---|---|
| Original seated-row videos | 6 videos / 30 GT reps, 30 fps, rear view | 1 (dev-exposed) | target-domain REFERENCE (never pristine test); Phase 0–2 development | active (reference only) |
| raw/seated_row_test.mp4 | 1 clip | same | early smoke test | inactive |
| Mendeley kgbb3yn47p v3 | 1,042 videos, 15 fps, 7 exercises, 3 views | 26 | training/benchmark corpus | active |
| → annotated subset (2.6) | 165 videos / 2,472 reps (back 2,109, curl 363) | 24 | TRAIN/VAL/TEST | active, frozen |
| Unseen-athlete set | spec v1.0 only | ≥10 (none recorded) | FINAL validation | **BLOCKED** |
No Kaggle/QEVD or other data was ever used. H36M touches the project only through pretrained
checkpoint weights (never as evaluation data).

## SECTION 9 — MENDELEY USAGE (verified from `mendeley_phase26_dataset_report.json` + split CSV)

165 videos / 2,472 reps / 24 subjects; views diagonal 885 / side 872 / front 715 reps; forms bad
1,310 / good 1,162; tiers verified 443 / auto 1,412 / uncertain 617. Exclusions: 3 MD5-duplicate
copies (annotated once), 4 short (<8 s) videos, 95 auto-dropped censored edge reps (logged), 1
manual occlusion FP. **Frozen split** (`ai/data/mendeley_subject_split.csv`): TRAIN(14)=001 003
005 006 007 010 012 013 014 016 018 019 020 023 · VAL(5)=002 008 015 017 022 · TEST(5)=004 009
011 021 024 (1,477/489/506 reps). Duplicate-linked components {005,006},{010,013},{014,019,020}
forced into TRAIN together; no subject/view/form crosses splits (tested).

## SECTION 10 — ANNOTATION PIPELINE

Definitions: START = visible bottom position before the pull (arm extended), PULL = peak
contraction apex, END = completed return; 0-based frames (legacy 6-video CSV is 1-based —
documented; offset ≪ tolerances). Creation: tracked-2D wrist-height signal proposes candidates →
2.5B: EVERY video human-verified frame-by-frame (±3-frame precision; the 443 'verified' tier) →
2.6 expansion: automated QC (period-CV, NaN, weak/spike/gap rules) + stratified 12-video visual
sample + a 24-rep weak-class sample → 'auto' tier (±4 frames) and 'uncertain' tier (617 reps:
shallow/spike/double-gap candidates). **Don't-care handling (tested, 10 tests)**: uncertain reps
never create FN; absorb at most ONE unmatched prediction each (v2: pull-tol OR IoU≥0.25);
censored edge movements are not annotated and, under protocol v2, detections outside the
annotated span are ignored. Residual annotation risks: auto-tier drafted by YOLO-based aid
(circularity mitigated by human anchoring to raw frames); tier recalls on TEST are consistent
(e.g. MB .882 verified / .884 auto) → no evidence auto-tier inflates scores; noise remains a
MEDIUM limitation for boundary-MAE claims below ~0.1 s.

## SECTION 11 — DATA LEAKAGE AUDIT

| Item | Verdict | Evidence |
|---|---|---|
| Subject leakage across splits | **PASS** | `test_split_is_complete_and_disjoint`, freeze_check hashes |
| Duplicate-file/linked-subject leakage | **PASS** | MD5 scan + `test_duplicate_linked_subjects_same_split` |
| Same-athlete cross-view leakage | **PASS** | split is subject-level (all views move together; tested) |
| Test-driven tuning | **PASS** | TEST behind `--unlock-test`, run once post-freeze; protocol v2 + all keep/reject decisions made on TRAIN/VAL (git-ordered commits) |
| Hyperparameter leakage | **PASS** | grid selected on VAL only; inner-CV in 2.5C on training folds |
| Threshold leakage (form) | **PASS** (back) / dev-data by design (seated-row provisional, labeled) |
| Expected-rep-count leakage | **PASS** | no count inputs anywhere; counts vary 5–21 |
| Filename leakage | **PASS** | inference consumes pose arrays + fps only |
| form_label / annotation_quality at inference | **PASS** | not in the feature path (code audit; labels only in eval/calibration) |
| Normalization leakage | **PASS** | per-video statistics only (torso length, crop_scale) |
| Fresh-athlete contamination | PASS (vacuous) | dataset not collected; namespace gate in validator |
One honest caveat: Phase-2's survival fix was motivated by diagnosing the held-out video (the
sanctioned purpose of that phase); with 6 videos a residual selection effect was documented at
the time and is superseded by the later subject-wise results.

## SECTION 12 — UNDERSTANDING vs MEMORIZATION

Evidence ladder, strongest claim each level supports:
- **A. Same-video/athlete (Phase 2: 0.93/0.97 LOVO)** — proves fit, not generalization.
- **B/C. Unseen subjects, same exercise (2.7 TEST, 5 held-out people, split frozen before
  training): PFv2 0.939, MB-family 0.81–0.83** — genuine cross-subject generalization of the
  trained system; cannot be memorization (TEST subjects' data never touched training/selection;
  enforced by tests + hashes).
- **D. Unseen views**: views are not held out independently (every subject has all views), so the
  claim is "generalizes across views for unseen subjects": PFv2 yes (0.924–0.953 all views);
  MB-family no on side.
- **E. Tempo**: TRAIN-defined terciles; PFv2 slow 0.980 / fast 0.850; MB fast 0.901 — moderate
  evidence both directions; fast-rep regime is PFv2's weak spot.
- **F. Good vs bad form**: segmentation holds on bad form (PFv2 0.925; 5/6 pipelines score bad ≥
  good in 2.5C) — the counter is form-independent, as designed.
- **G. Cross-exercise (curl transfer)**: weak-moderate: MB-Lite 0.650, PFv2 0.418 — the system is
  exercise-specialized; per-exercise modules are the stated architecture.
- **H. Target domain (seated row, zero-shot, different exercise+view+fps+camera)**: MB-Lite
  29/30 reps, pull MAE 0.128 s **without any seated-row training** — strong evidence the learned
  phase/duration abstraction transfers as *temporal structure*, not pixel/subject memory. Tempered:
  one athlete, development-exposed videos.
- **I. Fresh athletes**: NOT TESTED (blocked).
**MEMORIZATION RISK: LOW** (for rep segmentation — splits/tests make memorization of evaluation
identities impossible; the honest residual is *dataset-style* overfitting: one 15-fps gym corpus).
**GENERALIZATION EVIDENCE: MODERATE→STRONG** — strong for cross-subject in-domain and surprisingly
strong zero-shot target-exercise temporal transfer; moderate overall until level I exists.
Form-analysis generalization evidence: WEAK-MODERATE (directional only).

## SECTION 13 — REP SEGMENTATION DESIGN (why this architecture)

Torso-frame, size-normalized features (camera/athlete invariant); per-arm channels kept separate
then fused by "either arm pulls" (asymmetry robustness); small ridge phase regressor (data-
efficient, interpretable, cross-fitted for honest decoder statistics); label-free autocorrelation
tempo normalization (tempo-invariant durations); explicit-duration HSMM with log-normal PULL/RET
laws, geometric REST, censored edges, direct RET→PULL transitions and a learned peak-height law
(counts structure, not amplitude thresholds). Chosen over end-to-end sequence models for: 2,472-
rep data scale, interpretability of failures (Section 17 exists because every term is inspectable),
and provable leakage control. The 2.7 mandate allowed a TCN/Transformer ablation if the decoder
bottlenecked — failure attribution showed pose quality, not the decoder, dominates errors, so the
ablation was correctly not spent.

## SECTION 14 — RESULTS HISTORY (protocol differences marked — not directly comparable)

| Era | System | Protocol | Result |
|---|---|---|---|
| legacy | 2D elbow-span FSM | 6 videos, transductive | 0.62/0.69 |
| Phase 0 | v2 HSMM on rebuilt MotionBERT poses | same-athlete nested LOVO | 0.87 |
| Phase 2 | + survival fix | same-athlete nested LOVO | 0.93/0.97 (frozen tag) |
| 2.5C | 6 pretrained pipelines + fixed downstream | LOSO back, 31 videos | 0.67–0.864 |
| 2.7 | trained, frozen split | **unseen-subject TEST** | 0.812–0.939 |
| 2.8 | MB-Lite system | **zero-shot target exercise** | 0.841/0.853 |
The only like-for-like trained comparison is within 2.7; 2.8 is the only target-domain number;
Phase 2's 0.93 must never be cited as generalization (same-athlete, trained on those videos).

## SECTION 15 — PHASE 2.7 FULL RESULTS (TEST, back, 318 evaluable GT reps, protocol v2)

| | PFv2 | MotionBERT | MB-Lite |
|---|---|---|---|
| F1 / P / R | **0.939** / .937 / .940 | 0.825 / .774 / .884 | 0.812 / .784 / .843 |
| start/pull/end MAE (s) | .192/.135/.217 | .154/.121/.158 | **.131/.094/.151** |
| side / diagonal / front | **.924** / .934 / .953 | .473 / .975 / .974 | .383 / .979 / .974 |
| bad / good form | **.925** / .953 | .798 / .854 | .749 / .881 |
| fast / medium / slow | .850 / **.949** / **.980** | **.901** / .819 / .827 | .782 / .793 / .859 |
| ckpt / lift fps (165-video artifact) | 173 MB / 17.9 | 170 MB / 24.4 | 64 MB / **87.9** |
Curl transfer (secondary): MB-Lite **0.650**, PFv2 0.418, MB 0.370. Tier recalls consistent
(verified vs auto within 0.01–0.08). All available; nothing withheld.

## SECTION 16 — PHASE 2.8 FULL RESULTS (seated row, 30 GT reps, zero-shot)

Pooled: Section 6 (MB-Lite) and Section 7 (PFv2). Per-video IoU (verified from CSVs):
MB-Lite — correct_01 5GT/11pred (5TP,4FP), correct_02 5/5 clean, correct_03 5/5 clean,
asymmetry_01 5/15 (4TP,6FP,**1FN**), asymmetry_02 5/6 (5TP), asymmetry_03 5/5 clean.
PFv2 — correct_01 0TP/4FP, correct_02 2TP, correct_03 0TP/14FP (18 preds), asymmetry_01 3TP,
asymmetry_02 5TP/0FP (its only clean video), asymmetry_03 0TP/5FP.
**Documentation contradiction found during this audit (not silently fixed):**
`SEATED_ROW_TRANSFER_2_8.md` attributes the single MB-Lite FN to asymmetry_02's 51-frame camera
dropout; the artifact shows asymmetry_02 is clean under IoU and the FN is in **asymmetry_01**
(a fast rep absorbed amid 6 edge/shallow FPs). The dropout explanation is wrong; correct after
review. FP character: shallow/partial movements at set boundaries (same class as Mendeley edge
partials), concentrated in correct_01/asymmetry_01. Domain shift crossed: exercise (bent-over→
seated), view (front/side/diag→rear), fps (15→30), camera/gym.

## SECTION 17 — FAILURE TAXONOMY (VAL, deterministic classifier `failure_analysis.py`)

MB-Lite baseline (pre-protocol-fix): 136 FP / 30 FN → EDGE_PARTIAL 48 FP + 6 FN;
NEAR_UNCERTAIN 58 FP + 7 FN; TRACK_NAN 7+1; PAUSE_SPLIT 3; HIGH_JITTER 1; UNKNOWN 19 FP/16 FN.
After protocol v2: 47 FP / 30 FN remain (UNKNOWN 18/16, NEAR_UNCERTAIN 15/7, EDGE 9/6), with
side view holding 25 FN / 29 FP of the residue. Attribution: **pose-under-occlusion dominates**
(side-view bone-CV .149 vs .097 diagonal with clean tracking); decoder-attributable modes
(fast-rep merges, pause sway) are minor and pipeline-independent; TRACK_NAN ≤ 8 events total.

## SECTION 18 — TRACKING

Highest-conf-per-frame rejected on evidence (2.5B: wrong person on 15/34 videos, up to 110
jumps/video, margins 0.04). Current benchmark tracker: 8 area-sorted candidates; largest-box
median init (first 10 detected frames); IoU≥0.10 else center-gate association; re-acquire via
largest box after 15 lost frames; NaN+conf-0 on loss. Expanded-set log: **1,359/74,047 lost
(1.8%), 195 jumps, re-acquisitions logged**; worst video subject_007_back_bad_side 42% lost
(TRAIN-only, documented test exception). Verdict: not the limiting factor today (Section 17);
production multi-person tracking remains future work.

## SECTION 19 — FPS ROBUSTNESS

All temporal logic audited: smoothing in Hz, detrend/durations/tolerances in seconds or tempo
units; two immaterial frame constants documented (<30-frame filter guard; ±3-frame peak window).
Controlled experiment (`fps_consistency_motionbert_lite.csv`): identical motion re-decoded at
30 fps → median pull shift 0.033 s, 23/30 exact counts; at 7.5 fps → 0.067 s, 21/30 exact
(rest ±1); match rates 97%/95%. 2.8 then validated 15→30 fps end-to-end in anger. PFv2's 2.8
failure is NOT fps (controlled test). Status: VERIFIED fps-aware.

## SECTION 20 — 3D VALIDITY (proxies only — no mocap exists)

TRAIN+VAL medians (`phase27/diagnostics.csv`): bone-length CV — PFv2 **.066**, MB .116, MB-Lite
.124; L/R bone asymmetry — PFv2 .046 vs ~.11–.12; accel-outlier fraction — PFv2 .0005 vs .022–
.028; torso-direction jitter ~.015–.018 all; depth/XY ROM ratio 1.20–1.52 (plausible magnitude,
unverifiable absolutely). Meaning: PFv2 yields the most *self-consistent* skeletons; MotionBERT-
family bone lengths wobble ~12% — fine for rep timing, marginal for joint-angle analytics.
**What cannot be proven without mocap/calibrated capture: metric joint positions, true angles,
absolute ROM, depth scale.** All form outputs therefore use relative, within-athlete proxies.

## SECTION 21 — FORM ANALYSIS: REAL CURRENT STATE (implemented, v1)

Implemented in `ai/core/form_indicators.py` (+9 unit tests incl. synthetic 0.3 s-lag recovery):
| indicator | formula (window = decoded rep) | threshold source |
|---|---|---|
| lr_lag | argmax cross-correlation offset of 3 Hz-smoothed per-arm elbow-backward signals, |lag| s | p95 of good-form calibration reps |
| lr_amplitude_ratio | min/max of per-arm (p95−p5) amplitude | p05 |
| trunk_motion | p95−p5 of trunk-backward channel (torso-lengths) | p95 |
| rom_proxy | mean per-arm (p95−p5) elbow-backward travel | p05 |
Output: value / normalized(=value÷threshold, direction-aware) / threshold / severity{ok,attention}
/ plain-language message (no injury or verdict wording — tested) / confidence from in-window
keypoint validity. **Removed in v1**: lr_onset_lag (measurement degenerates, p95=0.0 — cannot be
calibrated); phase-peak lag (replaced by signal x-corr: asymmetry hits 0/15→3/15 at equal 1/15
false rate). **Thresholds are exercise-scoped** (controlled experiment: back-calibrated
thresholds inverted the seated-row sanity check); `form_thresholds_v1_back.json` (450 TRAIN-good
reps) and `..._seated_row.json` (**PROVISIONAL**: 15 dev-video reps).

## SECTION 22 — FORM ANALYSIS VALIDITY (honest classification)

Current indicators are **heuristic features with directional weak-supervision evidence**, not
validated error detectors: rom_proxy — quantitatively directional (VAL rank-AUC **0.778**,
attention 29.5% bad vs 1.8% good; seated-row decoded 16/26 vs 9/21); lr_amplitude_ratio — weakly
directional (0.634; 17.1% vs 3.0%); lr_lag & trunk_motion — qualitative only (no Mendeley signal
because its 'bad' label is unspecific and largely reduced-ROM; correct direction on seated-row
asymmetry 5/26 & 8/26 vs 2/21). Mendeley good/bad is video-level, unspecific, and provably noisy
(21 duplicate groups span both labels) → it cannot support error-type accuracy claims. No
biomechanical-correctness claim is made anywhere. Per-deviation precision/recall awaits the fresh
dataset's per-rep deviation labels (pre-registered in the spec).

## SECTION 23 — FRESH UNSEEN-ATHLETE VALIDATION: **BLOCKED**

Nothing recorded. It is the single most important remaining validation because every current
generalization claim rests on one public 15-fps corpus plus 6 development-exposed videos of one
athlete. Protocol (frozen, `UNSEEN_ATHLETE_DATASET_SPEC.md` v1.0): ≥10 new athletes (`ua_*`
namespace), ≥3 documented camera positions each, real seated row, good + documented-deviation
sets (5 safe deviation types), slow/normal/fast tempo coverage, natural clothing/lighting/
backgrounds, 15–60 fps ≥540p; subject-wise, single locked evaluation; human-verified annotations
only; automated PASS/FAIL gate implemented and fixture-tested before any data exists.

## SECTION 24 — ACCEPTANCE CRITERIA: **DEFINED (pre-registered 2026-10-08)**

Pooled IoU F1 ≥ 0.80 AND recall ≥ 0.85; per-athlete F1 ≥ 0.65 for ≥9/10 athletes; per-view
pooled F1 ≥ 0.70; pull MAE ≤ 0.25 s; form-indicator direction correct for ≥3/5 deviation types.
Written before any fresh data; will not be adjusted after seeing results.

## SECTION 25 — GENERALIZATION MATRIX (metric = IoU F1 unless noted)

| Claim | MB-Lite | MotionBERT | PFv2 |
|---|---|---|---|
| Same-domain same-subject | VALIDATED (hist. 0.93–0.97 era) | VALIDATED (0.97 tag) | — |
| Same-domain unseen-subject | VALIDATED 0.812 | VALIDATED 0.825 | **VALIDATED 0.939** |
| Cross-view (unseen subj.) | PARTIAL/FAILED side .383 | FAILED side .473 | VALIDATED .924–.953 |
| Cross-tempo | PARTIAL (.78–.86) | PARTIAL (fast .901) | PARTIAL (fast .850; slow .980) |
| Good/bad form | VALIDATED .749/.881 | VALIDATED .798/.854 | VALIDATED .925/.953 |
| Cross-exercise (curl) | PARTIAL 0.650 | FAILED 0.370 | FAILED 0.418 |
| Seated row (zero-shot ref.) | **VALIDATED 0.841/0.853** | NOT TESTED | FAILED 0.303 |
| Fresh unseen athletes | NOT TESTED | NOT TESTED | NOT TESTED |
| Multi-person | NOT TESTED | NOT TESTED | NOT TESTED |
| Realtime | NOT TESTED | NOT TESTED | NOT TESTED |

## SECTION 26 — CODE QUALITY

Clear separation exists: `ai/core` (production, regression-locked), `ai/research/*` (phased
experiments, retained for reproducibility), `ai/tests`, `scripts/`, `ai/models/README.md`
registry, `third_party/` pinned-by-README. Canonical result schema closed the collision class.
Known debt (non-blocking, honest): argparse-at-module-import patches in two research scripts
(breaks importing them as libraries); `sys.path` injection across phase27/28 instead of a
package; duplicated Cfg-rebuild-from-json snippets (3 sites); `te.PULL_TOL_F` mutated globally in
phase28 (works, fragile); mixed LF/CRLF; `docs/PHASE_PLAN.md` superseded but retained; no stale
model references or secrets found (checked); hardcoded absolute paths: none in code (REPO_ROOT-
relative throughout), machine-specific SSL workaround documented in third_party/README.

## SECTION 27 — TEST SUITE (verified this audit)

**90 passed, 0 failed, 0 skipped** (full run incl. slow; 12.9 s + slow regressions). Categories:
core unit (features/decoder/phase-model/pose/evaluation) 26; byte-identical baseline regressions
3 (slow); event-schema sync 13; Mendeley manifest/annotations integrity 8; Phase-2.6 dataset+
split+leakage 6; pose-benchmark artifact integrity 5; don't-care protocol 10; result-schema
regression 6; unseen-dataset validator fixtures 7; form indicators 9 (minor count overlaps due
to parametrization; total = 90).

## SECTION 28 — REPRODUCIBILITY: **PARTIAL→YES**

A new engineer can reproduce current results given: Windows/Linux + Python 3.10 venv from
`requirements.txt` (exact pins); checkpoints re-downloaded per `ai/models/README.md` URLs
(MotionBERT HF mirror, Drive IDs, fb/google URLs); Mendeley archive (SHA-256 pinned) +
`third_party` clones pinned by commit in its README; data artifacts (annotations, split,
thresholds, baselines) are IN the repo with hashes in `freeze_manifest.json`. One-command repro:
`scripts/eval_v2.ps1` (0.87), `scripts/eval_core.ps1` (0.93/0.97), phase27 `train_eval.py`
(VAL/TEST), phase28 script (2.8). Runtime: CPU-only suffices; heaviest full rebuild ≈ 1–2 days
CPU (pose caches); evaluation-only from caches ≈ hours. PARTIAL qualifiers: caches/videos are
git-ignored (must be regenerated or copied); gym-network SSL quirks documented but environment-
specific; this machine's results were produced at the pinned versions only.

## SECTION 29 — LICENSING

MotionBERT/-Lite: Apache-2.0 (code+weights) — product-compatible. PoseFormerV2: MIT. YOLO11n-pose:
**AGPL-3.0 (Ultralytics)** — fine academically; a commercial product needs an Ultralytics license
or a detector swap (flagged; currently in the product path!). VideoPose3D: CC BY-NC — excluded
from product use (already rejected). MediaPipe: Apache-2.0. Mendeley kgbb3yn47p: CC BY 4.0
(attribution; research use fine — verify before commercial training use). Fresh dataset: owner-
owned with participant consent (spec requires documentation).

## SECTION 30 — REALTIME READINESS: **NOT TESTED / OFFLINE-ONLY**

Current system is offline by construction: zero-phase filtering, 243-frame lifting context,
whole-sequence HSMM. Measured components only: YOLO 2D ~15–20 fps CPU; MB-Lite lifting 87.9 fps;
HSMM decode ~0.5–2 s per 30 s video. No end-to-end causal pipeline exists; REALTIME_EDGE design
(causal filters, chunked lifting, delayed finalization) is specified in ARCHITECTURE docs but
unimplemented. No GPU measurements exist (CPU-only dev machine).

## SECTION 31 — MULTI-PERSON: structures ready, capability NOT TESTED

Event contract carries device/session IDs; tracker is single-subject by design (largest-
foreground assumption). No multi-athlete video has ever been processed as multi-person. Claiming
readiness would be false.

## SECTION 32 — EXERCISE COVERAGE

Training: bent-over dumbbell row (primary), with curl as transfer probe. Product target: seated
row — zero-shot validated at reference level. Demo library plan (roadmap): bent-over row +
seated row + bicep curl (curl needs per-exercise phase model; transfer ≤0.65 proves necessity).
Exercise recognition: **does not exist** (exercise is configuration input today) — correctly
deferred until per-exercise modules are solid.

## SECTION 33 — TECHNICAL READINESS SCORECARD

| Area | Status | Evidence | Biggest gap |
|---|---|---|---|
| Pose estimation | STRONG (benchmarked) | 6-pipeline protocolized comparison | side/rear occlusion quality |
| Tracking | ADEQUATE (benchmark) | 1.8% lost; exonerated in failures | production/multi-person |
| Rep segmentation | STRONG | 0.939 unseen-subj; 0.85 zero-shot target | fast-rep merges, edge partials |
| Cross-subject gen. | STRONG | frozen-split locked TEST | single corpus |
| Cross-view | PARTIAL | PFv2 solves; product-primary MB-Lite weak on side | per-view routing unexplored |
| Cross-exercise | WEAK-PARTIAL | 0.650 best | per-exercise modules |
| Seated-row readiness | STRONG (reference) | 29/30, 0.128 s | fresh athletes |
| Form analysis | PARTIAL | AUC 0.778 best; direction-correct | per-rep deviation GT |
| 3D reliability | PARTIAL | proxies strong (PFv2) / adequate (MB) | no metric validation |
| Realtime | NOT TESTED | — | causal pipeline |
| Multi-person | NOT TESTED | — | everything |
| Dataset quality | GOOD-WITH-FLAWS | tiers, dedup, frozen splits; noisy labels documented | fresh data |
| Evaluation quality | STRONG | pre-registered criteria, don't-care protocol, guard tests | — |
| Leakage protection | STRONG | Section 11, enforced by tests | — |
| Reproducibility | GOOD | pins+hashes+commands | cache regeneration cost |
| Testing | GOOD | 90 green incl. regressions | integration/e2e absent |
| Code architecture | GOOD core / FAIR research | schema fix; debt list Section 26 | research-script packaging |

## SECTION 34 — INTERNAL TECHNICAL CORE MATURITY (UI/backend ignored)

**Graduation-grade already**: rep segmentation + its evaluation methodology (frozen splits,
locked TEST, pre-registered criteria, failure attribution) — this is the defensible heart.
**Research-grade**: form indicators (directional), 3D proxies, cross-exercise findings.
**Prototype-grade**: tracking (production), per-exercise scaling. **Production-grade**: nothing
yet — by definition impossible before fresh-athlete validation, realtime path, and deployment.

## SECTION 35 — "IS THE TECHNICAL CORE 100% COMPLETE?" — **NO.**

| Blocker | Rank |
|---|---|
| Fresh unseen-athlete validation not run (data not recorded) | CRITICAL |
| Form indicators lack per-deviation quantitative validation | HIGH |
| Realtime/causal inference path unbuilt & unmeasured | HIGH |
| MB-Lite side-view weakness unsolved in product path (PFv2 solves it but fails the domain; routing/ensemble untested) | HIGH |
| 3D metric validity unproven (mocap/multicam absent) | MEDIUM |
| Per-exercise scaling + exercise recognition absent | MEDIUM |
| YOLO AGPL in product path (license, not science) | MEDIUM (product) |
| Multi-person untested | MEDIUM (product) |
| Research-code packaging debt | LOW |

## SECTION 36 — SHORTEST DEFENSIBLE PATH TO TECHNICAL FREEZE

1. Owner records fresh dataset (spec frozen) → automated gate → single locked evaluation against
   pre-registered criteria (rep segmentation + indicator direction).
2. Form v2 calibration on fresh good-form reps + per-deviation precision/recall (labels exist in
   that dataset by design).
3. Realtime feasibility: causal variant of filtering+chunked MB-Lite lifting+windowed decode;
   measure end-to-end fps/latency vs offline rep-difference on dev videos (no new data needed —
   can start before (1) returns).
4. If (1) exposes side/view failures: one controlled routing experiment (MB-Lite↔PFv2 by view)
   on TRAIN/VAL only. 5. Regression-freeze: tag models+thresholds+configs; full suite green.

## SECTION 37 — DO NOT TOUCH (validated; further change = low value / regression risk)

The frozen artifacts (annotations, split, baselines, tags); `ai/core` decoder+features (byte-
locked); the evaluation protocol v2 + don't-care rules (tested); the pose-model roster — further
pose-model shopping is LOW VALUE until fresh-athlete data demands it; MB-Lite as seated-row
primary (evidence-based; revisit only with in-domain training data per DECISION_LOG).

## SECTION 38 — BLOCKER TABLE

| Blocker | Sev. | Component | Evidence | Owner | Claude-solvable | Needs recording | Needs ext. account | Blocks freeze | Blocks prod | Next action |
|---|---|---|---|---|---|---|---|---|---|---|
| Fresh dataset | CRIT | validation | spec+gate ready, 0 videos | **owner** | no | **yes** | no | yes | yes | record per spec |
| Form per-deviation validation | HIGH | form v1 | AUC table only | both | partially (needs (1)) | yes | no | yes | yes | fresh-data labels |
| Realtime path | HIGH | inference | none exists | Claude | **yes** | no | no | yes | yes | causal prototype + measurement |
| MB-Lite side view | HIGH | pose | TEST .383 | Claude | yes (experiment) | no | no | no | maybe | routing ablation (TRAIN/VAL) |
| 3D metric validity | MED | 3D | proxies only | owner+Claude | no | yes (mocap/multicam) | no | no | for form claims | one calibrated session |
| YOLO AGPL | MED | licensing | Ultralytics terms | owner | advise only | no | maybe | no | yes (commercial) | license or detector swap |
| Supabase/Vercel deploy | MED | product | code-complete, undeployed | owner+Claude | needs keys | no | **yes** | no | yes | keys → E/F phases |

## SECTION 39 — CANONICAL VERIFIED METRICS TABLE (all re-read from artifacts 2026-10-08)

| Model | Domain / split | Exercise | Subj. | GT | P | R | F1 | MAE s/p/e (s) | Views (F1) | Form (F1) | fps@ | ckpt |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| PFv2 | Mendeley TEST (unseen) | back | 5 | 318 | .937 | .940 | **.939** | .19/.14/.22 | s .924 d .934 f .953 | bad .925 good .953 | 15 | 173 MB |
| MotionBERT | Mendeley TEST | back | 5 | 318 | .774 | .884 | .825 | .15/.12/.16 | s .473 d .975 f .974 | .798/.854 | 15 | 170 MB |
| MB-Lite | Mendeley TEST | back | 5 | 318 | .784 | .843 | .812 | .13/.09/.15 | s .383 d .979 f .974 | .749/.881 | 15 | 64 MB |
| **MB-Lite** | Seated row zero-shot (ref.) | seated row | 1 (dev) | 30 | .74 | **.97** | **.841** (IoU) / .853 (pull) | .27/.128/.36 | rear only | corr/asym per-video Section 16 | 30 | 64 MB |
| PFv2 | Seated row zero-shot | seated row | 1 | 30 | .28 | .33 | .303 | .38/.49/.50 | rear | — | 30 | 173 MB |
| MB-Lite | curl transfer TEST | bicep curl | 2 | 86 | .688 | .616 | .650 | —/.075/— | side | — | 15 | 64 MB |
| core HSMM (tag) | 6 dev videos, same-athlete LOVO | seated row | 1 | 30 | .97(IoU) | .97 | .93/.97 | .17/.17/.23 | rear | — | 30 | — |
Lifting throughput (CPU, 165-video artifact): MB-Lite 87.9 / MB 24.4 / PFv2 17.9 fps. Form v1:
rom_proxy AUC .778 (att. 29.5% vs 1.8%); amp_ratio .634; lr_lag .529; trunk .379. N/A: GPU
numbers, realtime e2e, fresh-athlete anything.

## SECTION 40 — FINAL TECHNICAL VERDICT

**A. Unquestionably working**: the reproducible rep-segmentation pipeline + its evaluation
machinery (frozen data, locked tests, 90 green tests, byte-locked baselines).
**B. Strongly supported**: cross-subject in-domain generalization (0.939/0.81–0.83); zero-shot
target-exercise temporal transfer (0.841–0.853, R 0.97); fps invariance; tracking adequacy;
the PFv2↔MB-Lite domain-dependence finding.
**C. Partially supported**: form indicators (one validated-directional, three partial); 3D
stability (proxy-level); tempo robustness; MB-Lite cross-view.
**D. Failed (and documented)**: PFv2 on the target domain (0.303); MediaPipe/VideoPose3D/
AGFormer for counting; phase-peak lag + onset-lag features; conf-threshold side-view fix;
cross-exercise threshold transfer; the original claimed-0.93 reproducibility.
**E. Not tested**: fresh athletes, realtime, multi-person, metric 3D, deployment.
**F. Next**: Section 36 sequence — fresh dataset is the hinge; realtime prototype can start now.
**G. Graduation-defensible today?** The AI core: **yes** — honest metrics, real baselines,
documented failures, locked protocols survive committee scrutiny. The full demo system: not yet
(no integrated demo exists).
**H. Production-ready today?** **No** (Sections 23/30/31/35).
**I. Single largest remaining risk**: that performance drops on genuinely fresh athletes/cameras
— every current number comes from one public corpus + one dev-exposed athlete.
**J. The experiment that removes it**: the pre-registered single locked evaluation on the
≥10-athlete fresh seated-row dataset (spec + automated gate + acceptance criteria already frozen
in this repo).
