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

## Phase 2.3 update (2026-10-09): per-slice sensitivity/specificity breakdown

`ai/research/phase2/form_breakdown.py` — same frozen thresholds, no refitting. Reports
sensitivity (attention rate on BAD/deviated reps) and false-alert rate (attention rate on
GOOD reps = 1−specificity) per subject/view/tempo on Mendeley VAL, and per form/tempo on a
**clean** seated-row check (the 15 manually-annotated GT rep windows directly, NOT the
previously-used decoded zero-shot windows — removes the FP-inflation caveat §"Honest
limitations" above flagged). Pre-registered drop bar: an indicator is removed only if it fails
BOTH signals (Mendeley AUC near chance AND seated-row direction wrong/degenerate). **No
indicator met that bar — all four verdicts below are CONFIRMED, with corrections where the
cleaner seated-row check changed confidence:**

| indicator | Mendeley VAL pooled (sensitivity / false-alert) | clean seated-row (deviated / good) | per-slice findings | verdict |
|---|---|---|---|---|
| rom_proxy | 0.295 / 0.018 | 0.400 / 0.067 | **blind spot found**: 0/48 on diagonal-view bad reps (view: front 0.596, side 0.220, diagonal **0.000**); 0/22 and 0/6 on medium/slow-tempo bad reps (tempo: fast 0.364, medium/slow **0.000**, small n for slow); subject-dependent (0.000–0.552) | **VALIDATED (directional), now with documented blind spots**: front-view, fast-tempo is where this indicator actually works; diagonal-view and medium/slow-tempo bad reps are essentially invisible to it in Mendeley VAL |
| lr_amplitude_ratio | 0.171 / 0.030 | **0.067 / 0.067 (ZERO separation)** | strongest on side view (0.390) and subject_022 (0.328) in Mendeley; near-chance elsewhere; seated-row sensitivity drops to near-chance at slow tempo (0.000, n=5) | **CORRECTION, downgraded confidence**: the previously-reported seated-row sanity (4/26 vs 2/21 on decoded windows) does NOT reproduce on the clean 15-rep GT check (exact parity, 0.067 both groups) — that earlier result was likely an artifact of FP-inflated decoded windows, not real separability. Mendeley evidence alone (0.634 AUC) is still real but modest. Kept PARTIAL (does not fail BOTH signals — Mendeley signal survives) but flagged as weaker than previously stated; do not cite the old seated-row number again |
| lr_lag | 0.048 / 0.060 (false-alert HIGHER — no useful pooled Mendeley signal, confirms AUC≈0.53) | 0.200 / 0.067 | Mendeley gives no slice with real separation (its 'bad' isn't specifically asymmetric, as already documented); seated-row direction holds (3×) | **PARTIAL, confirmed** — Mendeley evidence is and remains uninformative by design (wrong population for asymmetry), seated-row is the only real signal |
| trunk_motion | 0.014 / 0.036 (false-alert HIGHER — Mendeley direction is backwards, confirms AUC 0.379) | **0.400 / 0.067 (6×, as strong as rom_proxy)** | Mendeley signal remains absent/inverted everywhere; seated-row signal is notably STRONGER on the clean check than its "PARTIAL, weak" label previously implied | **PARTIAL, confirmed but seated-row evidence is stronger than previously characterized** — still no Mendeley support (plausible exercise-mismatch explanation unchanged), but should not be thought of as the "weakest" of the three PARTIAL indicators on seated-row-specific grounds; still provisional (15 reps, 1 athlete) |

**Net effect of this measurement pass**: no indicator added or removed. One real downgrade
(`lr_amplitude_ratio`'s seated-row evidence doesn't survive a cleaner check — stop citing the
old 4/26-vs-2/21 number) and one real upgrade-in-confidence (`trunk_motion`'s seated-row signal
is stronger than its label suggested), both from using the clean GT-rep windows instead of
decoded zero-shot windows. `rom_proxy`'s per-slice blind spots (diagonal view, medium/slow
tempo) are new, actionable information for whoever next works on Domain C calibration or a
Phase 2 retrain — not a reason to distrust the pooled VALIDATED verdict, which still holds.

## Phase 2.4 update (2026-10-09): confidence is now a documented, multi-signal heuristic

`ai/core/form_indicators.py::heuristic_confidence_score` combines three already-[0,1] signals —
keypoint validity (`_frac_valid`, existing), raw pose confidence from the lifter
(`_mean_pose_conf`, NEW — mean over the same 6 joints the indicators actually read: shoulders,
elbows, pelvis, thorax), and distance from the decision threshold (`|normalized-1.0|`, NEW,
saturating at 0.5) — into one `confidence_score`, bucketed into the same `{high,medium,low}`
label every existing consumer reads. `rep_features` gained an optional `conf=` parameter (raw
`(T,17)` per-joint confidence); omitting it reproduces the old validity-only behaviour exactly
(fully backward compatible — no existing caller needed to change). 7 new/updated tests
(`ai/tests/test_form_indicators.py`) lock monotonicity in every input and the None-fallback.

**This was NOT gated on improving a metric** (per the pre-registered P2.4 criteria,
docs/EXPERIMENT_REGISTRY.md): true calibration (mapping the score to an actual P(correct))
needs per-rep deviation ground truth that will not exist before the Domain-C dataset arrives
(UNSEEN_ATHLETE_DATASET_SPEC.md §5). `confidence_score` is explicitly documented, everywhere it
appears, as a heuristic — never fit to any proxy label, never presented as a probability. A
real-data check (seated-row `correct_01`, 5 GT reps) shows the richer score moving sensibly
below the old validity-only fallback (e.g. rep1: 0.965→0.939) without flipping any bucket on
clean footage, exactly as intended — see the commit for the worked numbers.
