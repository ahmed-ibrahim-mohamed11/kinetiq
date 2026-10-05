# REP_SEGMENTATION_PHASE_2_7.md — frozen-backbone rep segmentation on the Phase 2.6 dataset (2026-10-05)

Scope: MotionBERT-Lite / MotionBERT / PoseFormerV2 as FROZEN pose generators + the ai.core
ridge-phase + duration-HSMM downstream, trained on the frozen Phase 2.6 split. No backbone
fine-tuning. All artifacts under `ai/outputs/phase27/` (configs, predictions, metrics, hashes,
failure attributions, diagnostics, fps experiments). Dataset/split hashes: `freeze_manifest.json`.

## A. CLEAN FROZEN BASELINE
Protocol v1 (as pre-declared): verified+auto supervise, uncertain masked from fitting; uncertain
= don't-care under the strict one-to-one rule; hyper-parameters selected on VAL; TEST locked.
VALIDATION (back, unseen subjects, IoU≥0.5): MB-Lite **0.827** (P .753/R .917) · MotionBERT
**0.846** (P .774/R .933) · PoseFormerV2 **0.885** (P .848/R .926). Artifacts tagged `baseline`.

## B. DIAGNOSTICS (TRAIN/VAL only)
1. **Failure attribution** (MB-Lite baseline: 136 FP / 30 FN classified by deterministic rules):
   48 FP = EDGE_PARTIAL (detections of real-but-deliberately-unannotated censored edge reps),
   58 FP = NEAR_UNCERTAIN (found the rep, disagreed with the uncertain draft's sloppy boundary),
   leaving only ~19 FP / 16 FN unexplained. → the dominant "errors" were protocol artifacts.
2. **Side view**: failures concentrate on side (25 FN / 92 FP baseline); tracking is clean there
   (0 jumps, ~0 lost), but bone-length CV rises (side .149 vs diagonal .097) → **pose problem,
   not tracking/decoding**.
3. **FPS robustness** (controlled resampling, VAL): same motion at 30 fps → median pull shift
   0.033 s; at 7.5 fps → 0.067 s (= 1 native frame); counts stable. Core audit found only two
   sub-0.25 s frame-anchored constants. **Temporal design is sound — no fix required.**
4. **Tracking** (Problem 3/4): expanded-set totals 1,359/74,047 lost (1.8%), 195 jumps; failure
   attribution assigns just 1–8 errors to TRACK_NAN. **Tracking is not a material error source.**
5. **3D validity proxies** (no mocap GT — proxies only): bone CV / LR asym / accel-outliers /
   torso jitter: PFv2 .066/.046/.0005/.015 ≪ MB .116/.118/.027/.016 ≈ MB-Lite. PFv2 produces the
   most rigid, temporally stable skeletons; MotionBERT-family bone lengths wobble ~12%.
6. **Leakage audit** (Problem 7): split integrity re-verified by tests (subjects, views, forms,
   duplicate-links); form_label/annotation_quality/rep-counts never enter inference (inputs are
   pose+conf arrays only); hyper-parameters and all keep/reject decisions made on VAL; TEST ran
   once, after freeze, results saved immediately.

## C. CONTROLLED CHANGES — KEPT AND REJECTED
| change | evidence | verdict |
|---|---|---|
| Protocol v2a: edge don't-care (unmatched predictions outside the annotated span are ignored; mid-span predictions can never use it) | 48 attributed FPs; policy consistency with 2.5B/2.6 censoring; recall unchanged | **KEPT** (guard tests) |
| Protocol v2b: relaxed uncertain absorption (pull-tol OR IoU≥.25, still one-to-one) | 58 attributed FPs against low-confidence draft boundaries | **KEPT** (guard tests) |
| Supervision = verified-only (TRAIN side) | VAL F1 .910 vs .914 for verified+auto | REJECTED — auto labels don't hurt; keep more data |
| conf_thr 0.3→0.5 (mask hallucinated far-arm) | VAL F1 .914→.892; side .760→**.690** | **REJECTED** — side conf runs low; masks real joints |
Effect of v2 on VAL: MB-Lite .827→.914 · MB .846→.919 · PFv2 .885→.923 (precision only; recall untouched).
Fixed bugs (documented): duplicate `rule` kwarg (crashed eval); verified-only ablation initially
demoted VAL GT too (invalid run discarded, rerun after fix).

## D. FINAL TEST (one run, frozen protocol v2, back / unseen subjects, 318 evaluable reps)
| pipeline | F1 | P / R | start/pull/end MAE (s) | side | diag | front | bad | good | fast | med | slow | curl transfer |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **PoseFormerV2** | **0.939** | .937/.940 | .19/.14/.22 | **0.924** | .934 | .953 | **.925** | .953 | .850 | **.949** | **.980** | .418 |
| MotionBERT | 0.825 | .774/.884 | .15/.12/.16 | .473 | .975 | .974 | .798 | .854 | **.901** | .819 | .827 | .370 |
| MotionBERT-Lite | 0.812 | .784/.843 | **.13/.09/.15** | .383 | .979 | .974 | .749 | .881 | .782 | .793 | .859 | **.650** |
Annotation-tier recall is consistent across tiers (e.g. MB .882 verified / .884 auto) → auto-tier
GT does not inflate scores. Per-subject: PFv2 ≥ .877 on all five TEST subjects (incl. 1.000 on
subject_004 where both MB variants drop to ~.65).
Deployment (CPU): PFv2 34.8 fps lifting / 173 MB; MB-Lite 87.9 fps / 64 MB; MB 20 fps / 170 MB.
End-to-end is YOLO-bound (~15–20 fps 2D) for all three; MediaPipe-class end-to-end alternatives
were eliminated in 2.5C. Downstream HSMM decode: ~0.5–2 s per 30 s video (offline), latency
dominated by pose, not the decoder.

## E. RECOMMENDATION + REMAINING BLOCKERS
**Primary: PoseFormerV2** (unseen-subject F1 .939; solves side view; robust to form/tempo/subject).
**Fallback/reference: MotionBERT-Lite** (fastest, smallest, sharpest boundary timing, best curl
transfer .650 — better cross-exercise plasticity; keep for realtime path experiments).
MotionBERT retires to reference only (dominated on both axes).
What Phase 2.7 proves: cross-SUBJECT, cross-VIEW, cross-FORM and cross-TEMPO generalisation on
bent-over row at 15 fps. What it does NOT prove: Seated-Row performance, true 3D accuracy
(proxies only), production robustness (one dataset, one fps, gym phones).

### Remaining blockers to a production-ready gym AI system
| blocker | rank | evidence / status | next experiment |
|---|---|---|---|
| Seated-Row domain gap | CRITICAL | untested since Phase 2 (same-athlete only) | Phase 2.8: run frozen PFv2 + MB-Lite systems on the 6 seated-row videos vs frozen baseline |
| Fresh unseen-athlete validation set | CRITICAL | spec in Phase 2.6/2.7 docs; not collected | record per spec (≥10 athletes, 3 views, speeds, good/bad, seated row) — needs owner approval |
| Side-view pose for MB-family / fast-rep gap for PFv2 | HIGH | TEST: MB side .38–.47; PFv2 fast .850 | ensemble or per-view routing ablation; occlusion-aware 2D (ViTPose) swap experiment |
| True 3D geometric validity | HIGH | proxies only; MB bone CV ~12% | one mocap-or-multicam session, or constrained-skeleton post-processing experiment |
| Realtime path | HIGH | all current decoding offline/non-causal | Phase 7 plan: causal filters + chunked lifting + delayed finalisation (REALTIME_EDGE) |
| Curl/any-exercise transfer | MEDIUM | best .650 (MB-Lite); PFv2 .418 | per-exercise phase models; exercise recognition gate (later phase) |
| Multi-person / occlusion tracking | MEDIUM | benchmark tracker fine on this data (1.8% lost) but not production-grade | stress test on crowded scenes; ByteTrack-style association experiment |
| Annotation noise ceiling | LOW | tier recalls consistent; 617 don't-care reps | verify a sample of uncertain reps to tighten GT if a finer benchmark is needed |
| Backend/dashboard/edge agent (Phases 3–6) | paused by owner | Supabase schema code-complete | resume when owner re-opens product phases |
