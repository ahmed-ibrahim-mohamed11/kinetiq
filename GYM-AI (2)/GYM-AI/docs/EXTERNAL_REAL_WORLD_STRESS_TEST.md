# EXTERNAL_REAL_WORLD_STRESS_TEST — Instagram smartphone-domain zero-shot evaluation (2026-10-08)

**Domain B** of the three-domain validation strategy. Never merged with the others:
- **A — internal development reference**: the 6 original seated-row videos (1 athlete, fixed rear
  camera; Phase 2.8 results).
- **B — external wild smartphone stress test**: 8 Instagram reels (this document). Research
  evaluation only: raw videos and extracted frame imagery are git-ignored and never
  redistributed; only URLs, hashes and skeletal data are stored.
- **C — final hardware-camera validation**: future ≥10 unseen athletes (untouched; spec frozen).

**Frozen-model rule (held)**: the exact Phase 2.7/2.8 MotionBERT-Lite system was run once,
zero-shot — YOLO11n-pose top-8 → deterministic tracker → MB-Lite lifting → frozen VAL config →
Mendeley-back-TRAIN-refit phase model + HSMM. No parameter, weight, threshold or smoothing
change before or after seeing results. GT was annotated manually from frame sheets before the
decoder ran on any unit; signal drafts served only as visual assistance (and were themselves
wrong often enough to prove independence — see §2.1).

## 1. Ingestion and audit

9 supplied URLs → **8 unique** (C5gFNP_Iega duplicated) → **8/8 downloaded** (yt-dlp) → MD5s
unique (no content duplicates). All 720×1280 portrait, 25–30 fps, 13.1–95.2 s
(`ai/data/external_stress_test/{url_map.json, manifest.csv}`).

| video | content (audit) | classification |
|---|---|---|
| ext_01 | machine row, orbiting close-up, ~5–6 reps (estimate) | **QUALITATIVE_ONLY** — downgraded during the manual GT pass: hands/handle repeatedly out of frame, quasi-static stretches indistinguishable from reps; not frame-level annotatable |
| ext_02 | static side-view seated cable row | **QUANTITATIVE** (full, 0–800) |
| ext_03 | multi-shot reel; front-oblique + rear rowing shots | **QUANTITATIVE** (segments 237–330, 361–470; both end in mid-rep shot cuts) |
| ext_04 | montage, heavy occlusion | QUALITATIVE_ONLY |
| ext_05 | slow coached demo, suspected caption cuts | QUALITATIVE_ONLY |
| ext_06 | 13 s three-exercise montage with overlays | QUALITATIVE_ONLY |
| ext_07 | montage | QUALITATIVE_ONLY |
| ext_08 | side-view strict rows, dark gym + neon overlay edit | **QUANTITATIVE** (segments 160–250, 310–435); segA cut mid-rep (qualitative), tail ≥440 different exercise (excluded) |

## 2. Manual ground truth

Frame-level annotation from subject-cropped contact sheets (stride 2–4, zoom verification at
stride ≤2 where needed; `ai/research/external_stress/{make_sheets.py, frame_grid.py}`).
Convention identical to Phase 2.5B/2.6: START = concentric onset, PULL = peak contraction
(center of the squeeze hold for paused reps, noted per rep), END = return to extension;
0-based frames; censored cut-edge reps carry `gt_tier=uncertain` (don't-care, flagged
partial/edited_cut) and are never scored as certain.

**Result: 10 certain reps + 4 uncertain (all four = reps cut mid-movement by shot edits)**
across 5 units / 3 videos / 3 athletes / 4 camera views (`annotations.csv`).

Two audit estimates were corrected by the frame-level pass (documented, not hidden):
ext_02 has **6** slow reps (~1.4–1.7 s squeeze holds), not ~11 — the coarse audit counted
sub-cycles; ext_01 was downgraded entirely (above).

### 2.1 GT independence evidence
The 2.5B signal proposer (annotation aid only, not the evaluated system) failed visibly on this
domain: on ext_02 its signal polarity is inverted for the left-facing view — all 6 of its draft
"pulls" sit at **full extension**; it proposed 0 reps for ext_03 segA and under-proposed
elsewhere (14 drafts vs ~27 audit-estimated). Every boundary in `annotations.csv` was set by
human frame reading; draft frames were corrected wholesale.

## 3. Results — frozen MotionBERT-Lite, zero-shot

Primary protocol **v2** (identical to the locked Phase 2.7/2.8 evaluations); v1 (strict) shown
for transparency. Tolerance seconds-defined: 16 f @30 fps, 13 f @25 fps. Artifacts:
`ai/outputs/external_stress_test/{metrics.csv, predictions.csv, diagnostics.csv, summary.json}`.

### Pooled (10 certain GT reps)

