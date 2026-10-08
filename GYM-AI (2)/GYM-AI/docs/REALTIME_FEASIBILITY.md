# REALTIME_FEASIBILITY.md — causal audit + streaming prototype benchmark (2026-10-08)

Prototype of a CAUSAL/REALTIME VARIANT of the product pipeline, kept strictly separate from the
validated OFFLINE REFERENCE (which is unchanged and remains the system of record). Code:
`ai/research/realtime/causal_prototype.py`; artifacts: `ai/outputs/realtime/{accuracy.csv,
timing.csv, latency.csv, summary.json}`. Hardware: this CPU-only Windows 11 laptop (torch CPU,
no GPU present; VRAM 0). Data: the 6 internal Domain-A dev videos (GT = 30 reps) — external
reels and Domain C were not touched.

## 1. Causal audit — where the frozen offline pipeline needs future frames

| stage | future-frame requirement in the offline system |
|---|---|
| YOLO11n-pose | none (per frame) — but dominant per-frame cost (§3) |
| tracker | none at steady state; init = largest box over first 10 detected frames (warm-up) |
| MB-Lite lifting | up to 242 future frames (bidirectional attention inside 243-frame windows) + `crop_scale` box computed over the WHOLE video |
| features | `sosfiltfilt` zero-phase smoothing (whole sequence) + **10 s centered median detrend → ±5 s look-ahead** |
| phase model | per-frame ridge — effectively causal given features |
| HSMM decoder | whole-video tempo estimate + full-sequence Viterbi |
| form indicators | per completed rep only (post-hoc by design) |

The offline system is therefore NOT realtime-capable as-is; "87.9 fps lifting" was never
realtime evidence. A causal variant must bound each of these: sliding-window lifting with a
finalize position, buffer-recomputed features committed with lag D, and fixed-lag Viterbi that
emits a rep only once its END is D old (then never revises it).

## 2. Streaming variants measured (commit-once semantics, end-of-stream flush)

| variant | proc fps | lift stride/finalize | flip-TTA | look-ahead D | warm-up |
|---|---|---|---|---|---|
| V1 low-latency | 30 | 81 / tail | on | 2.7 s | 8.1 s |
| V2 quality | 30 | 81 / middle | on | 5.4 s | 8.1 s |
| V3 budget | 15 (every 2nd frame) | 81 / tail | **off** | 5.4 s | 16.2 s |

## 3. Compute budget (measured on this laptop; medians over all chunks)

| stage | V1/V2 (30 fps) | V3 (15 fps) | notes |
|---|---|---|---|
| YOLO11n-pose per frame | **87.9 ms** (p95 109) | 87.9 ms | 4 independent runs gave medians 41 / 88 / 138 / 197 ms — strong thermal/load variance on this laptop; 88 ms is the clean-run median |
| MB-Lite re-lift per chunk (243-frame window) | 3,096–3,305 ms (TTA) | 1,037 ms (no TTA) | amortized 38–41 ms/frame (3× window overlap × 2 TTA) vs offline 11.4 ms/frame |
| tracker per chunk (frozen logic on buffer) | 13–14 ms (last chunk 22–39) | 7 ms | O(T) growth, negligible ≤60 s; incremental port is production work |
| features+decode per chunk | 58–75 ms (last chunk 117–157) | 29 ms (42) | O(T) growth ≈2× over a 30–60 s video → needs a sliding decode buffer for long sessions |
| form indicators per rep | 9–10 ms | 5 ms | negligible |
| **per-frame budget** | **127–130 ms** | **101 ms** | YOLO is the single largest cost |
| **sustainable fps** | **7.7–7.9** | **9.9** | vs 30 / 15 needed |
| **realtime factor** | **0.26×** | **0.66×** | <1 ⇒ a live stream would drop frames / queue unboundedly |
| chunk blocking time p50/p95 | 3.2–3.4 s / 4.0–4.5 s | 1.07 s / 1.34 s | V1/V2: one chunk takes LONGER than the 2.7 s it covers → unbounded queue on CPU |
| RSS | 461–466 MB | 552 MB | whole python process |
| GPU / VRAM | none / 0 | — | CPU-only machine |

## 4. Accuracy vs offline reference and vs GT (IoU rule, protocol v2, 30 GT reps)

