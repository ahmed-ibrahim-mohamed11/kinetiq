# PHASE6_PREREGISTRATION.md — final unseen-athlete validation (Domain C)

Pre-registered 2026-10-10, before any Phase 6 recording exists. Revised twice the same day after two
adversarial reviews (DECISION_LOG 2026-10-10); this is the version frozen at the tag.

## 0. Status, freeze and authority

- This document, the evaluated system and the evaluation code are frozen in
  `docs/PHASE6_FREEZE_CANDIDATE.json` at git tag **`ai-core-candidate-v1`**.
- The intake, annotation and agreement tools, the annotation guide (`docs/PHASE6_ANNOTATION_GUIDE.md`)
  and the recording spec (`docs/UNSEEN_ATHLETE_DATASET_SPEC.md` v1.1) are frozen in
  `docs/PHASE6_TOOLS_FREEZE.json` at git tag **`phase6-tools-v1`**, created next, also before any
  recording.
- Every input of the run (videos, manifest, annotations, intake and agreement records) is frozen in
  `docs/PHASE6_DATA_LOCK.json` at git tag **`phase6-data-lock-v1`** before the model sees any Phase 6
  video (§6 step 7).
- Each tag's commit hash is sent to the supervisor when the tag is made (§6 step 0 and step 7). The
  repository has no remote, so this message is the external, dated proof that the rules existed first.

From the first tag on, **no model, threshold or configuration change is allowed before the Phase 6
evaluation** (owner instruction). The runner enforces it: it reads the freeze files from their tags
and refuses to run (exit 3) on any drift (§1).

Later notes go only into `docs/PHASE6_ADDENDA.md` as dated entries. An addendum may clarify an
example, record an environment repair, document an evaluation-code fix (§7) or explain a change of
second annotator. **It can never change a rule of this document**: no gate, threshold, definition,
coverage rule, confidence tier or exclusion rule. The rule constants are frozen with the candidate
and compared on every run.

Relationship to the other documents:
- `UNSEEN_ATHLETE_DATASET_SPEC.md` §5 (frozen 2026-10-08): its five gates are kept here as G1, G2,
  G3, G5 and G6. G6 is made precise (paired design, §4.3). No gate number is lowered.
- This document adds the per-tempo gate G4, the data-quality rules, the abstention and exclusion
  rules, the agreement procedure, the data lock and the report. Where wording differs from the spec
  or the guide, **this document wins**.
- Executable form, pinned with this document:
  - `ai/scripts/phase6_evaluate.py`: the one command, every gate and every check;
  - `ai/evaluation/phase6_dataset.py`: file conventions, coverage and data-quality rules;
  - `ai/evaluation/protocol.py`: matching.
- The tools implement §2.3 and §3 and import their rules from the files above:
  `ai/scripts/validate_recordings.py` (intake), `ai/scripts/annotate_reps.py` (annotation),
  `ai/scripts/annotation_agreement.py` (agreement).
- `ai/tests/test_phase6.py` and `ai/tests/test_phase6_tools.py` test these rules.

## 1. What is evaluated

The frozen candidate. The runner refuses to start (exit 3) unless all of these hold:
- the working-tree freeze files equal the copies at their tags, and HEAD descends from both tags;
- every pinned **system** file equals its frozen hash: `ai/core/**`, the rep-segmentation artifact,
  both product checkpoints, the lifter config and vendored lifter code, the seated-row form
  thresholds, `requirements/core.txt`;
- every git-tracked pinned file has the frozen content **at the tag** too (the tag reproduces it);
- the key **package versions** are identical, build tags included (e.g. `torch 2.14.0+cu130`), as
  are the Python version, both OpenCV distributions and the hashes of the OpenCV binary and its
  FFmpeg plugin (they decide the decoded frame order);
- the **rule constants** (gates, protocol, bootstrap, coverage, data-quality, agreement, annotator
  ids) equal the frozen `rules` block;
