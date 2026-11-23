# DECISION_LOG.md — major technical decisions (newest first)

## 2026-10-10 — Phase 6 pre-registered and executable; candidate frozen at `ai-core-candidate-v1`
- **Decision**: the final unseen-athlete evaluation is pre-registered in
  `docs/PHASE6_PREREGISTRATION.md` and implemented as one command
  (`python -m ai.scripts.phase6_evaluate`). The system it evaluates (rep-segmentation artifact v1,
  fingerprint `63d36c31…`, both checkpoints, `ai/core`, lifter code, seated-row thresholds, package
  versions and OpenCV binaries), every rule constant, the evaluation code and the pre-registration
  itself are frozen in `docs/PHASE6_FREEZE_CANDIDATE.json` at git tag `ai-core-candidate-v1`.
  **From the tag on, no model, threshold or configuration change is allowed before Phase 6** (owner
  instruction).
- **What the pre-registration adds** to the 2026-10-08 gates (no gate number lowered):
  - a per-tempo gate G4 (≥ 0.70 for normal / slow / paused / fast);
  - G6 made precise as a paired comparison (same athlete, same view, normal tempo) that must hold
    pooled and for ≥ 2 athletes, declared a sanity check (≈ 31% chance pass with no signal);
  - data-quality rules checked before the run (≥ 3 certain reps per take, `low` ≤ 10% / 20% per
    slice, the G6 data condition), so no gate can be untestable after the run; a gate left NOT
    EVALUABLE counts as FAIL;
  - gates decided exactly on the counts (fractions), not on rounded floats;
  - abstention (= zero predictions), SYSTEM_ERROR (a crash of the frozen system = zero predictions)
    and exclusion (intake FAIL only) rules; Mode B can never block Mode A;
  - a primary annotator (`a1`) and agreement rounds whose subset and result the runner recomputes;
  - a data lock (`phase6-data-lock-v1`) before the model sees any Phase 6 video; one run per cohort;
    re-runs only on byte-identical inputs; a verdict of record;
  - storage outside OneDrive, a consent template, and an external timestamp (tag hashes emailed to
    the supervisor) at every tag.
- **How drift is caught**: the runner reads the freeze files from the git tags, checks the tagged
  content, the environment and the rule constants, and refuses to run on any difference.
  Evaluation-code fixes are possible only as a committed, documented `--evaluation-fix` (addendum +
  diff in the report), with the system and the rules unchanged.
