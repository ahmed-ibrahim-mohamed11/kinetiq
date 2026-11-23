# LIVE_MODE.md — Mode B (live / causal) engineering + benchmark (Phase 4)

Status: **DONE (2026-10-10).** Production components are proven equivalent, and the timing
matrix plus long sessions were measured on AC power (all runs VALID). **Realtime is measured on
both hardware classes**, with a session-length limit (§6). Mode A (offline, after-set) stays the
reliability-first reference and is not changed by anything here (owner rule).

| hardware class | best live config (pre-registered P4.5 rule) | realtime, 148 s stream | accuracy (6 dev videos, 30 GT reps) |
|---|---|---|---|
| NVIDIA GPU (RTX 5050 Laptop, measured) | **`cuda_V2`** (V2 quality preset) | **1.31×** overall, 1.17× last quarter; latency p95 0.24 s | IoU F1 0.783 (27/12/3), pull F1 0.841 |
| CPU only (i7-13650HX, measured) | **`cpu_V3`** (15 fps budget preset) | **1.07×** overall, 1.02× last quarter (narrow); latency p95 0.85 s | IoU F1 0.8125 (26/8/4), pull F1 0.875 |

Rep events arrive about 5.6–5.8 s (median) after a rep ends. That is the price of the bounded
look-ahead (5.4 s) that keeps live accuracy close to offline. The accuracy numbers come from the
contaminated internal dev set (one athlete), not from unseen athletes.

## 1. What was built (all in `ai/core`, each proven against its frozen research reference)

