# CHANGELOG

All behaviour-changing changes to the AI, data, schema, or services are recorded here.

## [Unreleased] — Phase 0: audit + freeze baseline (2026-10-02)

### Added
- `ai/research/rep_segmentation_v1_fsm.py` — copied verbatim from `~/Downloads/rep_segmentation_v1_fsm.py` (md5 `2d339ea14ff8f14862f32df39532e4ea`).
- `ai/research/rep_segmentation_v2_hsmm.py` — copied verbatim from `~/Downloads/rep_segmentation (1).py` (md5 `60f99f7d7de751b0fac673812ba922d4`). **Current baseline.**
- `ai/research/README.md` — provenance, input contract, baseline command.
- `ai/requirements-lock.txt` — `pip freeze` of `.venv` (Python 3.10.0).
- `docs/baselines/v2_hsmm_nested_lovo_claimed.json` — claimed v2 metrics (not yet reproduced; poses missing).
- `docs/PROJECT_STATE.md`, `docs/ARCHITECTURE.md`, `docs/AI_EVALUATION.md`, `docs/CLOUD_MIGRATION.md`, `docs/HARDWARE_SETUP.md`, `TODO.md`, `README.md`, `.gitignore`.

### Changed
- Nothing. No algorithm, data, or legacy `ai/src` code was modified.

### Known issues recorded (not fixed)
- MotionBERT (T,17,3) pose `.npy` files missing → baseline not reproducible yet.
- `ai/data/seated_row/{correct,asymmetry}` folder contents are swapped relative to file names.
- No git repository rooted at `GYM-AI/` (stray `.git` in the user home directory).
