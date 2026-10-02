# ARCHITECTURE.md — GYM-AI

## 1. Current AI pipeline (reconstructed from code, 2026-10-02)

### 1a. Research path (v2 — `ai/research/rep_segmentation_v2_hsmm.py`)

```
video (rear camera, ~30 fps)
  ↓  [NOT IN REPO] YOLO11n-Pose → COCO-17 2D → COCO→H36M-17 → normalise → MotionBERT
{video}.npy  (T,17,3)  estimated monocular 3D, H36M-17 order        ← script input
  ↓ prepare_video()      torso frame (pelvis/shoulder-centre), body-scaled, L/R arms kept separate,
                          optional per-joint confidence mask + row interpolation
  ↓ get_design()         zero-phase Butterworth low-pass (sosfiltfilt) + optional slow-drift removal
  ↓ PhaseModel (ridge)   shared L/R model → per-arm phase φ_L, φ_R ∈ [0,1]
  ↓ fuse()               nan-max fusion ("either arm is pulling") → fused phase
  ↓ estimate_tempo()     label-free tempo from phase velocity → video_scale()
  ↓ obs_features()       phase + velocity observations;  state_labels() REST/PULL/RET from GT (train only)
  ↓ fit_decoder()        Gaussian emissions per state, PULL/RET duration distributions (tempo units),
                          p_direct (RET→PULL, allows no-pause reps), learned peak-height likelihood
  ↓ hsmm_decode()        segmental Viterbi over REST/PULL/RET, censored first/last segments
  ↓ segments_to_reps()   START/PULL/END + arm_timing(): pull_left/right frame, lag_pull, lag_onset, amp_ratio, peak, peak_z
  ↓ run_cv()             NESTED leave-one-group-out: select_config() on inner folds only; crossfit_fused()
                          so decoder emissions are fit on out-of-fold phase predictions
```
The `label` column is dropped in `load_annotations()` — the segmenter never sees correct/asymmetry.

### 1b. Legacy 2D path (`ai/research/legacy_2d/`, kept for reference)

`pose_test.py` (YOLO → CSV) → `rep_counter.py` / `rep_segmentation.py` / `rep_video.py` (elbow-span ÷ shoulder-width signal, Savitzky–Golay, 30/70-percentile FSM) → `form_features.py` → `build_dataset.py`. Also `manual_rep_annotator.py` + `fix_annotation.py` (GT tooling) and `rep_signal_diagnostics.py` (peak-detector vs GT). Same signal/FSM code is duplicated across ~5 files; it fails on asymmetry videos (dev history §4.1) and is **not** the research baseline.

## 2. Target production architecture (edge AI + cloud data plane)

```
USB / RTSP / IP camera
   ↓
LOCAL EDGE AI SERVICE (Python, ai/edge_agent/)      ← inference plane
   capture → YOLO → MotionBERT/3D → rep segmentation (REALTIME_EDGE mode) → [later] form analysis
   ↓  compact JSON events over HTTPS (shared/event-schemas/, versioned)
SUPABASE (Postgres + Auth + Realtime + private Storage)   ← data/control plane
   ↓
NEXT.JS + TypeScript dashboard on VERCEL          ← visualisation/control plane
```
No continuous raw video to the cloud in the MVP. Durable DB writes are the source of truth; Realtime pushes updates to the browser.

## 3. Two AI modes (non-negotiable)

| | OFFLINE_EVAL | REALTIME_EDGE |
|---|---|---|
| Purpose | nested LOVO/LOPO, reproducible metrics | live inference on the edge box |
| Filtering | zero-phase `sosfiltfilt` (uses future frames) | causal filter |
| 3D | whole-sequence MotionBERT | sliding/chunked windows, bounded buffer |
| Decoder | full-sequence segmental Viterbi | bounded-buffer periodic re-decode + delayed rep finalisation |
| Rule | **never changed to make real-time easier** | benchmarked separately (accuracy delta vs OFFLINE_EVAL, latency) |

Known non-causal pieces in v2 to isolate in Phase 7: `lowpass()`/`sosfiltfilt`, `median_filter`, `estimate_tempo()` over the whole video, `hsmm_decode()` over the whole sequence, censored first/last segments.

## 4. Target repository layout

```
gym-ai/
├─ ai/{research, core, edge_agent, models/README.md, tests, src(legacy)}
├─ web/                      Next.js + TS
├─ supabase/{migrations, seed.sql, functions}
├─ shared/event-schemas/     versioned JSON schema shared by Python + TS
├─ docs/                     this folder
├─ .env.example  CHANGELOG.md  TODO.md  README.md
```
Phase 1 (2026-10-02): `ai/src` moved to `ai/research/legacy_2d` (path constants only); `ai/core/` extracted from v2 with a byte-identical regression test.

## 5. Event contract (v1.0 draft — to be formalised in `shared/event-schemas/`)

Event types: `rep_completed`, `session_started`, `session_ended`, `device_heartbeat`, `camera_error`, `alert_created`, `model_loaded`. Every event carries `schema_version`, `event_uuid` (idempotency key), `device_id`, `model_version`, `agent_version`. `rep_completed` carries `frames{start,pull,end}` and `metrics{duration_ms, pull_duration_ms, return_duration_ms, pull_left_frame, pull_right_frame, lag_pull_ms, lag_onset_ms, amp_ratio, peak, peak_z}` — these map 1:1 to the `arm_timing()` outputs of v2.

## 6. Language rules for all docs
"estimated monocular 3D pose" (not measured depth); "movement/form deviation" / "potential biomechanical concern requiring coach review" (not diagnosis); always state CURRENT vs TARGET and same-athlete LOVO vs new-person LOPO.
