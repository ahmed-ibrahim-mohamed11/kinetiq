# CURRENT_HANDOFF_REPORT.md — verified current state

# ▶ HANDOFF 2026-10-10 (CURRENT — read this first; the 2026-10-08 snapshot below is historical)

Working under the owner's master prompt (Phases 0–5 now, 6 on fresh-athlete recordings, then AI
CORE FREEZE, then 7; DB/UI/deploy DEFERRED until "START CLOSING PHASE"). After any context
compaction: re-read THIS section, docs/EXPERIMENT_REGISTRY.md (pre-registered plans + log),
docs/RISK_REGISTER.md, and the master-prompt global rules (baseline-first, pre-registered
accept/reject, subject-level splits, final test data never touched, licensed data only, Mode A
never degraded for Mode B, no unmeasured claims, one milestone = one commit).

Environment notes: git = `%LOCALAPPDATA%\Programs\MinGit\cmd\git.exe` (not on PATH); Python 3.10.11
per-user + repo `.venv` (repointed); torch **2.14.0+cu130** (RTX 5050 Laptop GPU usable). Use
`git commit -F <msgfile>` for multi-line messages (PowerShell here-strings mis-split).

## Phase status

| phase | status | commits |
|---|---|---|
| 0 — audit + baseline lock | **DONE** | `35eccfb` |
| 1 — data acquisition | **DONE** | `13a8034` |
| 2 — training/improvement (P2.1–P2.5) | **DONE — all five executed and decided** (details below) | `c2670eb`..`2451739` |
| 3 — robustness & safety (P3.0–P3.7) | **DONE** | `159e876`..`ef51cca` |
| 4 — live mode | **DONE** (updated later 2026-10-10): GPU `cuda_V2` 1.31×, CPU-only `cpu_V3` 1.07×, measured on AC; R-17 session-length limit | `6955cf5`, `c742b28` |
| 5 — AI service | **DONE** except the Docker build (Docker not installed) | `d91d6a4`, `a72719e`, `a73cc8f`, `9e91249` |
| 6 / 7 | not started (6 needs owner recordings) | — |
| DB / UI / deploy | **DEFERRED** (owner instruction) | — |

## Phase 2 — status and numbers (P2.1–P2.5 all DONE)

- **P2.1 orientation/depth-sign — REJECTED fix + new evidence.** Yaw sweep (synthetic): internal_sr
  is a step function (IoU F1 0.8406 for yaw 0–90°, 0.9667 for 105–180°); mendeley_val degrades in a
  staggered way (0.9137 → 0.623 at 105°). Per-video label-free auto-sign (pick higher Viterbi
  likelihood): internal_sr 0.8406→0.9667 and VAL side-view 0.760→0.859, BUT mendeley_val pooled
  0.9137→**0.7685** and external pull F1 0.842→**0.316** ⇒ REJECTED by its own gate.
  **P2.1-panoptic (real multi-view 3D GT, CMU Panoptic)**: PA-MPJPE **4.21 / 10.07 / 12.64 cm**
  (front / back / side; side also unstable, σ 5.8 cm); backward-signal correlation vs GT
  **−0.926 / −0.708 / +0.776** ⇒ the `FACING_IN_CAMERA` sign convention is correct for rear-ish
  cameras (our product setup) and inverted for front/side. First real-3D-GT number ever (R-11).
- **P2.2 paused-tempo extent — REJECTED fix.** Synthetic END-hold 0.5 s: internal_sr 0.841→0.720,
  mendeley_val 0.914→0.764 (closest match to the real external failure). Plateau-trim fix: mixed/
  negative (pull-hold case −0.15; untouched normal tempo regressed tp 29→28). Found **R-16**
  (`estimate_tempo` crash) — fixed in P3.0.
- **P2.3 form indicators — DONE, none dropped.** `rom_proxy` blind spot: 0/48 Mendeley diagonal-view
  bad reps, 0 on medium/slow-tempo bad reps. `lr_amplitude_ratio` clean seated-row check = exact
  parity 0.067/0.067 (the old 4/26-vs-2/21 claim is retracted). `trunk_motion` clean seated-row
  0.400 vs 0.067 (6×). `lr_lag` unchanged.
- **P2.4 confidence — ACCEPTED.** Heuristic = validity + raw pose confidence + threshold margin;
  explicitly NOT a probability; true calibration BLOCKED on Domain C deviation labels.
- **P2.5 annotation noise + gates — DONE.** F1 robust (0.9137→0.8982 at ±5 frames); pull MAE not
  (0.079→0.207 s); the MB-Lite vs PFv2 pull-MAE gap (0.041 s) is within ±2-frame noise. Domain C
  gates: 4/5 confirmed as-is; per-view gate flagged AT ELEVATED RISK on side/oblique (unchanged).

## Phase 3 highlights (DONE)
R-16 fixed (true no-op on real data). Failure injection: wrist dropout has ZERO effect at any
severity; shoulder dropout ≡ elbow dropout; resolution jitter σ0.05 → internal_sr 0.506
(mendeley_val 0.848 — far worse out-of-distribution); confidence noise σ0.3 → 0.648;
inconsistent tempo 0.597 (worse than constant 0.7× slowdown 0.704). `ai/core/guards.py`
(abstention + exercise guard): 0 false abstentions on 45 real videos. P3.7 fixed silent-output
gaps (bad fps, invalid Cfg, empty annotations). docs/ROBUSTNESS_REPORT.md.

## Phase 4 so far
torch → cu130, baseline byte-identical. Production `ai/core/{tracking,live}.py`,
`lifting.MotionBertLite`, `pose.YoloTopK` — each proven equal to its frozen research reference.
Live from RAW video: **cpu_V2 IoU F1 0.806 (27/10/3) = validated prototype**; cuda_V2 0.783
(27/12/3). Micro-bench (idle, AC): YOLO CPU 28.6 ms / CUDA 12.7 ms; MB-Lite 243-window TTA
CPU 1,450 ms / CUDA 116 ms (fp16 44 ms). Smoke cuda_V2 (AC): 23.8 ms/frame, 1.40× realtime,
latency p95 69 ms — **indicative only, no realtime claim yet**. Full timing matrix DISCARDED (laptop
slept + switched to battery mid-run); harness now refuses battery and flags suspend gaps.
Bug fixed: `form_indicators._xcorr_lag_s` crash on short windows. docs/LIVE_MODE.md.

