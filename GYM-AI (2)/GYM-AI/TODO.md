# TODO

## Phase 0 — Audit + freeze baseline
- [x] Read all files; reconstruct pipeline from code (`docs/ARCHITECTURE.md`)
- [x] Freeze v1/v2 scripts in `ai/research/` with md5
- [x] Lock dependency versions (`ai/requirements-lock.txt`)
- [x] Record claimed baseline (`docs/baselines/`)
- [ ] **BLOCKER B1:** obtain the six MotionBERT `{video}.npy` (T,17,3) H36M files, or the pipeline/checkpoint to regenerate them → place in `ai/data/poses3d/`
- [ ] Reproduce v2 nested LOVO with the documented command; replace "claimed" JSON with the real run output
- [ ] Add `scripts/eval_v2.ps1` (or Makefile target) = single reproducible command
- [ ] Fix swapped video folders (B2) — owner to confirm
- [ ] `git init` inside `GYM-AI/`, first commit (B3) — owner to confirm; remove stray home-dir `.git`
- [ ] Check GPU availability; record in `docs/HARDWARE_SETUP.md`

## Phase 1 — Clean AI package (no behaviour change)
- [ ] Extract `ai/core/{pose_prep, phase_model, hsmm_decoder, evaluation}.py` from v2
- [ ] Unit tests + metrics-equality regression test against reproduced baseline
- [ ] Config + structured logging

## Phase 2 — asymmetry_01 diagnosis
- [ ] Score-decomposition tool for `hsmm_decode` (frames ~15–145; candidates around pulls 60/93/126)
- [ ] Any change selected on training folds only; full nested LOVO rerun

## Phase 3 — Supabase (can start in parallel with B1)
- [ ] Migrations, RLS, seed, TS types, `.env.example`

## Phase 4 — Dashboard + simulator (can start in parallel with B1)
## Phase 5 — Edge agent
## Phase 6 — End-to-end
## Phase 7 — Real-time causal path
## Phase 8+ — Multi-athlete data, form analysis, multi-machine, Unreal
