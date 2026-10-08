# UNSEEN_ATHLETE_DATASET_SPEC.md — final validation dataset (v1.0, frozen 2026-10-08)

STATUS: **BLOCKED — requires physical recording by the owner.** Nothing below may be weakened
after results are seen. Acceptance criteria are pre-registered HERE, before any data exists.
This dataset is FINAL-TEST data: it must never be used for training, tuning, threshold selection,
or repeated evaluation. One locked evaluation after model/rule freeze (a second run only after a
documented invalidating bug, per TEST policy).

## 1. Subjects
- ≥ 10 athletes who appear NOWHERE in development data (not the original seated-row athlete, not
  any Mendeley subject). Broad diversity encouraged (body size, sex, age) — document what was
  achieved, never fabricate diversity that wasn't recorded.
- Subject IDs: `ua_001 … ua_0NN` (namespace disjoint from `subject_0XX` by construction; the
  validator enforces the prefix).

## 2. Recording protocol (per athlete)
- Exercise: REAL seated row on a real machine (document machine model/gym). No simulated motion.
- ≥ 3 camera views: `view_01` rear (the product's primary placement, ~2–3 m, chest height),
  `view_02` side/oblique (~45–90°), `view_03` a different practical position (other side, other
  distance 1.5–4 m, or other height). Record actual placement: distance (m), height (m), angle.
- Per view, per athlete, at least TWO sets: one `form=good` (5–12 natural reps) and one
  `form=deviated` with a DOCUMENTED, safe deviation per set from: asymmetric_pull,
  excessive_trunk_motion, reduced_rom, delayed_side_onset, inconsistent_tempo. No dangerous loads.
- Tempo coverage across the dataset: slow (≥3 s/rep), normal, fast (≤1.5 s/rep) — at least one
  set of each tempo class somewhere per athlete.
- Natural variation REQUIRED: everyday clothing (≥2 categories across dataset), gym lighting as-is
  (log bright/dim/mixed), real backgrounds (other people allowed; subject must be the nearest/
  largest person), phone cameras fine; 15–60 fps; ≥540p. Do NOT stage lab-perfect footage only.

## 3. Folder structure + metadata (machine-readable; validator-enforced)
```
ai/data/unseen_athletes/
  manifest.csv                 one row per video (schema below)
  subjects.csv                 ua_id, (optional) height_cm, build_category, clothing notes
  annotations/reps.csv         the rep ground truth (schema below)
  ua_001/view_01/ua_001_view01_good_set1.mp4 ...
```
manifest.csv columns (all required):
`video_id, subject_id, session_id, view_id, exercise, form_type, deviation_type, tempo_class,
camera_fps, width, height, lighting_condition, clothing_category, camera_distance_m,
camera_height_m, camera_angle_deg, recording_timestamp, annotation_version`
(`deviation_type` = `none` for good sets.)

## 4. Annotation standard (annotations/reps.csv)
`rep_id, video_id, subject_id, exercise, form_label, deviation_type, start_frame, pull_frame,
end_frame, start_time_s, pull_time_s, end_time_s, annotation_confidence, notes`
- 0-based frames; `start < pull < end`; times = frame / camera_fps of THAT video (fps-aware).
- `annotation_confidence ∈ {high, medium, low}`; ambiguous movements are labelled `low` or left
  un-annotated with a note — NEVER forced into good/bad. `low` reps are evaluated under the
  established don't-care rule. Annotate with the Phase 2.5B tooling (draft + human verification);
  every video must be human-verified (no auto-tier in a FINAL test set).

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

## 6. Automated validation
`ai/research/dataset_validation/validate_unseen_dataset.py` must print an all-PASS report before
any evaluation: structure, metadata completeness, video integrity (decodable, frame counts, fps
match), annotation ordering/ranges, per-athlete coverage (3 views, good+deviated, tempo classes),
duplicate files (MD5), subject-namespace leakage, and dev-subject collisions. FAIL blocks use.
