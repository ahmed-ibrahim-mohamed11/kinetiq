# DATASET_MENDELEY.md — audit of the multi-view fitness dataset (Phase 2.5A, 2026-10-04)

Source: "A Multi-View Raw Video Dataset of Seven Fitness Exercises with Good/Bad Form Labels",
doi 10.17632/kgbb3yn47p.**3** (published 2026-08-10). Local copy: `ai/data/mendeley/` (git-ignored).
Archive verified: `Dataset Subject-wise.zip`, 3,113,825,556 bytes, SHA-256
`6f6bfda33f094ba13e8509b1cd9a5c1e479062fd0e7749f6b6713db7d588ef84` (matches the Mendeley API record),
all 2,633 ZIP members CRC-OK. The other two published archives (Exercise-wise, Exercise-Quality-wise)
are the SAME videos re-organised; only Subject-wise was downloaded (subject-wise splits are our need).

## What actually exists (verified on disk, not from the README)
- **1,042 MP4 videos**, nothing else — **0 annotation files** of any kind.
- Structure: `Dataset Subject-wise/subject_XXX/<exercise>/<good|bad>/<front|side|diagonal>/<file>.mp4`
  (one video per leaf; the README's structure sketch omits the view level inside bad/, it exists for both).
- Naming: `subject_<ID>_<exercise>_<quality>_<view>.mp4` — holds for 1,042/1,042 files, with ONE typo:
  `subject_003_bicep_good_side.mp4` (folder = `bicep_curl/good/side`, so it is a bicep-curl video;
  treat the filename's `bicep` as `bicep_curl`).
- **26 subjects**: 24 complete (42 videos = 7 exercises × 2 qualities × 3 views),
  `subject_026` has 30 (missing `back` and `push_up` entirely), `subject_025` has only 4 (tricep only).
- **7 exercises**: abs (side bend), back (bent-over dumbbell row), bicep_curl (alternating), push_up,
  shoulder (press), squat, tricep (overhead extension). Counts good/bad per exercise: abs 75/75,
  back 72/72, bicep_curl 75/75 (incl. the typo file), push_up 72/72, shoulder 75/75, squat 75/75, tricep 77/77.
- **Views**: front 348, side 346, diagonal 348.
- **All videos are 15.0 fps** (every one; our seated-row research clips were 30 fps — the pipeline takes
  fps as a parameter, but filters/duration priors must use the real fps).
- Resolutions are heterogeneous (portrait phone video dominates): 810×1440 (717), 540×960 (156),
  358×636 (69), 1440×810 (46), plus small counts of 7 other sizes, including landscape and 1620×2880.
- Durations: median 30.7 s, mean 28.5 s, range 1.4–46.4 s. **11 videos are < 8 s** (listed in the
  manifest; includes `subject_013_back_good_side.mp4` at 1.4 s and all 6 push-up videos of subject_019
  at 2.7–7.1 s) — likely truncated recordings; exclude from benchmarks or treat with care.

## Labels that exist vs. labels that do NOT exist
| Label | Status |
|---|---|
| subject identity | YES (folder + filename) |
| exercise class (7) | YES (folder + filename) |
| form quality good/bad (video-level, binary) | YES (folder + filename) |
| camera view front/side/diagonal | YES (folder + filename) |
| repetition boundaries (START/PULL/END or any) | **NO** |
| rep counts | **NO** |
| form-error type (what is wrong in "bad") | **NO** (only binary good/bad) |
| 2D joints | **NO** |
| 3D joints | **NO** |
| camera calibration / intrinsics / sync between views | **NO** |
| camera metadata (device, distance, height) | **NO** |
Nothing was inferred; this table reflects the extracted archive contents (only MP4s exist).
Note: the three views of a subject/exercise/quality are separate recordings; nothing guarantees they
are time-synchronised takes of the same set, and the heterogeneous durations suggest they are not.

## Machine-readable inventory
`ai/data/mendeley_manifest.csv` (tracked in git) — one row per video:
`video_id, subject_id, exercise, camera_view, form_label, video_path, fps, frame_count, duration_sec,
width, height, parse_ok`. Built by `ai/research/build_mendeley_manifest.py` (re-runnable; probes every
file with OpenCV). 0 probe failures.

## Exercise recommendation for the first rep-segmentation benchmark
1. **back (bent-over dumbbell row) — PRIMARY.** The only pulling exercise: hinge + bilateral elbow
   pull toward the torso. Mechanically the closest to our seated row (elbow travel in the sagittal
   plane, clear PULL apex, return), though it is **not identical**: standing hinged posture, free
   dumbbells, and camera views (front/side/diagonal) differ from our rear view. 144 videos, complete
   good/bad × 3 views for 24 subjects.
2. **bicep_curl — SECONDARY (stress test).** Alternating arms: the extreme case for per-arm phase
   fusion ("either arm is pulling") and for asymmetry timing. Upper-body pull-adjacent (elbow flexion).
3. Not recommended first: squat/push_up/abs (whole-body or prone, farthest mechanics), shoulder/tricep
   (overhead pressing, inverse phase relationship to a pull).

## Proposed annotation subset for Phase 2.5B (NOT annotated yet)
`ai/data/mendeley_annotation_subset_PROPOSED.csv` — **34 videos, 16.6 min of footage**:
- **Core (24)**: back × 6 subjects (001, 005, 009, 014, 019, 023) × {good, bad} × {side, diagonal}.
  Subjects chosen to span the ID range (different recording sessions), resolutions (810×1440, 540×960,
  358×636 mixes) and pacing (019's sets are visibly shorter/faster: 18–28 s vs 30–34 s).
- **Cross-view probe (6)**: the same back exercise, front view, subjects 001, 009, 019 × {good, bad}.
- **Transfer probe (4)**: bicep_curl, side view, subjects 005, 023 × {good, bad}.
Deliberate properties: ≥6 people, 3 views, good+bad, slow+fast pacing, two movement styles.
Rep speed cannot be verified from metadata alone; the annotator confirms it during Phase 2.5B.
`subject_013_back_good_side.mp4` (1.4 s) is deliberately excluded.

## Duplicate files (found during Phase 2.5B, 2026-10-04)
MD5 hashing of all 1,042 videos: **33 duplicate groups covering 67 files (~6.4%)**, list committed at
`ai/outputs/mendeley_duplicate_groups.json`. Consequences, verified:
- **21 groups span good AND bad** (same clip filed as both) → the binary form label is noisy.
- Several duplicates cross **subjects** (005↔006, 010↔013, 014↔020, 015↔026, 019↔020) and even
  **exercises** (`subject_007_back_bad_diagonal` = `subject_007_squat_good_diagonal`) → subject-wise
  splits MUST keep each duplicate group's subjects on the same side (leakage risk), and exercise
  labels cannot be trusted blindly for those files.
- In our benchmark subset, 3 of 34 files were duplicate copies (subject_023) and are annotated once
  under the kept video_id; `subject_019_back_bad_diagonal` equals subject_020's file, so its subject
  identity is uncertain → subjects 019 and 020 are linked for all future splits.

## Rep-boundary benchmark annotations (Phase 2.5B, 2026-10-04)
`ai/data/mendeley_rep_annotations.csv` — **31 videos, 443 complete reps**, 0-BASED frames,
columns `video_id, subject_id, exercise, camera_view, form_label, rep_id, start_frame, pull_frame,
end_frame, fps`. Method: machine drafts (largest-box person selection → wrist-height signal → peak
proposal) with **every video visually verified against raw frames** and corrections applied
(`ai/research/mendeley_annotation/finalize_annotations.py` records every drop/fix with its reason).
Censored edge reps and setup/put-down movements are not annotated. The person-selection audit and the
aid's YOLO dependence (residual circularity risk for Phase 2.5C pose benchmarking) are documented in
the Phase 2.5B report; boundary precision is ~±3 frames (±5 on plateau-style pulls).

## Risks / ambiguities (for planning, all verifiable later)
- 15 fps halves temporal resolution vs our 30 fps clips; pull-timing MAE in frames is not comparable
  1:1 across datasets (1 frame = 66.7 ms here).
- Portrait video + standing hinge: person occupies the frame differently; YOLO person selection must be
  re-checked (gym background may contain other people — "highest-confidence box" needs verification).
- "good/bad" is video-level and binary; bad-form videos may contain a MIX of acceptable and wrong reps.
  It is metadata only and must never feed the segmenter.
- No view synchronisation ⇒ cross-view experiments are "same subject, same exercise, different take"
  unless verified otherwise.
- The 11 short (<8 s) videos and subjects 025/026's missing exercises make "complete grid" assumptions
  wrong; always select via the manifest, never by globbing expectations.
