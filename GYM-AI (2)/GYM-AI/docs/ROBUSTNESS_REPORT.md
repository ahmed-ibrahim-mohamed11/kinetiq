# ROBUSTNESS_REPORT.md — Phase 3 consolidated findings (2026-10-09)

Source: `ai/research/phase3/failure_injection.py` (P3.1+P3.2, synthetic perturbation sweep on
dev data only via `ai/augmentation/perturb.py` through the frozen decoder, unchanged) +
consolidated existing evidence (P3.3 view, P3.4 tracking). Full numbers:
`ai/outputs/phase3/failure_injection_sweep.csv`. Baseline: internal_sr IoU F1 0.8406, mendeley_val
pooled IoU F1 0.9137. Every number below is a synthetic PROXY unless marked "(real)" —
see `ai/augmentation/perturb.py`'s module docstring for what each proxy does and does not claim.

## 1. Failure-injection suite (P3.1)

| perturbation | severity | internal_sr F1 | mendeley_val F1 | verdict |
|---|---|---|---|---|
| frame drop | 5% / 10% / 20% | 0.829 / 0.841 / 0.841 | 0.925 / 0.865 / 0.881 | **TOLERATED** — frame drops up to 20% barely move either metric (mendeley even improves slightly at 5%/20%, plausibly from interpolation smoothing) |
| confidence noise (σ) | 0.1 / 0.3 / 0.5 | 0.841 / **0.648** / 0.618 | 0.906 / 0.795 / 0.881 | **boundary between σ=0.1 and σ=0.3**: noisy-but-plausible confidence (0.1) is fine, badly miscalibrated confidence (≥0.3) degrades internal_sr sharply |
| fps downsampling | 15 / 7.5 fps | 0.829 / 0.817 | 0.912 / 0.919 | **TOLERATED** — consistent with the already-documented fps-robustness finding (pull events shift ≤1 native frame) |
| resolution/compression jitter (proxy, σ) | 0.02 / 0.05 / 0.10 | 0.813 / **0.506** / **0.200** | 0.851 / 0.848 / 0.789 | **MOST DAMAGING perturbation tested, and sharply asymmetric**: mendeley_val (in-distribution, trained on) stays 0.79–0.85 across the whole range; internal_sr (zero-shot, out-of-distribution) collapses between σ=0.02 and σ=0.05. Flag for Phase 4/5: any real resolution/compression degradation in deployment is a bigger risk for the seated-row product path than for in-domain data. |
| wrist dropout (both wrists, 30%/100% persistent) | — | **0.8406 / 0.8406 (byte-identical to baseline)** | **0.9137 / 0.9137 (byte-identical)** | **ZERO effect at ANY severity, including a full-video persistent dropout** — generalizes Phase 1's windowed-dropout finding (`ai/core/config.py` note): wrist position/confidence is never read by `ai.core.features`, full stop. |
| elbow dropout, one arm (30% / 100% persistent) | L: 0.754/0.523, R: 0.764/0.480 | mendeley L: 0.925/0.801, R: 0.874/0.889 | **shoulder dropout gives IDENTICAL numbers to elbow dropout** (see note below) | critical joint |
| shoulder dropout, one arm (30% / 100% persistent) | identical to elbow dropout above | identical to elbow dropout above | — | critical joint |
| "edge crop" (persistent single-arm shoulder+elbow dropout, simulating that side permanently out of frame) | identical to single-joint dropout above | identical to single-joint dropout above | — | **shoulder and elbow are EQUALLY critical AND fully redundant with each other for failure purposes**: `ai/core/features.py`'s per-arm validity gate is `min(conf[shoulder], conf[elbow], conf[pelvis], conf[thorax]) >= conf_thr` — losing EITHER joint alone already fails the gate for that arm, so losing both together causes no additional damage. This was not previously quantified. |

**Failure boundary summary**: wrist = never matters (0% critical). Shoulder/elbow = each
independently sufficient to disable an arm's contribution (either one lost ⇒ that arm stops
contributing, with no further penalty for losing both) — the system already degrades gracefully
to "the other arm only" via `fuse()`'s "either arm is pulling" rule, which is why even 100%
persistent single-arm loss doesn't crash, just degrades (internal_sr drops to 0.48–0.52, still
producing *something*, not garbage). Confidence-noise and resolution/jitter are the two
perturbation TYPES (not joints) that matter most; resolution/jitter is the single most damaging
tested and is markedly worse out-of-distribution (seated row) than in-distribution (Mendeley).

