# AI_CORE_BASELINE_MANIFEST.md — locked Phase 0 baseline (2026-10-09)

Machine-readable twin: `docs/AI_CORE_BASELINE_MANIFEST.json` (regenerate with
`python ai/scripts/build_baseline_manifest.py --with-causal --run-tests`; verify any later change
against it with `python ai/scripts/verify_baseline.py [--with-causal] [--source artifact|refit]`,
which exits 1 on drift).
This replaces re-deriving these numbers by hand every time; every later experiment compares
BEFORE vs AFTER against this file (global rule 1).

## Current state (rebuilt 2026-10-10, Phase 5 part 3) — read this before the Phase 0 sections below

- All 8 headline metrics and the fitted-system fingerprint are **unchanged** from Phase 0 (table below).
- Latest rebuild: **618/618 tests** (8 min 36 s on battery). The only hash changes are
  `ai/core/pose.py` (Ultralytics telemetry switched off in `YoloTopK`; legacy-helper label) and
  `ai/core/lifting.py` (legacy-checkpoint label).
- Environment: torch **2.14.0+cu130** / torchvision 0.29.0+cu130 since Phase 4 (P4.0, byte-identical
  numbers); fastapi 0.143.0 / uvicorn 0.54.0 / python-multipart 0.0.32 / httpx 0.28.1 added for
  the local service (P5.4).
- Phase 5 part 1 rebuild: 598/598 passed (6 min 10 s on battery, slow tests included).
- The fitted system is now a persisted artifact, `ai/models/rep_segmentation/mblite_rowing_v1.json`
  (listed under `models[]`, role product). Its embedded fingerprint equals `fitted_system_sha256`.
  `verify_baseline.py` checks the artifact by default (`--source refit` re-fits on Mendeley TRAIN
  instead); suites whose inputs are missing are reported as SKIP, never as passed.
- Newly hash-tracked: `ai/core/{io,model_artifact,schema,pipeline}.py`,
  `ai/schemas/analysis_result.v1.json`.

## Lineage

- Baseline commit lineage: `92c0bcd2fc9133e8f0f4ba8bf3c152bdecc7a7e3` (as specified by the owner).
- This manifest was built on top of that commit, on a **different machine** than the original
  development laptop: the repo was copied (not cloned) from a CPU-only Windows laptop ("Lenovo")
  to this machine (Intel i7-13650HX, 20 logical/14 physical cores, 15.7 GB RAM, **NVIDIA RTX 5050
  Laptop GPU 8 GB VRAM**, driver 610.74). Neither `git` nor a matching `python` were installed on
  this machine at session start; both were installed locally this session (MinGit 2.56.0.2 at
  `%LOCALAPPDATA%\Programs\MinGit`, Python 3.10.11 at `%LOCALAPPDATA%\Programs\Python\Python310`,
  per-user, no admin rights used) and the existing `.venv` was repointed at the new interpreter
  (`.venv/pyvenv.cfg`) rather than rebuilt, preserving the exact pinned package set in
  `requirements.txt`. All imports verified (`torch 2.14.0+cpu`, `ultralytics 8.4.155`,
  `numpy 2.2.6`, `scipy 1.15.3`, `pandas 2.3.3`, `opencv 5.0.0.93`). Installed torch is the
  **CPU-only** wheel — the GPU is present but unused until a Phase 4 experiment explicitly swaps
  the torch build (see RISK_REGISTER.md R-03).

## Reproduction result: every documented headline number reproduces exactly

| metric | documented (92c0bcd docs) | recomputed today | match |
|---|---|---|---|
| Mendeley VAL IoU F1 (MB-Lite) | 0.914 | 0.9137 | ✅ |
| Internal seated-row IoU F1 | 0.841 | 0.8406 | ✅ |
| Internal seated-row pull F1 | 0.853 | 0.8529 | ✅ |
| External (Instagram) IoU F1 | 0.632 | 0.6316 | ✅ |
| External pull F1 | 0.842 | 0.8421 | ✅ |
| Causal V1 (low-latency) GT F1 | 0.740 | 0.7397 | ✅ |
| Causal V2 (quality) GT F1 | 0.806 | 0.806 | ✅ |
| Causal V3 (budget 15 fps) GT F1 | 0.812 | 0.8125 | ✅ |
| Full test suite | 98/98 passed | **136/136 passed** (14 `ai/evaluation` protocol-equivalence tests + 24 `ai/augmentation` tests added this session) | ✅ |

