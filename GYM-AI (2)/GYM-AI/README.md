# GYM-AI

Smart-gym computer-vision platform. Current MVP: seated row (rear camera) **repetition segmentation** from estimated monocular 3D pose. Form analysis is a later, independent phase.

| Doc | What |
|---|---|
| [docs/MASTER_PROMPT.md](docs/MASTER_PROMPT.md) | the spec (vision, rules, history, target architecture) |
| [docs/PHASE_PLAN.md](docs/PHASE_PLAN.md) | phases and done-criteria |
| [docs/PROJECT_STATE.md](docs/PROJECT_STATE.md) | what actually exists, blockers, contradictions |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | pipeline reconstructed from code + target edge/cloud architecture |
| [docs/AI_EVALUATION.md](docs/AI_EVALUATION.md) | protocol, matching rules, results |
| [docs/CLOUD_MIGRATION.md](docs/CLOUD_MIGRATION.md) · [docs/HARDWARE_SETUP.md](docs/HARDWARE_SETUP.md) | cloud/edge plan · hardware |
| [CHANGELOG.md](CHANGELOG.md) · [TODO.md](TODO.md) | changes · work queue |

## Setup
```powershell
py -3.10 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
```
Dataset videos (`ai/data/seated_row/{correct,asymmetry}/*.mp4`) and model weights are git-ignored — copy them in separately.

## Evaluate
```powershell
scripts\eval_legacy_2d.ps1   # legacy 2D sanity baseline: F1 0.62 (pull ±15) / 0.69 (IoU ≥ 0.5)
scripts\eval_v2.ps1          # v2 HSMM nested leave-one-video-out — needs ai\data\poses3d\*.npy (MotionBERT H36M (T,17,3))
```
Both write `ai/outputs/baselines/<name>.json` via the single evaluator `ai/research/eval_rep_segmentation.py`.
Claimed v2 result (not yet reproduced): pooled F1 0.93 under IoU ≥ 0.5, Start/Pull/End MAE 3.5/3.3/6.6 frames. All current numbers are same-athlete LOVO — not evidence of new-person generalisation.

## Layout
`ai/research/` frozen baselines + evaluator · `ai/src/` legacy 2D pipeline · `ai/data/` videos (+ `poses3d/` once available) · `ai/outputs/` GT, baselines, derived CSVs · `ai/models/` artifact registry · `scripts/` one-command runs · `docs/`.
