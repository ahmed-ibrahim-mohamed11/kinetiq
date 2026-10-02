# AI_EVALUATION.md — repetition segmentation

## 1. Ground truth
`ai/outputs/manual_rep_annotations.csv` — 6 videos × 5 reps = 30 reps; columns `video,label,rep,start_frame,pull_frame,end_frame`.
`label` is **form** ground truth and is **never** given to the rep segmenter.

| video | start/pull/end (frames) |
|---|---|
| correct_01 | 96/126/178 · 189/221/264 · 275/303/346 · 359/387/426 · 444/471/501 |
| correct_02 | 47/93/123 · 134/156/190 · 203/229/264 · 271/302/340 · 352/373/417 |
| correct_03 | 64/87/116 · 122/150/174 · 186/214/237 · 250/276/302 · 317/348/381 |
| asymmetry_01 | 22/60/77 · 81/93/107 · 111/126/140 · 144/159/173 · 184/193/214 |
| asymmetry_02 | 18/30/43 · 48/62/84 · 90/102/124 · 131/143/166 · 175/185/212 |
| asymmetry_03 | 41/65/85 · 100/119/145 · 155/171/192 · 207/229/253 · 257/281/314 |

**Validation scope:** leave-one-VIDEO-out, one athlete/setup. This does **not** demonstrate new-person generalisation. Switch to leave-one-PERSON-out (`--groups_csv video,athlete`) as soon as a second athlete is recorded.

## 2. Protocol (must be preserved)
- Match rule: temporal IoU ≥ 0.5 on `[start, end]`; metrics: TP/FP/FN, P/R/F1 pooled + per video; Start/Pull/End MAE (frames) on matched reps.
- **Nested**: hyper-parameters (`GRID`) chosen by inner leave-one-group-out on training folds only (`select_config`); decoder emissions fit on cross-fitted phase predictions (`crossfit_fused`). The held-out video is touched only in `predict_video`.
- Never report the `--save_model` (fit-on-all) model's numbers as validation.
- Never hardcode 5 reps; never tune on the held-out fold after inspecting it.

## 3. Results history (all same-athlete LOVO)

| Approach | GT | Pred | TP | FP | FN | P | R | F1 | Start/Pull/End MAE | Status |
|---|---|---|---|---|---|---|---|---|---|---|
| 2D elbow-span FSM (`ai/src`) | 30 | 25 | — | — | — | — | — | — | — | failed on asymmetry (asymmetry_01 → 1 rep) |
| 3D elbow-depth state machine | per-video counts 5/5/8/4/4/7 | | | | | | | | | not robust |
| 3D elbow angle (wrist-dependent) | | | | | | | | | | worse timing; wrist quality poor in rear view |
| v1 ridge phase + FSM | 30 | 25 | 24 | 1 | 6 | 0.96 | 0.80 | 0.87 | 9.1 / 3.5 / 6.5 | legacy, file kept |
| peak/prominence | | | | | | | | | | real rep @ asym_01 f93 prominence 0.0258 < false move @ corr_03 f406 (0.1234 L / 0.0589 R) → not viable |
| basic HMM / notebook HSMM / logistic-emission HSMM | | | | | | | | | | diagnostic only, no files on disk |
| **v2 ridge phase + explicit-duration HSMM** | 30 | 28 | 27 | 1 | 3 | 0.96 | 0.90 | **0.93** | 3.5 / 3.3 / 6.6 | **current baseline — CLAIMED, not yet reproduced (poses missing)** |

v2 per video: asymmetry_01 F1 0.50 (5 GT / 3 pred / 2 TP / 1 FP / 3 FN); all others 5/5, F1 1.00.

## 4. Open failure: asymmetry_01
Pred `[51,66,89] [143,156,174] [182,196,215]` vs GT above. Pred2/3 ≈ GT rep4/5. Pred1 spans part of rep1 + motion toward rep2; reps 2–3 missed. Tempo estimate ≈1.17 s/cycle vs GT pull spacing ≈33 frames ≈1.1 s — **tempo is not the primary cause**.

**Next research task (Phase 2):** instrument `hsmm_decode` (return_score path) for frames ~15–145 and decompose, for the chosen vs. candidate segmentations around GT pulls 60/93/126: REST/PULL/RET emission score, duration log-lik, RET→REST vs RET→PULL transition, peak-height term, smoothing/detrending effect, fused phase + velocity. Any change is selected on training folds and re-evaluated with full nested LOVO on all six videos.

## 5. Target gates (future LOPO, not current claims)
Pooled F1 ≥ 0.90 on held-out people (stretch 0.95); Pull MAE ≤ 5 frames @30 fps; per-person reporting; include clips with rep counts ≠ 5.

## 6. Form analysis (future phase — not started)
Only after rep boundaries are stable and multiple athletes exist: per-rep features (lag_pull, lag_onset, amp_ratio, elbow-path asymmetry, trunk lean, ROM, velocity consistency; tempo as context only), balanced labels, tempo control, LOPO, macro-F1 + per-class recall + confusion matrix + calibration. Wording: "form deviation / potential biomechanical concern requiring coach review".