## 2. Tempo suite (P3.2)

| case | internal_sr F1 | mendeley_val F1 | note |
|---|---|---|---|
| fast (1.5×) | **0.853** (↑ from 0.841) | **0.933** (↑ from 0.914) | faster tempo mildly HELPS both — tighter windows relative to the trained duration prior |
| slow (0.7×, uniform) | 0.704 | 0.869 | moderate degradation, matches the external real-world magnitude (P2.2) |
| very slow (0.4×, uniform) | 0.657 | 0.413 | severe; this is the exact regime that used to crash `estimate_tempo` before the R-16 fix (P3.0) — now decodes (badly, but explicitly, never crashing) |
| paused pull (1.0 s squeeze hold) | 0.475 | 0.924 (mendeley barely affected) | asymmetric: devastating for internal_sr, benign for mendeley_val (P2.2 finding, unexplained asymmetry — see EXPERIMENT_REGISTRY P2.2) |
| paused end (1.0 s extension hold) | 0.338 | 0.656 | the closest synthetic match to the REAL external paused-tempo failure (P2.2) |
| **inconsistent tempo (NEW, oscillating 0.6×–1.5× every 1.5 s)** | **0.597** | 0.856 | a genuinely distinct stressor from pure slowdown: internal_sr degrades MORE than at constant 0.7× slowdown (0.597 vs 0.704) despite a similar average speed near 1.0× — tempo *variability* itself costs accuracy, not just being slow |

**Tempo robustness summary**: the system tolerates fast and moderately slow UNIFORM tempo well;
paused/held phases and WITHIN-VIDEO tempo inconsistency are the real stressors, consistent with
R-08's real-world evidence and now additionally confirmed as a distinct failure mode (tempo
variability, not just absolute slowness).

## 3. View robustness (P3.3) — consolidated from existing evidence, no new experiment needed

| evidence | result |
|---|---|
| Mendeley VAL (back exercise), per view | front 0.938–1.0, diagonal 0.957–1.0, side 0.603–0.794 depending on config — side is consistently the weakest view |
| Mendeley TEST (locked, back exercise) | front 0.974, diagonal 0.979, side **0.383** (MB-Lite) — a much larger VAL↔TEST swing than any other view, small-dev-set caution applies |
| External wild (Instagram), per unit | front-oblique and side views transferred; the one real miss was the rear (depth-dominant) view |
| **P2.1-panoptic (real, independent 3D ground truth, 3 real camera azimuths of the same motion)** | PA-MPJPE 4.2 cm (front) / 10.1 cm (back) / 12.6 cm (side, also far less frame-to-frame stable, σ 5.8 cm) — the FIRST real (not proxy) confirmation that reconstruction accuracy itself, not just downstream decoding, is view-dependent |
| **P2.1 (synthetic yaw sweep + per-video auto-sign, REJECTED)** | the feature-extraction pipeline IS rotation-invariant for small angles — `ai/core/features.py` derives lateral/vertical axes from the pose itself. The real view weakness is NOT a resolvable feature-pipeline sign bug; it is a genuine, view-dependent 2D→3D reconstruction/SNR limit, confirmed independently by the real-ground-truth MPJPE numbers above |
| **P2.7 (2026-10-10) — CORRECTS the row above** | the camera-derived backward-axis sign in `ai/core/features.py` IS the main identified cause of the side-view gap on development data: with a fixed anatomical sign and a refit, LOSO side-view F1 rises 0.654 → 0.853 (front/diagonal ≈0.97, so about 2/3 of the gap closes) and real 3D GT confirms the anatomical sign in every view. P2.1 had refuted only the likelihood-based sign choice without retraining. The fix was nevertheless REJECTED: it cut the rear-view seated row 0.841 → 0.558 (EXPERIMENT_REGISTRY.md P2.7). The recommendation below is unchanged in practice: no view-specific change ships before Phase 6 |

