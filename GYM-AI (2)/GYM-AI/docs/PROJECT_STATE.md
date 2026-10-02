# PROJECT_STATE.md — GYM-AI

_Last update: 2026-10-02, Phase 0 COMPLETE (Path B rebuild, measured baseline F1 0.87). Update whenever the baseline, data, or blockers change._

## 1. What the project is
Smart-gym computer-vision platform. Current MVP: **dual-handed seated row, rear camera**.
Current research priority: **robust repetition segmentation** (START / PULL / END).
Form classification (correct vs asymmetry) is a **later phase** and is kept independent. Spec: `docs/MASTER_PROMPT.md`. Plan: `docs/PHASE_PLAN.md`.

## 2. What is in the repository (audited from disk)

```
GYM-AI/                                  git repo (branch main) since 2026-10-02
├─ ai/
│  ├─ data/
│  │  ├─ raw/seated_row_test.mp4                 early test clip (git-ignored)
│  │  ├─ seated_row/{correct,asymmetry}/*.mp4    6 research videos, 13–25 MB each (git-ignored; back up separately)
│  │  ├─ poses3d/{video}.npy                      MotionBERT (T,17,3) H36M poses, Path B rebuild (tracked in git)
│  │  └─ poses3d_conf/{video}.npy                 (T,17) 2D keypoint confidences, H36M order (tracked in git)
│  ├─ outputs/
│  │  ├─ manual_rep_annotations.csv               ← rep-segmentation GROUND TRUTH (30 reps, 5 per video, 1-based frames)
│  │  ├─ baselines/                               ← evaluator JSON + prediction CSVs (tracked)
│  │  │  ├─ legacy_2d_elbow_span_fsm.json          MEASURED legacy 2D sanity baseline
│  │  │  ├─ legacy_2d_predictions.csv
│  │  │  └─ v2_hsmm_nested_lovo_CLAIMED.json       claimed v2 numbers, NOT reproduced yet
│  │  ├─ dataset_build/<video>/{keypoints,features,reps}.csv   legacy 2D per-video outputs
│  │  └─ seated_row_*.csv, rep_signal_diagnostics.csv           other legacy derived data
│  ├─ research/
│  │  ├─ rep_segmentation_v2_hsmm.py              ← CURRENT BASELINE (frozen, md5 in ai/research/README.md)
│  │  ├─ rep_segmentation_v1_fsm.py               legacy baseline (frozen)
│  │  ├─ eval_rep_segmentation.py                 ← the single evaluator (Phase 0)
│  │  ├─ extract_poses3d.py                       Path B: YOLO → COCO→H36M → MotionBERT → poses3d/*.npy
│  │  └─ legacy_2d_predictions.py                 packages dataset_build/*/reps.csv for the evaluator
│  ├─ core/                                       production package extracted from v2 (Phase 1, regression-locked)
│  ├─ tests/                                      pytest: unit + byte-identical regression vs Phase 0 baseline
│  └─ models/README.md                            artifact registry (weights are git-ignored)
├─ scripts/eval_legacy_2d.ps1, eval_v2.ps1, extract_poses3d.ps1   one-command steps
├─ motionbert/                                    vendored official MotionBERT (commit 705d3a95, Apache-2.0, see VENDORED.md)
├─ yolo11n-pose.pt                                YOLO11n-Pose weights (git-ignored, auto-downloaded by ultralytics)
├─ .venv/  Python 3.10.0 — torch 2.14.0 (CPU), ultralytics 8.4.155, scipy 1.15.3, numpy 2.2.6, pandas 2.3.3
├─ requirements.txt                               exact versions (pip freeze)
└─ docs/, CHANGELOG.md, TODO.md, README.md
```

### Baseline vs legacy
| Status | Files |
|---|---|
| **Current baseline** | `ai/research/rep_segmentation_v2_hsmm.py` — ridge phase model + explicit-duration HSMM, nested LOVO |
| **Legacy baseline (kept)** | `ai/research/rep_segmentation_v1_fsm.py` |
| **Legacy 2D pipeline (kept, sanity reference only)** | `ai/research/legacy_2d/*.py` — YOLO 2D → elbow-span/shoulder-width → percentile FSM. Measured F1 0.62 (pull ±15) / 0.69 (IoU ≥ 0.5). |
| **Diagnostic history only, no files on disk** | 3D elbow-depth FSM, elbow angle, peak/prominence, basic HMM, notebook HSMM, logistic-emission HSMM |