| module | what | equivalence evidence |
|---|---|---|
| `ai/core/tracking.py` | production `track_subject` (verbatim) + `OnlineTracker` (frame-by-frame; buffers only until the first 10 detected frames, the offline rule's sole look-ahead) | `ai/tests/test_tracking.py`: byte-identical tracked keypoints AND logs vs the research tracker on **all 182** cached candidate files (14 seated-row/external + 168 Mendeley); `OnlineTracker` byte-identical to offline on all 182 |
| `ai/core/lifting.py::MotionBertLite` | product-primary lifter (R-01: `ai/core` previously only had full MotionBERT), device cpu/cuda, optional TTA, optional fp16; offline recipe `lift_video` + causal recipe `lift_window` | `ai/tests/test_lifting.py`: equal (atol 1e-6) to the research offline + causal-window lifters on CPU; reproduces the cached Phase 2.8 poses (atol 1e-5) on the new torch build; GPU drift bounded (fp32 < 1e-3, fp16 < 5e-2) |
| `ai/core/pose.py::YoloTopK` | per-frame top-8 candidates by box area (the tracker's input; replaces the legacy single-best-box rule for the product path) | `ai/tests/test_pose.py`: matches the cached video-stream-mode `yolo_top8` output on 30 real frames to 1e-3 px, identical NaN pattern |
| `ai/core/live.py::LiveStreamer` | the validated bounded-lag design (sliding-window lift, one finalized block per window, commit-once events after lag D), online tracker, `detect_every` + x/y/conf interpolation, decode through `ai.core.guards.safe_predict_video`, per-stage timing hooks; presets V1/V2/V3 | `ai/tests/test_live.py`: committed events IDENTICAL to the causal prototype for V2 on all 6 dev videos and V1/V3 spot-checks, every chunk guard status OK |

## 2. Bugs found by building/benchmarking it (both fixed, both regression-tested)

- `ai/core/form_indicators.py::_xcorr_lag_s` crashed on any rep window shorter than its max lag
  (e.g. a 12-frame window at 30 fps): the old slicing wrapped around via a negative index once
  the lag exceeded the window length, giving mismatched arrays to `np.corrcoef`. Offline never
  hit it (rep windows were always long enough); a live commit did. Fixed with explicit-length
  slices; `test_form_indicators.py` reproduces the crash with a frozen copy of the old code and
  proves the new code is identical on >200 random cases wherever the old code worked.
- `ai/core/pose.py` (`os` not imported) — caught before any run.

## 3. Environment change (P4.0)

torch 2.14.0 CPU-only wheel → **2.14.0+cu130** (same version; the RTX 5050 Laptop GPU, driver
610.74 / CUDA 13.3, was unused). Verified: all 8 frozen headline metrics — including the causal
suite, which runs live MB-Lite lifting — byte-identical; full test suite green.

## 4. Benchmark harness (P4.2) and measurement hygiene

`ai/research/phase4/live_benchmark.py` streams the real dev videos frame by frame
(cv2 decode = camera) through `LiveStreamer`, and from ONE run per config reports: per-stage ms
(capture/detect/track/lift/decode/form), sustainable fps, realtime factor, a simulated live
camera queue (frame j arrives at j/fps; latency median/p95/max — bounded iff realtime),
rep-event latency (commit time − arrival of the rep's END frame), warm-up, look-ahead, RSS,
process CPU %, peak CUDA memory, and GT accuracy (protocol v2). Rerun on any target machine:

```
python ai/research/phase4/live_benchmark.py                      # full matrix
python ai/research/phase4/live_benchmark.py --configs cuda_V2    # one config
python ai/research/phase4/live_benchmark.py --long-session cuda_V2 cpu_V2   # ~148 s continuous stream
```

**Hygiene (added after a discarded run)**: the 2026-10-09 matrix run is DISCARDED — Windows
Kernel-Power events show the laptop slept at least twice and switched to battery mid-run
(one frame "cost" 18,765 s). The harness now (a) refuses to run on battery unless
`--allow-battery` (results then marked `INVALID_BATTERY`), (b) inhibits system sleep for its own
process only (`SetThreadExecutionState`, released at exit — no change to power settings), and
(c) marks a run `INVALID_SUSPEND_GAP` if any single frame cost exceeds 10 s. Every reported
timing row carries its `timing_validity`.

## 5. Results

### Accuracy (timing-independent — valid from the first run)

| config | GT IoU F1 (TP/FP/FN) | GT pull F1 | vs reference |
|---|---|---|---|
| validated causal prototype V2 (cached 2D, CPU) | 0.806 (27/10/3) | — | reference (REALTIME_FEASIBILITY.md) |
| **`cpu_V2` — production streamer, live per-frame YOLO from raw video, CPU** | **0.806 (27/10/3)** | 0.836 | **identical to the reference end-to-end from raw video** |
| `cuda_V2` — same on GPU (fp32) | 0.783 (27/12/3) | 0.841 | −0.023 IoU F1 (2 extra FPs from GPU numeric drift flipping borderline decodes); within the pre-registered 0.03 band; pull F1 +0.005 |

### Timing matrix (P4.3): 2026-10-10, AC power, idle machine, all 9 runs `VALID`

One run per config over the 6 raw dev videos (2,220 frames, 74 s), with timing and accuracy from
the same run. Artifacts: `ai/outputs/phase4/live_benchmark_matrix{,_videos,_accuracy}.csv`,
`environment.json`. Accuracy gate (pre-registered): within 0.03 IoU F1 of the unoptimized
same-preset CPU reference (V2: `cpu_V2` 0.806; V1: prototype 0.7397; V3: prototype 0.8125).

| config | ms/frame (p95) | realtime × | queue latency med / p95 / max (s) | IoU F1 (TP/FP/FN) | pull F1 | accuracy gate |
|---|---|---|---|---|---|---|
| `cpu_V2` (reference) | 60.7 (60.4) | 0.55 | 2.34 / 9.44 / 13.6 (grows) | 0.806 (27/10/3) | 0.836 | reference |
| `cuda_V2` | 25.4 (33.3) | **1.31** | 0.024 / 0.11 / 0.23 | 0.783 (27/12/3) | 0.841 | pass (−0.023) |
| `cuda_V2_fp16` | 22.5 (28.4) | 1.48 | 0.021 / 0.032 / 0.094 | 0.771 (27/13/3) | 0.829 | **fail** (−0.035) |
| `cuda_V2_notta` | 26.4 (34.9) | 1.26 | 0.025 / 0.064 / 0.22 | 0.771 (27/13/3) | 0.829 | **fail** (−0.035) |
| `cuda_V1` | 27.2 (35.8) | 1.23 | 0.026 / 0.10 / 0.25 | 0.740 (27/16/3) | 0.767 | pass (= V1 ref) |
| `cpu_V2_notta` | 54.4 (59.9) | 0.61 | 2.65 / 7.50 / 11.1 (grows) | 0.771 (27/13/3) | 0.829 | **fail** (−0.035) |
| `cpu_V2_notta_320` | 33.8 (37.1) | 0.99 | 0.031 / 1.05 / 2.29 | 0.635 (20/13/10) | 0.698 | **fail** (−0.171) |
| `cpu_V2_notta_det2` | 33.4 (50.7) | 1.00 | 0.043 / 0.90 / 1.90 | 0.783 (27/12/3) | 0.841 | pass (−0.023) |
| `cpu_V3` (15 fps) | 56.0 (63.2) per processed frame | **1.19** | 0.051 / 0.065 / 1.64 | 0.8125 (26/8/4) | 0.875 | pass (= V3 ref) |

Each optimization's speed gain and accuracy cost, against its unoptimized reference:

| optimization | speed | accuracy | verdict |
|---|---|---|---|
| CUDA vs CPU (V2) | 60.7 → 25.4 ms/frame (2.4×) | −0.023 (GPU rounding flips 2 borderline decodes) | **accepted** |
| fp16 lift (CUDA) | 25.4 → 22.5 ms (1.13×) | −0.035 | rejected (gate) |
| no flip-TTA (CUDA) | none (lift already < 1 ms/frame on GPU) | −0.035 | rejected |
| no flip-TTA (CPU) | 60.7 → 54.4 ms (1.12×) | −0.035 | rejected, and still 0.61× |
| YOLO at 320 px (CPU, no TTA) | 54.4 → 33.8 ms (1.61×) | −0.171 (7 more misses) | rejected |
| detector every 2nd frame + interpolation (CPU, no TTA) | 54.4 → 33.4 ms (1.63×) | −0.023 | accuracy ok; not realtime in the long session (0.88×) |
| V3 budget preset (15 fps, tail, no TTA) | half the frames processed | = V3 reference (0.8125) | **accepted for CPU** |

Per-stage cost (ms per processed frame): on the GPU, video decode ≈ 9 and YOLO ≈ 15 dominate,
and lifting is under 1. On the CPU, YOLO ≈ 35 (640 px), lifting ≈ 14 with TTA (7 without) and
decode ≈ 11. Tracking, rep decoding and form analysis are each under 0.3. Peak CUDA memory
≈ 200 MB. Peak RSS ≈ 1.0 GB (first config; later rows accumulate within one process).

### Long continuous sessions (P4.4): 6 videos × 2 = 148 s as ONE stream, AC, `VALID`

Pre-registered gate: realtime ≥ 1.0 overall AND in the last quarter, AND latency p95 < 1 s.
Artifact: `ai/outputs/phase4/live_benchmark_long_session.csv`.

| config | realtime overall / first ¼ / last ¼ | latency med / p95 / max (s) | decode ms per chunk, first → last | verdict |
|---|---|---|---|---|
| `cuda_V2` | 1.309 / 1.402 / **1.168** | 0.024 / **0.244** / 0.411 | 9.9 → 279 (28×) | **REALTIME-CAPABLE** |
| `cpu_V3` | 1.067 / 1.127 / **1.024** | 0.282 / **0.847** / 1.55 | 17.7 → 166 (9×) | **REALTIME-CAPABLE (narrow)** |
| `cpu_V2_notta_det2` | 0.884 / 0.972 / 0.836 | 8.0 / 19.2 / 21.3 | 12 → 361 (30×) | not realtime |

### Decision (P4.5)

Pre-registered rule: among realtime-capable configs, pick the highest GT IoU F1 (ties: lower
event latency), per hardware class.
- **GPU class: `cuda_V2`.** IoU F1 0.783, 31% realtime headroom.
- **CPU-only class: `cpu_V3`.** IoU F1 0.8125, 2–7% headroom.
- The V2 quality preset is **not** realtime on this CPU (0.55×). It needs roughly 1.8× this
  CPU's YOLO + lifting throughput, or a GPU.
- Pooling both classes, the rule would pick `cpu_V3` (0.8125 vs 0.783). That is a 1–2-count
  difference on 30 GT reps, which global rule 4 treats as noise. On GPU hardware we default to
  `cuda_V2` for its realtime headroom. That default is a judgment call beyond the rule, stated
  as such.

## 6. Limits and next engineering steps

- **Session length.** The streamer re-decodes its whole growing buffer at every chunk, so decode
  cost grows linearly with stream length (measured 28× over 148 s on `cuda_V2`). Realtime is
  measured for 148 s only. Extrapolating, it would be lost after roughly 4–7 min (`cuda_V2`) or
  3–6 min (`cpu_V3`) of continuous streaming. The lower bound comes from the observed
  quarter-to-quarter slowdown, the upper from decode growth alone. A seated-row set is well under
  that, so the product should start one stream per set. A sliding decode buffer would remove the
  limit, and must pass the same accuracy gate (RISK_REGISTER R-17).
- **Not attempted** (realtime is already reached in both classes):
  - capture/detect/lift pipelining on threads, which could hide the 9–11 ms decode;
  - ONNX/OpenVINO export for CPU YOLO;
  - incremental lifting to avoid lifting each frame ~3× (window 243 / stride 81). On the GPU the
    lift costs under 1 ms/frame, so there is nothing to gain. On the CPU it would change the
    validated overlapping-window finalization, an accuracy risk that the V3 preset makes
    unnecessary.
- The GPU config's −0.023 IoU F1 is numeric drift on borderline decodes, not a different
  algorithm. CPU stays the numeric reference.
- **Frame drops.** The simulated camera queue is unbounded, so no frame is ever dropped and
  overload shows up as latency growth instead. With a real bounded camera buffer of C seconds,
  frames drop exactly when latency would exceed C. So `cuda_V2` (max 0.41 s over 148 s) drops
  none with a buffer ≥ 0.5 s, and `cpu_V3` (max 1.55 s) needs ≥ 1.6 s.

### Earlier indicative numbers (superseded)

A single smoke run of `cuda_V2` on 2026-10-09 (23.8 ms/frame, 1.40×) and the discarded matrix
(sleep + battery) are superseded by the validated runs above. Micro-benchmarks (idle, AC):
YOLO11n-pose 28.6 ms CPU / 12.7 ms CUDA; MB-Lite 243-frame window with TTA 1,450 ms CPU /
116 ms CUDA fp32 / 44 ms CUDA fp16.
