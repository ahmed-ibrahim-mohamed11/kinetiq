# PROJECT_ROADMAP.md — evidence-driven roadmap (owned by the technical lead; updated when results demand)

_Last update: 2026-10-08. History of superseded plans: PHASE_PLAN.md (original), DECISION_LOG.md._

## Done (validated, committed, frozen)
- **AI foundation**: honest baselines (Phase 0–2, tag `baseline-v0.2.0-frozen`), dataset audit +
  443-rep verified benchmark (2.5A/B), 6-pipeline pose benchmark (2.5C), 2,472-rep / 24-subject
  dataset + frozen subject split (2.6), frozen-backbone training with locked unseen-subject TEST
  (2.7), seated-row zero-shot transfer (2.8): **MB-Lite product primary / PFv2 in-domain
  benchmark primary**.
- **Phase A–C**: canonical result schema (root-cause fix), unseen-athlete dataset gate,
  form-analysis v1 (interpretable indicators; rom_proxy directional AUC 0.778; exercise-scoped
  thresholds; seated-row set PROVISIONAL).
- **External wild stress test (Domain B, commit bc3472e)**: frozen MB-Lite zero-shot on curated
  Instagram units → **PARTIAL EXTERNAL TRANSFER** (pull F1 0.842 / IoU 0.632 over 10 certain
  reps; window-extent degradation on paused tempo; one rear-view miss; editing/cuts are the
  dominant wild obstacle). docs/EXTERNAL_REAL_WORLD_STRESS_TEST.md.
- **Backend groundwork** (code-complete, undeployed): Supabase schema + RLS + seed + RLS tests;
  shared event contract v1.0 (JSON Schema + pydantic + TS, sync-tested).

## Now
- **Awaiting owner inputs for the two remaining tracks**: (a) fresh unseen-athlete recordings
  (spec: UNSEEN_ATHLETE_DATASET_SPEC.md; per stress-test findings add ≥1 slow/paused-tempo set
  and a rear view) — CRITICAL for any production claim; (b) Supabase keys + Vercel account +
  live-demo hardware decision (GPU vs 15 fps budget mode vs after-set mode; evidence in
  REALTIME_FEASIBILITY.md §6).
- Technical-lead work available without owner input: incremental tracker/decoder ports and a
  sliding decode buffer for the causal variant (production engineering of the validated
  streaming design); graduation deliverables drafting.

## Done 2026-10-08 (was "Now")
- **Realtime/causal feasibility**: causal audit + streaming prototype (V1/V2/V3). V2 (5.4 s
  look-ahead): F1 0.806 vs offline 0.841, 44/47 offline reps reproduced, rep events ~5 s after
  rep end. Compute on CPU laptop: 0.26–0.66× realtime (YOLO 88 ms/frame dominant) → live demo
  needs GPU or budget mode; after-set processing feasible today. docs/REALTIME_FEASIBILITY.md.

## Next (ordered; reorder only with evidence)
1. **Fresh-athlete validation gate** the moment recordings arrive: validate dataset → freeze →
   single locked evaluation against pre-registered criteria (either outcome reported).
2. **Integration restart (graduation spine)**: deploy Supabase (needs owner keys) → minimal coach
   dashboard (Next.js) on simulated events → edge agent (video-file camera, MB-Lite offline
   path, outbox) → end-to-end demo: video → reps+indicators → DB → dashboard.
3. **Live-demo engineering** per owner hardware decision: GPU port of YOLO+MB-Lite, incremental
   tracker/decode, sliding decode buffer (design already validated; accuracy gates = match V2
   numbers on dev videos).
4. **Exercise library step** (after 1–3): bent-over row + seated row + bicep curl as the
   3-exercise demo set (curl needs per-exercise phase model — evidence: transfer F1 ≤ 0.65);
   exercise recognition gate only after modules are reliable.
5. **Final validation on fresh athletes** + graduation deliverables (FINAL_RESULTS,
   architecture, demo script).
- **Unreal visualization**: optional after 2; consumes stored 3D sequences; never a dependency.

## Deferred / rejected for now
- Pose-backbone fine-tuning (frozen representations suffice; revisit only if form-analysis
  features prove pose-limited). Multi-person production tracking (single-athlete first; event
  schema already carries session/device IDs for later). Injury prediction (never claimed).
- VideoPose3D/MediaPipe paths (eliminated 2.5C; VideoPose3D also CC BY-NC).

## Risk register
| risk | rank | status / evidence | mitigation / next experiment |
|---|---|---|---|
| Seated-row domain gap | MEDIUM (was HIGH) | MB-Lite 0.85 zero-shot + wild pull F1 0.842 (Domain B) | new-athlete seated-row proof still needs fresh recordings |
| No fresh unseen-athlete data | CRITICAL | spec written; needs owner | record per spec (+ paused-tempo set, rear view); single locked evaluation |
| MB-family side/rear (depth-dominant) views | HIGH | TEST side 0.383; 1 wild rear-view FN | per-view routing ablation if needed; Domain C includes rear view |
| Window extent under paused tempo | MEDIUM (new, Domain B) | wild IoU 0.632 vs 0.841: holds absorbed into windows, pulls still correct | any fix developed on TRAIN/VAL only, validated on Mendeley + Domain C, never on the reels |
| True 3D accuracy unproven | HIGH | proxies only (no mocap) | constrained-skeleton post-processing; label limitations in all outputs |
| Realtime compute gap | MEDIUM (was HIGH "unknown") | MEASURED: 0.26–0.66× realtime on CPU laptop; YOLO 88 ms/f dominant; causal design itself validated (V2 F1 0.806) | owner hardware decision; GPU port or budget mode; after-set mode available now |
| Cross-exercise transfer weak | MEDIUM | best 0.650 (curl) | per-exercise modules (roadmap 4) |
| Annotation noise ceiling | LOW | tier recalls consistent | verify uncertain sample if needed |
| Supabase/web undeployed | MEDIUM | code-complete | roadmap 2; needs owner keys |
