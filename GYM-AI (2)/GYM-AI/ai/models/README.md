# ai/models — model artifact registry

Model weights are **not** committed (`*.pt *.pth *.bin *.onnx` are git-ignored). This file says where each artifact comes from.

| Artifact | Task | Source | Status (2026-10-02) |
|---|---|---|---|
| `yolo11n-pose.pt` (repo root) | 2D COCO-17 pose | Auto-downloaded by `ultralytics==8.4.155` on first `YOLO("yolo11n-pose.pt")` | present, 6 MB |
| MotionBERT checkpoint (2D→3D lifting, estimated monocular 3D) | 2D COCO-17 → H36M-17 (T,17,3) | Official MotionBERT release checkpoint | **absent** — Step 0 decision, see `docs/PROJECT_STATE.md` B1 |
| Rep-segmentation phase model + HSMM decoder (`--save_model` JSON from `rep_segmentation_v2_hsmm.py`) | rep segmentation | Produced in-repo | not yet produced (needs poses) |

Versioning target (Phase 3): the `model_versions` table (name, semantic_version, task, git_commit, metrics_json, artifact_uri, active). Every rep event carries `model_version`.
