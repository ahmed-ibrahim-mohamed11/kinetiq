# ai/research — frozen research baselines + evaluator

The two `rep_segmentation_*` scripts are the scientific record. **Do not edit them in place.**
Improvements go in new files (`rep_segmentation_v3_*.py`) or, after Phase 1, in `ai/core/`.

| File | Role | Origin (copied 2026-10-02) | md5 |
|---|---|---|---|
| `rep_segmentation_v2_hsmm.py` | **Current baseline**: ridge phase model + explicit-duration HSMM decoder, nested LOVO | `~/Downloads/rep_segmentation (1).py` | `60f99f7d7de751b0fac673812ba922d4` |
| `rep_segmentation_v1_fsm.py` | Legacy baseline: same phase model + fused-phase FSM with return-to-rest | `~/Downloads/rep_segmentation_v1_fsm.py` (identical to `~/Downloads/rep_segmentation.py` except trailing newline) | `2d339ea14ff8f14862f32df39532e4ea` |
| `eval_rep_segmentation.py` | The single evaluator (Phase 0). Rules: `pull_tol15` (default) + `iou_0.50` (= v2's internal rule). | written in-repo | — |
| `legacy_2d_predictions.py` | Packages `ai/outputs/dataset_build/*/reps.csv` (legacy 2D pipeline) into a predictions CSV | written in-repo | — |

## Input contract (v1/v2)
- `--poses_dir`: one `{video}.npy` per video, MotionBERT output in **H36M-17 order, shape (T, 17, 3)**, frame-aligned with the annotation CSV. ⚠️ **Not present as of 2026-10-02** (blocker B1 in `docs/PROJECT_STATE.md`). Target location: `ai/data/poses3d/`.
- `--annotations`: `ai/outputs/manual_rep_annotations.csv`. The `label` column is dropped on load.
- `--conf_dir` (optional): `{video}.npy` of shape (T, 17) joint confidences.
- `--videos_dir` (optional, FPS only) else `--fps` (default 30). `--groups_csv video,athlete` switches to leave-one-PERSON-out.
- Output: `<out_dir>/predicted_reps_with_arm_timing.csv` (held-out predictions incl. per-arm timing) + diagnostic plots.

## Commands
```powershell
scripts\eval_legacy_2d.ps1                       # legacy 2D sanity baseline
scripts\eval_v2.ps1 [-PosesDir ai\data\poses3d]  # v2 nested LOVO, then evaluator
```
Evaluator directly:
```powershell
.venv\Scripts\python ai\research\eval_rep_segmentation.py --gt ai\outputs\manual_rep_annotations.csv `
    --pred <predictions.csv> --match pull iou --name <run_name> --out ai\outputs\baselines\<run_name>.json
```
Expected v2 result (claimed, IoU rule): `ai/outputs/baselines/v2_hsmm_nested_lovo_CLAIMED.json`.
