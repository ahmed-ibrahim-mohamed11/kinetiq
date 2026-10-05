# SEATED_ROW_TRANSFER_2_8.md — zero-shot domain transfer to the target exercise (2026-10-05)

Reference evaluation (NOT pristine test: these 6 videos shaped Phases 0–2). Zero-shot protocol:
Phase 2.7 systems (Mendeley BACK TRAIN-fitted, VAL-frozen configs, 15 fps training data) applied
unchanged to seated row, rear view, 30 fps, one athlete. Tolerances seconds-defined. Artifacts:
`ai/outputs/phase28/` (cached 2D/poses, per-video CSVs, summary.json).

## Results (30 GT reps)
| system | rule | TP | FP | FN | P | R | F1 | start/pull/end MAE (s) |
|---|---|---|---|---|---|---|---|---|
| **MotionBERT-Lite** | IoU | 29 | 10 | 1 | 0.74 | **0.97** | **0.841** | 0.27 / **0.17** / 0.36 |
| **MotionBERT-Lite** | pull±0.53s | 29 | 9 | 1 | 0.76 | 0.97 | **0.853** | 0.25 / 0.13 / 0.37 |
| PoseFormerV2 | IoU | 10 | 26 | 20 | 0.28 | 0.33 | **0.303** | 0.38 / 0.49 / 0.50 |
| PoseFormerV2 | pull±0.53s | 13 | 23 | 17 | 0.36 | 0.43 | 0.394 | — |
Context (different protocol — same-athlete LOVO *trained on these videos*, tag v0.2.0): 0.93/0.97.

## Root-cause isolation for the PFv2 collapse
- Controlled fps experiment: seated-row input downsampled to 15 fps (training rate) → **F1 0.338**
  — fps/frequency mismatch is NOT the cause (PFv2's DCT representation was the suspect; ruled out).
- Tracking exonerated: 0–5 jumps; MB-Lite consumed the SAME tracked 2D and scored 0.84.
- Conclusion: PFv2's heavily-smoothed representation is **exercise/viewpoint-brittle** — rear-view
  seated pulling lies off its competent manifold (consistent with its weak curl transfer, 0.418,
  vs MB-Lite 0.650). Over-segmentation dominates (e.g. correct_03: 18 preds / 5 reps); even when
  counts match, boundaries mis-place (correct_01/02: 5 preds, 0 IoU-TP).

## Decision (DECISION_LOG 2026-10-05b)
- **Deployment/product path (seated row): MotionBERT-Lite is PRIMARY.**
- **PoseFormerV2 remains primary for in-domain cross-subject benchmarking** (its 0.939 unseen-
  subject result stands) and stays a candidate IF in-domain seated-row training data arrives
  (fresh recordings) — its failure is zero-shot transfer, not capability.
- General principle recorded: representation choice must be validated per target domain;
  in-domain unseen-subject F1 does not predict cross-exercise transfer.

## MB-Lite residual errors (actionable for form-analysis phase)
1 FN (asymmetry_02 rep under heavy 2D dropout — the known 51-frame no-detection stretch);
10 FP = extra shallow/partial movements, mostly around set boundaries on correct_01 and
asymmetry_01 — the same censored-edge/partial class seen on Mendeley; acceptable for rep counting
(count MAE ≤ 2 per video), relevant when form analysis consumes windows.

## What this does and does not prove
Proves: the MB-Lite system counts and times seated-row reps zero-shot at 30 fps with high recall
(97%) and 0.13 s pull accuracy — sufficient to build form-analysis features on its windows.
Does not prove: new-athlete seated-row generalization (one athlete, development-exposed videos);
that still requires the fresh recording set (owner action, spec in 2.6/2.7 docs).
