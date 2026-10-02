# ai/research — frozen research baselines

These scripts are the scientific record. **Do not edit them in place.**
Improvements go in new files (`rep_segmentation_v3_*.py`) or, after Phase 1, in `ai/core/`.

| File | Role | Origin (copied 2026-10-02) | md5 |
|---|---|---|---|
| `rep_segmentation_v1_fsm.py` | Legacy baseline: ridge phase model + fused-phase FSM with return-to-rest | `~/Downloads/rep_segmentation_v1_fsm.py` (identical to `~/Downloads/rep_segmentation.py` except trailing newline) | `2d339ea14ff8f14862f32df39532e4ea` |
| `rep_segmentation_v2_hsmm.py` | **Current best baseline**: same phase model + explicit-duration HSMM decoder | `~/Downloads/rep_segmentation (1).py` | `60f99f7d7de751b0fac673812ba922d4` |

## Input contract (both scripts)

- `--poses_dir`: one `{video}.npy` per video, MotionBERT output in **H36M-17 order, shape (T, 17, 3)**, frame-aligned with the annotation CSV. ⚠️ **Not present in this repo as of 2026-10-02** — see `docs/PROJECT_STATE.md`.
- `--annotations`: `ai/outputs/manual_rep_annotations.csv` (`video,label,rep,start_frame,pull_frame,end_frame`). The `label` column is dropped on load (`load_annotations`), so the segmenter never sees correct/asymmetry.
- `--conf_dir` (optional): `{video}.npy` of shape (T, 17) joint confidences.
- `--videos_dir` (optional): used only to read FPS; otherwise `--fps` (default 30).
- `--groups_csv` (optional): `video,athlete` → switches to leave-one-PERSON-out.

## Baseline command (v2)

```powershell
.venv\Scripts\python ai\research\rep_segmentation_v2_hsmm.py `
  --poses_dir ai\data\poses3d `
  --annotations ai\outputs\manual_rep_annotations.csv `
  --fps 30 `
  --out_dir ai\outputs\rep_eval_v2
```

Expected (claimed, not yet reproduced here): see `docs/baselines/v2_hsmm_nested_lovo_claimed.json`.
