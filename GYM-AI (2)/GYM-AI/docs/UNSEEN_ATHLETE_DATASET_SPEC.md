# UNSEEN_ATHLETE_DATASET_SPEC.md — final validation dataset (v1.1, 2026-10-10; v1.0 frozen 2026-10-08)

STATUS: **BLOCKED — requires physical recording by the owner.** Nothing below may be weakened
after results are seen. Acceptance criteria are pre-registered HERE, before any data exists.
This dataset is FINAL-TEST data: it must never be used for training, tuning, threshold selection,
or repeated evaluation. One locked evaluation after model/rule freeze (a second run only after a
documented invalidating bug, per TEST policy).

**v1.1 (2026-10-10, before any recording; frozen at tag `phase6-tools-v1`).** Owner instruction: the
side view and slow/paused tempo are mandatory for every athlete. No §5 gate is changed;
`docs/PHASE6_PREREGISTRATION.md` adds the per-tempo gate, the data-quality, exclusion and abstention
rules and the procedure, and **is the authority where wording differs**.
- Stricter: views fixed (rear + side + oblique for every athlete); `paused` tempo added and every
  tempo required from the rear and the side view; operational tempo definitions; good and deviated
  takes per view at normal tempo; blind annotation with no model-drafted marks, plus a
  pre-registered agreement check.
- **Narrower population (deliberate, declared as a limitation):** exactly one person in the frame
  (v1.0 allowed other people if the athlete was the nearest), shorter side ≥ 1080 px (was 540p),
  24–60 fps (was 15–60). Results will hold only for recordings like these.
- Naming `A01_<view>_<tempo>_take<N>`, athlete IDs `A01…` (replacing `ua_001`); data root outside the
  repository and outside OneDrive (`%LOCALAPPDATA%\gym-ai\phase6`).
- Tooling: `ai/scripts/validate_recordings.py` (intake), `ai/scripts/annotate_reps.py`,
  `ai/scripts/annotation_agreement.py`, `ai/scripts/phase6_evaluate.py`.

## 1. Subjects
- ≥ 10 athletes who appear NOWHERE in development data (not the original seated-row athlete, not
  any Mendeley subject). Broad diversity encouraged (body size, sex, age) — document what was
  achieved, never fabricate diversity that wasn't recorded.
- Athlete IDs `A01`, `A02`, … (disjoint from the development namespaces by construction). The owner
  attests that no athlete appears in development data.
- Written consent from every athlete, on `docs/PHASE6_CONSENT_TEMPLATE.md` (fill in the retention
  date and contacts first). The ID ↔ name key and the signed forms are kept offline, apart from the
  footage.

## 2. Recording protocol (per athlete)
- Exercise: REAL seated cable row on a real machine (document machine model/gym). No simulated
  motion. **Attachment: two single D-handles or a straight bar**, so the hands can move
  independently (needed for `asymmetric_pull` and `delayed_side_onset`); note it in `notes`.
- **Three camera views, all mandatory** (record the placement actually used: distance m, height m,
  angle °):

  | view | placement |
  |---|---|
  | `rear` | behind the athlete, the product's primary placement, about 2–3 m, chest height |
  | `side` | about 90°, perpendicular to the pulling direction |
  | `oblique` | about 45°, between rear and side |

  Record the views **simultaneously** (three phones on tripods): one set then gives all three
  videos. Every phone shows the whole body and the handle at full arm extension, with margin.
- **Tempos** (cue the athlete; the definitions are checked from the annotations and reported):

  | tempo | definition |
  |---|---|
  | `normal` | the athlete's natural controlled tempo |
  | `slow` | every rep (start of the pull → arms straight again) takes ≥ 3 s |
  | `paused` | in every rep, **both** a 1–2 s hold at full contraction (handle at the body) **and** a 1–2 s hold at full extension (arms straight) before the next pull |
  | `fast` | every rep takes ≤ 1.5 s, still controlled |

  Each tempo is recorded from the rear AND the side view (with three phones: from all three).
