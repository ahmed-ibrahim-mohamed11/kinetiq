# Model selection protocol

No model is chosen because it is popular. Each component is selected on **measured** results from the Phase 2/4/6 benchmarks on authorized footage from the actual gym cameras. Until then, `model_versions.benchmark` is NULL and the rows are labelled `candidate`.

## Candidates (registered rows: see `supabase/seed.sql`)

| Component | Candidate | Runtime | License | Notes |
| --- | --- | --- | --- | --- |
| Detector | **YOLOX-s / YOLOX-nano / YOLOX-tiny** (default candidate) | ONNX Runtime / TensorRT | Apache-2.0 | Official Megvii ONNX exports; adapter `OnnxPersonDetector(layout="yolox")` |
| Detector | RT-DETR-L | ONNX Runtime / TensorRT | Apache-2.0 | Transformer detector; good on crowded scenes; heavier |
| Detector | YOLOv8n/s | ONNX Runtime / TensorRT | **AGPL-3.0** | Fast; requires Ultralytics commercial license for this product |
| Pose | **RTMPose-m body7 256x192** (default candidate) | ONNX Runtime | Apache-2.0 | Top-down, uses detector boxes; strong accuracy/latency trade-off; adapter `RtmPoseOnnx` |
| Pose | YOLOv8-pose | ONNX Runtime | **AGPL-3.0** | Same license issue |
| ReID | OSNet x1.0 | ONNX Runtime | MIT | Lightweight person re-identification |
| Face | InsightFace buffalo_l | ONNX Runtime | Code MIT; **weights non-commercial** | Must be replaced or licensed before production |
| Tracker | ByteTrack (in-house IoU two-pass) | Python | MIT idea; our implementation | `edge_ai/tracking/iou_tracker.py` |

"Default candidate" means only that the edge adapters support the model out of the box and its license is permissive; it is **not chosen** until it passes the benchmark below on gym footage. The pairing YOLOX + RTMPose is the license-clean starting point; the AGPL rows stay listed for comparison only.

## License-clean default candidates: sources and adapters

| Model | Where to get it | File / input | Pre-/post-processing implemented by the adapter |
| --- | --- | --- | --- |
| YOLOX-s (or -nano / -tiny for weak hardware) | GitHub releases of [Megvii-BaseDetection/YOLOX](https://github.com/Megvii-BaseDetection/YOLOX/releases), tag `0.1.1rc0`: `yolox_s.onnx`, `yolox_nano.onnx`, `yolox_tiny.onnx` | 1x3x640x640 (s), 1x3x416x416 (nano/tiny); output 1xNx85 raw | BGR 0..255, aspect-preserving resize pasted top-left on a 114 canvas; grid decode with strides 8/16/32 (`xy = (out + grid) * stride`, `wh = exp(out) * stride`), `score = obj * cls`, person = class 0, NMS, undo resize. `layout="yolox"` / `EDGE_DETECTOR_LAYOUT=yolox` |
| RTMPose-m body7 | OpenMMLab ONNX SDK zip on `download.openmmlab.com` (`mmpose/v1/projects/rtmposev1/onnx_sdk/rtmpose-m_simcc-body7_pt-body7_420e-256x192-…zip`, linked from the mmpose RTMPose README); use the `end2end.onnx` inside | 1x3x256x192; outputs `simcc_x` 1x17x384, `simcc_y` 1x17x512 | Crop from the detector/track box padded ×1.25 and fixed to the 3:4 aspect ratio (affine warp), RGB, mean [123.675, 116.28, 103.53] / std [58.395, 57.12, 57.375]; SimCC argmax / split ratio 2.0, score = min of the two axis maxima (mmpose `get_simcc_maximum`); keypoints mapped back to frame pixels; COCO-17 order for `edge_ai/pose/angles.py`. `EDGE_POSE_KIND=rtmpose` (or `auto`) |

Weights' training data (COCO; for body7 also AI Challenger, CrowdPose, MPII, sub-JHMDB, Halpe, PoseTrack18) carries its own terms: the legal review is recorded in `model_versions.license` before production.

**Download + SHA-256 procedure** (details and PowerShell commands in [`services/edge-ai/models/README.md`](../../services/edge-ai/models/README.md)):

1. Download from the official URL above into an empty scratch folder; extract the RTMPose zip there; never execute anything from it.
2. Compute the SHA-256 (`Get-FileHash -Algorithm SHA256`, lowercased) of the `.onnx` actually loaded.
3. Record URL, release tag / archive name, date and hash in `services/edge-ai/models/README.md` and in `model_versions` (`checksum_sha256`, `license`), then move the file into `services/edge-ai/models/`.
4. Smoke-test on an authorised frame: `python -m edge_ai.tools.detect_image --detector models/yolox_s.onnx --layout yolox --pose models/rtmpose-m_body7_256x192.onnx --pose-kind rtmpose --image frame.jpg --out annotated.png --repeat 20` (prints providers, per-stage p50/p95 latency, boxes, knee/elbow angles).
5. Only then run the benchmark procedure below; the measured numbers go into `model_versions.benchmark`.

Also to evaluate: NVIDIA DeepStream (PeopleNet + BodyPose3DNet) under the NVIDIA AI Enterprise terms if the deployment hardware qualifies; it would replace the OpenCV decode path with hardware decode (NVDEC) and batched TensorRT inference.

## Benchmark procedure

1. **Footage**: ≥ 2 hours per camera position, authorised, with consent of everyone visible, spanning peak and off-peak. Store on the edge only; delete after labelling per retention policy.
2. **Labels**: person boxes every 10th frame (detection/tracking), identity across cameras for ≥ 20 members, per-rep timestamps for ≥ 500 reps across the catalogue exercises, set boundaries, equipment occupancy intervals. Two independent labellers; disagreement adjudicated.
3. **Metrics**:
   - Detection: AP50 on persons, recall at the operating threshold.
   - Tracking: IDF1, ID switches per hour, per camera.
   - Cross-camera: identity precision (wrong-member attributions must be **zero** at the chosen threshold; recall is the tunable).
   - Reps: per-exercise precision/recall of rep events (±500 ms tolerance), set count accuracy, rest interval error.
   - Occupancy: start/end error seconds.
   - Latency: p50/p95 end-to-end frame→event on the target GPU with N concurrent streams; GPU utilisation and memory.
4. **Decision**: Apache/MIT-licensed models are preferred; AGPL/non-commercial weights are acceptable only with a purchased license recorded in `model_versions.license`.
5. **Record**: write results into `model_versions.benchmark` (json) and this document, with hardware, driver, dataset id and date.

## Operating thresholds (initial, to be calibrated)

| Parameter | Initial | Where |
| --- | --- | --- |
| Detector confidence | 0.35 | `OnnxPersonDetector.conf_threshold` |
| Track confirm hits / max age | 3 / 30 frames | `IouTracker` |
| ReID association threshold / ambiguity margin | 0.55 / 0.08 cosine | `GlobalIdentityRegistry` |
| Face match threshold / margin | 0.62 / 0.06 cosine | `FaceMatcher` |
| Identity bind threshold | 0.85 | `IDENTITY_BIND_THRESHOLD` |
| Rep dedup window | 700 ms | `REP_DEDUP_WINDOW_MS` |
| Zone enter dwell / exit grace | 2 s / 5 s | constants |
