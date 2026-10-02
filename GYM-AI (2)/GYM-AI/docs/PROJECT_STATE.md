# PROJECT_STATE.md — GYM-AI

_Last update: 2026-10-02, Phase 0 (freeze) — two commits in. Update whenever the baseline, data, or blockers change._

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
│  │  └─ poses3d/                                 ← DOES NOT EXIST YET: MotionBERT (T,17,3) H36M .npy go here (blocker B1)
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
│  │  └─ legacy_2d_predictions.py                 packages dataset_build/*/reps.csv for the evaluator
│  ├─ src/                                        legacy 2D YOLO-only pipeline (9 scripts, ~5.9k lines, untouched)
│  └─ models/README.md                            artifact registry (weights are git-ignored)
├─ scripts/eval_legacy_2d.ps1, eval_v2.ps1        one-command evaluations
├─ motionbert/                                    EMPTY
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
| **Legacy 2D pipeline (kept, sanity reference only)** | `ai/src/*.py` — YOLO 2D → elbow-span/shoulder-width → percentile FSM. Measured F1 0.62 (pull ±15) / 0.69 (IoU ≥ 0.5). |
| **Diagnostic history only, no files on disk** | 3D elbow-depth FSM, elbow angle, peak/prominence, basic HMM, notebook HSMM, logistic-emission HSMM |

## 3. Blockers
| # | Blocker | Status |
|---|---|---|
| B1 | **MotionBERT 3D pose arrays missing.** No `{video}.npy` (T,17,3) H36M files, no MotionBERT code/checkpoint, no COCO→H36M + inference script anywhere on this machine (searched Desktop, Downloads, Documents, OneDrive, all PyCharm/Python project folders). | **OPEN — Step 0 question to owner** (Path A: files exist somewhere; Path B: rebuild from the official MotionBERT repo + pretrained checkpoint, new honest baseline). |
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
| v1 ridge + FSM | nested LOVO | — | 0.87 | claimed |
| v2 ridge + HSMM | nested LOVO | — | **0.93** (TP 27 / FP 1 / FN 3), MAE 3.5/3.3/6.6 | **claimed, not reproduced** |

All of this is **same-athlete leave-one-video-out**, not new-person LOPO.

## 6. Phase status
| Phase | Status |
|---|---|
| 0 Freeze | git ✔ · requirements ✔ · docs ✔ · single evaluator ✔ · legacy sanity baseline ✔ · **v2 reproduction ✘ (B1 / Step 0)** |
| 1 Clean package | not started (needs reproduced baseline for the byte-identical check) |
| 2 asymmetry_01 diagnosis | not started (needs poses) |
| 3 Supabase · 4 Dashboard | not started — independent of B1, can run in parallel |
| 5–9 | not started |