## Phase 5 so far (working tree, NOT yet committed at this handoff)
- P5.1 `ai/core/model_artifact.py` + `ai/scripts/export_model_artifact.py` →
  `ai/models/rep_segmentation/mblite_rowing_v1.json` (13,275 B, fingerprint 63d36c31… = manifest);
  `ai/tests/test_model_artifact.py` 6/6; `verify_baseline.py --source artifact` reproduces all
  headline metrics; `ai/core/io.py::segment_video/load_model` now work (core of R-01 closed).
- P5.3 `ai/core/schema.py` (AnalysisResult v1.0, strict) + `ai/schemas/analysis_result.v1.json` +
  `ai/tests/test_schema.py` 16/16 (incl. mapping onto the existing edge-event contract).
- P5.2 `ai/core/pipeline.py`, `ai/scripts/analyze_set.py`, `ai/tests/test_pipeline.py` — written,
  NOT yet run.
- `ai/outputs/phase4/` holds only the indicative smoke-run CSVs — do not commit as evidence.

## Datasets touched this session (licences in docs/LICENSING.md, registry ai/data/dataset_registry.json)
- **CMU Panoptic Studio** `171204_pose1_sample` — DOWNLOADED (~65 MB: calibration, 3D body GT for
  101 frames, 7 of 31 HD views) to `ai/data/panoptic/` (git-ignored). Licence: non-commercial
  research only, no account, redistribution forbidden → never committed.
- MM-Fit — CC BY 4.0, approved, NOT downloaded. Fit3D — DROPPED (owner). QEVD — needs owner EULA
  approval, not downloaded. AIST++ / MPI-INF-3DHP — not pursued. RepCount-A — excluded
  (YouTube-scraped). Final-test (Domain C) data — does not exist yet, never touched.

## LATEST — owner decisions + four tasks (2026-10-10, later)
Owner decisions applied (`09b9e1b`): YOLO academic/non-commercial only; Ultralytics `sync` off
(product path makes zero network calls); yt-dlp removed; QEVD skipped; Docker deferred to closing.
Owner's task list, in order: (1) last pre-freeze attempt on R-07/R-08 — **P2.7 DONE, REJECTED**
(pre-registered `badf440`; anatomical sign: LOSO side 0.654 → 0.853 but internal_sr 0.841 → 0.558;
R-07/R-08 known limitations, no further tuning); (2) Phase 6 pre-registration
(`docs/PHASE6_PREREGISTRATION.md`) + runner (`ai/scripts/phase6_evaluate.py`) + freeze tag
`ai-core-candidate-v1` (NO model/threshold/config change after the tag); (3) intake tooling
(`validate_recordings`, `annotate_reps`, `annotation_agreement`, `docs/PHASE6_ANNOTATION_GUIDE.md`,
spec v1.1); (4) `docs/CLEANUP_PLAN.md` audit only — delete nothing without owner approval. Then STOP
and wait for the owner's recordings.

## EARLIER (end of 2026-10-10)
Phases 0–5 are at DoD as far as possible without the owner. P5.5 was verified from a fresh clone
(`9e91249`). Phase 4 timing was measured on AC: GPU `cuda_V2` 1.31×, CPU-only `cpu_V3` 1.07×
(docs/LIVE_MODE.md). A Phase 2 DoD gap found in review is closed: LOSO for the trained
components, pooled 0.878 with side view 0.654 (P2.6, `8303ce1`). Current scorecard: top of
TECHNICAL_MASTER_REPORT.md. Stop condition (C) is reached. The remaining work needs the owner:
Domain C recordings (Phase 6), the YOLO AGPL decision, and a Docker install (Dockerfile
verification). Optional engineering without owner input: a sliding decode buffer (R-17).

## EXACT NEXT STEP (as written earlier on 2026-10-10; items 1–3 since completed)
Done since this handoff was first written: P5.1–P5.3 (`d91d6a4`), P5.4 service + P5.7 privacy
(`a72719e`), P5.5 tooling + P5.6 regression tests + P5.7b telemetry fix + P5.8 boundaries/README
(Phase 5 part 3 commit). All details in EXPERIMENT_REGISTRY.md.
1. **P5.5 clean-clone verification**: `git clone` this repo into a temp dir, fresh venv (per-user
   Python 3.10.11, CPU torch from the PyTorch CPU index), then follow docs/REPRODUCIBILITY.md
   literally (download_checkpoints → pytest → rebuild_pose_caches → verify_baseline --with-causal →
   analyze_set on a dev video). Compare the rebuilt caches with this repo's caches, then record the
   results in REPRODUCIBILITY.md's verification log and a registry row, and commit.
2. Phase 5 DoD check, then the STOP-condition (C) status report (phase table, scorecard, top risks,
   owner actions).
3. When the laptop is on AC power: `python ai/research/phase4/live_benchmark.py` (full matrix) and
   `--long-session cuda_V2 cpu_V2_notta_320 cpu_V2`, then decide P4.3–P4.5 per the pre-registered
   criteria and update docs/LIVE_MODE.md.

## Owner actions pending
Plug the laptop in (Phase 4 timing) · fresh ≥10-athlete recordings (Phase 6) · YOLO AGPL-3.0
decision · Docker install (only to verify the Dockerfile) · optional: QEVD EULA decision.

---

# HISTORICAL SNAPSHOT 2026-10-08 (preserved as reviewed)

> **Post-script (2026-10-08, after owner approval)**: the external stress test described as
> "in progress" below was completed the same day — verdict **PARTIAL EXTERNAL TRANSFER**
> (commit `bc3472e`, docs/EXTERNAL_REAL_WORLD_STRESS_TEST.md; suite now 98 tests). The stale
> docs flagged in §19 were fixed in the follow-up hygiene commit. The body below is preserved
> as the point-in-time snapshot the owner reviewed.

Prepared on owner request ("pause new development, report, stop"). Every number below was
re-read from repository artifacts on 2026-10-08, not from memory or older docs. Where a document
and an artifact disagree, the artifact wins and the conflict is flagged (§19). No development,
tuning, model change, dataset change, or deployment was performed for this report; the only runs
were (a) the already-in-flight external-video pose extraction / GT-draft generation started
before the pause order, and (b) the existing test suite (read-only verification, allowed).

Baseline for "since the last approved state": commit `084c282` (TECHNICAL_MASTER_REPORT.md,
2026-10-08), the last owner-reviewed checkpoint. Everything before it is audited in that report;
this report covers the delta plus a full verified status snapshot.

---

## 1. WHAT DID YOU DO (since 084c282)