## 3. Blockers
| # | Blocker | Status |
|---|---|---|
| B1 | **MotionBERT 3D pose arrays missing.** No `{video}.npy` (T,17,3) H36M files, no MotionBERT code/checkpoint, no COCO→H36M + inference script anywhere on this machine. | **RESOLVED 2026-10-02 via Path B** (owner deferred to recommendation): official MotionBERT repo vendored at `motionbert/` (commit `705d3a95`), pose3d checkpoint downloaded (see `ai/models/README.md`), `ai/research/extract_poses3d.py` regenerated all 6 pose files → `ai/data/poses3d/*.npy` (tracked). New honest baseline **F1 0.87** replaces the claimed 0.93 (original poses lost, claim never reproduced). |
| B2 | Video folders were swapped (`correct/` held `asymmetry_0x.mp4` and vice-versa) at the first audit. | **RESOLVED** — folders verified correct at the second audit (fixed by owner between audits). |
| B3 | No git repo at `GYM-AI/` (stray zero-commit `.git` in `C:\Users\Lenovo`). | **RESOLVED** — `git init` in `GYM-AI/`, commits `3ccdc20` (freeze) and the Phase 0 evaluator commit. The stray home-dir `.git` is left for the owner to delete (outside the project). |

## 4. Contradictions between the prompts and the files
1. `docs/MASTER_PROMPT.md` §5 and `docs/CLAUDE_CODE_PROMPT.md` say the v1/v2 scripts are (or may be) missing. **They were recovered** from `~/Downloads` (`rep_segmentation_v1_fsm.py`, `rep_segmentation (1).py` = v2) and frozen in `ai/research/`. What is missing is only the **MotionBERT stage** (code, checkpoint, COCO→H36M conversion) and its **output `.npy` poses**.
2. MASTER_PROMPT §3 calls the H36M `.npy` sequences "current pose files" — none exist (B1).
3. MASTER_PROMPT §4.6–4.7 experiments: no files exist; history only.
4. CLAUDE_CODE_PROMPT asks for a default matching rule of one-to-one PULL within ±15 frames; the v1/v2 scripts and the **claimed 0.93 use temporal IoU ≥ 0.5**. The evaluator reports **both** rules; the claimed baseline must be compared under IoU. See `docs/AI_EVALUATION.md` §2.
5. CLAUDE_CODE_PROMPT says `ai/models/` is empty — it did not exist; it now holds only `README.md` (weights are git-ignored by design).
6. v2 metrics are **claimed** (`ai/outputs/baselines/v2_hsmm_nested_lovo_CLAIMED.json`), not reproduced.

## 5. Current metrics
| Pipeline | Validation | Pull ±15 F1 | IoU ≥ 0.5 F1 | Status |
|---|---|---|---|---|
| Legacy 2D elbow-span FSM | none (transductive per-video percentiles; nothing learned) | **0.62** (TP 17 / FP 8 / FN 13) | **0.69** (TP 19 / FP 6 / FN 11) | MEASURED 2026-10-02, commit 3ccdc20 |
| v1 ridge + FSM | nested LOVO | — | 0.87 | claimed (original lost poses) |
| v2 ridge + HSMM (original poses, lost) | nested LOVO | — | 0.93 (TP 27 / FP 1 / FN 3), MAE 3.5/3.3/6.6 | claimed, never reproduced — superseded |
| **v2 ridge + HSMM on Path B rebuilt poses — CURRENT BASELINE** | nested LOVO | **0.87** (TP 24 / FP 1 / FN 6), MAE 5.0/4.5/18.7 | **0.87** (TP 24 / FP 1 / FN 6), MAE 4.6/5.1/11.0 | **MEASURED 2026-10-02** — `ai/outputs/baselines/v2_hsmm_nested_lovo.json`. All misses are asymmetry_01 (decoded as 1 giant rep) + 1 FN on asymmetry_02. |

All of this is **same-athlete leave-one-video-out**, not new-person LOPO.

## 6. Phase status
| Phase | Status |
|---|---|
| 0 Freeze | **COMPLETE 2026-10-02** — git ✔ · requirements ✔ · docs ✔ · single evaluator ✔ · legacy sanity baseline ✔ · Path B rebuild ✔ · measured v2 baseline (F1 0.87) ✔. One command regenerates poses (`scripts\extract_poses3d.ps1`), one command reproduces the baseline (`scripts\eval_v2.ps1`). |
| 1 Clean package | next (regression target = `v2_hsmm_nested_lovo.json`, byte-identical) |
| 2 asymmetry_01 diagnosis | queued (failure now: whole video decoded as 1 truncated rep) |
| 3 Supabase · 4 Dashboard | can start; deploys blocked on owner (Supabase URL/keys, Vercel) |
| 5–9 | not started (Phase 5 blocked on camera/edge hardware details) |
