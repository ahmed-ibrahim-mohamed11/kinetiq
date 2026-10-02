# GYM-AI

Smart-gym computer-vision platform. Current MVP: seated row (rear camera) **repetition segmentation** from estimated monocular 3D pose.

- Project status, blockers, contradictions → [docs/PROJECT_STATE.md](docs/PROJECT_STATE.md)
- Pipeline + target architecture → [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- Evaluation protocol + results → [docs/AI_EVALUATION.md](docs/AI_EVALUATION.md)
- Cloud/edge plan → [docs/CLOUD_MIGRATION.md](docs/CLOUD_MIGRATION.md)
- Hardware → [docs/HARDWARE_SETUP.md](docs/HARDWARE_SETUP.md)
- Changes → [CHANGELOG.md](CHANGELOG.md) · Work queue → [TODO.md](TODO.md)

## Reproduce the current baseline (v2 HSMM, nested leave-one-video-out)

Requires `ai/data/poses3d/{video}.npy` (MotionBERT, H36M-17, shape (T,17,3)) — **currently missing, see PROJECT_STATE.md**.

```powershell
.venv\Scripts\python ai\research\rep_segmentation_v2_hsmm.py `
  --poses_dir ai\data\poses3d `
  --annotations ai\outputs\manual_rep_annotations.csv `
  --fps 30 `
  --out_dir ai\outputs\rep_eval_v2
```

Expected: pooled F1 0.93 (GT 30 / Pred 28 / TP 27 / FP 1 / FN 3), Start/Pull/End MAE 3.5/3.3/6.6 frames. Same-athlete LOVO — not evidence of new-person generalisation.

## Layout
`ai/research/` frozen baselines · `ai/src/` legacy 2D pipeline · `ai/data/` videos · `ai/outputs/` GT + derived CSVs · `docs/`.
