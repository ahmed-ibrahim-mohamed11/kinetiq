# DECISION_LOG.md — major technical decisions (newest first)

## 2026-10-05b — MotionBERT-Lite becomes PRIMARY for the seated-row product path
- **Decision**: deployment/product primary = MotionBERT-Lite; PoseFormerV2 demoted to in-domain
  benchmark primary / future candidate pending in-domain seated-row training data.
- **Evidence**: Phase 2.8 zero-shot seated row — MB-Lite F1 0.841/0.853 (R 0.97, pull MAE 0.13 s)
  vs PFv2 0.303/0.394; fps-matched control (15 fps) still 0.338 → exercise/view brittleness,
  not fps. Same tracked 2D for both. See docs/SEATED_ROW_TRANSFER_2_8.md.
- **Consequences**: form-analysis v1 builds on MB-Lite windows; PFv2 revisited when fresh
  seated-row training data exists; roadmap risk 'cross-exercise transfer' upgraded for PFv2.

## 2026-10-05 — Phase 2.8 (seated-row zero-shot transfer) chosen as next step
- **Decision**: run the frozen Phase 2.7 systems on the 6 seated-row videos BEFORE starting form
  analysis, backend resumption, or realtime work.
- **Alternatives**: start form analysis; resume Supabase/dashboard; realtime feasibility; fresh-data
  collection first.
- **Evidence/reason**: domain gap is the top CRITICAL risk; the experiment is cheap (hours), gates
  form analysis (needs trustworthy seated-row rep windows), and tests the 30 fps claim end-to-end.
  Fresh-data collection needs the owner physically and can proceed in parallel.
- **Consequences**: form analysis starts only after transfer results are known.

## 2026-10-05 — PoseFormerV2 replaces MotionBERT-Lite as PRIMARY pose representation
- **Decision**: PFv2 primary; MB-Lite fallback/speed/timing reference; MotionBERT retired to reference.
- **Alternatives**: keep MB-Lite (2.5C winner); keep MotionBERT (production continuity).
- **Evidence**: Phase 2.7 locked TEST (unseen subjects): PFv2 F1 0.939 vs 0.825/0.812; side view
  0.924 vs 0.473/0.383; per-subject worst-case 0.877 vs ~0.65; best 3D stability proxies (bone CV
  0.066). MB-Lite retains best boundary timing (0.094 s pull MAE), speed (87.9 fps lift, 64 MB) and
  curl transfer (0.650).
- **Consequences**: PFv2's fast-rep gap (0.850) and weaker cross-exercise transfer are accepted,
  tracked in the risk register. License check: PFv2 repo is MIT (compatible with product use).

## 2026-10-05 — Evaluation protocol v2 (edge don't-care + relaxed uncertain absorption)
- **Decision**: unmatched predictions outside the annotated span are ignored; uncertain reps absorb
  at most one prediction under pull-tol OR IoU>=0.25.
- **Evidence**: failure attribution showed 106/166 baseline FPs were artifacts of the deliberate
  censored-edge/uncertain annotation policy; recall is untouched by the change; 10 guard tests
  prevent abuse. v1 results preserved for comparison.

## 2026-10-05 — Supervision = verified+auto, uncertain masked
- **Evidence**: controlled ablation (verified-only: VAL 0.910 vs 0.914) shows auto labels are
  supervision-grade. Uncertain frames are excluded from all fitting (never forced into the loss).

## 2026-10-04 — One-phase-at-a-time protocol; dataset/split freeze discipline
- Owner-directed; superseded 2026-10-05 by technical-lead autonomy (this log + roadmap now carry
  the decision burden). Frozen artifacts (annotations, split, baselines, tags) remain immutable.

## 2026-10-04 — Benchmark subject tracker replaces highest-confidence selection (benchmark only)
- **Evidence**: 2.5B audit — conf-best switches to background people on 15/34 videos (up to 110
  jumps). Tracker: largest-init + IoU continuity + re-acquisition over 8 area-sorted candidates;
  1.8% lost frames on the expanded set; later exonerated as an error source (Phase 2.7 attribution).

## 2026-10-02 — Phase 2 survival-law fix; claimed 0.93 retired; Path B rebuild
- See CHANGELOG commits 8d80fce / 60074c4: honest measured baselines replaced unreproducible claims;
  structural decoder correction validated by full nested LOVO (0.87 -> 0.93/0.97 same-athlete LOVO).
