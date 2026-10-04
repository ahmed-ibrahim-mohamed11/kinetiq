# POSE_BENCHMARK_2.5C.md — pretrained pose-pipeline benchmark (2026-10-05)

Goal: rank pretrained pose representations by usefulness for **rep segmentation**, not by paper MPJPE.
No pose backbone was trained or fine-tuned. The frozen production baseline (`baseline-v0.2.0-frozen`)
is untouched. All artifacts: `ai/outputs/pose_bench/` (summary.csv, breakdown_*.csv, stability.csv,
tracking_log.csv, f1_vs_fps.png, downstream/*_videos.csv, poses/*/deployment.json).

## Protocol (identical for every pipeline)
- **Data**: the Phase 2.5B benchmark — 31 MD5-unique videos, 443 verified reps (back 410, bicep_curl 33),
  6 subjects, 3 views, good+bad form, 15 fps. No duplicate clip is counted twice; subject_019's
  duplicate-link to subject_020 is irrelevant here (020 absent).
- **Subject selection (mandatory fix)**: deterministic tracker (largest-box init over first 10
  detected frames → IoU/centre continuity → re-acquisition after 15 lost frames) over 8 area-sorted
  YOLO candidates/frame. 2.1% frames lost (interpolated, conf 0), 36 jumps logged
  (`tracking_log.csv`), overlays visually verified on the 3 hardest videos. All YOLO-based lifters
  consume the SAME tracked 2D. MediaPipe is end-to-end and uses its own internal tracker (documented
  difference; its person choice cannot be forced to match ours).
- **Downstream (common)**: the ai.core ridge-phase + explicit-duration-HSMM pipeline, trained per
  fold with **leave-one-subject-out** on back videos only; hyper-parameters by inner
  leave-one-subject-out on the 5 training subjects (identical grid as production). No threshold was
  tuned per video/pipeline; labels and rep counts never enter the segmenter.
- **Curl videos** = cross-exercise transfer: decoded by the fold model of their (held-out) subject,
  trained only on other subjects' back videos.
- **Matching**: fps-aware pull tolerance 8 frames = 0.533 s @15 fps (seated-row default was 15 @30 fps
  = 0.5 s), plus scale-free temporal IoU ≥ 0.5. MAE in frames and seconds (1 frame = 66.7 ms).

## Headline table (back exercise, leave-one-subject-out, 410 GT reps)
| pipeline | F1 (IoU) | P / R | F1 (pull±0.53s) | start/pull/end MAE (frames) | (seconds) | curl-transfer F1 | wrist jitter | lift fps (CPU) | model MB |
|---|---|---|---|---|---|---|---|---|---|
| **MotionBERT-Lite** | **0.864** | 0.87/0.86 | 0.886 | 2.4 / 1.7 / 2.6 | 0.16/0.11/0.17 | **0.77** | 0.039 | 42.9 | 64 |
| **MotionBERT** | 0.861 | 0.86/0.86 | **0.893** | **2.3 / 1.5 / 2.4** | 0.15/0.10/0.16 | 0.53 | **0.033** | 20.3 | 170 |
| PoseFormerV2 | 0.823 | 0.83/0.82 | 0.857 | 3.2 / 2.4 / 3.0 | 0.21/0.16/0.20 | 0.68 | 0.042 | 34.8 | 173 |
| MotionAGFormer-B | 0.807 | 0.82/0.79 | 0.827 | 2.7 / **1.3** / 2.8 | 0.18/0.09/0.18 | 0.50 | 0.062 | 39.3 | 142 |
| MediaPipe (full) | 0.695 | 0.69/0.70 | 0.724 | 3.2 / 2.8 / 2.4 | 0.21/0.19/0.16 | 0.11 | 0.164 | 28.4 (end-to-end) | 9.4 |
| VideoPose3D | 0.670 | 0.66/0.68 | 0.749 | 2.7 / 3.1 / 2.8 | 0.18/0.20/0.18 | 0.70 | 0.051 | **1380.8** | 68 |

Deployment notes: CPU-only machine; VRAM 0 for all. "lift fps" excludes the shared YOLO 2D stage
(~15–20 fps on this CPU, identical cost for all YOLO-based rows); MediaPipe's 28.4 fps is **end-to-end**
(own detector+tracker), i.e. the fastest complete path on CPU. VideoPose3D's lifting is nearly free
(fully convolutional), so its end-to-end cost ≈ YOLO alone. Peak extra RSS: 108–376 MB (all modest).

