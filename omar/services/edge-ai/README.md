# smartgym-edge-ai

Edge pipeline: camera ingest → detection → tracking → cross-camera identity fusion → equipment association → rep/set counting → durable, signed event sync to Supabase.

```
edge_ai/
  camera/      RtspReader (threaded, reconnecting), probe (Phase 0 tool), onvif_discovery (WS-Discovery), SyntheticFrameSource
  detection/   Detection/Detector protocol, OnnxPersonDetector (YOLO/RT-DETR style exports)
  tracking/    IouTracker (two-pass association, lifecycle, tracking_confidence)
  pose/        COCO-17 Pose, PoseEstimator protocol, YoloPoseOnnx adapter, joint-angle signal extractors
  reps/        RepProfile (from exercise_catalog), RepCounter state machine, SetSegmenter
  fusion/      GlobalIdentityRegistry (ReID + gating, refuses ambiguous merges), OnnxReidEmbedder,
               ZoneAssociator (dwell/grace), RepDeduplicator (cross-camera window)
  identity/    EncryptedTemplateStore (AES-256-GCM, SQLite), FaceMatcher, ConsentGate,
               face_pipeline (Haar/YuNet detectors, ArcFace-style ONNX embedder, explicit no-PAD placeholder),
               EntranceIdentifier, enrollment (enroll/revoke/apply cloud revocations)
  cloud/       DeviceSyncClient (signed GET device-sync → consents, catalogue, zones, revocations; cached offline)
  inference/   OnnxSession (CUDA→CPU provider selection), crop/normalise helpers
  events/      PersistentEventQueue (SQLite WAL, sequences), EventBuilder, SyncClient (HMAC)
  contracts/   events_v1 Pydantic mirror of @smartgym/api-contracts
  pipeline/    Pipeline + CameraWorker orchestration (detector → tracker → ReID → zones → pose → reps → entrance id),
               IdentityWorkoutState, config-sync loop
  tools/       simulate (synthetic end-to-end), default_profiles, evaluate_reps, benchmark
  health.py    /healthz + /metrics
```

## Develop

```bash
python -m venv .venv && .venv/Scripts/pip install -e ".[dev]"     # Linux/macOS: .venv/bin/pip
.venv/Scripts/pytest -q
.venv/Scripts/ruff check .
```

Install the real camera/inference stack with `pip install -e ".[cv]"` (OpenCV, ONNX Runtime GPU, SciPy, onvif-zeep). Unit tests do not need it.

## Run

```bash
cp .env.example .env            # device id/secret/ingest URL
cp config/cameras.example.yaml config/cameras.yaml
cp config/camera-credentials.example.yaml config/camera-credentials.yaml
python -m edge_ai discover --interface 192.168.10.5 --seconds 5     # ONVIF devices on the camera VLAN
python -m edge_ai probe rtsp://192.168.10.21:554/Streaming/Channels/101 --seconds 20
python -m edge_ai sync          # pull consents / catalogue / zone config from the cloud (device-sync)
python -m edge_ai run           # models from EDGE_*_MODEL_PATH; stages without a model are disabled and logged
python -m edge_ai enroll --member <uuid> --image photo1.jpg --image photo2.jpg
python -m edge_ai revoke --member <uuid> --reason member_request
python -m edge_ai simulate      # synthetic workout → events → sync (needs the cloud or the tests/db shim)
python -m edge_ai drain         # flush queued events and exit
```

## Guarantees (tested)

- Every emitted event validates against `events.v1` and the shared fixtures (`tests/test_contracts.py`).
- Sequences are monotonic and survive restarts; acked events are deleted; failed batches back off without loss (`tests/test_queue_and_sync.py`).
- Reps require a full hysteresis cycle within a plausibility window; invisible joints never count; half-observed first reps never count (`tests/test_rep_counter.py`).
- Two cameras watching the same person on the same machine produce one rep stream (`tests/test_pipeline_simulation.py`).
- Ambiguous identity matches are refused, never merged (`tests/test_global_identity.py`).
- Templates are unreadable without the device key; enrollment without consent is refused; withdrawal deletes ciphertext (`tests/test_face_store.py`, `tests/test_device_sync_and_enrollment.py`).
- The ONNX adapters (detector, pose, ReID, face) are exercised through ONNX Runtime with synthetic models: letterboxing, NMS, un-letterboxing, pose→track matching and normalisation are verified (`tests/test_onnx_adapters.py`).
- Real decode path (OpenCV/FFmpeg) is exercised on a generated video file (`tests/test_video_reader.py`).
- Entrance identification binds a consenting member exactly once and never runs on floor cameras (`tests/test_pipeline_entrance.py`).
- The whole chain edge → signed HTTP → `ingest_events` → PostgreSQL is verified in `tests/db/e2e-edge.test.ts`.

## What is NOT here yet

No model **weights** are bundled and no accuracy numbers exist: every adapter is ready for the models chosen in Phase 2/3/4 per `docs/architecture/model-selection.md`, but nothing has been measured on real gym footage. No presentation-attack-detection model is selected (`NoLivenessDetector` reports `null`, never a guess). ONVIF stream-URI resolution needs the `cv` extra (`onvif-zeep`) and a real device.