- **Review**: three adversarial review rounds before the tag. Round 1: 13 issues (2 blocking: the
  freeze was not tied to the tag; G6 could pass on one athlete's evidence). Round 2: 39 issues
  (8 blocking: a complete cohort could still end INCOMPLETE through G6; the agreement precondition
  could be met without a measured agreement; nothing locked the ground truth before the run; the
  data and pose caches defaulted to a OneDrive-synced folder; reopening a take re-bound old marks to
  a changed file; no external timestamp; the suite was failing). Round 3 verified the fixes. All were
  fixed before tagging.

## 2026-10-10 — P2.7 rejected: the product keeps the camera-derived sign; R-07/R-08 are known limitations
- **Decision**: the owner's single last pre-freeze attempt (anatomical backward-axis sign) is
  REJECTED by its pre-registered gates (G4, G5). No product change; no further pre-freeze tuning.
- **Why**: it largely fixes the side view on the development data (LOSO side 0.654 → 0.853, pooled
  0.878 → 0.934, every Mendeley paused probe up in IoU F1) but breaks the product domain, the seated
  row filmed from behind (internal_sr 0.841 → 0.558, live V2 0.806 → 0.708, every internal paused
  probe down). Mode A is reliability-first for the product domain; a gain on the proxy exercise does
  not buy a loss on the product view.
- **What we learned**: the camera-derived sign convention is the main identified cause of the
  side-view weakness on development data (fixing it closes about 2/3 of the LOSO side gap; real 3D GT
  confirms the anatomical convention is right in every view). The frozen model's rear-view
  robustness goes with the mixed conventions it was trained with; the mechanism is not established.
  Getting both needs in-domain seated-row training data from several views — a new development
  cohort, never Domain C. Evidence: EXPERIMENT_REGISTRY.md P2.7, `ai/outputs/phase2/p27_*`.

## 2026-10-10 — Owner decisions on the open items (licence, telemetry, environment, data, Docker)
- **YOLO11n-pose licence: academic / non-commercial use only.** YOLO stays in the pipeline; no
  Ultralytics Enterprise licence. Any commercial use needs a new owner decision first (licence or
  detector swap). LICENSING.md, RISK_REGISTER R-14 (accepted for academic use).
- **Ultralytics telemetry: off machine-wide.** `yolo settings sync=False` applied on the
  development machine (the venv's `yolo.exe` launcher is stale, so the identical entrypoint was run
  via `python`; `%APPDATA%\Ultralytics\settings.json` now has `"sync": false`). Socket-level audit
  on the product, research and legacy paths: no analytics on any of them. The remaining
  import-time DNS online check is removed from the product path (`YoloTopK` defaults
  `YOLO_OFFLINE=1`; new test). PRIVACY.md, EXPERIMENT_REGISTRY P5.7c.
- **yt-dlp removed** from `.venv` (`pip check` clean; no code imports it).
- **QEVD: skipped.** EULA not accepted, never downloaded (LICENSING.md, dataset registry).
- **Docker: deferred to the closing phase.** The owner installs Docker then; the Dockerfile build
  check moves from "blocked" to the closing-phase list (PROJECT_ROADMAP.md).

## 2026-10-10 — Best live configs: `cuda_V2` on GPU, `cpu_V3` on CPU only (P4.5)
- **Decision**, by the pre-registered rule (highest GT IoU F1 among realtime-capable configs, per
  hardware class): GPU → `cuda_V2` (V2 quality; 1.31×, 148 s stream 1.17× last quarter, latency
  p95 0.24 s; IoU F1 0.783). CPU only → `cpu_V3` (15 fps budget; 1.07×, 1.02× last quarter, p95
  0.85 s; IoU F1 0.8125). Realtime is claimed only for these measured cases (≤148 s streams).
- **Rejected by the accuracy gate** (more than 0.03 below the same-preset CPU reference): fp16
  lift, no flip-TTA, YOLO at 320 px. Sparse detection (every 2nd frame) passed on accuracy but
  is not realtime in the long session (0.88×).
- **Judgment beyond the rule, stated as such**: pooled across classes, the rule would pick
  `cpu_V3` by 1–2 counts on 30 GT reps (noise per global rule 4). On GPU hardware we default to
  `cuda_V2` for its realtime headroom (31% vs 2–7%).
- **Consequence**: one live stream per set until a sliding decode buffer exists (R-17).
  Evidence: docs/LIVE_MODE.md, EXPERIMENT_REGISTRY.md P4.3–P4.5.

## 2026-10-10 — Local service: one worker thread, no live streaming endpoint yet (P5.4)
- **Decision**: the FastAPI service (`ai/service`) runs Mode A jobs on ONE worker thread with a
  bounded queue, deletes uploads after processing, and binds to localhost without auth. The
  optional live-mode streaming endpoint is NOT built yet.
- **Why**: model instances are never shared across threads; Mode B realtime is unmeasured until
  P4.3–P4.5 (blocked on AC power); the product's live mode is expected to run in-process next to
  the camera (`ai.core.live.LiveStreamer`), where HTTP frame transport only adds latency.
- **Revisit**: after P4.5. Details: docs/AI_SERVICE.md.

## 2026-10-10 — Mode A uses the video's own frame rate by default (P5.2)
- **Decision**: `ai/core/pipeline.py` uses the container's reported (average) fps; `--fps`
  overrides it for wrong metadata or nominal-rate reproduction.
- **Evidence**: from raw video the poses equal the Phase 2.8 cache bit-for-bit. At nominal 30.0 fps
  (the frozen evaluation's assumption) reps are byte-identical; at the true 29.81–29.90 fps,
  TP/FP/FN and F1 are identical (IoU 0.8406, pull 0.8529) with 5 boundaries moved by exactly one
  frame. Truthful timing for arbitrary videos outweighs a 1-frame match to a nominal-rate
  assumption. EXPERIMENT_REGISTRY.md P5.2.

## 2026-10-10 — The fitted system is a persisted, fingerprint-verified artifact (P5.1)
- **Decision**: `ai/models/rep_segmentation/mblite_rowing_v1.json` (cfg + phase model + HSMM
  decoder + provenance) is the product's rep-segmentation model; loading verifies its fingerprint
  against the content. The runtime refit on Mendeley TRAIN remains available
  (`verify_baseline.py --source refit`) as the reproduction path.
- **Evidence**: reps byte-identical to the refit on all three suites; fingerprint = manifest.

## 2026-10-10 — CUDA torch build adopted; CPU stays the numeric reference (P4.0)
- **Decision**: torch 2.14.0+cu130 replaces the CPU-only wheel (same version). Frozen numbers,
  regression tests and cache rebuilds use CPU; CUDA is an optimization whose accuracy impact is
  always reported (cuda_V2 live 0.783 vs cpu_V2 0.806, inside the pre-registered 0.03 band).
- **Evidence**: all 8 headline metrics byte-identical after the swap. EXPERIMENT_REGISTRY.md P4.0.

## 2026-10-09 — Phase 3 safety: explicit abstention + explicit input errors
- **Decision**: `ai/core/guards.py::safe_predict_video` wraps the frozen decoder with six named
  abstention statuses; invalid fps, invalid `Cfg` and empty annotation files raise explicitly.
- **Evidence**: 0/45 false abstentions on real videos; reps byte-identical when OK.

## 2026-10-09 — Phase 2 decisions: two fixes rejected, confidence labelled heuristic
- **Rejected** per-video label-free depth-sign selection (P2.1b): it helped internal_sr
  (0.8406→0.9667) but regressed mendeley_val (0.9137→0.7685) and external pull F1 (0.842→0.316).
  The model's own likelihood is not a trustworthy sign arbiter. CMU Panoptic later confirmed that
  the sign convention is right for rear cameras and inverted for front/side views.
- **Rejected** paused-tempo window-extent trimming (P2.2b): mixed results, and it regressed
  untouched normal tempo (0.8406→0.8235). Next idea, untried: gate on near-zero phase velocity.
- **Accepted** `Indicator.confidence_score` as an explicitly HEURISTIC score, not a probability;
  calibration is blocked on Domain C labels (P2.4).
- Numbers: EXPERIMENT_REGISTRY.md.

## 2026-10-08 — External wild stress test: PARTIAL EXTERNAL TRANSFER; no model change
- **Decision**: the Instagram Domain-B result (frozen MB-Lite zero-shot: pull-rule F1 0.842 /
  IoU F1 0.632 over 10 certain reps, 3 athletes) is recorded as **PARTIAL EXTERNAL TRANSFER**;
  per the pre-declared frozen-model rule NOTHING in the pipeline changes because of it.
- **Evidence**: ai/outputs/external_stress_test/{metrics,predictions,diagnostics,summary};
  docs/EXTERNAL_REAL_WORLD_STRESS_TEST.md. Failure attribution: 6/7 IoU-rule errors = window
  extent under paused tempo (TEMPORAL MODEL, decoded tempo 4.2 s vs 1.65 s train median);
  1 rear-view FN (CAMERA VIEW); 1 pull-rule error = annotation ambiguity inside a 1 s hold.
  Tracking/2D/lifting clean on all units despite phone compression, dark footage, overlays.
- **Consequences**: Domain C spec gains two requirements (≥1 slow/paused-tempo set; rear view);
  new MEDIUM risk "window extent under paused tempo" in the register; any future extent fix is
  developed on TRAIN/VAL only and validated on Mendeley + Domain C, never on the reels.
  The three domains (A internal / B wild / C hardware validation) stay unmerged.

## 2026-10-08 — Form Analysis v1 controlled decisions (mirrored from FORM_ANALYSIS_V1.md)
- Signal-xcorr lag REPLACED phase-peak lag (0/15 → 3/15 asymmetry hits at equal false rate);
  `lr_onset_lag` REMOVED (degenerate, p95 = 0.0); thresholds are EXERCISE-SCOPED (back
  thresholds on seated row inverted the sanity check); seated-row thresholds PROVISIONAL
  (15 reps, 1 athlete) until the fresh dataset. Legacy pre-scoping file
  ai/models/form_thresholds_v1.json deleted 2026-10-08 (superseded; nothing references it).

## 2026-10-05b — MotionBERT-Lite becomes PRIMARY for the seated-row product path
- **Decision**: deployment/product primary = MotionBERT-Lite; PoseFormerV2 demoted to in-domain
  benchmark primary / future candidate pending in-domain seated-row training data.
- **Evidence**: Phase 2.8 zero-shot seated row — MB-Lite F1 0.841/0.853 (R 0.97, pull MAE 0.13 s)
  vs PFv2 0.303/0.394; fps-matched control (15 fps) still 0.338 → exercise/view brittleness,
  not fps. Same tracked 2D for both. See docs/SEATED_ROW_TRANSFER_2_8.md.
- **Consequences**: form-analysis v1 builds on MB-Lite windows; PFv2 revisited when fresh
  seated-row training data exists; roadmap risk 'cross-exercise transfer' upgraded for PFv2.

## 2026-10-05 — Phase 2.8 (seated-row zero-shot transfer) chosen as next step
- **Decision**: run the frozen Phase 2.7 systems on the 6 seated-row videos BEFORE starting form
  analysis, backend resumption, or realtime work.
- **Alternatives**: start form analysis; resume Supabase/dashboard; realtime feasibility; fresh-data
  collection first.
- **Evidence/reason**: domain gap is the top CRITICAL risk; the experiment is cheap (hours), gates
  form analysis (needs trustworthy seated-row rep windows), and tests the 30 fps claim end-to-end.
  Fresh-data collection needs the owner physically and can proceed in parallel.
- **Consequences**: form analysis starts only after transfer results are known.

## 2026-10-05 — PoseFormerV2 replaces MotionBERT-Lite as PRIMARY pose representation
- **Decision**: PFv2 primary; MB-Lite fallback/speed/timing reference; MotionBERT retired to reference.
- **Alternatives**: keep MB-Lite (2.5C winner); keep MotionBERT (production continuity).
- **Evidence**: Phase 2.7 locked TEST (unseen subjects): PFv2 F1 0.939 vs 0.825/0.812; side view
  0.924 vs 0.473/0.383; per-subject worst-case 0.877 vs ~0.65; best 3D stability proxies (bone CV
  0.066). MB-Lite retains best boundary timing (0.094 s pull MAE), speed (87.9 fps lift, 64 MB) and
  curl transfer (0.650).
- **Consequences**: PFv2's fast-rep gap (0.850) and weaker cross-exercise transfer are accepted,
  tracked in the risk register. License check: PFv2 repo is MIT (compatible with product use).

## 2026-10-05 — Evaluation protocol v2 (edge don't-care + relaxed uncertain absorption)
- **Decision**: unmatched predictions outside the annotated span are ignored; uncertain reps absorb
  at most one prediction under pull-tol OR IoU>=0.25.
- **Evidence**: failure attribution showed 106/166 baseline FPs were artifacts of the deliberate
  censored-edge/uncertain annotation policy; recall is untouched by the change; 10 guard tests
  prevent abuse. v1 results preserved for comparison.

## 2026-10-05 — Supervision = verified+auto, uncertain masked
- **Evidence**: controlled ablation (verified-only: VAL 0.910 vs 0.914) shows auto labels are
  supervision-grade. Uncertain frames are excluded from all fitting (never forced into the loss).

## 2026-10-04 — One-phase-at-a-time protocol; dataset/split freeze discipline
- Owner-directed; superseded 2026-10-05 by technical-lead autonomy (this log + roadmap now carry
  the decision burden). Frozen artifacts (annotations, split, baselines, tags) remain immutable.

## 2026-10-04 — Benchmark subject tracker replaces highest-confidence selection (benchmark only)
- **Evidence**: 2.5B audit — conf-best switches to background people on 15/34 videos (up to 110
  jumps). Tracker: largest-init + IoU continuity + re-acquisition over 8 area-sorted candidates;
  1.8% lost frames on the expanded set; later exonerated as an error source (Phase 2.7 attribution).

## 2026-10-02 — Phase 2 survival-law fix; claimed 0.93 retired; Path B rebuild
- See CHANGELOG commits 8d80fce / 60074c4: honest measured baselines replaced unreproducible claims;
  structural decoder correction validated by full nested LOVO (0.87 -> 0.93/0.97 same-athlete LOVO).
