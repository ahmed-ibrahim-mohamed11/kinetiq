# PROJECT_STATE.md — GYM-AI

_Last audit: 2026-10-02 (Phase 0 start). Update this file whenever the baseline, data, or blockers change._

## 1. What the project is

Smart-gym computer-vision platform. Current MVP: **dual-handed seated row, rear camera**.
Current research priority: **robust repetition segmentation** (START / PULL / END).
Form classification (correct vs asymmetry) is a **later phase** and is kept independent.

## 2. What is actually in the repository (audited from disk)

```
GYM-AI/
├─ ai/
│  ├─ data/
│  │  ├─ raw/seated_row_test.mp4              (single early test clip)
│  │  └─ seated_row/{correct,asymmetry}/*.mp4  (6 videos — ⚠️ folders are SWAPPED, see §5)
│  ├─ outputs/
│  │  ├─ manual_rep_annotations.csv            ← rep-segmentation GROUND TRUTH (30 reps, 5 per video)
│  │  ├─ seated_row_dataset.csv                (legacy 2D per-rep form features, 25 reps)
│  │  ├─ dataset_build/<video>/{keypoints,features,reps}.csv
│  │  ├─ rep_signal_diagnostics.csv
│  │  └─ seated_row_{keypoints,reps,form_features}.csv, *_pose.mp4, *_rep_counter.mp4
│  ├─ research/                                ← NEW (Phase 0): frozen v1 FSM + v2 HSMM baselines
│  ├─ src/                                     (legacy 2D YOLO-only pipeline, 9 scripts, ~5.9k lines)
│  └─ requirements-lock.txt                    ← NEW: pip freeze of .venv
├─ motionbert/                                 ⚠️ EMPTY directory
├─ yolo11n-pose.pt                             YOLO11n-Pose weights (ultralytics)
├─ .venv/                                      Python 3.10.0; numpy 2.2.6, pandas 2.3.3, scipy 1.15.3,
│                                              torch 2.14.0, torchvision 0.29.0, ultralytics 8.4.155, matplotlib 3.10.9
└─ docs/, CHANGELOG.md, TODO.md, README.md     ← NEW (Phase 0)
```

### Baseline vs legacy

| Status | Files |
|---|---|
| **Current baseline** | `ai/research/rep_segmentation_v2_hsmm.py` (phase ridge model + explicit-duration HSMM) |
| **Legacy baseline (keep)** | `ai/research/rep_segmentation_v1_fsm.py` |
| **Legacy 2D pipeline (keep, not the research path)** | `ai/src/*.py` — YOLO 2D keypoints → elbow-span/shoulder-width signal → percentile FSM. Known to fail on asymmetry videos (dev history §4.1). Still useful: `manual_rep_annotator.py` / `fix_annotation.py` produced the GT CSV. |
| **Diagnostic history only** | peak/prominence, basic HMM, notebook HSMM, logistic-emission HSMM — **none of these files are on disk**. |

## 3. Blockers (explicit)

| # | Blocker | Impact | Resolution |
|---|---|---|---|
| B1 | **MotionBERT 3D pose arrays missing.** No `{video}.npy` (T,17,3) H36M files exist in the repo, `~/Downloads`, Documents, OneDrive, or other project folders. `motionbert/` is empty. | v2 baseline **cannot be reproduced**; Phase 0 "done" criterion unmet; Phase 1 refactor cannot be regression-tested. | Owner must supply the six `.npy` files (and optional (T,17) confidence files), **or** the COCO→H36M + MotionBERT inference script that produced them, **or** the MotionBERT checkpoint + repo so we can regenerate them. |
| B2 | Video folders swapped on disk (§5). | Re-running `ai/src/build_dataset.py` would mislabel every rep; `fix_annotation.py` / `manual_rep_annotator.py` cannot find videos. | Move files back (owner to confirm it was accidental). |
| B3 | No real git repo. `git rev-parse --show-toplevel` → `C:/Users/Lenovo` (a stray `git init` in the home directory, zero commits). | No history, no reproducibility, no CHANGELOG enforcement. | `git init` inside `GYM-AI/` with the `.gitignore` added in Phase 0. The home-dir `.git` should be removed by the owner (not done automatically). |

## 4. Contradictions between the master prompt and the files

1. Prompt says the repo "should contain something equivalent to `rep_segmentation_v2_hsmm.py`". It did not; it was in `~/Downloads/rep_segmentation (1).py`. Copied into `ai/research/` with md5 recorded.
2. Prompt describes H36M-17 `.npy` pose sequences as "current pose files". **None exist** (B1).
3. Prompt lists prominence/HMM/HSMM-notebook/logistic experiments. **No such files exist**; they are documented here as history only.
4. Prompt says "6 videos, 5 reps each". The legacy 2D dataset (`seated_row_dataset.csv`) has 25 auto-detected reps (asymmetry_01 → 1 rep), confirming the 2D signal's failure on asymmetry. GT CSV has 30. ✔ consistent with prompt.
5. The v2 metrics in the prompt are **claimed**, recorded in `docs/baselines/v2_hsmm_nested_lovo_claimed.json`; they are **not** independently reproduced yet.

## 5. Data integrity issue: swapped video folders

```
ai/data/seated_row/asymmetry/  contains  correct_01.mp4, correct_02.mp4, correct_03.mp4
ai/data/seated_row/correct/    contains  asymmetry_01.mp4, asymmetry_02.mp4, asymmetry_03.mp4
```
`manual_rep_annotations.csv` and `seated_row_dataset.csv` both label `correct_0x` as `correct`, and the per-video output folders in `dataset_build/` match the file names, so the **file names are trusted and the folder placement is wrong**. The v1/v2 research scripts key everything by file stem, so they are unaffected; the legacy `ai/src` pipeline keys by folder and **is** affected.

## 6. Current metrics (CLAIMED, same-athlete nested LOVO — not new-person LOPO)

v2 HSMM: pooled P 0.96 / R 0.90 / F1 0.93 (GT 30, Pred 28, TP 27, FP 1, FN 3); Start/Pull/End MAE 3.5 / 3.3 / 6.6 frames @30 fps.
Only failure: `asymmetry_01` (F1 0.50, first three fast cycles merged/missed). See `docs/AI_EVALUATION.md`.

## 7. Phase status

| Phase | Status |
|---|---|
| 0 Audit + freeze baseline | **In progress** — docs, lockfile, frozen scripts done; reproduction blocked by B1 |
| 1 Clean AI package | Not started (needs B1 for regression tests) |
| 2 Diagnose asymmetry_01 | Not started (needs B1) |
| 3 Supabase foundation | Not started — can proceed in parallel with B1 |
| 4 Web dashboard + simulator | Not started — can proceed in parallel with B1 |
| 5–10 | Not started |
