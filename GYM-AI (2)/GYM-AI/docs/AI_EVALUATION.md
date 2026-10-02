# AI_EVALUATION.md — repetition segmentation

## 1. Ground truth
`ai/outputs/manual_rep_annotations.csv` — 6 videos × 5 reps = 30 reps; columns `video,label,rep,start_frame,pull_frame,end_frame`.
`label` is **form** ground truth and is **never** given to the rep segmenter (both the research scripts and the evaluator drop it on load).

| video | start/pull/end (frames) |
|---|---|
| correct_01 | 96/126/178 · 189/221/264 · 275/303/346 · 359/387/426 · 444/471/501 |
| correct_02 | 47/93/123 · 134/156/190 · 203/229/264 · 271/302/340 · 352/373/417 |
| correct_03 | 64/87/116 · 122/150/174 · 186/214/237 · 250/276/302 · 317/348/381 |
| asymmetry_01 | 22/60/77 · 81/93/107 · 111/126/140 · 144/159/173 · 184/193/214 |
| asymmetry_02 | 18/30/43 · 48/62/84 · 90/102/124 · 131/143/166 · 175/185/212 |
| asymmetry_03 | 41/65/85 · 100/119/145 · 155/171/192 · 207/229/253 · 257/281/314 |

**Frame convention.** The annotator (`ai/research/legacy_2d/manual_rep_annotator.py`) records `frame_index + 1`, so GT frames are **1-based**. The legacy 2D pipeline is also 1-based. The v1/v2 research scripts index the (T,17,3) arrays directly with the GT numbers, i.e. a systematic +1 shift that applies identically to training targets and evaluation — harmless, but any new code must not "fix" it on one side only.

**Validation scope.** Leave-one-VIDEO-out, one athlete/setup. This does **not** demonstrate new-person generalisation. Switch to leave-one-PERSON-out (`--groups_csv video,athlete`) as soon as a second athlete is recorded.

## 2. Protocol (must be preserved)

### Evaluator
`ai/research/eval_rep_segmentation.py` is the single evaluator. It takes a predictions CSV (`video, start, pull, end`) and the GT CSV and writes `ai/outputs/baselines/<name>.json` with per-video and pooled TP/FP/FN, precision/recall/F1 and START/PULL/END MAE (frames, matched reps only).

### Matching rules (one-to-one, greedy, deterministic) — both are always reported
| key | rule | role |
|---|---|---|
| `pull_tol15` | closest pairs first, accepted if `|pred.pull − gt.pull| ≤ 15` frames | **project default** (CLAUDE_CODE_PROMPT) — strict on *when* the pull happens |
| `iou_0.50` | highest temporal IoU of `[start, end]` first, accepted if `IoU ≥ 0.5` | rule used **inside** `rep_segmentation_v1/v2` (copied verbatim) — the **claimed v2 numbers are under this rule** |

The rules can disagree: on the legacy 2D pipeline they give F1 0.62 vs 0.69, because its "pull" is the first HIGH-threshold crossing rather than the peak-contraction frame. When comparing with the claimed baseline use `iou_0.50`; when judging timing quality for the product use `pull_tol15`.

### Nested validation
Hyper-parameters (`GRID` in v2) are chosen by inner leave-one-group-out on training folds only (`select_config`); decoder emissions are fit on cross-fitted phase predictions (`crossfit_fused`). The held-out video is touched only in `predict_video`. Never report the `--save_model` (fit-on-all) model as validation. Never hardcode 5 reps. Never tune on the held-out fold after inspecting it.

### Commands
```powershell
scripts\eval_legacy_2d.ps1        # legacy 2D sanity baseline (no pose extraction needed)
scripts\eval_v2.ps1               # v2 nested LOVO + evaluator  (needs ai\data\poses3d\*.npy)
```

## 3. Results (all same-athlete LOVO; "claimed" = reported by the owner, not yet reproduced here)

