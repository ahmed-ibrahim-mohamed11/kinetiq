# FORM_ANALYSIS_V1.md — interpretable coach-attention indicators (2026-10-08)

No correct/incorrect classifier. No injury language. Four per-rep indicators computed inside rep
windows (`ai/core/form_indicators.py`, 9 unit tests), each returning value / normalized /
threshold / severity{ok,attention} / message / confidence (from keypoint validity in-window).

## Indicators + validation status (explicit labels)
| indicator | measurement | status | evidence |
|---|---|---|---|
| rom_proxy | mean L/R elbow-backward travel range (torso-lengths) | **VALIDATED (directional)** | Mendeley VAL rank-AUC **0.778**, attention 29.5% bad vs 1.8% good; seated-row decoded: 16/26 asymmetry vs 9/21 correct |
| lr_amplitude_ratio | min/max per-arm signal amplitude | **PARTIAL** | AUC 0.634 (17.1% vs 3.0%); seated-row 4/26 vs 2/21 |
| lr_lag | cross-correlation offset of per-arm elbow signals (s) | **PARTIAL** | no Mendeley separation (AUC 0.53 — its 'bad' is not asymmetric); seated-row direction correct (5/26 vs 2/21, catches the 0.4–0.5 s lags); synthetic test recovers a 0.3 s injected lag ±0.07 |
| trunk_motion | p95−p5 trunk-backward range (torso-lengths) | **PARTIAL** | no Mendeley separation; seated-row 8/26 vs 2/21 |

## Controlled decisions (kept/rejected, all on TRAIN/VAL or dev data)
- Signal-xcorr lag REPLACED phase-peak lag: asymmetry hits 0/15 → 3/15 at equal 1/15 false rate
  (root cause: shared-weight phase model over-smooths per-arm timing). KEPT.
- `lr_onset_lag` REMOVED: measurement degenerates (p95 = 0.0 on seated row). Revisit only with
  per-rep deviation labels (fresh dataset).
- **Thresholds are exercise-scoped** — experimentally required: Mendeley-back thresholds applied
  to seated row inverted the sanity check (0% asymmetry vs 24% correct); per-exercise calibration
  fixed direction everywhere. Files: `ai/models/form_thresholds_v1_back.json` (Mendeley TRAIN-good,
  207→p95/p05) and `..._seated_row.json` (**PROVISIONAL**: 15 dev-video good reps — demo-grade,
  replaced by fresh-dataset calibration before any production use).

## Honest limitations
Mendeley 'bad' is video-level and unspecific → AUCs are directional evidence, not error-type
accuracy. Seated-row sanity ran on DECODED zero-shot windows (includes 2.8's edge-partial FPs →
correct-group counts inflated; rep counts 26/21 vs 15/15 GT). 3D is estimated monocular (proxy-
stable only). Per-indicator precision/recall per deviation type is NOT claimable until the fresh
dataset provides per-rep deviation annotations — pre-registered in UNSEEN_ATHLETE_DATASET_SPEC §5.