- the **evaluation files** (this document included) equal their frozen hashes (the only exception
  is §7);
- the **tools**, the guide and the spec equal theirs.

| item | value |
|---|---|
| Mode A pipeline (primary) | YOLO11n-pose top-8 @640 → deterministic tracker → MotionBERT-Lite (flip-TTA) → `prepare_video` → guarded HSMM decode (`safe_predict_video`) → form indicators (`ai/core/pipeline.py`) |
| rep-segmentation model | `ai/models/rep_segmentation/mblite_rowing_v1.json`, fingerprint `63d36c31761c95a8…` (Mendeley BACK TRAIN fit, Phase 2.7 cfg) |
| backward-axis sign | camera-derived, as frozen (the anatomical sign was tested and REJECTED in P2.7) |
| form thresholds | `ai/models/form_thresholds_v1_seated_row.json` (PROVISIONAL, 15 reps of 1 athlete) |
| frame rate | each video's container average fps (production default) |
| device | CPU (the numeric reference; the final run refuses `--device cuda`) |
| Mode B (secondary, no gate) | live presets `V2_quality` and `V3_budget_15fps`, replayed on the same detections (§9) |

**Known limitations, declared before any data exists.** The report prints each one next to its gate.
- **R-07, side view.** LOSO side IoU F1 0.654 and locked TEST 0.383, against 0.97 for front and
  diagonal. P2.7 found the main cause (the camera-derived sign), but the fix broke the rear view and
  was rejected. G3 for the side view is **at elevated risk**. Every tempo slice is about one third
  side view, so R-07 also weighs on all four G4 rows.
- **R-08, holds.** A synthetic 0.5 s extension hold cuts F1 to 0.72 (internal) and 0.76 (Mendeley
  VAL); the fixes were rejected (P2.2, P2.7). G4 for paused and slow is **at elevated risk**.
  Squeeze holds also move the predicted PULL away from the annotated hold centre, so **G5 is at
  risk** too.
- **Seated-row form thresholds are PROVISIONAL** (R-09), so G6 is at risk; G6 is also only a sanity
  check that can pass by chance (§4.3).
- **Tested population.** Results hold only for recordings that pass the intake check (§2.3): one
  person in frame, ≥ 1080p, 24–60 fps, body and handle inside the frame. Spec v1.0 allowed other
  people in the background and 540p / 15 fps; those recordings are **out of scope** here. The
  narrowing is deliberate (privacy, protocol control) and is stated in every report.
- Every development number is either a proxy exercise (Mendeley bent-over row) or contaminated
  (internal_sr: one athlete, used for model choice). Phase 6 is the first clean test in the product
  domain.

## 2. Data

### 2.1 Cohort, storage and consent
- **≥ 10 athletes** who appear nowhere in development data. The owner attests that none is the
  original seated-row athlete or a Mendeley subject.
- IDs `A01`, `A02`, …; diversity is recorded as achieved, never invented.
- Written consent from every athlete, using `docs/PHASE6_CONSENT_TEMPLATE.md` (purpose, who sees the
  footage, local storage, retention, withdrawal, publication of pseudonymous results). The ID ↔ name
  key and the signed forms are kept offline, apart from the footage.
- **Storage.** The data root is a local folder outside the repository and outside any cloud-synced
  folder: by default `%LOCALAPPDATA%\gym-ai\phase6` (or `$GYM_AI_PHASE6_ROOT`). The runner and the
  tools refuse a root inside OneDrive. Pose caches and per-video results stay under the root.
  Published artifacts contain metrics, pseudonymous IDs and frame numbers only.
- Annotators are identified only by pseudonymous ids (`a1`, `a2`, …); no name appears in any file.
- **Nobody runs any part of the system on a Phase 6 video before the data lock**, except the intake
  tool's detector (§2.3). No GYM-AI app or live mode is used during the recording sessions.

### 2.2 Coverage and data quality — enforced: the runner refuses an incomplete cohort (exit 2)