| Approach | GT | Pred | TP | FP | FN | P | R | F1 | Start/Pull/End MAE | Rule | Status |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Legacy 2D elbow-span FSM (`ai/research/legacy_2d`) | 30 | 25 | 17 | 8 | 13 | 0.68 | 0.57 | **0.62** | 12.5 / 8.9 / 16.7 | pull ±15 | **measured** 2026-10-02 |
| same | 30 | 25 | 19 | 6 | 11 | 0.76 | 0.63 | **0.69** | 13.7 / 11.2 / 6.4 | IoU ≥ 0.5 | **measured** 2026-10-02 |
| 3D elbow-depth state machine | per-video counts 5/5/8/4/4/7 | | | | | | | | | | history, no file |
| 3D elbow angle (wrist-dependent) | | | | | | | | | worse timing | | history, no file |
| v1 ridge phase + FSM | 30 | 25 | 24 | 1 | 6 | 0.96 | 0.80 | 0.87 | 9.1 / 3.5 / 6.5 | IoU ≥ 0.5 | claimed, file kept |
| peak/prominence | | | | | | | | | | | history: real rep @ asym_01 f93 prominence 0.0258 < false move @ corr_03 f406 (0.1234 L / 0.0589 R) |
| basic HMM / notebook HSMM / logistic-emission HSMM | | | | | | | | | | | history, no files |
| v2 ridge phase + explicit-duration HSMM (original, lost poses) | 30 | 28 | 27 | 1 | 3 | 0.96 | 0.90 | 0.93 | 3.5 / 3.3 / 6.6 | IoU ≥ 0.5 | claimed — **never reproduced**; its pose files are lost, superseded by the Path B rebuild below |
| **v2 unchanged, on Path B rebuilt poses** | 30 | 25 | 24 | 1 | 6 | 0.96 | 0.80 | **0.87** | 5.0 / 4.5 / 18.7 | pull ±15 | **measured** 2026-10-02 — **current baseline** |
| same | 30 | 25 | 24 | 1 | 6 | 0.96 | 0.80 | **0.87** | 4.6 / 5.1 / 11.0 | IoU ≥ 0.5 | **measured** 2026-10-02 |

Legacy 2D per video (pull ±15 / IoU): asymmetry_01 F1 0.33 / 0.00 (1 prediction, the incomplete trailing rep) · asymmetry_02 0.75 / 0.50 · asymmetry_03 0.80 / 0.80 · correct_01 0.20 / 0.80 · correct_02 0.73 / 0.91 · correct_03 0.80 / 0.80. Note the legacy pipeline learns nothing (per-video percentile thresholds), so this is a sanity reference, not a nested-validation result.

**Measured v2 (rebuilt poses) per video, IoU:** asymmetry_01 **F1 0.00** (1 pred / 0 TP / 1 FP / 5 FN) · asymmetry_02 0.89 (4 pred, 1 FN, the 51 no-detection frames are in this video) · asymmetry_03, correct_01..03 all 5/5, F1 1.00. Under pull ±15 asymmetry_01 gets 1 TP (its single pull at frame 50 is within 15 of GT pull 60), F1 0.33.

**Pose provenance (Path B rebuild, 2026-10-02).** `ai/data/poses3d/*.npy` regenerated in-repo by `ai/research/extract_poses3d.py`: YOLO11n-pose → COCO-17→H36M-17 → `crop_scale` normalisation → MotionBERT `FT_MB_release_MB_ft_h36m` (official checkpoint, vendored repo commit `705d3a95`), 243-frame clips, flip TTA, root-relative. The original pose files behind the claimed 0.93 are lost; the rebuilt 2D front-end may differ from the original (unknown detector/person-selection/normalisation), so 0.87 vs 0.93 is **not** a refutation of the claim — it is the honest number for the pipeline that exists and is reproducible. YOLO no-detection frames (interpolated, conf 0): asymmetry_02 51/250, correct_03 11/418, asymmetry_03 3/341.

## 4. Open failure: asymmetry_01 (v2, measured on rebuilt poses)
The decoder outputs **one** rep spanning the whole video: start 33 / pull 50 / end 280, truncated, decoded RET median 7.19 tempo units vs training range [0.39, 0.55] — after the first PULL it never exits RET. Held-out tempo estimate 1.07 s/cycle is below the training range [1.33, 2.83] (flagged by the script, nominally handled by tempo normalisation). GT reps are 1.1 s/cycle, by far the fastest video.
History (claimed run, original poses): pred `[51,66,89] [143,156,174] [182,196,215]`, F1 0.50 — a different, milder failure of the same video.

**Phase 2 task:** instrument `hsmm_decode` (`return_score` path) for frames ~15–145 and decompose, for the chosen vs. candidate segmentations around GT pulls 60/93/126: REST/PULL/RET emission score, duration log-lik, RET→REST vs RET→PULL transition, peak-height term, smoothing/detrending effect, fused phase + velocity. First suspects on the rebuilt poses: phase amplitude after the first rep (does fused phase stay high?), and the duration prior under the out-of-range tempo. Any change is selected on training folds and re-evaluated with full nested LOVO on all six videos.

## 5. Target gates (future LOPO, not current claims)
Pooled F1 ≥ 0.90 on held-out people (stretch 0.95); Pull MAE ≤ 5 frames @30 fps; per-person reporting; include clips with rep counts ≠ 5.

## 6. Form analysis (Phase 8 — rule-based only, no classifier)
Per-rep symmetry features inside decoded rep windows (lag_pull, lag_onset, amp_ratio, trunk lean, ROM; tempo as context only) and provisional rule thresholds producing "potential deviation — coach review" alerts. A classifier needs multiple athletes, balanced labels, tempo control and LOPO; it will not be trained on 6 videos of one athlete. Wording: "movement/form deviation", "potential biomechanical concern requiring coach review" — never a diagnosis.
