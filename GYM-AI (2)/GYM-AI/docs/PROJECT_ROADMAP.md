# PROJECT_ROADMAP.md — evidence-driven roadmap (owned by the technical lead; updated when results demand)

_Last update: 2026-10-05. History of superseded plans: PHASE_PLAN.md (original), DECISION_LOG.md._

## Done (validated, committed, frozen)
- **AI foundation**: honest baselines (Phase 0–2, tag `baseline-v0.2.0-frozen`), dataset audit +
  443-rep verified benchmark (2.5A/B), 6-pipeline pose benchmark (2.5C), 2,472-rep / 24-subject
  dataset + frozen subject split (2.6), frozen-backbone training with locked unseen-subject TEST
  (2.7): **PoseFormerV2 primary (F1 0.939), MotionBERT-Lite fallback**.
- **Backend groundwork** (code-complete, undeployed): Supabase schema + RLS + seed + RLS tests;
  shared event contract v1.0 (JSON Schema + pydantic + TS, sync-tested).

## Now
- **2.8 DONE**: MB-Lite transfers (F1 0.85, R 0.97); PFv2 collapses zero-shot (0.30; fps ruled out) -> MB-Lite is the seated-row product primary (docs/SEATED_ROW_TRANSFER_2_8.md). **Now: form-analysis v1 on MB-Lite windows.**
- **Owner-blocked, requested in parallel**: record the fresh unseen-athlete validation set
  (spec: DATASET_PHASE_2_6.md §readiness + REP_SEGMENTATION_PHASE_2_7.md §E). CRITICAL for any
  production claim; development continues without it.

## Next (ordered; reorder only with evidence)
1. **Form-analysis v1 (seated row + back), rule-based, interpretable** — per-rep features inside
   decoded windows (lag_pull, lag_onset, amp_ratio, trunk-lean proxy, ROM proxy) → "coach-attention
   indicator" events with provisional thresholds; NO correct/incorrect classifier (6 seated-row
   videos / 1 athlete cannot support one); evaluate feature separability good-vs-bad on Mendeley
   back (subject-wise). Language: movement-quality / coach-attention only.
2. **Integration restart (graduation spine)**: deploy Supabase (needs owner keys) → minimal coach
   dashboard (Next.js) on simulated events → edge agent (video-file camera, PFv2 offline path,
   outbox) → end-to-end demo: video → reps+indicators → DB → dashboard.
3. **Realtime feasibility measurement** (REALTIME_EDGE): causal variants, chunked lifting, true
   end-to-end FPS/latency budget on target hardware; decide demo hardware.
4. **Exercise library step** (after 1–3): bent-over row + seated row + bicep curl as the 3-exercise
   demo set (curl needs per-exercise phase model — evidence: transfer F1 ≤ 0.65); exercise
   recognition gate only after modules are reliable.
5. **Final validation on fresh athletes** (once recorded; single evaluation) + graduation
   deliverables (FINAL_RESULTS, architecture, demo script).
- **Unreal visualization**: optional after 2; consumes stored 3D sequences; never a dependency.

## Deferred / rejected for now
- Pose-backbone fine-tuning (frozen representations suffice; revisit only if form-analysis
  features prove pose-limited). Multi-person production tracking (single-athlete first; event
  schema already carries session/device IDs for later). Injury prediction (never claimed).
- VideoPose3D/MediaPipe paths (eliminated 2.5C; VideoPose3D also CC BY-NC).

## Risk register
| risk | rank | status / evidence | mitigation / next experiment |
|---|---|---|---|
| Seated-row domain gap | HIGH (was CRITICAL) | MB-Lite transfers 0.85 zero-shot; PFv2 fails | new-athlete seated-row proof still needs fresh recordings |
| No fresh unseen-athlete data | CRITICAL | spec written; needs owner | record per spec; single locked evaluation |
| MB-family side view / PFv2 fast reps | HIGH | TEST breakdowns | per-view/tempo routing ablation if form-analysis needs it |
| True 3D accuracy unproven | HIGH | proxies only (no mocap) | constrained-skeleton post-processing; label limitations in all outputs |
| Realtime budget unknown | HIGH | all offline today | roadmap item 3 measurement |
| Cross-exercise transfer weak | MEDIUM | best 0.650 (curl) | per-exercise modules (roadmap 4) |
| Annotation noise ceiling | LOW | tier recalls consistent | verify uncertain sample if needed |
| Supabase/web undeployed | MEDIUM | code-complete | roadmap 2; needs owner keys |