- **Form**: per athlete and per view, one `form=good` set AND one `form=deviated` set, **both at
  tempo `normal`**. The deviated set has ONE documented, safe deviation from: asymmetric_pull,
  excessive_trunk_motion, reduced_rom, delayed_side_onset, inconsistent_tempo. Across the cohort,
  each of the first four types **must** come from ≥ 2 athletes (rota below). The slow, paused and
  fast sets are performed with good form. No dangerous loads. 5–12 natural reps per set; every take
  needs ≥ 3 clear reps.
- **Natural variation** REQUIRED:
  - everyday clothing (≥ 2 categories across the dataset);
  - gym lighting as-is (log bright/dim/mixed);
  - real backgrounds, but **exactly one person in the frame**. Keep other gym users out of the shot
    and avoid mirrors in the background: a reflection of the athlete counts as a second person (the
    intake check flagged background people or reflections in 4 of 6 Mendeley side-view videos);
  - phone cameras are fine.

  Do NOT stage lab-perfect footage only.
- **Technical minimums** (checked by `validate_recordings.py` after every session): shorter side
  ≥ 1080 px; 24–60 fps (**30 fps recommended**: it halves file sizes and processing time against 60);
  5–600 s per file, **one set per file**; whole body and both hands inside the frame for the whole
  set.
- **Session routine**: start all phones, **then step out of every view** before the athlete starts;
  stop the phones after the set. Never trim, re-encode or rename-by-editing a video; copy the files
  unchanged into `videos/<athlete>/` with their final names. Run the intake check
  (`python -m ai.scripts.validate_recordings --athlete A01`) while the athlete is still there and
  re-record every FAIL as the next take number. Fill the manifest rows during the session.
- **Blinding at the gym**: no GYM-AI app, service or live mode is run during the sessions or on the
  recordings before the evaluation.

### 2a. Shot list per athlete (three phones, simultaneous)

| set | tempo | form | files (rear / side / oblique) |
|---|---|---|---|
| 1 | normal | good | `A01_rear_normal_take1`, `A01_side_normal_take1`, `A01_oblique_normal_take1` |
| 2 | normal | deviated (type from the rota) | `A01_{rear,side,oblique}_normal_take2` |
| 3 | slow | good | `A01_{rear,side,oblique}_slow_take1` |
| 4 | paused | good | `A01_{rear,side,oblique}_paused_take1` |
| 5 | fast | good | `A01_{rear,side,oblique}_fast_take1` |

5 sets = 15 files per athlete, about 20–30 minutes including rests. With **one camera**, move it
and repeat: rear sets 1–5, side sets 1–5, oblique sets 1–2 (12 sets; watch fatigue, rest longer).

### 2b. Deviation rota (each type from ≥ 2 athletes)

| athletes | deviated set (set 2) |
|---|---|
| A01, A05, A09, … | asymmetric_pull (one arm pulls shorter or later) |
| A02, A06, A10, … | excessive_trunk_motion (swing the torso back and forth) |
| A03, A07, … | reduced_rom (stop each pull halfway) |
| A04, A08, … | delayed_side_onset (one side starts clearly after the other) |

`inconsistent_tempo` has no v1 indicator; record it only as an extra deviated set if wanted.

### 2c. Expected effort
- Recording: about 30 minutes per athlete; intake check after each session (a few minutes per
  athlete) and once for the whole cohort (about an hour for 150 files).
- Annotation: `a1` about 10–15 hours for 150 files; `a2` about 2 hours per agreement round.
- Final run: several hours on the laptop CPU (roughly 4–10 h for 100–150 minutes of 30 fps footage);
  AC power, sleep off.
- Before the first real session, a **pilot** in a separate data root runs the whole chain once
  (intake, annotation, agreement, finalize, `phase6_evaluate --check-only`); pilot videos are never
  evaluated.

