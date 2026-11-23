# DATASET_VALIDATION.md — dataset registry (Phase 1, 2026-10-09)

Two parts: (1) datasets **already in use** — this section cross-references the existing, detailed
audits rather than duplicating them, and states the split/QA status in one place; (2) a **survey
of public datasets** considered for acquisition, with license verified against primary sources,
and a licensing decision for each. Machine-readable twin: `ai/data/dataset_registry.json`.

## 1. Datasets already in use

| dataset | role | license | subjects | size | views | fps | annotations | QA status | detail doc |
|---|---|---|---|---|---|---|---|---|---|
| Mendeley "Multi-View Raw Video Dataset of Seven Fitness Exercises" | TRAIN/VAL/TEST (back exercise) | **CC BY 4.0** (verified: doi 10.17632/kgbb3yn47p.3, Mendeley Data license field) | 24 (frozen split 14/5/5) | 165 videos / 2,472 reps used (of 1,042 videos / 26 subjects total) | front/side/diagonal | 15.0 | START/PULL/END, quality tier (verified/auto/uncertain don't-care) | **DONE** — duplicate MD5 scan (33 groups/67 files, split-linkage enforced), subject-leakage check in `freeze_check.py` (automated, re-run this session: PASS), 11 short/truncated clips excluded, fps/structure probed with OpenCV (0 failures) | DATASET_MENDELEY.md, DATASET_PHASE_2_6.md |
| Internal seated-row videos | DEV / **CONTAMINATED** reference (zero-shot transfer target) | owner's own footage, no third-party licence issue | 1 | 6 videos / 30 GT reps | rear | 30 | manual START/PULL/END | QA: N/A (too small for duplicate/leakage checks; single-athlete, acknowledged as contaminated — chose MB-Lite as primary AND calibrated provisional form thresholds, PROJECT_STATE.md) | CURRENT_HANDOFF_REPORT.md §6 |
| Instagram external stress test (8 reels → 5 usable units) | STRESS REFERENCE, eval-only, **never trained/tuned on** | per-clip Instagram ToS; referenced by URL+MD5 only, raw video git-ignored, never redistributed (frozen-model rule, DECISION_LOG 2026-10-08) | 3 athletes | 5 units / 10 certain + 4 uncertain reps | front-oblique/side/rear | 25–30 | manual frame-level, independent of decoder | **DONE** — URL dedup (9→8), MD5 content-dedup (0 duplicates), GT independence check documented (§2.1 of the report) | EXTERNAL_REAL_WORLD_STRESS_TEST.md |
| Future ≥10 fresh athletes (Domain C) | **FINAL TEST**, locked, single evaluation | owner-recorded | ≥10 (new, disjoint from above) | TBD | rear + 2 more | 15–60 | per-rep, human-verified, deviation-labeled | spec frozen; `validate_unseen_dataset.py` automated gate (7 tests, all passing on the fixture) ready; **BLOCKED on owner recording** | UNSEEN_ATHLETE_DATASET_SPEC.md |

**Split integrity** (re-verified this session, `ai/research/phase27/freeze_check.py`): no subject
appears in two splits; no duplicate-linked Mendeley subject pair is split apart; all Mendeley/
split/annotation file hashes match the frozen Phase 2.6/2.7 manifest.

## 2. Public dataset survey (new acquisition candidates)

Researched 2026-10-09 via primary-source verification (official project pages, Zenodo/HF records,
license PDFs — not secondhand summaries). **Owner hard rules applied**: scraped-YouTube/social-
media collections are EXCLUDED for training regardless of license; anything requiring a signed
licence/EULA click-through is marked REQUIRES OWNER APPROVAL, not downloaded.

| dataset | license (verified, primary source) | academic use | commercial use | video source | exercises (rowing?) | size | labels | relevance to GYM-AI | decision |
|---|---|---|---|---|---|---|---|---|---|
| **MM-Fit** (video: Zenodo record 10.5281/zenodo.7672767; sensors: mmfit.github.io) | **CC BY 4.0** (verified on the Zenodo record page, "Open" access) | YES | YES (attribution only) | own recordings (10 participants, multi-viewpoint RGB-D) | 11 exercise classes incl. **dumbbell rows**; rep-counted (exact per-rep temporal boundary format not yet confirmed — needs the annotation file, not just the paper abstract) | 20–21 sessions, 616 sets, 6,160 reps, time-synced 2D/3D pose @30 Hz | rep counts + exercise label; multi-sensor (IMU) sync | **HIGH** — only CC-BY, own-recorded, dumbbell-row-containing dataset found; candidate for cross-exercise robustness/augmentation and possibly rep-counting pretraining signal | **APPROVED — may download now** (CC BY 4.0, attribution-only; still verify the actual rep-boundary annotation file format before relying on it for training, since the search could not confirm per-rep start/end vs. rep-count-only) |
| **Fit3D** (fit3d.imar.ro) | **Non-commercial research/education/artistic only** (verified: fit3d.imar.ro/legal, click-through, no registration form but a binding EULA; commercial use requires contacting licenses@imar.ro) | N/A | N/A | own recordings, 13 subjects, 4 RGB cameras + mocap | — | 611 multi-view sequences | per-rep, 3D GT skeletons | N/A | **DROPPED 2026-10-09 (owner directive): registration/access not available to the owner — do not use.** Replaced for the same two goals (3D accuracy + multi-view) by CMU Panoptic Studio, below. |
| **CMU Panoptic Studio** (domedb.perception.cs.cmu.edu) | **"freely available for non-commercial and research purpose only"** (verified: panoptic-toolbox README License section; no account/sign-up anywhere — confirmed by downloading it) | YES | **NO** | own multi-camera studio captures (triangulated 3D, not IMU/marker mocap, but ground-truth-grade) | general pose/social-interaction sample (not an exercise), used only for generic 3D-accuracy + multi-view-consistency measurement, never mixed into seated-row metrics | sample sequence `171204_pose1_sample`: 101 frames, 31 HD camera views (3 downloaded) | triangulated 3D body keypoints (joints19/COCO19) + full calibration | **HIGH** for the two stated goals (3D accuracy, multi-view orientation check); **NONE** for rep-segmentation training (no exercise content, by design never used for that) | **APPROVED & USED 2026-10-09** — see EXPERIMENT_REGISTRY.md P2.1-panoptic for results (PA-MPJPE 4.2/10.1/12.6 cm front/back/side; depth-sign consistency confirmed view-dependent) |
| AIST++ (google.github.io/aistplusplus_dataset) | annotations: no separately stated license; underlying videos require an **Application Form** (AIST Dance Video Database Terms of Use) | ambiguous | not evaluated | multi-view dance capture, 3D keypoints via triangulation | dance, not exercise | 10.1M frames, 9 views | 3D keypoints + camera params | would have been HIGH for multi-view (9 views, no account for the *annotations*), but the video ToU's Application Form is a registration gate | **NOT PURSUED** — Application Form treated as a sign-up gate per owner rule; CMU Panoptic already covers the same goals without this ambiguity |
| MPI-INF-3DHP (vcai.mpi-inf.mpg.de/3dhp-dataset) | **unverifiable** — no terms/license text anywhere on the official page; linked archive is a suspicious 10 KB stub (real dataset reported at tens of GB elsewhere); only an author email given | unknown | unknown | mocap, 14 camera angles | — | reported >1.3M frames | 3D GT | would be HIGH if access were confirmed clean, but it isn't | **NOT PURSUED** — access path unclear/unverified; not needed once CMU Panoptic covered both goals |
| **RepCount-A / LLSP** (via TransRAC, CVPR 2022; svip-lab.github.io) | license not stated by the primary source found; a derivative HF mirror tags "license: other" (custom, unverified) | UNKNOWN | UNKNOWN | **Part-A videos are scraped from YouTube** (confirmed on the primary page) | includes rowing ("athletic events such as rowing, pommel horse") | Part-A: web-scraped clips; Part-B: volunteer recordings (sit-ups/pull-ups, no rowing confirmed), **not publicly released** per the author's own note | per-rep begin/end cycle locations + rep count | Part-A would be the best rowing-specific rep-boundary match found — **but is exactly the data class the owner rule excludes** | **EXCLUDED for training** (YouTube-scraped, owner hard rule, independent of license). Part-B: not publicly available, moot. |
| **QEVD-Fit-300K / QEVD-Fit-Coach** (Qualcomm; developer.qualcomm.com/software/qevd-dataset) | **Qualcomm Dataset Research License** (verified via the license-PDF excerpts): non-commercial research use only, binding on download, no sublicensing, explicitly "excludes any commercial use" | YES (non-commercial research) | **NO** | own Qualcomm-collected dataset (not stated as YouTube-scraped) | 148 exercise classes with form-error/corrective-feedback labels; **rowing/pulling presence UNCONFIRMED** (class-list PDF was unreachable — redirect) | 300K: 289k short clips (2–10s) + 14k everyday-activity clips; Fit-Coach: 149 structured workout videos | fine-grained form-error attribute labels (e.g. "knee over toe") — exactly the "form quality" label type we lack | **REQUIRES OWNER LICENSE APPROVAL** before download (binding research-use EULA); if approved, check the class list first for any rowing/pulling class before spending download bandwidth |
| **UI-PRMD** (webpages.uidaho.edu/ui-prmd) | **Open Data Commons PDDL 1.0** (verified: public-domain, no restrictions) | YES | YES | own recordings, 10 subjects, Vicon + Kinect mocap | 10 rehab movements (squat, lunge, sit-to-stand, shoulder ab/extension, scaption, etc.) — **no rowing/pulling movement** | 10 subjects × 10 reps × 10 movements | per-rep joint angles/positions (Vicon + Kinect skeletons) | **LOW** — fully open but mechanically irrelevant (no pulling motion); only possible use is generic cross-dataset robustness/augmentation of the lifter, not rep-segmentation training | **NOT PURSUED** (relevance too low to justify integration effort right now; license is clean if revisited) |
| **KIMORE** (Capecci et al., TNSRE 2019) | **UNKNOWN** — not found from a primary source in this search pass | UNKNOWN | UNKNOWN | clinical recordings (44 healthy + patients) | 5 rehab actions, no rowing | 353 samples, RGB-D + skeleton | per-exercise quality score | **LOW** relevance (no pulling motion) and unverified license | **NOT PURSUED** (would need direct license verification from the paper/authors before any further consideration; low priority given no rowing content) |
| **InfiniteRep** (Infinity AI; toinfinity.ai/infiniterep, GitHub toinfinityai/InfiniteRep) | described as "synthetic, open-source" by a secondary (paperswithcode) summary; **license text not independently verified** (would need the GitHub LICENSE file) | UNKNOWN (likely yes, unconfirmed) | UNKNOWN | **synthetic** (rendered avatars, not real video) | multiple common fitness/PT exercises; rowing presence unconfirmed | ~1k synthetic videos | per-rep (synthetic ground truth) | **LOW-MEDIUM** — synthetic avatar rendering has a real domain gap vs. our monocular real-video pipeline; more useful as an idea source for our OWN synthetic-perturbation generator (§3) than as training data | **NOT PURSUED for training**; license unverified. Noted as prior art for our synthetic generator design. |

### Conclusion

**No publicly available, clearly-licensed dataset of seated/cable row (or even a close machine-row
variant) with per-rep mocap/pose annotations exists.** This confirms the Mendeley `back` (bent-over
dumbbell row) exercise remains the best available proxy pulling movement, and makes the owner's
future ≥10-athlete seated-row recording (Domain C) the only path to in-domain training/validation
data. MM-Fit (CC BY 4.0, dumbbell rows, own-recorded) is approved but not yet downloaded. Fit3D is
**DROPPED** (owner directive 2026-10-09: no access available). For the two goals Fit3D would have
served — general 3D accuracy and real multi-view evaluation — **CMU Panoptic Studio** (free,
no-account, non-commercial-research license) was used instead; see EXPERIMENT_REGISTRY.md
P2.1-panoptic. QEVD is **SKIPPED** (owner decision 2026-10-10): EULA not accepted, never downloaded.

### Action items
1. Download MM-Fit video (CC BY 4.0, 39.1 GB, Zenodo 7672767) and its sensor/annotation companion
   from mmfit.github.io; verify the actual per-rep annotation file format (rep **count** vs. rep
   **start/end boundaries**) before deciding whether it can train rep segmentation or only serve
   as a robustness/cross-exercise transfer check (like the Mendeley `bicep_curl` secondary set).
2. ~~QEVD class-list check~~ — closed: QEVD SKIPPED by owner decision (2026-10-10).

## 3. Synthetic / augmented data

`ai/augmentation/perturb.py` (added this session): time-warp (tempo), per-joint confidence
dropout, confidence noise, frame drops, fps downsampling, a resolution/compression PROXY
(keypoint-level positional jitter — not a true re-run of the detector on degraded video), and a
bounded viewpoint (yaw) rotation. 24 tests (`ai/tests/test_augmentation.py`): 22 unit tests on
synthetic arrays + 2 integration tests that run every transform through the REAL frozen decoder
(`ai.evaluation.harness`), which is what caught two wrong initial assumptions before they could
reach a doc as an unverified claim (logged as EXPERIMENT_REGISTRY E-01):
- **Wrist dropout has zero effect on any decoded rep or form indicator** — `ai/core/features.py`
  never reads the wrist joint at all (only shoulder/elbow/pelvis/thorax). Documented in
  `ai/core/config.py` and locked as a regression test.
- **Yaw rotation is a decoder no-op only for small angles.** `ai/core/features.py` derives its
  backward/"pull" axis sign from the fixed constant `FACING_IN_CAMERA`, not purely from the
  rotated pose, so a LARGE rotation (confirmed at 180°) flips which direction counts as backward
  and changes decoded reps (n_pred 47→32 on `internal_sr`). This means `yaw_rotate` is a bounded
  sanity probe, not a general-purpose view-augmentation — it does **not** approximate "what the
  model would reconstruct from a side/rear camera" (that weakness is upstream, in 2D→3D lifting).

All synthetic outputs are tagged `synthetic=True` in their metadata and must never be presented
as real-world evidence (owner rule). Synthetic data is for ROBUSTNESS EVALUATION (Phase 3) and,
if a measured weakness justifies it, TRAINING AUGMENTATION (Phase 2) — never for final
validation (Domain C stays real, human-recorded, per the master prompt).