All numbers come from `python ai/scripts/build_baseline_manifest.py --with-causal --run-tests`,
which calls the SAME frozen fitting/decoding code as the three research scripts
(`ai/research/phase28/seated_row_transfer.py`, `ai/research/external_stress/evaluate_external.py`,
`ai/research/realtime/causal_prototype.py`) through the new shared, tested harness
`ai/evaluation/harness.py` (pure-function protocol `ai/evaluation/protocol.py`, proven
byte-identical to `ai/research/phase27/train_eval.py`'s `evaluate_dontcare`/`_match` by 14
property-based tests, `ai/tests/test_evaluation_protocol.py`). Nothing was tuned; this is a
reproduction run, not a new experiment.

Small cosmetic numeric differences from historical docs (e.g. `0.914` vs `0.9137`, `0.740` vs
`0.7397`) are rounding in the original hand-written reports, not drift — the underlying TP/FP/FN
counts are identical (verified: Mendeley VAL IoU TP 286 FP 28 FN 26; internal TP 29 FP 10 FN 1;
external TP 6 FP 3 FN 4).

## What is pinned

- **Models** (hash + source in the JSON `models[]`): product = `yolo11n-pose.pt` (Ultralytics
  auto-download), MotionBERT-Lite `FT_MB_lite_MB_ft_h36m_global_lite/best_epoch.bin`; benchmark-
  only = full MotionBERT, PoseFormerV2, MotionAGFormer, VideoPose3D, MediaPipe checkpoints (kept
  for reproducing Phase 2.5C/2.7 comparisons, not product-used).
- **Configs**: `ai/outputs/phase27/motionbert_lite/config_final.json` (smooth_hz=2.0,
  detrend_s=10.0, dur_weight=1.0, dur_sigma_floor=0.3 — selected on VAL, locked since Phase 2.7);
  all `ai/core/*.py` + `ai/research/pose_benchmark/tracking.py` source hashes (CRLF-normalised so
  the hash is identical on Windows and in a future Linux/Docker clone).
- **Data**: Mendeley annotations + frozen subject split + duplicate-group list; internal
  seated-row GT; external (Instagram) GT.
- **Fitted-system fingerprint**: `63d36c31761c95a86e380eee698ea002a196ce0dc5df7a7b2414ecc6532e0720`
  — sha256 of the phase-model weights + decoder parameters + config, rounded to 6 decimals
  (robust to BLAS ULP noise across machines/library versions) after refitting on the frozen
  Mendeley BACK TRAIN split with masked supervision. This is the number `verify_baseline.py`
  checks to catch silent numeric drift in the fitting code itself, independent of the metric
  suites (which could theoretically tie by coincidence).

## Split roles (restated so every later phase uses data correctly — see global rule 3)

| split | role |
|---|---|
| Mendeley TRAIN (14 subjects) | fitting only |
| Mendeley VAL (5 subjects) | model/config selection (used since Phase 2.7) |
| Mendeley TEST (5 subjects) | **LOCKED** — single run only (`train_eval.py --unlock-test`); used once in Phase 2.7 |
| Internal seated-row (6 videos, 1 athlete) | DEV / **CONTAMINATED** — chose MB-Lite as product primary (DECISION_LOG 2026-10-05b) AND calibrated provisional seated-row form thresholds; never usable as independent evidence again |
| External (Instagram, 5 units) | STRESS REFERENCE — eval-only, never tuned on (frozen-model rule, DECISION_LOG 2026-10-08); licensing caveat tracked in LICENSING.md |
| Domain C (future ≥10 fresh athletes) | **FINAL TEST** — untouched; single locked evaluation per UNSEEN_ATHLETE_DATASET_SPEC.md |

## Known environment caveats for anyone rerunning this

- `ai/outputs/realtime/{timing.csv,latency.csv,summary.json}` timing numbers are **machine-
  specific** and were measured on the original CPU-only laptop; this session's causal-accuracy
  reproduction (F1 numbers above) is valid evidence, but re-measuring *timing* on this machine
  would overwrite them with a different machine's numbers without authorization — not done here
  (the contaminated timing run from this session was discarded, not committed; see CHANGELOG).
- Running `ai/scripts/build_baseline_manifest.py --with-causal` takes ~2 GPU-idle minutes on this
  CPU; never run alongside another heavy job (Phase 4 rule).
