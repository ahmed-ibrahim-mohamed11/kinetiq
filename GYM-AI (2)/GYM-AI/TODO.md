# TODO

## Phase 0 — Freeze (in progress)
- [x] Read all files; reconstruct pipeline from code (`docs/ARCHITECTURE.md`)
- [x] Freeze v1/v2 scripts in `ai/research/` with md5
- [x] `git init`, `.gitignore`, freeze commit `3ccdc20`
- [x] `requirements.txt` with exact versions
- [x] Single evaluator `ai/research/eval_rep_segmentation.py` (pull ±15 default + IoU ≥ 0.5), rule recorded in `docs/AI_EVALUATION.md`
- [x] Legacy 2D sanity baseline measured → `ai/outputs/baselines/legacy_2d_elbow_span_fsm.json`
- [x] One-command scripts `scripts/eval_legacy_2d.ps1`, `scripts/eval_v2.ps1`
- [x] Video folders verified correct (B2 resolved)
- [x] **STEP 0:** owner deferred to recommendation → **Path B** (2026-10-02)
- [x] Path B: vendored MotionBERT (commit `705d3a95`), downloaded pose3d checkpoint, `ai/research/extract_poses3d.py`, regenerated 6 pose files, ran v2 unchanged → **new honest baseline F1 0.87** (`ai/outputs/baselines/v2_hsmm_nested_lovo.json`)
- [ ] Owner: delete the stray zero-commit `C:\Users\Lenovo\.git` (outside the project; not done automatically)
- [ ] Owner: back up the 6 research videos off this machine (git-ignored, not protected by the repo)
- [x] Record GPU status (none; CPU timings measured) in `docs/HARDWARE_SETUP.md`

## Phase 1 — Clean package (no behaviour change) — DONE 2026-10-02
- [x] Move `ai/src/` → `ai/research/legacy_2d/` (AI_DIR constants only; legacy eval unchanged 0.62/0.69)
- [x] Extract `ai/core/{config, pose, lifting, features, phase_model, decoder, evaluation, io, log}.py` from v2
- [x] pytest: 23 unit tests + byte-identical (md5) regression vs `v2_hsmm_nested_lovo.json` — all green

## Phase 2 — asymmetry_01 diagnosis — DONE 2026-10-02
- [x] Score-decomposition tool `ai/research/diagnose_asymmetry01.py` → culprit: −30 survival floor beyond dmax
- [x] Structural fix (survival consistent with truncated duration law), full nested LOVO rerun → F1 0.93/0.97
- [ ] Later (more data): revisit correct_01's missed rep and asymmetry_01's trailing-partial FP — do NOT tune further on these 6 videos

## Phase 3 — Supabase — CODE COMPLETE 2026-10-02
- [x] Migrations (schema + RLS), `seed.sql`, `.env.example`, `supabase/README.md`
- [x] RLS test suite (auto-skips without `SUPABASE_DB_URL`)
- [x] `shared/event-schemas/` JSON Schema v1.0 → pydantic + TypeScript, sync-tested
- [ ] **Owner: create a free Supabase project, put URL/keys/DB URL in `.env`** → then: apply migrations + seed, run `pytest supabase/tests` (phase "done when security tests pass")

## Phase 4 — Dashboard + simulator (parallelisable)
- [ ] Owner: Vercel account
## Phase 5 — Edge agent · Phase 6 — End-to-end · Phase 7 — REALTIME_EDGE · Phase 8 — Rule-based form alerts · Phase 9 — Deliverables
- [ ] Owner (before Phase 5): camera model + edge computer details
