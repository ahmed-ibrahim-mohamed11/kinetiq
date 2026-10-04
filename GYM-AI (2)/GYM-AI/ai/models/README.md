# ai/models — model artifact registry

Model weights are **not** committed (`*.pt *.pth *.bin *.onnx` are git-ignored). This file says where each artifact comes from.

| Artifact | Task | Source | Status (2026-10-02) |
|---|---|---|---|
| `yolo11n-pose.pt` (repo root) | 2D COCO-17 pose | Auto-downloaded by `ultralytics==8.4.155` on first `YOLO("yolo11n-pose.pt")` | present, 6 MB |
| `motionbert/FT_MB_release_MB_ft_h36m/best_epoch.bin` (2D→3D lifting, estimated monocular 3D) | H36M-17 2D+conf → (T,17,3) 3D | Official MotionBERT pose3d checkpoint, downloaded 2026-10-02 from the author's HuggingFace mirror `walterzhu/MotionBERT` (`checkpoint/pose3d/FT_MB_release_MB_ft_h36m/best_epoch.bin`, 169,958,969 bytes, DSTformer 42.5M params, config `configs/pose3d/MB_ft_h36m.yaml`). Re-download with the same URL if lost. | present (Path B rebuild) |
| Rep-segmentation phase model + HSMM decoder (`--save_model` JSON from `rep_segmentation_v2_hsmm.py`) | rep segmentation | Produced in-repo | not yet produced (needs poses) |
| `motionbert/FT_MB_lite_MB_ft_h36m_global_lite/best_epoch.bin` | 2D→3D lifting (lite, global head) | HF `walterzhu/MotionBERT` `checkpoint/pose3d/FT_MB_lite_MB_ft_h36m_global_lite/best_epoch.bin` (64,099,897 B) | present (Phase 2.5C benchmark) |
| `motionagformer/motionagformer-b-h36m.pth.tr` | 2D→3D lifting | Google Drive id `1Iii5EwsFFm9_9lKBUPfN8bV5LmfkNUMP` (MotionAGFormer README, Base/H36M, 141,930,389 B) | present (benchmark) |
| `poseformerv2/27_243_45.2.bin` | 2D→3D lifting | Google Drive id `14SpqPyq9yiblCzTH5CorymKCUsXapmkg` (PoseFormerV2 README, 243-frame/27-coeff, 172,754,985 B) | present (benchmark) |
| `videopose3d/pretrained_h36m_detectron_coco.bin` | 2D→3D lifting (COCO input) | https://dl.fbaipublicfiles.com/video-pose-3d/ (67,892,577 B; CC BY-NC — research only) | present (benchmark) |
| `mediapipe/pose_landmarker_full.task` | end-to-end pose | https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_full/float16/latest/ (9,398,198 B) | present (benchmark) |

Versioning target (Phase 3): the `model_versions` table (name, semantic_version, task, git_commit, metrics_json, artifact_uri, active). Every rep event carries `model_version`.