| variant | GT F1 (TP/FP/FN) | offline F1 on same videos | reps matched to offline | pull dev vs offline | median rep-event latency |
|---|---|---|---|---|---|
| V1 low-latency | 0.740 (27/16/3) | 0.841 (29/10/1) | 45/47 | ≤5 f | **3.6 s** after rep end |
| **V2 quality** | **0.806 (27/10/3)** | 0.841 | 44/47 | ≤4.2 f | **4.9 s** |
| V3 budget | 0.812 (26/8/4) | 0.841 | 38/47 | ≤3.7 f | 4.9 s |

Failure patterns (all on dev videos, consistent with known behavior):
- **V1** duplicates slow reps (correct_02: 9 commits vs 5; asymmetry_03: 8 vs 5): with only
  2.7 s of right context the decoder's early boundary estimates shift after commitment —
  premature commitment is the cost of low latency.
- **V2** is the stable configuration: every video within ±2 predictions of offline; the
  remaining gap vs offline (0.806 vs 0.841) is boundary-extent flicker near the commit horizon.
- **V3** matches V2 pooled, but collapses on the fast-tempo video (asymmetry_01: TP 1/FN 4 —
  0.6 s reps = 9 frames at 15 fps; IoU matching on quantized boundaries fails although 7/15
  offline reps were still found). 15 fps + no-TTA is viable except for very fast tempos.

## 5. Owner-requested metrics (explicit)

Effective end-to-end FPS: 7.7–9.9 sustainable (vs 15–30 required) → **realtime factor
0.26–0.66× on this CPU**. Median per-frame pipeline latency: 127–130 ms (V1/V2), 101 ms (V3);
p95 chunk stall 4.0–4.5 s (V1/V2). Warm-up: 8.1–16.2 s + 10-frame tracker init. Look-ahead:
2.7–5.4 s by design. Detection 87.9 ms/f · tracking ≤0.2 ms/f amortized · MB-Lite 12.8–41 ms/f
amortized · temporal model ~0.4–0.9 ms/f amortized (grows with buffer) · form 5–10 ms/rep.
Dropped frames: none in file simulation; at live rates the V1/V2 queue grows without bound
(factor <1), so frames WOULD drop — sustainable fps is the operative number. Queue growth:
decode cost 2× from first to last chunk (30–60 s videos); lift chunk time exceeds V1/V2 chunk
period on CPU. RAM 461–552 MB; CPU single-process (torch CPU threads); GPU/VRAM none.

## 6. Verdict — is realtime technically feasible?

- **On this CPU laptop, live 30 fps processing: NOT FEASIBLE** (0.26× realtime; the queue grows
  faster than it drains). This is a measured compute limit, not an algorithmic one.
- **The causal ALGORITHM is validated**: bounded-lag streaming (V2) reproduces 94% of offline
  rep events (44/47) with F1 0.806 vs 0.841 and pull timing within ~0.15 s of offline, emitting
  each rep ~5 s after it ends. Rep-after-rep coaching feedback tolerates a 5 s delay; a live
  per-frame overlay does not tolerate 0.26×.
- **Paths to a live demo** (ranked, all compute-side; none changes the validated models):
  1. modest GPU for YOLO + MB-Lite (the two dominant costs; both are standard torch models) —
     expected ≥5× on both, bringing V2 comfortably above 1×;
  2. V3-style budget mode (15 fps, no TTA) + lighter detector ≤66 ms/f → ~1× on CPU, with the
     measured fast-tempo weakness accepted and disclosed;
  3. near-live "after-set" mode (current offline system, processing a set during the rest
     period) — already feasible today on CPU (offline pipeline runs ~0.3–0.7× realtime
     end-to-end including detection) and uses the fully validated configuration.
- **Main bottleneck**: YOLO11n-pose CPU inference (87.9 ms/frame), then sliding-window re-lift
  overlap (3×) × flip-TTA (2×).

REALTIME STATUS stays **NOT VALIDATED** for live operation until run ≥1× on target hardware;
the streaming design itself is now measured and viable. Hardware decision = owner action
(target device for the demo).

## 7. Prototype honesty notes

The streaming simulator reuses the frozen stages verbatim (tracker re-run on the growing
buffer; full-buffer feature/decode recomputation per chunk) rather than re-implementing
incremental versions — costs above are therefore upper bounds for tracker/decode and honest
steady-state for YOLO/lift. Three prototype bugs were found and fixed during bring-up (middle-
finalize end-of-stream flush; videos shorter than one window; duplicate commits under pull-only
dedupe) — all in the NEW wrapper, zero changes to `ai/core/` or any frozen artifact. Dev-video
GT was used only to score the variants; no parameter of the frozen system was altered.
