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
- [ ] **STEP 0 (owner):** Path A (poses / MotionBERT files exist) or Path B (rebuild MotionBERT stage)
- [ ] Path A: copy `.npy` to `ai/data/poses3d/`, run `scripts/eval_v2.ps1`, replace `*_CLAIMED.json` with the real run — report real numbers even if ≠ 0.93
- [ ] Path B: official MotionBERT repo + pretrained checkpoint (`ai/models/README.md`), `ai/research/extract_poses3d.py` (YOLO → COCO-17 → H36M-17 → normalise → MotionBERT → `.npy`), regenerate poses, run v2 unchanged, new honest baseline
- [ ] Owner: delete the stray zero-commit `C:\Users\Lenovo\.git` (outside the project; not done automatically)
- [ ] Record GPU status (none on this machine: `torch.cuda.is_available() == False`) in `docs/HARDWARE_SETUP.md`

## Phase 1 — Clean package (no behaviour change)
- [ ] Move `ai/src/` → `ai/research/legacy_2d/` (update path constants only)
- [ ] Extract `ai/core/{pose, lifting, features, phase_model, decoder, evaluation}.py` from v2; config + logging
- [ ] pytest: unit tests + `results`-identical regression test against the Phase 0 baseline JSON

## Phase 2 — asymmetry_01 diagnosis
- [ ] Score-decomposition tool for `hsmm_decode` (frames ~15–145; candidates around pulls 60/93/126)
- [ ] Any change selected on training folds only; full nested LOVO rerun

## Phase 3 — Supabase (parallelisable with Step 0)
- [ ] Migrations for MASTER_PROMPT §13 tables, RLS by organization, RLS tests, `seed.sql`, `.env.example`
- [ ] `shared/event-schemas/` JSON Schema → pydantic + TypeScript
- [ ] Owner: Supabase project URL + keys

## Phase 4 — Dashboard + simulator (parallelisable)
- [ ] Owner: Vercel account
## Phase 5 — Edge agent · Phase 6 — End-to-end · Phase 7 — REALTIME_EDGE · Phase 8 — Rule-based form alerts · Phase 9 — Deliverables
- [ ] Owner (before Phase 5): camera model + edge computer details
