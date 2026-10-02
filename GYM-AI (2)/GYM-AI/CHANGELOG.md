# CHANGELOG

All behaviour-changing changes to the AI, data, schema, or services are recorded here.

## [Unreleased] — Phase 0: audit + freeze baseline (2026-10-02)

### Commit 2 — single evaluator + legacy 2D sanity baseline
- Added `ai/research/eval_rep_segmentation.py`: the single evaluator (TP/FP/FN, P/R/F1, START/PULL/END MAE, per video + pooled). Two one-to-one greedy matching rules, both always reported: `pull_tol15` (project default) and `iou_0.50` (verbatim copy of the v2 rule, so claimed numbers are comparable). Drops the GT `label` column; no fixed rep count.
- Added `ai/research/legacy_2d_predictions.py`: packages existing `ai/outputs/dataset_build/*/reps.csv` into `ai/outputs/baselines/legacy_2d_predictions.csv` (no re-run of YOLO).
- Added `scripts/eval_legacy_2d.ps1`, `scripts/eval_v2.ps1`: one-command evaluations.
- **Measured** legacy 2D baseline → `ai/outputs/baselines/legacy_2d_elbow_span_fsm.json`: pooled F1 0.62 (pull ±15) / 0.69 (IoU ≥ 0.5), GT 30 / Pred 25.
- Docs updated with measured numbers, the matching-rule decision and the frame convention (GT is 1-based).

### Commit 1 (`3ccdc20`) — freeze current state
- `git init` at `GYM-AI/` (branch `main`); `.gitignore` excludes `.venv`, videos, model weights, large arrays.
- `ai/research/rep_segmentation_v1_fsm.py` and `rep_segmentation_v2_hsmm.py` copied verbatim from `~/Downloads` (md5 `2d339ea1…`, `60f99f7d…`). **v2 = current baseline.**
- `requirements.txt`: exact versions from the working `.venv` (Python 3.10.0).
- `ai/outputs/baselines/v2_hsmm_nested_lovo_CLAIMED.json`: claimed v2 metrics (not reproduced; poses missing).
- `ai/models/README.md`: artifact registry.
- Docs: `MASTER_PROMPT` (spec), `PHASE_PLAN`, `PROJECT_STATE`, `ARCHITECTURE`, `AI_EVALUATION`, `CLOUD_MIGRATION`, `HARDWARE_SETUP`, `TODO`, `README`.

### Changed
- No algorithm, data, or legacy `ai/src` code was modified.

### Known issues
- **B1 open:** MotionBERT (T,17,3) pose `.npy` files, MotionBERT code/checkpoint and the COCO→H36M conversion are missing → v2 baseline not reproducible yet (Step 0 decision pending).
- Resolved since first audit: swapped video folders (B2), missing git repo (B3).
