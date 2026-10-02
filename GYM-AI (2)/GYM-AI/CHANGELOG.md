# CHANGELOG

All behaviour-changing changes to the AI, data, schema, or services are recorded here.

## [Unreleased] — Phase 3: Supabase foundation (2026-10-02, code complete; deploy awaits owner's project)
- `supabase/migrations/20261002000001_core_schema.sql`: all MASTER_PROMPT §13 tables (organizations → evidence_assets), constraints, idempotency keys (`reps.event_uuid` unique, `(session_id, rep_index)` unique), heartbeat retention function.
- `supabase/migrations/20261002000002_rls.sql`: RLS enabled on every table; organization scoping via `profiles` + `auth_org_id()`/`auth_role()`; admin-only config writes; coach alert/form review; anon sees nothing; edge devices use a secured ingestion endpoint (Phase 6), never direct table access.
- `supabase/seed.sql`: two orgs (isolation testable), demo gym/machine/device/sessions/reps/alert, active model version `rep-seg-core-hsmm 0.2.0`.
- `supabase/tests/test_rls.py`: 6 RLS isolation tests (JWT-claim injection, rollback-only); auto-skip until `SUPABASE_DB_URL` is provided.
- `shared/event-schemas/`: `v1/events.schema.json` (source of truth, v1.0) + pydantic (`gym_ai_events`) + TypeScript (`events.ts`); sync enforced by `ai/tests/test_event_schema.py` (13 tests, green).
- `.env.example`, `supabase/README.md`; requirements: + pydantic, jsonschema, psycopg.

## [Unreleased] — Phase 2: asymmetry_01 diagnosis + survival-law fix (2026-10-02)
**Behaviour change in `ai/core/decoder.py` (`build_tables`), validated by a full nested-LOVO rerun.**
- Added `ai/research/diagnose_asymmetry01.py`: reproduces the asymmetry_01 outer fold and decomposes
  the decoded vs ground-truth path scores into emission / duration / transition / peak terms.
- Finding: the decoded giant-rep path won because a right-censored RET of 231 frames paid only a constant
  `-30.0` log-survival "floor", although the fitted (truncated at +4 sigma) RET duration law caps real
  returns at 50 frames. The per-segment duration overhead of the 5 true reps (~-62.6) could not compete.
- Fix: survival beyond `dmax` is now `-inf`, consistent with the truncated duration pmf the decoder
  already uses. No constant, no new hyper-parameter, nothing fitted on held-out data. The diagnosis used
  the held-out video's decode (that is what Phase 2 is for) but the fix is a global structural correction;
  with 6 videos a residual selection-on-test risk remains and is documented in docs/AI_EVALUATION.md.
- Full nested LOVO rerun (`scripts/eval_core.ps1` -> `ai/outputs/baselines/core_hsmm_nested_lovo.json`):
  pooled **F1 0.93 (pull +/-15) / 0.97 (IoU >= 0.5)**, GT 30 / Pred 30 / IoU: TP 29 FP 1 FN 1
  (was 0.87/0.87). asymmetry_01: 0 -> 5/5 TP (+1 FP trailing partial movement). Trade-off: correct_01
  4/5 (one rep now missed; was 5/5). MAE start/pull/end 5.2/5.0/6.9 (IoU rule).
- Added `ai/research/run_core_lovo.py` + `scripts/eval_core.ps1` (the current baseline command).
  The frozen v2 script and `scripts/eval_v2.ps1` still reproduce the Phase 0/1 baseline (F1 0.87).
- `ai/tests/test_regression.py` now locks `ai.core` to `core_hsmm_nested_lovo.json` (byte-identical md5).

## [Unreleased] — Phase 1: clean package, no behaviour change (2026-10-02)
- Moved `ai/src/` → `ai/research/legacy_2d/` (only the `AI_DIR` path constants changed: `.parent.parent` → `.parent.parent.parent`). `scripts/eval_legacy_2d.ps1` re-run: F1 0.62/0.69 unchanged.
- Extracted `ai/core/` from the frozen `rep_segmentation_v2_hsmm.py` (verbatim function copies): `config` (Cfg/GRID/constants), `features`, `phase_model`, `decoder`, `evaluation` (incl. nested LOVO), `io` (+ `segment_video` live entry), `pose` + `lifting` (moved from `extract_poses3d.py`, which is now a thin CLI), `log`.
- Added `ai/tests/` (pytest; `pytest.ini` at repo root): 23 unit tests + 3 slow regression tests. The regression test re-runs the full nested LOVO through `ai.core` and asserts the predictions CSV is **byte-identical** (md5) to the one recorded in `ai/outputs/baselines/v2_hsmm_nested_lovo.json`, plus pooled/per-video metric equality. All pass.
- `requirements.txt`: added pytest and its dependencies (exact versions).
- The frozen research scripts in `ai/research/` are untouched; `ai.core` is now the import surface for Phases 5–8.

## [Unreleased] — Phase 0: audit + freeze baseline (2026-10-02)

### Commit 3 — Path B rebuild of the MotionBERT stage + measured v2 baseline (Phase 0 complete)
- Vendored the official MotionBERT repo at `motionbert/` (upstream commit `705d3a95`, Apache-2.0, `.git` removed, nothing modified; see `motionbert/VENDORED.md`).
- Downloaded the official pose3d checkpoint `FT_MB_release_MB_ft_h36m/best_epoch.bin` (162 MB, git-ignored; source URL in `ai/models/README.md`).
- Added `ai/research/extract_poses3d.py` + `scripts/extract_poses3d.ps1`: YOLO11n-pose → COCO-17→H36M-17 → `crop_scale` normalisation → MotionBERT (243-frame clips, flip TTA, root-relative) → `ai/data/poses3d/{video}.npy` (T,17,3) + `ai/data/poses3d_conf/{video}.npy` (T,17). Both output dirs are tracked in git (`.gitignore` exceptions). Frames with no YOLO detection are interpolated with conf 0 (asymmetry_02: 51/250, correct_03: 11/418, asymmetry_03: 3/341).
- Ran the frozen `rep_segmentation_v2_hsmm.py` unchanged on the rebuilt poses, nested LOVO → **measured baseline** `ai/outputs/baselines/v2_hsmm_nested_lovo.json`: pooled **F1 0.87** under both rules (GT 30 / Pred 25 / TP 24 / FP 1 / FN 6); MAE start/pull/end 4.6/5.1/11.0 (IoU rule). asymmetry_01 decodes as one truncated rep (0 IoU TP) — Phase 2 target. The claimed 0.93 (original, lost poses) is retired as never reproduced.
- Fixed `scripts/eval_v2.ps1` parse failure under Windows PowerShell 5.1 (em-dash in a no-BOM UTF-8 file); all scripts are now ASCII-only.
- Docs updated: `PROJECT_STATE` (B1 resolved, Phase 0 complete), `AI_EVALUATION` (measured results, pose provenance, new asymmetry_01 failure description), `ai/research/README.md`, `ai/models/README.md`, `TODO`.

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