## 3. Folder structure + metadata (machine-readable; tool-enforced)
```
%LOCALAPPDATA%\gym-ai\phase6\            (outside the repository and outside OneDrive)
  videos/A01/A01_rear_normal_take1.mp4   <athlete>_<view>_<tempo>_take<N>.<mp4|mov>
  manifest.csv                           one row per video file (columns below)
  qa/intake_report.json, .md             validate_recordings.py (full check): the verdict per file
  qa/intake_history.jsonl                every intake check, append-only
  qa/annotation_agreement.json           agreement rounds (annotation_agreement.py)
  annotations/<annotator>/<video_id>.csv per-annotator marks + .meta.json (annotate_reps.py)
  annotations/reps.csv                   FINAL ground truth (annotate_reps.py --finalize)
  cache/, results/                       written by the final run
```
`manifest.csv` columns (`validate_recordings.py --write-manifest-template` writes the rows of new
files to `qa/manifest_template.csv`; append them to `manifest.csv` and fill them in):
`video_id, athlete_id, view, tempo, take, form_type, deviation_type, lighting_condition,
clothing_category, camera_distance_m, camera_height_m, camera_angle_deg, recording_date, notes`
(`form_type` ∈ {good, deviated}; `deviation_type` = `none` for good sets.) The take number links the
simultaneous videos of one set (A01_rear_slow_take1 and A01_side_slow_take1 are the same set). A
take that FAILED the intake check stays in the manifest while its file exists; a FAIL file showing a
bystander is deleted, and its manifest row removed (the intake history keeps its hash and reasons).

## 4. Annotation standard
Definitions, confidence labels, blinding, the agreement rounds and the tools:
`docs/PHASE6_ANNOTATION_GUIDE.md` (the same START/PULL/END definitions as all development ground
truth). The primary annotator `a1` marks every take that passed the intake check; `a2` marks the
agreement subsets.
- Final ground truth `annotations/reps.csv`:
  `video_id, rep_id, start_frame, pull_frame, end_frame, annotation_confidence, annotator, notes`.
- 0-based frames in OpenCV sequential decoding order, `start < pull < end`, reps never overlap;
  times are frame / the container's average fps.
- `annotation_confidence ∈ {high, medium, low}`. Ambiguous movements are labelled `low` (the
  don't-care tier) — NEVER forced; `low` ≤ 10% of all reps and ≤ 20% per view or tempo, ≥ 3
  certain reps per take (otherwise re-record).
- Every rep is placed by a person. There are no automatic drafts: annotators never see model output
  for these videos.

## 5. Pre-registered acceptance criteria (rep detection, MB-Lite production system, frozen today)
Evaluated with the Phase 2.7 protocol v2, seconds-defined tolerances (pull ±0.533 s, IoU ≥ 0.5),
`low`-confidence reps as don't-care. PASS requires ALL of:
- pooled F1 ≥ 0.80 (IoU) AND recall ≥ 0.85;
- per-ATHLETE F1 ≥ 0.65 for at least 9 of 10 athletes (no athlete catastrophically fails);
- per-VIEW pooled F1 ≥ 0.70 for every view;
- pull MAE ≤ 0.25 s on matched reps;
- form-indicator sanity: deviated sets trigger the matching indicator at a higher rate than good
  sets for ≥ 3 of the 5 deviation types (direction only — no accuracy claim pre-registered).
FAIL on any criterion → stop, publish the failure analysis (per-athlete/view/tempo/lighting),
diagnose, and do NOT re-run on this set until the fix is frozen and documented.
Every weak slice is reported; aggregate numbers never hide per-slice failures.

## 5b. Phase 2.5 review (2026-10-09) — the §5 gates themselves, NOT amended, reviewed against
all evidence gathered since they were frozen (P2.1–P2.4, EXPERIMENT_REGISTRY.md)

Annotation-noise sensitivity (`ai/research/phase2/annotation_noise.py`: jitters GT boundaries by
a random ±k frames, re-scores the SAME frozen predictions — isolates evaluation-protocol noise
from model behaviour) on Mendeley VAL: F1 is robust (0.9137→0.8982 even at k=±5, a tiny 1.7%
relative drop); pull MAE is NOT (0.079 s→0.207 s at k=±5, climbing roughly linearly with k).
Consequence: **the pooled-F1-based criterion below is solid; a pull-MAE-based RANKING between
close pipelines is not** — e.g. the TEST pull-MAE gap between MB-Lite (0.094 s) and PFv2
(0.135 s) is only ~0.041 s, which is within the ±0.042 s band that ±2 frames of pure annotation
jitter alone produces on VAL. Internal `pull MAE ≤ 0.25 s` criterion below is NOT undermined by
this: VAL's noise-inclusive MAE only reaches ~0.207 s even at k=±5 (a larger jitter than this
project's documented annotation precision of ±3–4 frames), leaving comfortable headroom under
0.25 s — the gate is appropriately set, not noise-dominated.

Per-criterion review (none of the five numbers below is changed — a frozen pre-registered gate
is amended only with a specific, justified reason, not a routine update):
- Pooled F1 ≥ 0.80 (IoU) AND recall ≥ 0.85: **CONFIRMED appropriate.** Both internal_sr (0.841)
  and mendeley_val (0.914) clear it comfortably; F1's demonstrated robustness to annotation
  noise (above) means this is a meaningful bar, not a coin flip.
- Per-athlete F1 ≥ 0.65 for ≥9/10: **CONFIRMED appropriate**, no new evidence changes this.
- Per-view F1 ≥ 0.70 for every view: **CONFIRMED as written, but flagged AT ELEVATED RISK of
  failing on the side/oblique view specifically**, now with MORE evidence than when this gate
  was frozen: Mendeley TEST side F1 is 0.383 (vs VAL's milder 0.760 — a large split-to-split
  swing already known); P2.1-panoptic (independent, real multi-view 3D ground truth) measured
  PA-MPJPE 12.6 cm (side) vs 4.2 cm (front) and confirmed the depth-sign convention is inverted
  for non-rear cameras. If Domain C's side/oblique view fails this gate, that is an ANTICIPATED
  outcome given this evidence, not a surprise — the spec's own FAIL procedure (publish the
  failure analysis, fix on dev data only, no re-run on this set) already covers it; no change
  needed to the gate number itself.
  **Added 2026-10-10 (P2.6, development data only, gate unchanged):** a leave-one-subject-out run
  over the 15 Mendeley development components puts side-view pooled F1 at **0.654**, below 0.70.
  Diagonal is 0.974 and front 0.967. The same run supports the other gates: pooled 0.878 / recall
  0.886, and per-athlete ≥ 0.65 for 14 of 15 components (EXPERIMENT_REGISTRY.md P2.6).
  **Added 2026-10-10 (P2.7, gate unchanged):** the owner's last pre-freeze attempt (anatomical
  backward-axis sign) raised LOSO side-view F1 to 0.853 but cut the rear-view seated row from
  0.841 to 0.558 and was rejected. The frozen candidate therefore keeps the known side-view
  weakness; a side-view FAIL in Phase 6 remains an anticipated outcome.
