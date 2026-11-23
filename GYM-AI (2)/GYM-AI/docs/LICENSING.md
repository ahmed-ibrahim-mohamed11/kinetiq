# LICENSING.md — consolidated licensing summary (Phase 1, 2026-10-09)

Every dataset and model artifact the project touches, in one place. "Allowed use" states what
THIS project may do today; "commercial status" is tracked separately per the owner's rule
(academic/graduation use is not blocked by a non-commercial license; a future commercial product
would be). Sources are primary (official repo LICENSE files, dataset hosting-page license
fields, project EULAs) — verified 2026-10-09 except where a prior commit date is cited.

## Code (vendored / third-party repos)

| component | license | source verified | allowed use (this project) | commercial status |
|---|---|---|---|---|
| MotionBERT / MotionBERT-Lite (`motionbert/`) | **Apache-2.0** | `motionbert/LICENSE` (full text present), `motionbert/VENDORED.md` | full use, modification, product deployment | commercial-OK (code); **weights** trained on H36M/AMASS-derived data carry those datasets' non-commercial research terms — treat the released checkpoints as research-use unless MotionBERT's authors state otherwise (not independently re-verified this session; flagged, not re-litigated) |
| PoseFormerV2 (`third_party/PoseFormerV2/`, benchmark-only) | **MIT** | `third_party/README.md` (pinned commit `008036a`) | benchmark/reference use only (not product path since Phase 2.8 demotion) | commercial-OK (code); weights trained on H36M (same caveat as above) |
| MotionAGFormer (`third_party/MotionAGFormer/`, benchmark-only) | **Apache-2.0** | `third_party/README.md` (pinned commit `4756fd1`) | benchmark-only | commercial-OK (code); weights trained on H36M |
| VideoPose3D (`third_party/VideoPose3D/`, benchmark-only) | **CC BY-NC 4.0** | `third_party/README.md` (pinned commit `1afb1ca`) | **research/benchmark ONLY** — explicitly excluded from the product path (already enforced: not used since Phase 2.5C demotion) | **NOT commercial-eligible** without relicensing from Meta/FAIR |
| Ultralytics YOLO11n-pose (`yolo11n-pose.pt`, auto-downloaded by `ultralytics` package) | **AGPL-3.0** | Ultralytics' own licensing model (established fact, `ai/models/README.md`, DECISION_LOG) | academic/graduation use: fine. **Any commercial deployment of a product built on this detector requires an Ultralytics Enterprise license** (AGPL-3.0's copyleft/network-use clause would otherwise require open-sourcing the whole service) | **OWNER DECISION 2026-10-10: academic / non-commercial use only.** YOLO11n-pose stays in the product path for the academic project; no Enterprise licence is bought. Any commercial use needs a new owner decision first (Enterprise licence or detector swap) — RISK_REGISTER R-14 |
| MediaPipe Pose Landmarker (`ai/models/mediapipe/`, benchmark-only, eliminated 2.5C) | Apache-2.0 | Google's stated license for MediaPipe | not product-used (benchmark reference only) | commercial-OK, moot since not used |
| Local service stack (Phase 5, pip): fastapi 0.143.0, pydantic 2.13.5, annotated-doc 0.0.5 (MIT); starlette 1.7.0, uvicorn 0.54.0, httpx 0.28.1, httpcore 1.0.9 (BSD-3-Clause); python-multipart 0.0.32, opentelemetry-api 1.45.1, huggingface_hub 2.1.1 (Apache-2.0) | MIT / BSD-3-Clause / Apache-2.0 | installed package metadata (`License-Expression`), read 2026-10-10 | full use | commercial-OK (permissive) |

## Datasets