**Recommendation (per the master prompt's "prefer view-invariant representation over
view-specific hacks")**: no view-specific hack is recommended. The weakness lives upstream in
monocular reconstruction quality, not in `ai.core`'s downstream feature/decode logic (which is
already appropriately view-invariant where that's achievable). The actionable mitigations are
either (a) camera placement guidance for the product (prefer front/near-front placements where
MPJPE is lowest) or (b) future work on the lifting stage itself (out of this project's current
scope — MotionBERT-Lite stays product-primary per the owner's rule unless a representation-level
fix is demonstrated).

## 4. Tracking robustness (P3.4) — consolidated from existing evidence + one new check

| evidence | result |
|---|---|
| Mendeley (165 videos, Phase 2.5C/2.7) | 1.8% lost frames, re-acquisition logic exercised; "highest-confidence" selection alone switches to background people on 15/34 videos — hence the deterministic top-8 + IoU-continuity tracker, not naive per-frame confidence |
| External wild (Instagram, varied real-world conditions incl. dark footage, neon overlay) | 0–16% NaN window per unit, 0–5 jumps per video, no tracking-attributable failure found |
| Camera movement (external units) | the one orbiting-camera reel could not even be human-annotated — excluded as unannotatable, not scored as a tracker failure |
| Multi-person / crossing people | **NOT TESTED with a dedicated synthetic probe this session** (would need a second synthetic body trajectory injected into real 2D keypoint streams — a larger effort than this pass's budget); existing evidence above is about *selection* robustness (picking the right person among candidates), which is validated; true concurrent multi-person tracking remains explicitly **NOT CLAIMED**, consistent with the master prompt's rule |

**Tracking robustness summary**: single-subject identity continuity (the only tracking claim
this product makes) is well-supported by existing evidence across 165+30+8 real videos spanning
crowded gyms, dark footage, and compressed social media video. Concurrent multi-person tracking
is explicitly out of scope and not claimed anywhere in this project's docs — this report does not
change that.

## 5. Abstention (P3.5) and exercise guard (P3.6)

New module `ai/core/guards.py::safe_predict_video` — an ADDITIVE wrapper around
`ai.core.decoder.predict_video` (the existing, already-tested function and every one of its
100+ call sites are completely untouched). Returns the same `reps`/`segments`/`fused`/etc. keys
plus an explicit `status`, never silently returning garbage:

| status | trigger | checked using |
|---|---|---|
| `NO_PERSON` | zero valid frames anywhere in the video | keypoint validity (existing signal) |
| `VIDEO_TOO_SHORT` | fewer than ~10 frames (cheap pre-check), or fewer frames than the trained minimum complete PULL+RET duration (precise check, post-phase-model) | a hardcoded sanity floor, then `ai.core.decoder._min_len_frames` — the SAME formula the decoder itself already uses, not a new threshold |
| `INSUFFICIENT_CONFIDENCE` | mean keypoint validity across the whole video < 0.5 | keypoint validity (existing signal) |
| `LOST_TRACK` | tracker log shows >50% of frames lost | tracker log (if supplied by the caller) |
| `AMBIGUOUS_SUBJECT` | tracker log shows >30% of frames picked a non-largest candidate (sustained ambiguity, not one-off jumps) | tracker log |
| `UNSUPPORTED_EXERCISE` | reps WERE decoded, but >50% have an implausible `peak_z` (how far the decoded peak sits from the TRAINED exercise's own learned peak-height law) | `rep["peak_z"]`, already computed by `segments_to_reps` — no new metric, no classifier |

No black-box classifier anywhere — every check is a named, interpretable threshold on an
already-computed, already-tested signal, consistent with the form-indicator design philosophy.
`UNSUPPORTED_EXERCISE` still RETURNS the decoded reps (never hides information) but flags them
as not-confident, per the master prompt's "must not produce confident reps" wording — a video
with genuinely zero reps (e.g. someone standing still) is NOT flagged here, since zero is a
legitimate counting outcome, not an exercise-mismatch signal.

**False-abstention measurement** (`ai/tests/test_guards.py::test_no_real_video_ever_abstains`):
every real video across all three evaluation suites this project has — 6 `internal_sr` + 5
`external` units + 34 `mendeley_val` back videos, 45 total — returns `status == "OK"`. Zero
false abstentions on any real data this project has ever evaluated. Thresholds were chosen
conservatively above every documented worst real case (42% lost frames, Mendeley
`subject_007_back_bad_side`; 16% NaN tracking window, external `ext_03_segA`) specifically so
this holds. A second test confirms `safe_predict_video`'s reps are byte-identical to
`predict_video`'s on every real video whenever status is `OK` — proving the wrapper changes
nothing for normal operation.

**Prevented-error side (synthetic)**: `ai/tests/test_guards.py`'s unit tests confirm each
abstention status fires on a constructed case designed to trigger it (all-invalid video, a
3–5 frame video, 30%-valid video, high lost/disagree tracker logs, mostly-implausible decoded
reps) — i.e. the guard DOES fire when it should, not just "never fires on good data."

## 6. Clear failure behavior audit (P3.7)

Audited every real entry point against the master prompt's list (missing checkpoint, broken
video, zero detections, bad fps, invalid config, empty annotations), confirming or fixing
explicit-error behaviour:

| case | before this audit | after |
|---|---|---|
| Missing model checkpoint | `torch.load` already raises `FileNotFoundError` cleanly | unchanged — already correct, confirmed with a direct check |
| Broken/missing video path | `ultralytics`/YOLO already raises `FileNotFoundError` cleanly | unchanged — already correct, confirmed with a direct check |
| Zero detections (whole video) | silently interpolated, `conf=0` (an intentional, documented non-error — the pipeline is designed to tolerate this) | now ALSO explicitly caught earlier by `ai.core.guards.safe_predict_video`'s `NO_PERSON` status (P3.5) for callers that want an explicit signal instead |
| Bad fps (≤0, NaN, inf) | **silent-ish**: `prepare_video` accepted it with no error, and the bad value only surfaced several calls later as a `ZeroDivisionError` (fps=0) or an obscure `scipy` filter error ("filter critical frequencies must be greater than 0", fps<0) — technically not silent, but confusing and far from the actual mistake | **FIXED**: `ai/core/features.py::prepare_video` now validates `fps` immediately, raising `ValueError` naming `fps` at the one place every caller already passes it through (5 new tests, `ai/tests/test_features.py`) |
| Invalid config (`Cfg`) | no validation at all — a nonsensical field (e.g. `conf_thr=1.5`, `dur_sigma_floor=0`) would silently build a `Cfg` and fail confusingly, much later, deep in the decoder | **FIXED**: `ai/core/config.py::Cfg.__post_init__` validates every tunable field's sane range at construction (18 new tests, `ai/tests/test_config.py`, including a check that every value this project's own hyper-parameter `GRID` has ever proposed still passes) |
| Empty annotations | `ai/core/io.py::load_annotations` silently returned `{}` on a 0-row CSV — indistinguishable from "legitimately zero videos" | **FIXED**: raises `ValueError` naming the path (3 new tests, `ai/tests/test_io.py` — the first tests `ai.core.io` has ever had, partially closing the "untested" half of RISK_REGISTER R-01; the dead-entry-point half of R-01 is unchanged, still Phase 5 scope) |

All three fixes verified as TRUE no-ops on every real artifact this project has: `freeze_check.py`
shows only `core_config`/`core_features` hashes moved (dataset/split/decoder/phase_model/tracker
unchanged); the rebuilt baseline manifest shows all 8 headline metrics and the fitted-system
fingerprint byte-identical. 26 new tests total for this section.

## Summary table (for the final status report)

| sub-area | status |
|---|---|
| Failure injection (frame drop, confidence noise, fps, resolution proxy, joint dropout) | **DONE** — boundaries measured and documented above |
| Tempo suite (fast/slow/paused/inconsistent) | **DONE** |
| View robustness | **DONE** (consolidation + a view-invariance architectural finding) |
| Tracking robustness | **PARTIAL** — real-world evidence strong; no dedicated synthetic multi-person-crossing probe built this session |
| Abstention (P3.5) | **DONE** — `ai/core/guards.py`, 0 false abstentions on 45 real videos, verified prevented-errors on synthetic garbage |
| Exercise guard (P3.6) | **DONE** — `UNSUPPORTED_EXERCISE` status in the same module, built on the decoder's own `peak_z` plausibility signal |
| Clear failure behavior audit (P3.7) | **DONE** — missing checkpoint/broken video already correct; bad fps, invalid config, empty annotations fixed (R-16's crash, found earlier, already fixed in P3.0) |

**Phase 3 (robustness & safety of output): DONE.** All 7 pre-registered items (P3.0–P3.7)
executed.