- Pull MAE ≤ 0.25 s: **CONFIRMED appropriate** (see annotation-noise analysis above — comfortable
  headroom over the measured noise floor).
- Form-indicator direction ≥3/5 deviation types: **CONFIRMED appropriate**; P2.3's per-slice
  work (FORM_ANALYSIS_V1.md) found real per-slice weaknesses (`rom_proxy` on diagonal view/
  slow tempo) but no indicator failed outright, consistent with keeping this bar as-is.

_Note (2026-10-10, v1.1): the pre-registration states that squeeze holds put G5 (pull MAE) at risk
as well (the P2.5 headroom was measured without long holds), and makes G6 precise (paired design,
pre-registration §4.3)._

## 6. Automated validation (v1.1)
1. **Intake** after every session and once for the cohort, before annotation:
   `python -m ai.scripts.validate_recordings`. Per file: naming, folder and repeated names;
   decoding; resolution, fps, duration and rotation metadata; framing with the product detector on
   CPU. Rules about the camera set-up FAIL (body and hands inside the frame, one person); rules
   about how well the detector sees the athlete only WARN (check those takes by eye). Per cohort:
   the same coverage and form rules as the final run, manifest ↔ files, identical files. It runs no
   rep model and computes no metric. FAIL takes are re-recorded and never annotated; every check is
   kept in the append-only history.
2. **Annotation**: `annotate_reps.py` refuses to re-bind marks to a changed file;
   `--finalize` (primary annotator only) refuses unfinished takes, open reps, changed files and CSVs
   edited outside the tool. `annotation_agreement.py` runs the pre-registered agreement rounds.
3. **Evaluation**: `phase6_evaluate.py --check-only` re-checks everything without running the
   model; `--lock` freezes the inputs (committed, tagged, timestamped); the final run refuses any
   inconsistency.

The v1.0 validator `ai/research/dataset_validation/validate_unseen_dataset.py` checked the v1.0
layout (`ua_` namespace); it is superseded by these tools.