| requirement | rule |
|---|---|
| views | `rear`, `side` and `oblique` (≈ 45°), all three for every athlete; no other view exists in the naming scheme |
| tempos | `normal`, `slow`, `paused`, `fast` (definitions below), **each recorded from both `rear` and `side`**; `oblique` at least at `normal` (the form row needs it) |
| form | per athlete AND per view: ≥ 1 good-form take and ≥ 1 deviated take, **both at tempo `normal`**, with a documented deviation type; across the cohort, each indicator-mapped deviation type (§4.3) deviated at normal tempo by ≥ 2 athletes |
| reps per take | 5–12 natural reps is the guidance; **≥ 3 certain reps** (`high` or `medium`) per take is enforced |
| `low` share | `low` (don't-care) reps ≤ **10%** of all annotated reps, and ≤ **20%** within any view or tempo slice |
| G6 data condition | for each indicator-mapped type, in the paired cells of §4.3: ≥ 10 certain deviated reps from ≥ 2 athletes and ≥ 10 certain good reps |

All of these are computed from the manifest and the annotations only, before any model run, so a
cohort that passes them cannot end with an untestable gate.

If a data-quality rule fails, the affected takes are **re-recorded** (and annotated afresh). `low`
labels are never changed to meet a cap.

**Tempo definitions** (the athlete is cued; the labels are kept as recorded, §4.5):

| tempo | definition |
|---|---|
| `normal` | the athlete's natural controlled tempo |
| `slow` | every rep (START → END) takes ≥ 3 s |
| `paused` | in every rep, **both** a 1–2 s hold at full contraction (handle at the body) **and** a 1–2 s hold at full extension (arms straight) before the next pull |
| `fast` | every rep (START → END) takes ≤ 1.5 s, still controlled |

With three cameras recording simultaneously, the minimum is 5 sets per athlete (normal-good,
normal-deviated, slow, paused, fast) = 15 files; with one camera it is 12 sets. The spec v1.1 shot
list and deviation rota give the exact plan.

### 2.3 Technical intake (checked by `ai/scripts/validate_recordings.py` BEFORE annotation)

The thresholds are committed in that tool and frozen at `phase6-tools-v1`. The tool runs the product
detector (YOLO11n-pose) on CPU on about 3 frames per second. It never runs the rep model and never
computes a metric. Rules that measure the camera set-up FAIL; rules that measure how well the
detector sees the athlete only WARN, so that a detector weakness is measured in the final run (as an
abstention) instead of being filtered out before it.

| check | FAIL | WARN |
|---|---|---|
| name | not `A01_<rear\|side\|oblique>_<normal\|slow\|paused\|fast>_take<N>.<mp4\|mov>` in `videos/<athlete>/`; a name repeated anywhere under `videos/` (every copy fails) | — |
| decoding | the file does not decode; the container frame size differs from the decoded frame size | — |
| resolution | shorter side < 1080 px | — |
| frame rate | outside 24–60.5 fps | variable frame rate |
| duration | outside 5–600 s | above 180 s |
| athlete detected | — | box confidence ≥ 0.5 in < 90% of sampled frames: **check the take by eye** and keep it if the athlete is fully visible |
| whole body in frame | the athlete's box at least 1% inside every frame edge in < 90% of the frames with the athlete | 90–97% |
| handle inside frame (proxy: both wrists) | both wrist positions inside the frame in < 90% of the frames with the athlete | — |
| handle visible | — | both wrists visible (keypoint confidence ≥ 0.3) in < 70% of frames from `side` / `oblique`, < 30% from `rear` |
| exactly one person | another person (box confidence ≥ 0.5, height ≥ 25% of the athlete's) in > 5% of sampled frames; mirror reflections count | any such frame |
| lighting | — | mean luma below 40 |

- The intake report (`qa/intake_report.json`) always reflects exactly the files present, records
  that the framing check ran (`framing_checked`), the device and the package versions, and carries
  the tool's own hash. `--no-detector` writes a separate technical report that the evaluation never
  reads.
- Every check is also appended to `qa/intake_history.jsonl` (append-only), so takes that failed and
  were re-recorded or deleted stay on record; the report lists them (§8).
- The cohort check uses the same coverage rules as the runner (`phase6_dataset.coverage` and
  `form_coverage`); without a manifest the cohort is INCOMPLETE, never PASS.
- A take that FAILS is re-recorded under a new take number. FAIL takes are **not annotated**. A FAIL
  file may be deleted (it must be, if it shows a bystander); its manifest row is then removed, and
  the history keeps its hash and reasons.
- Calibration on development videos (2026-10-10): the tightly framed internal rear-view videos fail
  "whole body in frame", as intended (the camera must be further back); full-body Mendeley side views
  have both hands visible in 84–99% of frames; 4 of those 6 side views fail the one-person rule
  because of people or reflections in the background.

### 2.4 Layout
As defined in `ai/evaluation/phase6_dataset.py`:
- `videos/<athlete>/<video_id>.<ext>` and `manifest.csv` (one row per video file);
- `qa/intake_report.json`, `qa/intake_history.jsonl`, `qa/annotation_agreement.json`;
- `annotations/<annotator>/<video_id>.csv` + `.meta.json` sidecar (video sha256, decoded frame count,
  fps, OpenCV version, CSV sha256, status);
- `annotations/reps.csv`: the final ground truth, built from the primary annotator's files;
- `cache/` and `results/`: written by the runner.

## 3. Ground truth

### 3.1 Definitions
The same as for all development ground truth (Phase 2.5B/2.6, external stress test). The guide is
`docs/PHASE6_ANNOTATION_GUIDE.md`.

| mark | definition |
|---|---|
| **START** | concentric onset: the first frame the handle starts moving toward the body from full extension |
| **PULL** | peak contraction: the frame of maximum elbow flexion (handle closest to the body); for a squeeze hold, the CENTRE of the hold |
| **END** | return to full extension: the first frame the arms are fully extended again; an extension hold belongs to the rest between reps |

Frames are 0-based, in OpenCV sequential decoding order. That is the same decoder and order the
pipeline uses, so annotated and predicted frames are the same frames; the runner checks that the
decoded frame count equals the one the annotation tool saw.

### 3.2 Confidence
`high` / `medium` / `low`. `low` reps are don't-care (protocol v2 uncertain tier): they never create
a miss and absorb at most one prediction each. Their share is capped (§2.2), reported per slice
(§8), and a non-gating strict score counts them as certain.

### 3.3 Blinding
- Annotators never see model output for these videos; there are **no model-drafted annotations**
  (this replaces the spec v1.0 line "draft + human verification": a draft anchors the annotator).
- The annotation tool shows only the video and the annotator's own marks; it loads no model.

### 3.4 Annotators and agreement (pre-registered; recomputed by the runner)
- **`a1` is the primary annotator**: annotates every included take; `a1`'s files are the final ground
  truth. **`a2`** (a second person) annotates the agreement subset independently, from a hand-off that
  contains only the subset videos and the manifest, never `a1`'s files.
- **Round procedure.**
  1. When `a1` has finished every included take, `annotation_agreement.py --select-subset` draws
     round r's subset deterministically: the included takes not used in earlier rounds, stratified
     by view × tempo (one per cell first, then one per cell in turn), seed `20261010 + r − 1`, size
     ≥ max(8, ⌈10% of the included takes⌉), covering every view and every tempo. It records the
     eligible list and a snapshot of `a1`'s files.
  2. `a2` annotates exactly that subset.
  3. `--measure` scores `a2` against `a1` (reference; `a1`'s `low` reps are don't-care) with protocol
     v2. **Accepted iff IoU F1 ≥ 0.90 and pull MAE ≤ 0.15 s**, decided exactly (unrounded), with the
     size and coverage rules met. The result is written once and never overwritten; tier agreement
     (how `a2` labelled `a1`'s tiers on matched reps) is recorded.
  4. Not accepted: a dated addendum adds clarifying examples to the guide (never a new definition or
     tier); `a1` re-reviews **all** takes; then a new round starts on a fresh subset disjoint from
     every earlier one. A different second annotator needs an addendum saying why.
- No individual rep is adjudicated: the ground truth stays `a1`'s files.
- The runner refuses (exit 2) unless the latest round is accepted, was drawn from exactly the
  evaluated takes, has the subset the rule selects, and its result is reproduced from the annotation
  files. Every round, failed ones included, is reported, with how many of `a1`'s files changed after
  each round was drawn.
- If no round can be accepted before the subsets run out, Phase 6 stops before any model run and this
  is reported.

### 3.5 Inputs tied to the evaluated files
The runner refuses to run (exit 2) unless:
- every video file is in the manifest and vice versa, and every file has a current intake verdict
  with its sha256 (excluded takes included); no name occurs twice;
- every included take's intake verdict has framing measurements from the CPU detector, a frame count
  and an fps, and the intake report was written by the frozen intake tool;
- every final rep comes from `a1`, and `reps.csv` equals `a1`'s own files;
- each sidecar names the same file (sha256), is complete with no open rep, and records the same frame
  count, fps and OpenCV version as the intake and the frozen environment, and the CSV was not edited
  outside the tool;
- every annotated END lies inside the video.

### 3.6 Data lock
`phase6_evaluate --lock` writes `docs/PHASE6_DATA_LOCK.json`: the dataset fingerprint and the sha256
of every evaluated video, the manifest, `reps.csv`, the intake report and history, the agreement
record and the addenda, plus the primary annotator and the two tag commits. It is committed, tagged
`phase6-data-lock-v1`, and the tag's commit hash is sent to the supervisor. The final run refuses
unless HEAD descends from the lock tag and the inputs equal the lock byte for byte.

## 4. Metrics and gates

### 4.1 Matching
Protocol v2, `ai/evaluation/protocol.py`, unchanged since 2026-10-05:
- one-to-one greedy matching to the certain reps;
- **IoU rule**: temporal IoU ≥ 0.5; **pull rule**: |Δ pull| ≤ 0.533 s, i.e. `round(0.533 × fps)` frames;
- predictions whose pull lies outside the annotated span are ignored;
- each leftover prediction may absorb one `low` rep.

### 4.2 Metrics
- Primary: IoU-rule F1, micro-pooled over all reps.
- Also reported: precision and recall; pull-rule F1; pull / start / end MAE in seconds over IoU-matched
  pairs; rep-count error per video ((TP + FP) − certain reps), as mean absolute and mean signed; the
  count and share of `low` reps per slice; a strict IoU F1 that counts `low` reps as certain.

### 4.3 Gates — all must PASS

| gate | rule | origin |
|---|---|---|
| **G1** | pooled IoU F1 ≥ **0.80** AND recall ≥ **0.85** | spec §5 |
| **G2** | per-athlete IoU F1 ≥ **0.65** for all but at most ⌊10% × N⌋ athletes (9 of 10) | spec §5 |
| **G3** | per-view IoU F1 ≥ **0.70** for `rear`, `side` and `oblique` (every view present is gated; a slice under 5 athletes is flagged `low_n`) | spec §5 ("for every view") |
| **G4** | per-tempo IoU F1 ≥ **0.70** for `normal`, `slow`, `paused`, `fast` | **new here**; the same bar as the per-view gate, set by that precedent, not from data |
| **G5** | pull MAE ≤ **0.25 s** over IoU-matched pairs | spec §5 |
| **G6** | form-indicator direction shown for ≥ **3** deviation types (below) | spec §5, made precise |

**G6 — paired design.** Indicator mapping: `asymmetric_pull` → `lr_amplitude_ratio`,
`excessive_trunk_motion` → `trunk_motion`, `reduced_rom` → `rom_proxy`, `delayed_side_onset` →
`lr_lag`; `inconsistent_tempo` has no v1 indicator and cannot count.
- For type X, each athlete's deviated-X takes are compared with **the same athlete's good takes in
  the same view, all at tempo `normal`** (the paired cells). The indicators depend on body
  proportions and view, so an unpaired comparison would measure who performed the deviation and from
  where, not the deviation.
- A type shows the **RIGHT DIRECTION** when, over IoU-matched predicted reps in the paired cells,
  (a) the indicator's `attention` rate is higher on the deviated side, pooled; (b) there are ≥ 10
  matched deviated reps from ≥ 2 athletes and ≥ 10 matched good reps; and (c) the rate is higher on
  the deviated side for ≥ 2 athletes individually. Otherwise its direction is NOT SHOWN, including
  when the system missed the deviated reps.
- G6 PASSES iff ≥ 3 types show the right direction. The data condition of §2.2 is checked before
  the run, so every type can be tested.
- Per type, the report gives the rate difference with an athlete-bootstrap 95% CI and each
  athlete's direction (not gating).
- **G6 is a sanity check, not a validation of the indicators.** If each type's direction were a
  coin flip, ≥ 3 of 4 would come out right with probability 5/16 ≈ 0.31 (less with condition (c)).

**Verdict.** **PASS** iff every gate passes; otherwise **FAIL**. A gate left NOT EVALUABLE (a
required slice with no data, or no matched rep at all for G5) counts as FAIL. The pre-run checks of
§2.2 make that impossible for the data; it can only follow from the system's own output.

**Multiplicity.** The verdict needs all 11 gate rows to pass (G1, G2, three views, four tempos, G5,
G6), so a PASS needs no multiplicity correction; it does make a false FAIL more likely, which is
accepted: each slice must stand on its own. The per-slice intervals are reported uncorrected.

### 4.4 Decisions and uncertainty
- Gates are decided **exactly**: F1 and recall from the integer counts as fractions (e.g.
  `2·TP / (2·TP + FP + FN) ≥ 4/5`), pull MAE from integer frame errors and each video's fps. No
  floating-point rounding can flip a gate at its threshold. The report shows 4 decimals.
- Athlete-cluster bootstrap 95% CIs (2,000 resamples, seed 20261010) are reported for pooled F1,
  recall, pull MAE and every per-view / per-tempo F1. The gates use the point estimates.
- With about 10 athletes the intervals will be wide, and the report says so.

### 4.5 Tempo compliance (reported, never gated)
From the final annotations (certain reps): `slow` complies if its median rep (START → END) ≥ 3.0 s;
`fast` if ≤ 1.5 s; `paused` if the median rest between reps ≥ 1.0 s **and** the median rep ≥ the
same athlete's normal median + 1.0 s. Takes keep their recorded label (intention to treat); the
report lists every non-compliant take next to the G4 rows.

## 5. Exclusions and abstentions
- **The only exclusion is an intake FAIL** (§2.3), decided before annotation and before any model
  run. Every excluded or replaced file is listed in the report with its reasons. No take is excluded
  after the data lock, for any reason.
- **Abstention.** Any Mode A status other than `OK` counts as **zero predictions**: every annotated
  rep of that video is a miss in every metric and gate. The statuses are `NO_PERSON`,
  `VIDEO_TOO_SHORT`, `INSUFFICIENT_CONFIDENCE`, `LOST_TRACK`, `AMBIGUOUS_SUBJECT` and
  `UNSUPPORTED_EXERCISE` (the last one still returns reps; they are not counted).
- **SYSTEM_ERROR.** An exception raised inside the frozen system on a video (pose extraction,
  analysis, result validation) is recorded with its traceback and scored like an abstention (zero
  predictions). The run continues. It is a result about the system, not an evaluation bug.
- **Mode B.** Events emitted by a decode whose status was not `OK` are dropped the same way; an
  exception in a Mode B replay drops that preset's answer for that video (all its reps missed in
  Mode B) and never affects Mode A.
- The report gives the abstentions by status. Every abstention on an included take is a **false
  abstention**, since each one is a QA-passed seated-row set. It also gives metrics restricted to
  answered videos, labelled **conditional** and not a gate.
- An integrity failure (the decoded frame count or fps differs from what the annotations refer to)
  aborts the run (exit 5): the inputs would not line up, so nothing is scored.

## 6. Procedure

0. **Before the first recording**: tags `ai-core-candidate-v1` and `phase6-tools-v1` exist; the owner
   emails the supervisor both tag commit hashes and the sha256 lines of the tag messages (or pushes
   the tags to a private remote shared with the supervisor). A timestamp made later proves nothing.
1. **Pilot** (recommended): one pilot session in a separate root goes through intake, annotation,
   agreement, finalize and `phase6_evaluate --check-only` (identity and data checks only: no model,
   no ledger). Pilot videos are never evaluated; the pilot athlete may join the cohort with fresh
   recordings.
2. **Record** per spec v1.1 (shot list, deviation rota); after each session run
   `python -m ai.scripts.validate_recordings`, re-record every FAIL while the athlete is present,
   and complete `manifest.csv`.
3. When the cohort is complete, the intake cohort check must be PASS.
4. **Annotate**: `a1` annotates every included take, blind (§3).
5. **Agreement** rounds until one is accepted (§3.4).
6. `python -m ai.scripts.annotate_reps --annotator a1 --finalize` builds `annotations/reps.csv`;
   `python -m ai.scripts.phase6_evaluate --check-only` must report no problem.
7. **Lock**: `python -m ai.scripts.phase6_evaluate --lock`; commit `docs/PHASE6_DATA_LOCK.json`, tag
   `phase6-data-lock-v1`, email the tag's commit hash to the supervisor.
8. **Run**: `python -m ai.scripts.phase6_evaluate` on CPU, on AC power with sleep off. Expect several
   hours (Mode A runs at about 0.3–0.7× realtime on this laptop's CPU; 100–150 min of 30 fps footage
   is roughly 4–10 h with Mode B). An interrupted run is resumed by a re-run on identical data
   (§7); the pose cache makes it faster.
9. **Publish**: commit `ai/outputs/phase6/` as it is, whatever the verdict. The runner refuses a new
   run while earlier outputs are uncommitted.
10. PASS → **AI CORE FREEZE** (tag `ai-core-v1`), then Phase 7. FAIL → §7.

| exit code | meaning |
|---|---|
| 0 | done (report written, any verdict; or `--check-only` / `--lock` succeeded) |
| 2 | data, coverage, quality or tie-in error, or a cloud-synced root; nothing was run |
| 3 | frozen-identity drift or data-lock problem |
| 4 | run state: the cohort was already run, outputs uncommitted, ledger unreadable or changed, another run active |
| 5 | crash of the evaluation code, or integrity abort; the ledger keeps the entry |
| 6 | an option not allowed for the final run: `--device cuda`, `--no-mode-b`, `--out` |

The final run writes `ai/outputs/phase6/runs/<run_id>/` (`PHASE6_RESULTS.md`,
`phase6_results.json`), copies them to the top level, and keeps `RUN_LEDGER.json`. Full per-video
results and pose caches stay under the data root, with their hashes in the report.

## 7. Failure, re-run and change policy

**Any completed final run consumes the cohort.**

**Verdict of record**: the first completed final run. It is replaced only by a completed re-run with
`--evaluation-fix` on byte-identical inputs. Every run stays in the ledger and in the report.

**FAIL**
- Publish the full report plus a failure analysis by athlete, view, tempo, lighting and abstention.
- Do not tune on this cohort. Develop any fix on development data only.
- A changed system needs a new pre-registration, a new candidate tag and a **new cohort**; this
  cohort cannot validate a model tuned after seeing it.
- The failed result stays published.

**Re-runs** — only on inputs byte-identical to the first run (enforced), and only for:
- a run that did not complete (power loss, sleep, a crash of the evaluation code or I/O): no code
  change, `--rerun-reason "<what happened>"`;
- an **invalidating bug in the evaluation code** (scoring, report, I/O): first a dated addendum
  documents the bug, the diff and a test, all committed; then the re-run uses
  `--evaluation-fix <addendum id>`. Only the evaluation files may differ from the tag; the rule
  constants and the system files cannot (enforced); the diff's stat and sha256 go into the ledger
  and the report.

Not a reason for a re-run: a model weakness, a disappointing score, a SYSTEM_ERROR, relabelling,
re-annotation, re-recording, or a data problem found after the lock (it is reported, not fixed).

**Environment**: the run uses the frozen environment (same package versions including build tags,
same Python, same OpenCV binaries). If it cannot be restored (e.g. hardware loss), the final run
cannot proceed under this freeze: a new candidate freeze of the same system files in the new
environment needs an addendum and a new tag before the run, and it is reported.

## 8. Reporting
`PHASE6_RESULTS.md` contains:
- the verdict of the run and the verdict of record; the frozen-identity status with the tag commits,
  tag dates and freeze-file hashes; any evaluation-fix note with its diff stat;
- the known limitations, next to the gates they affect;
- every gate with its value, threshold and status, `low_n` and limitation flags, the `low` share;
- every slice: pooled, view, tempo, view × tempo, athlete, form / deviation type, lighting, with
  bootstrap CIs and the "wide" warning; the strict score;
- G6 per type: rates, difference and CI, paired athletes and each athlete's direction;
- tempo compliance; rep-count error; abstentions, SYSTEM_ERRORs and false abstentions; the
  conditional answered-only metrics;
- Mode B;
- every agreement round, with tier agreement and how many of `a1`'s files changed after each round;
- the excluded takes and every file that ever failed intake (re-recorded, deleted or kept);
- the addenda (headings and hash, now and at the lock);
- a per-video table and the run history.

Failures are published with the same prominence as passes. Aggregates never hide a weak slice.

## 9. Mode B — secondary readout, no gate
- The live presets `V2_quality` (GPU default) and `V3_budget_15fps` (CPU-only default) run through
  `ai.core.live.LiveStreamer`, replayed on the per-frame detections Mode A recorded (same frames, same
  detector), and are scored with the same protocol, per preset (§5 for abstentions and errors).
- The final run refuses `--no-mode-b`. Timing is hardware-dependent and not part of Phase 6
  (docs/LIVE_MODE.md).
- Check run on the 6 development videos laid out in the final formats: Mode A IoU F1 0.8406
  (29/10/1), pull F1 0.8529, Mode B V2 0.806 (27/10/3), V3 0.8125 (26/8/4), equal to the frozen
  references (`ai/tests/test_phase6.py`, slow test). The dry run relaxes only the cohort-level rules
  (coverage, data quality, G6 data, agreement), the framing measurements and one frame of END slack
  for the legacy 1-based development ground truth; it is never Phase 6 evidence.

## 10. Not covered by this pre-registration
- Realtime speed on target hardware.
- Form-indicator accuracy (only direction, G6).
- Probability calibration of the heuristic confidence: it needs per-rep deviation labels, which this
  protocol does not create.
- Multi-person scenes and recordings below 1080p / 24 fps (excluded by the intake rules, §1).

## 11. Development numbers, for context only (not predictions)

| set | IoU F1 |
|---|---|
| Mendeley LOSO (15 components) | pooled 0.878: side 0.654, front 0.967, diagonal 0.974 |
| locked Mendeley TEST | 0.812 (side 0.383) |
| internal seated row (1 athlete, contaminated) | 0.841 |
| external reels (3 athletes) | 0.632 |

Sources: EXPERIMENT_REGISTRY.md P2.6, P2.7; AI_CORE_BASELINE_MANIFEST.json.
