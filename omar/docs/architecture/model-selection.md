# Model selection protocol

No model is chosen because it is popular. Each component is selected on **measured** results from the Phase 2/4/6 benchmarks on authorized footage from the actual gym cameras. Until then, `model_versions.benchmark` is NULL and the rows are labelled `candidate`.

## Candidates registered (see `supabase/seed.sql`)

| Component | Candidate | Runtime | License | Notes |
| --- | --- | --- | --- | --- |
| Detector | RT-DETR-L | ONNX Runtime / TensorRT | Apache-2.0 | Transformer detector; good on crowded scenes; heavier |
| Detector | YOLOv8n/s | ONNX Runtime / TensorRT | **AGPL-3.0** | Fast; requires Ultralytics commercial license for this product |
| Pose | RTMPose-m | ONNX Runtime | Apache-2.0 | Top-down, uses detector boxes; strong accuracy/latency trade-off |
| Pose | YOLOv8-pose | ONNX Runtime | **AGPL-3.0** | Same license issue |
| ReID | OSNet x1.0 | ONNX Runtime | MIT | Lightweight person re-identification |
| Face | InsightFace buffalo_l | ONNX Runtime | Code MIT; **weights non-commercial** | Must be replaced or licensed before production |
| Tracker | ByteTrack (in-house IoU two-pass) | Python | MIT idea; our implementation | `edge_ai/tracking/iou_tracker.py` |

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