All of it belongs to the owner-ordered EXTERNAL_WILD_STRESS_TEST track (Instagram reels).
**None of it is committed yet** (deliberately: track incomplete, raw videos must never be
committed). No other development occurred.

### Task 1 — URL intake and deduplication
- Why: owner supplied 9 Instagram reel URLs; spec requires URL-level dedup before ingestion.
- Result: 9 URLs → **8 unique** (C5gFNP_Iega appeared twice). Mapping frozen in
  `ai/data/external_stress_test/url_map.json` (ext_01…ext_08 → shortcode + URL).
- Files: `ai/data/external_stress_test/url_map.json`.

### Task 2 — Download of all 8 reels
- Why: spec requires local files or a stop-and-ask; direct download succeeded, so no owner action
  was needed.
- Method: yt-dlp with `--no-check-certificates` (this machine's SSL CA interception broke the
  default path; first attempt failed with CERTIFICATE_VERIFY_FAILED — failure recorded, retried
  with the documented workaround).
- Result: 8/8 downloaded to `ai/data/external_stress_test/raw/` (ext_01…ext_08.mp4). MD5s
  recorded; **no content duplicates** among the 8. `.gitignore` extended with
  `ai/data/external_stress_test/raw/` so the videos can never be committed (licensing rule:
  reference/hash only, no redistribution).
- Files: 8 mp4 (git-ignored), `.gitignore` (modified, uncommitted),
  `ai/outputs/external_stress_test_probe.json` (per-video fps/frames/duration/resolution/md5).

### Task 3 — Technical probe
- Result: all 8 are 720×1280 portrait; fps 25 (ext_03) or ~30 (rest); durations 13.1–95.2 s;
  frame counts 394–2,856.

### Task 4 — Visual audit (contact sheets + dense sheets)
- Why: spec requires per-video inclusion decisions before any annotation.
- Result classification (quantitative = confidently annotatable rowing with visible pose):
  - **QUANT, full video**: ext_01 (close-up machine row, orbiting camera, ~5–6 reps,
    medium/low annotation confidence — hips cropped), ext_02 (static side-view seated cable row,
    ~11 reps, high confidence).
  - **QUANT, segments**: ext_03 frames 237–330 and 361–470 (multi-shot reel; two usable rowing
    shots), ext_08 frames 160–250 and 310–435 (two side-view strict-row shots).
  - **QUALITATIVE_ONLY**: ext_04 (montage/occlusion), ext_05 (slow coached demo with suspected
    caption cuts between reps), ext_06 (13 s three-exercise montage with overlays), ext_07
    (montage), ext_08 segment A (~frames <160, suspected cut mid-rep).
  - **EXCLUDED**: ext_08 tail (≥~440: different exercise — shrug station).
  - Expected GT volume: **~27–28 reps across 6 quantitative units** (to be fixed by manual
    annotation, not yet done — see §8).

### Task 5 — Frozen-pipeline pose extraction (completed during this report)
- Why: 2D/3D caches are needed both for GT-draft signals and the later evaluation; extraction
  itself involves no tuning.
- Method: the exact frozen production path — YOLO11n-pose top-8 area-sorted candidates →
  deterministic tracker → MotionBERT-Lite lifting. **No parameter was changed.**
- Result: all 8 videos extracted. Tracking on external footage is clean: 16 lost frames total
  across 7,361 frames (worst: ext_03 with 15 lost / 3 track jumps; five videos 0 lost / 0 jumps).
- Files: `ai/outputs/external_stress_test/{2d_ext_XX.npz, pose_ext_XX.npy, pose_ext_XX_conf.npy}`.

### Task 6 — GT draft generator (assisted annotation, same standard as Phase 2.5B)
- Why: GT must be human-made and independent of the evaluated decoder; the Mendeley workflow uses
  a signal-based proposer + human frame-level verification, so the same tool was adapted.
- Script: `ai/research/external_stress/annotate_external.py` (new). 6 units defined
  (ext_01 full, ext_02 full, ext_03 segA/segB, ext_08 segB/segC); full-video signal, proposals
  filtered to each unit's frame window; drafts + verification images written to
  `ai/outputs/external_stress_test/drafts/`.
- **Bug found and fixed before any GT existed**: first version computed segment-relative signals
  but rendered verification frames with absolute indices (mismatched images). Patched to
  full-video signal + window filtering; absolute 0-based frames throughout.
- Result (completed during this report): drafts generated — ext_01 3, ext_02 6, ext_03 segA **0**,
  ext_03 segB 1, ext_08 segB 1, ext_08 segC 3 = **14 auto-drafts vs ~27–28 expected reps**.
  The Mendeley-tuned proposer under-fires on smartphone footage, as anticipated; the drafts are
  only an annotation aid. **Full manual verification/annotation pass is required and has NOT
  been done.** GT is therefore NOT final; no evaluation has been run.

### Not done (and correctly so)
- No annotations.csv / manifest.csv finalized; no MB-Lite or PFv2 evaluation on external videos;
  no failure taxonomy; no stress-test doc; no commit. The track was paused at exactly this point
  by the owner's report order.

---

## 2. CURRENT PROJECT STATUS

| area | status | one-line evidence |
|---|---|---|
| Rep segmentation (in-domain) | **VALIDATED** | locked unseen-subject TEST, protocol v2: PFv2 F1 0.939 / MB 0.825 / MB-Lite 0.812 (`ai/outputs/phase27/summary_test_iou.csv`) |
| Rep segmentation (seated row) | **PARTIAL** | zero-shot F1 0.841/0.853 but on 6 same-athlete dev videos only (`ai/outputs/phase28/summary.json`) |
| MotionBERT-Lite | **VALIDATED** (as product primary) | 2.8 transfer + speed/size/timing artifacts (§3) |
| PoseFormerV2 | **PARTIAL** | best in-domain (0.939) but **FAILED** zero-shot seated row (0.303; fps ruled out) |
| Form Analysis v1 | **PARTIAL** | rom_proxy directional-validated (AUC 0.778); 3 indicators partial; seated-row thresholds provisional (15 reps) |
| External Instagram stress test | **PARTIAL (in progress, paused)** | ingestion/audit/extraction done; GT annotation not finished; **zero evaluation run** |
| Fresh unseen-athlete validation | **BLOCKED** (owner recording) | spec + automated gate ready (`validate_unseen_dataset.py`, 7 tests) |
| Realtime / causal inference | **NOT STARTED** | no causal pipeline exists; nothing measured |
| Multi-person support | **PARTIAL** | single-subject *selection* in crowded scenes validated (tracker, 1.8% lost on 165 videos); concurrent multi-person NOT STARTED |
| 3D stability | **PARTIAL** | proxy metrics only (bone CV, jitter); no mocap ground truth — by design, never claimed as metric accuracy |
| Dataset tooling | **DONE** | audit → proposer → manual annotator → QC → split freezer → validator; frozen artifacts hashed |
| Evaluation tooling | **DONE** | don't-care protocol v1/v2 (10 guard tests), canonical result schema (6 tests), failure taxonomy, fps-consistency harness |
| Code quality | **DONE** (current scope) | 90/90 tests pass in 38 s; core modules regression-locked |
| Supabase | **PARTIAL + BLOCKED** | schema/RLS/seed/contract code-complete (commit 0bf7007); deploy blocked on owner keys |
| Vercel | **NOT STARTED** (blocked: owner account) | — |
| UI | **NOT STARTED** | by owner-approved order (G last) |
| Unreal | **NOT STARTED** | optional, never a dependency |