| protocol/rule | TP | FP | FN | P | R | F1 | START MAE | PULL MAE | END MAE |
|---|---|---|---|---|---|---|---|---|---|
| **v2 / IoU** | 6 | 3 | 4 | 0.667 | 0.600 | **0.632** | 0.631 s | 0.365 s | 0.666 s |
| **v2 / pull** | 8 | 1 | 2 | 0.889 | 0.800 | **0.842** | 0.974 s | **0.282 s** | 0.908 s |
| v1 / IoU | 6 | 4 | 4 | 0.600 | 0.600 | 0.600 | — | — | — |
| v1 / pull | 8 | 2 | 2 | 0.800 | 0.800 | 0.800 | — | — | — |

Rep **count**: 11 predictions over the 5 units vs 10 certain GT; 2 predictions matched the
cut/uncertain reps (correctly absorbed), so the effective count error is **−1** (the missed
rear-view rep). No spurious extra reps anywhere.

### Per unit (v2; nothing hidden inside the pool)

| unit | view / fps / decoded tempo | GT(+unc) | pred | IoU TP/FP/FN | pull TP/FP/FN | pull MAE | tracking (window) |
|---|---|---|---|---|---|---|---|
| ext_02_full | side (left-facing), 30, **4.20 s** | 6 | 6 | 3/3/3 | **6/0/0** | 0.322 s | 0 lost, 0 jumps |
| ext_03_segA | front-oblique, 25, 2.44 s | 1(+1u) | 1 | 1/0/0 | 1/0/0 | 0.160 s | 15 NaN frames (16%) — still succeeded |
| ext_03_segB | **rear**, 25, 2.72 s | 1(+1u) | 1 | 0/0/**1** | 0/0/**1** | — | 0 lost |
| ext_08_segB | side, dark+overlay, 30, 1.67 s | 1(+1u) | 1 | 1/0/0 | 0/1/1 | — | 0 lost |
| ext_08_segC | side, dark, 30, 1.57 s | 1(+1u) | 2 | 1/0/0 (+1 ignored→u) | 1/0/0 (+1 ignored→u) | 0.167 s | 0 lost |

**Best unit**: ext_03_segA (clean despite 16% tracked-frame dropout in-window) and ext_08_segC.
**Worst unit**: ext_03_segB (the one real miss); ext_02 for window extent (below).

## 4. Failure taxonomy (every v2 error attributed)

| failure | count | attribution | evidence |
|---|---|---|---|
| ext_02 reps 1, 2, 6: IoU 0.45–0.49, just under the 0.5 gate → 3 FP + 3 FN under IoU rule, while all 6 pulls are within tolerance | 6 of the 7 IoU-rule errors | **TEMPORAL MODEL** (duration/extent prior under tempo shift) | predicted windows tile the video contiguously (e.g. 0–136 vs GT 46–110): the HSMM absorbs the long inter-rep extension holds into rep windows; decoded tempo 4.20 s vs TRAIN rep durations 0.4–3.7 s (median 1.65 s) — the paused style is outside the learned duration range |
| ext_03_segB certain rep missed (FN both rules); the unit's single prediction (406–470) matches the *second*, cut rep instead (ignored as uncertain) | 1 FN | **CAMERA VIEW** (rear; depth-dominant arm motion) | same failure family as the known MB-Lite side/view weakness (Mendeley side F1 0.383); rear-view pull happens mostly along the camera axis; tracking/pose clean, so pose representation—not detection—is the bottleneck |
| ext_08_segB pull-rule FP+FN: predicted pull 181 vs GT 206, error 25 f > 16 f | 1 FP + 1 FN (pull rule only; IoU rule matches) | **ANNOTATION AMBIGUITY** (primary) | GT pull = center of a ~30-frame squeeze hold, flagged `ambiguous_rep` at annotation time; model placed pull at concentric end — inside the hold, arguably correct; the window itself is a TP |
| 2 predictions on cut-censored reps (ext_03_segB, ext_08_segC) | 0 errors | **VIDEO CUT**, correctly absorbed by the uncertain tier | both cut reps were genuinely started; don't-care handling worked as designed |

**Not observed**: TRACKING, 2D POSE dropout, 3D LIFTING instability, MOTION BLUR, LOW
RESOLUTION, COMPRESSION, VARIABLE FPS, CAMERA MOTION, OCCLUSION and MULTI-PERSON failures —
tracking was near-perfect on all quantitative units (worst: 16% NaN window, no consequence),
despite portrait crops, social-media compression, dark footage and a neon overlay edit.

## 5. Smartphone-domain analysis (what the wild domain actually stressed)

1. **Portrait 720×1280, social-media compression, phone optics**: no measurable harm —
   detection/tracking/lifting clean on every quantitative unit.
2. **Editing is the dominant obstacle**: 5 of 8 reels are unusable for quantitative GT because
   of cuts/montage/overlays, and all 4 uncertain reps are shot-cut casualties. The wild
   bottleneck is *continuity*, not image quality.
3. **Tempo style shift is the dominant model failure**: slow paused reps (squeeze holds +
   extension holds) stretch decoded windows → IoU boundary failures with pulls still correct.
4. **View diversity bites exactly where expected**: the one real miss is the rear
   (depth-dominant) view — consistent with the MB-family's known cross-view weakness; no new
   failure mode appeared.
5. **Dark footage + neon overlay edit (ext_08)**: pose pipeline unaffected.

## 6. Comparison vs internal seated-row reference (Domain A, same protocol v2)

| metric | A: internal 6 videos (30 reps) | B: external wild (10 reps) | change |
|---|---|---|---|
| IoU F1 | 0.841 | 0.632 | **moderately degraded** (window extent on paused tempo) |
| pull-rule F1 | 0.853 | 0.842 | **similar** |
| recall (pull rule) | 0.967 | 0.800 | degraded by 1 rear-view miss (small n) |
| pull MAE | 0.128 s | 0.282 s | ~2.2× noisier, still ≪ 0.533 s tolerance |
| count behaviour | +9 FP of 39 pred | 11 pred vs 10 GT, no spurious reps | similar-or-better |

Overall: **moderately degraded** — rep detection and pull timing transfer; rep-window extent
does not, specifically under paused-tempo style; one rear-view detection failure.

## 7. Generalization questions (answered with measured evidence)

1. *Does the frozen pipeline detect reps at all on wild smartphone footage?* Yes — 10/11
   predictions correspond to real (certain or cut) reps; no hallucinated reps.
2. *Does pull timing survive?* Yes — pull F1 0.842, MAE 0.282 s (tolerance 0.533 s).
3. *Do rep windows survive?* Partially — IoU F1 0.632; fails specifically on paused-tempo reps
   (holds absorbed into windows).
4. *Does tracking survive crowded/dark/compressed phone footage?* Yes — ≤16% window NaN worst
   case, 0–3 jumps per video, no failure attributable to tracking.
5. *Does 25 vs 30 fps matter?* No new failure — the 25 fps video's failures are view/cut
   related; consistent with the Phase 2.7 fps-robustness result.
6. *Portrait aspect / person scale?* No measurable effect.
7. *Camera motion?* The orbiting-camera video could not even be annotated by a human — it is
   excluded as unannotatable rather than counted as model failure; static/near-static units
   were unaffected.
8. *Editing/cuts?* The decisive wild-domain obstacle: breaks GT annotatability (5/8 reels) and
   produces censored partial reps (all 4 uncertain reps). A production system needs continuity,
   i.e. a controlled camera — exactly the Domain C design.
9. *Cross-view?* Front-oblique and side views work; rear (depth-dominant) view produced the one
   real miss — matches the known representation weakness, now seen in the wild.
10. *Cross-tempo?* The genuinely new finding: paused/slow style (decoded tempo 4.2 s vs 1.65 s
    training median) degrades window extent while pulls stay correct.
11. *Cross-athlete?* 3 new athletes, all detected; too few for per-athlete claims.
12. *Is the smartphone domain usable as validation data?* Only with careful curation: 3 of 8
    reels yielded quantitative GT, 10 certain reps total — supporting evidence, never a
    substitute for Domain C.

## 8. Verdict

**PARTIAL EXTERNAL TRANSFER** (n = 10 certain reps / 3 videos / 3 athletes — small-sample,
stated prominently).

Basis: rep detection and pull timing transfer essentially intact (pull F1 0.842 vs 0.853
internal; no spurious reps); rep-window extent degrades on paused-tempo reps (IoU 0.632 vs
0.841); one rear-view miss consistent with the known view weakness. Not STRONG (IoU gap + miss),
not WEAK (detection, timing, tracking and robustness to phone-domain artifacts all held), and
the data, while small, is sufficient to support this graded conclusion.

**No model change follows from this verdict** (frozen-model rule). Implications are recorded as
validation-design facts: Domain C should include at least one slow/paused-tempo set and a rear
view; a window-extent treatment (if ever attempted) must be developed on TRAIN/VAL data only
and justified against Mendeley + Domain C, never against these reels.

## 9. Reproduction & integrity

- `ai/research/external_stress/`: `annotate_external.py` (draft aid), `make_sheets.py` /
  `frame_grid.py` (annotation sheets), `evaluate_external.py` (frozen eval).
- Integrity tests: `ai/tests/test_external_stress.py` (URL/hash dedup, GT validity, domain
  separation from Mendeley, raw-video git-ignore, excluded videos absent from metrics,
  uncertain-tier isolation).
- Licensing: Instagram content is referenced by URL + MD5 only; `raw/`, `sheets/`, `drafts/`
  are git-ignored (frame imagery is never committed or redistributed); committed artifacts are
  skeletal/numeric only.