| dataset | license (verified) | allowed use here | commercial status | where used |
|---|---|---|---|---|
| Mendeley "Multi-View Raw Video Dataset of Seven Fitness Exercises" (doi 10.17632/kgbb3yn47p.3) | **CC BY 4.0** | full use incl. redistribution of derived annotations, with attribution | commercial-OK (attribution required) | TRAIN/VAL/TEST for rep segmentation, form-threshold calibration (back exercise) |
| Internal seated-row videos (6 videos, 1 athlete) | owner's own footage | full use | owner's own IP; no third-party restriction, but subject consent/privacy practice should be confirmed with the owner before any future external sharing | DEV/CONTAMINATED reference (chose MB-Lite as primary, provisional form thresholds) |
| Instagram external stress test (8 reels, 5 usable units) | **per-clip Instagram/creator ToS — NOT a project license we hold** | **evaluation-only**, by explicit project rule (frozen-model rule, DECISION_LOG 2026-10-08): raw video never committed/redistributed, only URL + MD5 + extracted skeletal numbers are kept; **never used for training** (consistent with the owner's no-scraped-social-media-for-training rule — this project already enforces that, by construction, for eval only) | not applicable — not redistributed, not trained on | stress-test reference only (docs/EXTERNAL_REAL_WORLD_STRESS_TEST.md) |
| Future ≥10-athlete recordings (Domain C) | owner-recorded | full use (final test only, never retrained on) | owner's own IP; privacy/consent practice to be confirmed with the owner | locked final validation (Phase 6) |
| **MM-Fit** (video: Zenodo 10.5281/zenodo.7672767; sensors/labels: mmfit.github.io) | **CC BY 4.0** (verified on the Zenodo record) | full use with attribution; **NOT YET DOWNLOADED** — approved for download, not yet acquired | commercial-OK (attribution required) | **candidate, not yet integrated** (DATASET_VALIDATION.md §2) |
| Fit3D | **non-commercial research/education only**, binding EULA (fit3d.imar.ro/legal) | N/A | N/A | **DROPPED 2026-10-09 (owner directive): registration/access not available — do not use, do not attempt to download** |
| RepCount-A / LLSP (TransRAC) | license unstated by primary source / "other" on a derivative mirror | **EXCLUDED regardless of license**: Part-A videos are YouTube-scraped (owner hard rule); Part-B not publicly released | n/a | **not used** |
| QEVD-Fit-300K / QEVD-Fit-Coach (Qualcomm) | **Qualcomm Dataset Research License** — non-commercial research use, binding on download | N/A | **NOT commercial-eligible** | **SKIPPED (owner decision 2026-10-10)**: EULA not accepted, never downloaded, not to be requested again |
| UI-PRMD | **Open Data Commons PDDL 1.0** (public domain) | full use | commercial-OK | **not pursued** (no rowing/pulling movement — DATASET_VALIDATION.md) |
| KIMORE, InfiniteRep | license unverified from a primary source this session | not used pending verification | unknown | **not pursued** |
| **CMU Panoptic Studio** (domedb.perception.cs.cmu.edu, via CMU-Perceptual-Computing-Lab/panoptic-toolbox) | **"freely available for non-commercial and research purpose only"** (verified: the toolbox repo's own README License section; no sign-up/account/form anywhere in the download path — confirmed by actually downloading it) | full use (must cite the papers listed in the README) | **NOT commercial-eligible** | **USED 2026-10-09** — sample sequence `171204_pose1_sample` (calibration + triangulated 3D body keypoints + 3 of 31 HD camera views, ~65 MB total) downloaded to `ai/data/panoptic/` (git-ignored). First real-3D-ground-truth accuracy measurement for this project (docs/EXPERIMENT_REGISTRY.md, entry P2.1-panoptic). Redistribution of the raw data is forbidden by the license — never committed; only derived numeric results are kept (`ai/outputs/phase2/panoptic_*.csv`). |
| AIST++ (google.github.io/aistplusplus_dataset) | annotations (keypoints/motion/camera) hosted directly on GitHub with no separately stated license; the underlying VIDEOS are governed by the **AIST Dance Video Database Terms of Use**, which requires submitting an **Application Form** before use | ambiguous for the video portion | not evaluated | **NOT PURSUED** — the Application Form is close enough to "sign-up" to skip rather than guess past (owner rule); CMU Panoptic already covers both target goals (3D accuracy + multi-view) without this ambiguity. |
| MPI-INF-3DHP (vcai.mpi-inf.mpg.de/3dhp-dataset) | **UNVERIFIABLE from the public page** — no license/terms text anywhere on the official site; the linked archive is a suspicious 10 KB (the real dataset is tens of GB per third-party reports), with only an author email as a contact, suggesting real access may require contacting the author directly | unknown | unknown | **NOT PURSUED** — access path unclear and unverified; not needed once CMU Panoptic covered both goals. |

## Policy reminders (so this file stays authoritative)

1. Any new dataset that requires a **signed agreement, registration, or EULA click-through** is
   downloaded only after explicit owner approval — two such candidates were identified and neither
   was approved: Fit3D DROPPED (2026-10-09, no access), QEVD SKIPPED (2026-10-10, owner decision).
2. **Never** download or train on scraped social-media/YouTube video collections, independent of
   their stated license (owner hard rule). The Instagram stress-test track is evaluation-only by
   design and does not violate this — it is never used for training/tuning.
3. Any synthetic/augmented data (see `ai/augmentation/`) is tagged `synthetic=True` everywhere
   and carries no licensing restriction of its own (generated in-repo from already-licensed
   source poses), but must never be presented as real-world evidence.
4. **Ultralytics AGPL-3.0 — owner decision 2026-10-10: academic / non-commercial use only.** No
   Enterprise licence. This remains the single biggest blocker to any future commercial deployment;
   commercial use requires a new owner decision (Enterprise licence or detector swap) before it
   starts — RISK_REGISTER R-14, DECISION_LOG 2026-10-10.
5. Re-verify any license claim marked "unverified"/"UNKNOWN" from a primary source before acting
   on it; nothing in this file should be treated as legal advice.