---

## 3. CURRENT PRIMARY MODEL

- **Seated-row PRODUCT PRIMARY: MotionBERT-Lite** (frozen backbone, DSTformer lifting, 243-frame
  windows, flip-TTA).
- **In-domain BENCHMARK PRIMARY / reference: PoseFormerV2**; MotionBERT retired to reference.

Evidence (all re-read from artifacts today):
- `ai/outputs/phase28/summary.json`: seated-row zero-shot — MB-Lite IoU F1 **0.841**
  (TP 29, FP 10, FN 1), pull-rule F1 **0.853**; PFv2 **0.303 / 0.394** on identical tracked 2D.
- fps-matched control (15 fps input): PFv2 still 0.338 (recorded in
  docs/SEATED_ROW_TRANSFER_2_8.md) → collapse is exercise/view brittleness, not frame rate.
- `ai/outputs/phase27/summary_test_iou.csv`: in-domain unseen-subject TEST (back, 318 reps,
  protocol v2): PFv2 0.939 > MB 0.825 > MB-Lite 0.812 — which is why PFv2 keeps the in-domain
  benchmark title.
- `ai/outputs/pose_bench/poses/*/deployment.json`: MB-Lite 87.9 fps lifting CPU / 64.1 MB vs
  MB 24.4 fps / 170 MB vs PFv2 17.9 fps / 172.8 MB.
- Curl transfer (recomputed today from `videos_test_final.csv`, IoU rule): MB-Lite 0.650 >
  PFv2 0.418 > MB 0.370.
- Decision recorded in DECISION_LOG.md (2026-10-05b); rule: no ranking change without controlled
  evidence; in-domain scores alone cannot restore PFv2.

---

## 4. CURRENT VERIFIED METRICS (one canonical table)

Protocols are NOT directly comparable across blocks; each block is labeled. "v2" = edge
don't-care + relaxed uncertain absorption, one-to-one matching, IoU≥0.5 (pull rule where stated).

**Block A — Phase 2.7 locked TEST, Mendeley back, 5 unseen subjects, protocol v2, 15 fps**
(`ai/outputs/phase27/summary_test_iou.csv`, breakdowns in same folder)

| model | GT | pred | TP | FP | FN | P | R | F1 | START MAE | PULL MAE | END MAE | side-view F1 | bad-form F1 | fast-tempo F1 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| PoseFormerV2 | 318 | 419 | 299 | 20 | 19 | 0.937 | 0.940 | **0.939** | 0.192 s | 0.135 s | 0.217 s | 0.924 | 0.925 | 0.850 |
| MotionBERT | 318 | 467 | 281 | 82 | 37 | 0.774 | 0.884 | 0.825 | 0.154 s | 0.121 s | 0.158 s | 0.473 | 0.798 | 0.901 |
| MotionBERT-Lite | 318 | 445 | 268 | 74 | 50 | 0.784 | 0.843 | 0.812 | 0.131 s | **0.094 s** | 0.151 s | 0.383 | 0.749 | 0.782 |

**Block B — Phase 2.7 TEST cross-exercise transfer (bicep curl, 86 reps, protocol v2)**
(recomputed today from `videos_test_final.csv`)

| model | IoU-rule F1 | pull-rule F1 |
|---|---|---|
| MotionBERT-Lite | **0.650** | 0.920 |
| PoseFormerV2 | 0.418 | 0.655 |
| MotionBERT | 0.370 | 0.691 |

**Block C — Phase 2.8 seated-row zero-shot, 6 dev videos / 1 athlete / 30 GT reps, 30 fps**
(`ai/outputs/phase28/summary.json` + per-video CSVs)

| model | TP | FP | FN | R | IoU F1 | pull F1 | PULL MAE |
|---|---|---|---|---|---|---|---|
| MotionBERT-Lite | 29 | 10 | 1 | 0.967 | **0.841** | **0.853** | 0.128 s (pull-matched) |
| PoseFormerV2 | 10 | 26 | 20 | 0.333 | 0.303 | 0.394 | — (collapsed); 15 fps control 0.338 |

