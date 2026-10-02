# HARDWARE_SETUP.md

_Status: current research setup documented from data; production setup is a target._

## Current research capture (what the six videos imply)
- Exercise: dual-handed seated cable row.
- Camera: single camera **behind the athlete** (rear view), ~30 fps assumed (`--fps 30` default; verify with `--videos_dir`).
- Consequence: wrist keypoints are low-quality in this view → wrist-dependent features (elbow angle) are not used as primary segmentation features.
- 3D is **estimated monocular 3D (MotionBERT)**, not calibrated physical depth. Depth sign convention is a single flip vector in the research scripts (`BACK` vector comment near the H36M index block).

## Development machine (audited 2026-10-02)
Windows 11 Home, Python 3.10.0 in `.venv`, torch 2.14.0 **CPU-only**, ultralytics 8.4.155, YOLO11n-Pose weights at repo root. GPU: none usable (`torch.cuda.is_available() == False`, checked 2026-10-02). Measured CPU throughput: full pose rebuild (YOLO + MotionBERT, 6 videos, ~2,200 frames) ≈ 4 min; v2 nested LOVO + evaluation ≈ 2 min.

## Target edge station (Phase 5+)
- Edge computer with GPU (or a capable CPU for YOLO11n + MotionBERT at reduced rate) running the Python edge agent as a long-running service.
- Camera: USB webcam (index) first; RTSP/IP camera later. Mount fixed behind the seated-row station at the same height/distance as the research recordings until a multi-setup dataset exists.
- Network: outbound HTTPS only; local inference must continue during internet loss (SQLite outbox).
- Measure and record: capture FPS, inference FPS, end-to-end event latency.

## To fill in
Camera model, resolution, mount distance/height, lighting notes, edge hardware spec, measured FPS/latency.