## Robustness breakdowns (back, IoU F1)
| pipeline | diagonal | front | side |  | bad | good |
|---|---|---|---|---|---|---|
| MotionBERT-Lite | 0.942 | 0.955 | **0.734** |  | 0.876 | 0.851 |
| MotionBERT | 0.939 | 0.977 | 0.719 |  | **0.877** | 0.844 |
| PoseFormerV2 | 0.879 | 0.879 | **0.736** |  | 0.838 | 0.805 |
| MotionAGFormer-B | 0.926 | **0.983** | 0.573 |  | 0.800 | 0.814 |
| MediaPipe | 0.800 | 0.971 | 0.428 |  | 0.719 | 0.667 |
| VideoPose3D | 0.881 | 0.770 | 0.409 |  | 0.750 | 0.593 |

- **Side view is the bottleneck for every pipeline** (far arm occluded in single-arm rows; the wrist
  signal geometry is worst there). The spread (0.41 → 0.74) is the single biggest discriminator.
- All pipelines hold up on bad-form videos (bad ≥ good for 5/6) — the segmenter does not depend on
  correct form, as required.
- Failure attribution: pose vs segmenter — on side-view videos the per-arm phase signals of the weak
  pipelines (MediaPipe/VideoPose3D) are visibly corrupted before the HSMM (high wrist jitter 0.16 /
  0.05 vs 0.03), while front/diagonal failures are rare for all: the dominant failure mode is POSE
  quality under occlusion, not the temporal decoder. The decoder's own residual failures (merged
  quick reps, trailing partials) appear equally across pipelines.

## Answers to the benchmark questions
1. **Best rep F1**: MotionBERT-Lite (0.864), statistically tied with MotionBERT (0.861).
2. **Best START timing**: MotionBERT (2.25 f = 0.15 s). **PULL**: MotionAGFormer (1.31 f = 0.09 s),
   then MotionBERT (1.51 f). **END**: MotionBERT (2.40 f = 0.16 s).
3. **Most robust across views**: MotionBERT-Lite (worst-view 0.734; smallest spread with PFv2).
4. **Most robust on bad form**: MotionBERT (0.877) / MotionBERT-Lite (0.876) — effectively tied.
5. **Fastest**: lifting only — VideoPose3D (1381 fps); end-to-end on CPU — MediaPipe (28.4 fps
   complete vs ~9–15 fps for YOLO+lifter paths).
6. **Best quality/speed trade-off**: **MotionBERT-Lite** — top F1, 2.1× MotionBERT's lifting speed,
   0.38× its size (64 MB).
7. **To Phase 2.7 training**: (1) **MotionBERT-Lite** (primary), (2) **MotionBERT** (quality
   reference, already the frozen production stage), (3) **PoseFormerV2** (best side-view F1 0.736,
   different architecture family → diversity). MotionAGFormer dropped despite best PULL MAE: worst
   transformer side-view score (0.573) and highest jitter of the lifters.
8. **Is MotionBERT still justified as baseline?** Yes for quality — but the Lite variant matches F1
   at half the cost; recommend promoting Lite to default after Phase 2.7 confirms it on training.
9. **Is MediaPipe competitive for realtime?** As a product path: partially. It is the fastest
   complete CPU pipeline and 9.4 MB, but F1 0.695 overall, 0.428 on side views and 0.11 curl
   transfer are not acceptable for counting; usable only as a fallback/preview mode unless Phase 2.7
   training on its representation closes the gap.
10. **Failure modes pose vs segmenter**: see breakdown section — occlusion-driven pose corruption
    dominates (side views); segmenter-attributable failures (quick-rep merges, censored-edge
    partials) are secondary and pipeline-independent.

## Fairness caveats / honest notes
- MediaPipe could not use the common tracker (end-to-end design); it also tracks a single person
  internally and never visibly switched to background people in its outputs (checked via pelvis-jump
  traces), but identity policy is not identical to the YOLO rows.
- Each lifter uses its repo's official in-the-wild recipe (normalisation, clip handling, flip-TTA);
  these recipes ARE part of the pipeline under test and are documented in
  `ai/research/pose_benchmark/lifters.py`. No per-pipeline downstream tuning.
- PoseFormerV2 windows were batched for CPU throughput (numerics unchanged); its fps reflects the
  243-frame sliding-window design honestly.
- The GT annotations were drafted with YOLO-wrist signals before human frame verification
  (Phase 2.5B); a residual circularity in favour of YOLO-based rows cannot be fully excluded,
  though every boundary was anchored to raw frames.
- MotionBERT-Lite's "global" head is not root-relative; pelvis was re-zeroed (downstream is
  pelvis-relative anyway). MediaPipe world-y was flipped to match the up convention.
- curl-transfer sample is tiny (3 videos / 33 reps) — directional evidence only.
- No integration failures: all 5 required + 1 optional candidates ran. Blockers hit and solved:
  mediapipe 1.x removed the legacy API (ported to tasks API), PoseFormerV2/AGFormer needed
  `torch_dct`/`einops`/`timm` and `module.` prefix stripping, Google Drive SSL interception required
  curl/schannel downloads.