Per-video (MB-Lite, IoU rule): correct_01 5TP/4FP (11 pred), correct_02 & correct_03 clean 5/5,
asymmetry_01 4TP/6FP/**1FN** (15 pred, fastest tempo 0.6 s), asymmetry_02 5TP/0FP (artifact
confirms asymmetry_02 is clean — the FN sits in asymmetry_01; SEATED_ROW_TRANSFER_2_8.md still
says otherwise, see §19), asymmetry_03 clean 5/5.

**Block D — deployment characteristics, 165-video lifting run**
(`ai/outputs/pose_bench/poses/*/deployment.json`; offline lifting throughput, NOT realtime)

| model | lift fps (CPU) | ms/frame | size | load |
|---|---|---|---|---|
| MotionBERT-Lite | 87.9 | 11.4 | 64.1 MB | 0.4 s |
| MotionBERT | 24.4 | 41.0 | 170.0 MB | 1.0 s |
| PoseFormerV2 | 17.9 | 55.7 | 172.8 MB | 5.5 s |

**Block E — references with different protocols (not comparable to A–D)**
- Same-athlete seated-row LOVO baseline (tag `baseline-v0.2.0-frozen`, trained on those videos):
  F1 0.93 / R 0.97 — reference only.
- Phase 2.5C six-pipeline benchmark (31 verified videos, 410 reps, LOSO, protocol v1):
  MB-Lite 0.864 / MB 0.861 / PFv2 0.823 / MotionAGFormer 0.807 / MediaPipe 0.695 / VP3D 0.670
  (`ai/outputs/pose_bench/summary.csv`).
- fps robustness: pull events shift ≤1 native frame at 7.5/30 fps vs 15 fps
  (`ai/outputs/phase27/fps_consistency_motionbert_lite.csv`).

No END-only or START-only external metrics exist yet; external smartphone metrics do not exist
yet (test incomplete, §8).

---

## 5. PHASE 2.7 STATUS

- **Bug**: `dict() got multiple values for keyword argument 'rule'` — RESOLVED at root cause.
- **Architectural root cause**: `evaluate_dontcare()` returned run metadata (`rule`) inside its
  metrics dict while callers composed rows via `dict(**meta, **metrics)` — a schema problem, not
  a call-site typo; it recurred in Phase 2.8 precisely because the first fix was local.
- **Permanent fix**: canonical result schema `ai/research/phase27/result_schema.py` —
  `RESERVED_META` frozenset, `check_metrics()`, `make_row(meta, metrics)` raising
  `ResultSchemaError` on any collision; evaluators return metrics-only dicts; all writers go
  through `make_row`.
- **Regression tests**: `ai/tests/test_result_schema.py` (6 tests), including a fixture that
  reconstructs the old colliding pattern (per owner rule: broken code is reproduced as a test
  fixture, never reintroduced in production paths).
- **Latest rerun**: VAL + TEST rows regenerated post-fix are content-identical to the frozen
  results (column order only) — old outputs still reproduce.
- **Unresolved**: nothing in-scope. One documented data exception stands: TRAIN-only video
  `subject_007_back_bad_side` has 42% lost tracking frames (occlusion), carried as an explicit
  test exception (commit a41d759), not a hidden failure.

## 6. PHASE 2.8 STATUS

Verified from `ai/outputs/phase28/` (see Block C above): MB-Lite F1 0.841 IoU / 0.853 pull,
recall 0.967 (29/30), precision 0.744 IoU (29/39 predictions), pull MAE 0.128 s, 1 FN
(asymmetry_01), 10 IoU FPs (mostly edge-partials and fast-tempo splits on correct_01 /
asymmetry_01). PFv2: TP 10 / FP 26 / FN 20, F1 0.303 IoU / 0.394 pull; 15 fps fps-matched control
0.338 → fps excluded as the cause. Both models consumed identical tracked 2D.
**Conclusion (standing decision)**: MB-Lite is the seated-row product primary; PFv2 remains
in-domain benchmark primary and a future candidate only if in-domain seated-row training data
appears. Caveat stays: this is one athlete's six dev videos — supportive, not production evidence.

## 7. FORM ANALYSIS V1 STATUS

Implemented: `ai/core/form_indicators.py` (4 indicators, severity ok/attention, per-rep
confidence from keypoint validity; 9 unit tests) + per-exercise threshold files + calibration/
separability pipeline `ai/research/phase_c/form_v1.py`. **The overall system is NOT complete**:
it is a directional, demo-grade v1; per-deviation precision/recall is not claimable until the
fresh dataset provides per-rep deviation labels (pre-registered in UNSEEN_ATHLETE_DATASET_SPEC §5).

| indicator | implemented | formula (per rep window) | threshold source | validation evidence (artifact-verified) | verdict |
|---|---|---|---|---|---|
| rom_proxy | yes | mean L/R elbow-backward travel range, torso-lengths | back: p05 of 450 TRAIN-good reps = 0.161; seated row provisional 0.0578 | Mendeley VAL rank-AUC **0.778**; attention 29.5% bad vs 1.8% good | **VALIDATED (directional)** |
| lr_amplitude_ratio | yes | min/max of per-arm signal amplitudes | p05: back 0.5903 / SR 0.3114 | AUC 0.634; 17.1% vs 3.0% | **PARTIAL** |
| lr_lag (pull lag) | yes | cross-correlation offset of per-arm elbow signals (s) | p95: back 0.0667 s / SR 0.2833 s | AUC 0.529 on Mendeley (its "bad" isn't asymmetric — expected); seated-row direction correct (5/26 vs 2/21); synthetic test recovers 0.3 s injected lag ±0.07 | **PARTIAL** |
| trunk_motion | yes | p95−p05 trunk-backward range, torso-lengths | p95: back 0.1126 / SR 0.0272 | AUC 0.379 on Mendeley (no separation); seated-row 8/26 vs 2/21 | **PARTIAL** |
| lr_onset_lag | **removed** | — | — | measurement degenerate (p95 = 0.0 on seated row) | **REJECTED** |

False-alert behavior on good/correct data: back VAL good-rep attention rates 1.8–6.0% per
indicator (`ai/outputs/phase_c/val_separability.csv`); seated-row sanity counts ran on decoded
zero-shot windows, so correct-group counts are inflated by 2.8's edge-partial FPs (26/21 decoded
vs 15/15 GT reps) — documented limitation, not hidden. Known weaknesses: Mendeley "bad" is
video-level and unspecific; seated-row thresholds come from 15 reps of one athlete
(**PROVISIONAL**, replace before any production use); monocular 3D → proxies only.

## 8. EXTERNAL INSTAGRAM STRESS TEST — honest status: **IN PROGRESS, NOT EVALUATED**

| item | value |
|---|---|
| URLs supplied / unique | 9 / **8** (C5gFNP_Iega duplicated) |
| successfully accessed / downloaded locally | **8 / 8** (yt-dlp; no owner files needed) |
| duplicate handling | URL dedup 9→8; MD5 content check: 0 duplicates among files |
| quantitatively usable (audit verdict) | 2 full videos + 4 segments (6 units; ext_01, ext_02, ext_03×2, ext_08×2) |
| qualitative-only | 4 videos + 1 segment (ext_04, ext_05, ext_06, ext_07, ext_08 segA) |
| excluded | ext_08 tail ≥~440 (different exercise) |
| total GT reps | **not final** — expected ~27–28 from visual audit; 14 auto-drafts generated; manual annotation pass NOT done |
| manual annotations created | **NO** (drafts + verification images only; GT independent of the decoder by construction) |
| MB-Lite evaluated | **NO** (pose extraction done for all 8; zero decoding/evaluation runs) |
| per-video / pooled metrics | none exist yet |
| failure types | not yet (taxonomy prepared from 2.7 categories) |
| model tuned on these videos | **NO — nothing in the pipeline was touched**; extraction used the frozen path verbatim |

Nothing is needed from the owner for this track: all videos are local. The track paused at the
GT-annotation step by this report order. Licensing discipline is in force: raw files git-ignored,
hashes + URLs recorded, no redistribution.

## 9. FRESH UNSEEN-ATHLETE DATASET — tooling READY, recording BLOCKED (owner)

Ready and tested: collection protocol + folder structure + metadata schema + annotation schema +
acceptance criteria (docs/UNSEEN_ATHLETE_DATASET_SPEC.md, pre-registered: pooled IoU F1 ≥0.80 AND
R ≥0.85; per-athlete F1 ≥0.65 for ≥9/10; per-view ≥0.70; pull MAE ≤0.25 s; indicator direction
≥3/5); automated PASS/FAIL gate `ai/research/dataset_validation/validate_unseen_dataset.py`
(quality, leakage vs Mendeley subjects, byte-level duplicate check, rep-ordering validity) with
7 tests. **Owner must physically provide**: recordings per the spec — ≥10 new athletes on the
seated-row machine, 3 views, good + deviated reps, per-rep deviation notes; nothing else is
missing.

## 10. REALTIME / CAUSAL STATUS — **NOT VALIDATED**

No causal/realtime pipeline exists. No end-to-end FPS, latency, look-ahead, or resource numbers
have ever been measured. The only related artifact is offline lifting throughput (87.9 fps CPU,
MB-Lite), which is explicitly NOT realtime evidence: the pipeline is non-causal (243-frame
window + flip-TTA + whole-video HSMM decode). A causal prototype is startable without new data
(roadmap item), but it has not been started.

## 11. DATASETS

| dataset | role | train | val | test | transfer | stress | subjects/exercises/views | annotations | licensing | status |
|---|---|---|---|---|---|---|---|---|---|---|
| Mendeley (expanded, frozen) | core dev dataset | ✔ (14 subj) | ✔ (5) | ✔ (5, locked) | curl subset | — | 24 subj / back+curl / 3 views | 2,472 reps, tiers verified 443 / auto 1,412 / uncertain 617 (don't-care) | CC BY 4.0 — clean | **USED** |
| Original 6 seated-row videos | dev/transfer + provisional form calibration | ✖ (zero-shot) | — | — | ✔ (2.8) | — | 1 athlete / seated row | 30 GT reps manual | owner's own footage | **USED** |
| Instagram 8 reels | external wild stress test | ✖ never | ✖ | ✖ | — | ✔ (in progress) | ~8 athletes / row variants / phone | GT pending | eval-only; never redistributed/committed | **USED (ingested), eval pending** |
| Fresh unseen-athlete set | production validation gate | ✖ | — | single-shot eval | — | — | ≥10 athletes / seated row / 3 views | per-rep + deviations (spec) | owner-recorded | **PLANNED / BLOCKED** |
| QEVD | — | — | — | — | — | — | — | — | — | **NOT USED** (never downloaded) |
| Kaggle workout dataset | — | — | — | — | — | — | — | — | — | **NOT USED** |
| H36M | only via pretrained lifter weights | indirect | — | — | — | — | — | — | upstream model licenses | **USED (weights only)** |

Model licenses: MotionBERT/Lite Apache-2.0, PFv2 MIT, YOLO11n-pose **AGPL-3.0 (product flag — owner
decision needed before commercial deployment)**, VideoPose3D CC BY-NC (excluded from product).

## 12. GENERALIZATION STATUS (say which kind, always)

| axis | status | evidence |
|---|---|---|
| same-subject (seated row) | **VALIDATED** | LOVO 0.93/0.97 (frozen baseline tag; trained on those videos) |
| unseen-subject (back, in-domain) | **VALIDATED** | locked TEST: 0.939 / 0.825 / 0.812 (Block A) |
| cross-view | **PARTIAL** | PFv2 side 0.924; MB 0.473 / MB-Lite 0.383 — side view is the MB-family's open weakness |
| cross-tempo | **PARTIAL** | fast: PFv2 0.850, MB-Lite 0.782; slow: 0.980 / 0.859 |
| good vs bad form (segmentation robustness) | **PARTIAL** | bad-form F1: PFv2 0.925, MB-Lite 0.749 |
| cross-exercise (curl) | **PARTIAL** | MB-Lite 0.650 IoU (0.920 pull-rule); others worse |
| seated row (zero-shot exercise+view+fps shift) | **PARTIAL** | 0.841/0.853 but one athlete, 6 videos |
| external smartphone footage | **NOT TESTED** | stress test in progress; zero metrics exist |
| fresh unseen athletes (hardware camera) | **NOT TESTED** | blocked on recording |
| multi-person (concurrent) | **NOT TESTED** | only single-subject selection in crowds is validated |
| realtime | **NOT TESTED** | no causal pipeline |

## 13. WHAT FAILED (and what it taught us)

| failure | root cause known? | fixed? | fix retained? | lesson |
|---|---|---|---|---|
| PFv2 seated-row zero-shot collapse (0.303; 15 fps control 0.338) | yes — exercise/view brittleness, fps ruled out | not "fixable" — model demoted | decision logged | in-domain rank ≠ transfer rank; test transfer before trusting any leaderboard |
| result-dict `rule` collision (hit 2.7, recurred in 2.8) | yes — metadata inside metrics dicts | yes — canonical schema + make_row | yes (6 regression tests; reruns content-identical) | local patches to schema bugs recur; fix the contract |
| verified-only supervision ablation, first run (F1 0.000, 435 ignored) | yes — demotion wrongly applied to VAL GT | yes | corrected result 0.910 vs 0.914 → ablation **rejected**, auto labels kept | eval-side and train-side label policies must be separated explicitly |
| conf_thr 0.3→0.5 ablation | — | — | **rejected** (VAL 0.914→0.892; side 0.760→0.690) | the default confidence gate was already right |
| conf-best person selection (pre-2.5C) | yes — background people steal selection (15/34 videos) | yes — area-sorted top-8 + tracker + re-acquisition | yes (1.8% lost frames on 165 videos; later exonerated as an FP source) | crowded-gym footage needs identity continuity, not per-frame confidence |
| subject_007_back_bad_side tracking (42% lost) | yes — occlusion | documented exception (TRAIN-only) | yes (explicit test exception, a41d759) | some footage is honestly untrackable; mark it, don't hide it |
| phase-peak lag indicator (0/15 hits) | yes — shared-weight phase model over-smooths per-arm timing | replaced by signal cross-correlation lag (3/15 at equal false rate) | yes | measure asymmetry on signals, not on decoded phases |
| lr_onset_lag indicator | yes — degenerate (p95 = 0.0) | removed | yes | revisit only with per-rep deviation labels |
| cross-exercise form thresholds | yes — back thresholds inverted seated-row sanity (0% vs 24%) | exercise-scoped thresholds | yes | form calibration does not transfer across exercises |
| Instagram download, first attempt (SSL) | yes — machine-wide CA interception | yes — `--no-check-certificates` | documented workaround | — |
| external GT drafter v1 (frame mismatch) | yes — segment-relative signal vs absolute frames | yes — patched before any GT existed | yes | — |
| legacy claimed 0.93 baseline (pre-Phase-B) | yes — unreproducible | rebuilt honestly (Path B) | yes (tag baseline-v0.2.0-frozen) | never carry unreproducible numbers |

## 14. CURRENT BLOCKERS

| blocker | severity | why it matters | solvable alone? | needs owner? | needs recording? | needs credentials? | blocks AI freeze? | blocks grad demo? | blocks production? |
|---|---|---|---|---|---|---|---|---|---|
| Fresh ≥10-athlete recordings | **CRITICAL** | only path to production-grade validation + real form-indicator calibration | no | yes | yes | no | yes (production freeze) | no | **yes** |
| External stress test unfinished | **HIGH** | only near-term evidence on real-world smartphone domain | **yes** (paused by owner order, not by any technical block) | approval only | no | no | partially (informs freeze) | no | indirectly |
| MB-Lite side-view weakness (0.383) | HIGH | limits camera placement freedom for the product | partially (needs data or model work) | for data | eventually | no | no | no (demo can fix camera angle) | yes |
| Realtime path unmeasured | HIGH (product) / MED (demo) | product claim "live feedback" unproven | yes (prototype) | hardware choice | no | no | no | partially | yes |
| Supabase keys | HIGH (for integration) | Phase E cannot deploy | no | yes | no | yes | no | yes (demo spine) | yes |
| Vercel account | MEDIUM | Phase F | no | yes | no | yes | no | yes (demo spine) | yes |
| YOLO11 AGPL-3.0 | MEDIUM | commercial licensing exposure | no | yes (decision) | no | no | no | no | yes |
| Seated-row form thresholds provisional (15 reps) | MEDIUM | form v1 is demo-grade only | no (needs fresh data) | via recording | yes | no | no | no | yes |

## 15. WHAT I NEED FROM YOU (only owner-actionable items)

1. **Record the fresh unseen-athlete dataset** per docs/UNSEEN_ATHLETE_DATASET_SPEC.md
   (≥10 new athletes, seated row, 3 views, good + deviated reps, per-rep deviation notes).
   This is the single highest-leverage owner action in the project.
2. **Approval to resume** the external Instagram stress test (everything needed is local; ~the
   remaining work is manual GT annotation + the frozen evaluation + report).
3. **Supabase project keys** (when you decide to open the integration phase — gated behind the
   ML validation gate per the approved order).
4. **Vercel account** (same gate).
5. **Hardware decision** for the realtime target (which machine/camera the demo must run on).
6. **Licensing decision** on YOLO11n-pose AGPL-3.0 for any commercial path (academic demo is fine).

No video files are needed — all 8 reels downloaded successfully.

## 16. WHAT SHOULD HAPPEN NEXT (recommendation only — not started)

**Single highest-value step: finish the external Instagram stress test.**
- Why now: it is 70% done, costs zero owner resources, touches no frozen component, and is the
  only available evidence about the real-world smartphone domain — exactly the risk the project
  hasn't measured yet.
- Dependency: owner approval only.
- Expected output: final GT (manifest.csv + annotations.csv), frozen MB-Lite per-unit + pooled
  metrics, failure taxonomy, optional PFv2 secondary, integrity tests,
  docs/EXTERNAL_REAL_WORLD_STRESS_TEST.md, summary.json, commit, and a verdict:
  STRONG / PARTIAL / WEAK EXTERNAL TRANSFER or INSUFFICIENT USABLE DATA.
- PASS: honest verdict with every usable unit annotated and evaluated under the frozen pipeline.
- FAIL: would only fail by violating its own rules (tuning after seeing results, fabricated GT,
  merged domains) — the protections are already written down.

Then, in order:
2. **Doc hygiene commit** — fix the SEATED_ROW_TRANSFER_2_8.md FN attribution error
   (artifact says asymmetry_01, doc says asymmetry_02), refresh stale PROJECT_STATE.md /
   PROJECT_ROADMAP.md (§19), commit the external-test code once approved.
   PASS: docs match artifacts. FAIL: any number in docs still contradicting an artifact.
3. **Realtime/causal feasibility prototype** — causal variant of the frozen pipeline (chunked
   MB-Lite, online decoder), measured end-to-end FPS + latency on this laptop. No new data needed.
   PASS: honest latency table + a go/no-go on live demo hardware. FAIL: numbers that only cover
   lifting, not end-to-end.
4. **Fresh-dataset validation gate** (the moment recordings arrive) — single-shot evaluation
   against the pre-registered criteria. PASS/FAIL defined in the spec; either outcome is reported.
5. **Integration spine** (after the gate + keys): Supabase deploy → dashboard → end-to-end demo
   (approved order E→F→G).

## 17. TECHNICAL CORE STATUS (ignoring UI/Supabase/Vercel/Unreal)

**NEAR FREEZE** — not READY TO FREEZE. What prevents freeze:
1. External smartphone evidence missing (stress test unfinished) — in my control once approved.
2. Fresh unseen-athlete validation not run (owner recording) — required for any production freeze;
   the pre-registered gate exists so freeze is a single evaluation away once data arrives.
3. Seated-row form thresholds provisional (15 reps, 1 athlete).
4. MB-Lite side-view weakness unresolved (0.383) — acceptable for a camera-controlled demo,
   not for freeze-for-production.
5. Realtime path unmeasured (if "core" includes the causal variant; the offline core itself
   does not depend on it).
Graduation-demo scope alone: the segmentation + form-v1 core is effectively freezable after
item 1 plus doc hygiene; production scope: NOT READY until items 2–4.

## 18. COMMITS / TESTS / ARTIFACTS

- Latest commits: `084c282` master report · `41976e6` Phase C form v1 · `c2702ba` Phase A+B
  schema fix + dataset gate · `13310a7` Phase 2.8 · `a41d759` 2.7 follow-up · `d1bbef4` 2.7 ·
  `03d2a5e` 2.6 · tag `baseline-v0.2.0-frozen`.
- Git status (uncommitted, intentional — track incomplete): modified `.gitignore` (raw-video
  ignore rule); untracked `ai/data/external_stress_test/` (url_map.json; raw/ ignored),
  `ai/outputs/external_stress_test/` (+probe json), `ai/research/external_stress/`.
  No stashes.
- Tests: **90 passed, 0 failed, 38 s** (fresh run today, full suite including the 3
  `slow`-marked full-pipeline regression tests in `test_regression.py`). Guard/regression
  highlights: 10 don't-care protocol tests, 6 result-schema tests, 7 unseen-dataset-gate tests,
  9 form-indicator tests, baseline regression locks.
- Key artifacts: frozen annotations + split (`mendeley_rep_annotations_expanded.csv`,
  `mendeley_subject_split.csv`), `ai/outputs/phase27/` (freeze_manifest.json, per-model
  config_final.json, locked TEST results, breakdowns, diagnostics, fps consistency),
  `ai/outputs/phase28/` (transfer results + caches), `ai/outputs/phase_c/`
  (val_separability.csv, seated_row_indicators.csv), `ai/outputs/pose_bench/`
  (benchmark + tracking_log.csv + deployment.json), external stress-test caches (uncommitted).
- Checkpoints: `ai/models/{motionbert, poseformerv2, motionagformer, videopose3d, mediapipe}`
  + README with re-download sources.
- Thresholds: `form_thresholds_v1_back.json` (450 TRAIN reps) and
  `form_thresholds_v1_seated_row.json` (PROVISIONAL, 15 reps). **Leftover flagged**: legacy
  `ai/models/form_thresholds_v1.json` (pre-exercise-scoping, contains removed indicators) —
  superseded; recommend deleting in the next hygiene commit (not deleted now: read-only order).

## 19. DOCUMENTATION STATUS

| doc | status |
|---|---|
| TECHNICAL_MASTER_REPORT.md | **CURRENT** as of 084c282 (predates external-track progress by hours; this handoff covers the delta) |
| DECISION_LOG.md | CURRENT through 2.8; **minor gap**: Phase C controlled decisions live in FORM_ANALYSIS_V1.md and aren't mirrored here |
| FORM_ANALYSIS_V1.md, UNSEEN_ATHLETE_DATASET_SPEC.md, REP_SEGMENTATION_PHASE_2_7.md, DATASET_PHASE_2_6.md, DATASET_MENDELEY.md, POSE_BENCHMARK_2.5C.md | **CURRENT** |
| SEATED_ROW_TRANSFER_2_8.md | **STALE IN ONE FACT** (known, flagged in master report): attributes the MB-Lite FN to asymmetry_02 camera dropout; artifact shows the FN is in asymmetry_01 and asymmetry_02 is clean. Correction awaiting your go-ahead. |
| PROJECT_STATE.md | **STALE** (last update 2026-10-05): says 65 tests (now 90), says form thresholds "don't exist yet" (Phase C finished them), no Phase A–C/master-report/external-track status |
| PROJECT_ROADMAP.md | **STALE** header section ("Now: form-analysis v1" — done); ordering itself still valid |
| AI_CORE_FREEZE_REPORT.md | **DOES NOT EXIST** — never created; appropriate, since §17 says NEAR FREEZE, not frozen |
| External stress-test doc (docs/EXTERNAL_REAL_WORLD_STRESS_TEST.md) | **NOT YET CREATED** (planned deliverable of the paused track) |
| Realtime documentation | none exists (nothing to document yet — consistent with §10) |

## 20. FINAL SUMMARY

CURRENT PRIMARY MODEL:
MotionBERT-Lite (seated-row product primary, frozen backbone); PoseFormerV2 retained only as
in-domain benchmark reference.

BEST VERIFIED RESULT:
In-domain unseen-subject TEST (back, 318 reps, protocol v2): PFv2 F1 0.939; MB-Lite 0.812 with
best pull timing 0.094 s. Seated-row zero-shot (product path): MB-Lite F1 0.841 IoU / 0.853 pull,
recall 0.967, pull MAE 0.128 s.

CURRENT FORM ANALYSIS STATUS:
PARTIAL — 4 interpretable indicators implemented and tested; rom_proxy directionally validated
(AUC 0.778); seated-row thresholds provisional (15 reps); no per-deviation accuracy claims.

CURRENT REALTIME STATUS:
NOT VALIDATED — no causal pipeline exists; no end-to-end latency ever measured.

CURRENT GENERALIZATION STATUS:
Unseen-subject in-domain VALIDATED; cross-view/tempo/exercise and seated-row zero-shot PARTIAL;
external smartphone, fresh athletes, multi-person, realtime NOT TESTED.

BIGGEST TECHNICAL BLOCKER:
Fresh ≥10-athlete recordings (owner) — gates production validation and real form calibration.
Biggest blocker inside my control: the paused external stress test.

WHAT YOU NEED FROM ME:
Recordings per UNSEEN_ATHLETE_DATASET_SPEC; approval to resume the external stress test;
later: Supabase keys, Vercel account, realtime hardware choice, AGPL licensing decision.
No video files needed — all 8 reels are local.

NEXT RECOMMENDED STEP:
Resume and finish the external Instagram stress test (manual GT → frozen MB-Lite evaluation →
failure taxonomy → report with STRONG/PARTIAL/WEAK/INSUFFICIENT verdict). Zero owner cost,
nothing frozen gets touched.

AI CORE STATUS:
NEAR FREEZE (graduation-demo scope needs the stress test + doc hygiene; production freeze
additionally needs fresh-athlete validation, real thresholds, side-view answer).

GRADUATION DEMO STATUS:
NOT YET — AI core nearly ready, but no integrated spine (Supabase undeployed, no dashboard,
no end-to-end flow).

PRODUCTION STATUS:
NO — fresh unseen-athlete validation not run; realtime unmeasured; provisional form thresholds;
AGPL decision pending.

LATEST COMMIT:
084c282 "Technical master report: complete verified audit (Phases 0-2.8 + A-C)" (branch main;
external-track work intentionally uncommitted pending track completion).

TEST STATUS:
90/90 passing (full suite, 38 s, includes 3 slow regression tests; run fresh 2026-10-08).
