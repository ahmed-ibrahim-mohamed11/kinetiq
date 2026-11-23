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

Windows PowerShell 5.1 (use `py -3.10` or a real Python 3.10+ if `python` is the Microsoft Store stub):

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m mypy edge_ai
```

Linux/macOS:

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pytest -q
.venv/bin/ruff check .
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
python -m edge_ai revoke --member <uuid> --reason member_request   # takes effect in a running `run` at once
python -m edge_ai revoke --undecryptable                           # after a key change (reason key_rotation)
python -m edge_ai simulate [--cameras cameras.yaml]   # synthetic workout → events → sync (cloud or tests/db shim)
python -m edge_ai drain         # flush queued events and exit
```

Cloud URLs must be `https://` (plain `http://` only for loopback hosts, or with `EDGE_ALLOW_INSECURE_HTTP=1` in development).
In production keep the biometric key out of the data volume: `EDGE_BIOMETRIC_KEY_FILE=/run/secrets/edge_biometric_key`
(a root-only file / Docker secret holding the base64 key) takes precedence over `EDGE_BIOMETRIC_KEY_B64`.

Simulation overrides (synthetic data; defaults reproduce the seed-based scenario): `EDGE_SIM_MEMBER_ID`,
`EDGE_SIM_CONSENT_ID` (the member's `cctv_workout_tracking` record), `EDGE_SIM_CHECKIN=qr|skip`
(skip = already checked in at the kiosk, bind without `identity.checkin`), `EDGE_SIM_CHECKOUT=edge|skip`
(skip = the member checks out in the app). Camera/zone/equipment ids come from `--cameras`.

## Privacy and robustness behaviour

- **Exclusion zones** (`type: exclusion`, from cameras.yaml or drawn in the admin app): a detection whose foot point,
  or most of whose box, lies in one is dropped before tracking — never tracked, ReID-embedded, pose-estimated or
  face-identified (`detections_excluded` in /healthz).
- **Face identification** runs only on `entrance` cameras (anywhere in view when no entrance zone is drawn) and inside
  entrance zones of `mixed` cameras; a `mixed` camera without an entrance zone never identifies. Nothing is detected or
  embedded while no member could pass the consent gate.
- **Consent gate** staleness (24 h) is measured from the snapshot's `generated_at`; a cached snapshot never looks
  fresher than when the cloud produced it, and consent `expires_at` is enforced offline. Members whose
  `cctv_workout_tracking` consent disappears (or when the cache goes stale) are unbound: the workout in progress is
  closed, the track ends and the person continues as an anonymous track.
- **Snapshot cache** (`data/device-sync.json`) holds no member roster and is AES-GCM encrypted with a key derived from
  the biometric key; without that key only configuration (cameras, zones, catalogue) is cached. Consents are not kept in
  any other file.
- **Templates** are reconciled with the cloud registry after every successful, non-cached sync: refs the cloud revoked,
  and refs it no longer lists as active whose enrolment event has already been delivered, are deleted. A template whose
  `biometric.enrolled` is rejected is deleted at once. Deletion never decrypts, uses SQLite `secure_delete` and
  truncates the WAL. A running matcher reloads its gallery as soon as any process changes the store.
- **Key rotation**: change the key, run `edge-ai revoke --undecryptable` (emits `biometric.revoked`
  reason `key_rotation`), then re-enroll members. Undecryptable rows are skipped for matching and counted in
  `biometric_templates_undecryptable`.
- **Sync**: strict FIFO (nothing overtakes an event that is backing off); rejected events (every C4 `code`) go to the
  queue's `dead_letter` table (30-day retention) and are counted per code in /healthz and /metrics; the batch size
  shrinks on 400/413 and grows back after successes; HTTP 403 `device_retired` stops syncing (queue kept, `device_retired`
  in health), closes the consent gate and disables identification until the device is re-activated and restarted.
- **Durability**: the event queue commits with `synchronous=FULL`, so events survive a power cut (a UPS is still
  recommended for the edge host).

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
- Exclusion zones, mixed-camera identification rules and the closed-gate short-cut (`tests/test_exclusion_and_identification.py`).
- Consent staleness from `generated_at`, encrypted roster-free cache, template reconciliation/orphan clean-up, consent-based
  unbinding, cross-process gallery reload, deletion without decryption, secure delete (`tests/test_consent_and_templates.py`).
- Strict FIFO, C4 codes and dead letter, batch-size recovery, `device_retired`, TLS-only URLs, `synchronous=FULL`
  (`tests/test_sync_robustness.py`); simulation overrides (`tests/test_pipeline_simulation.py`).
- The whole chain edge → signed HTTP → `ingest_events` → PostgreSQL is verified in `tests/db/e2e-edge.test.ts`.

## What is NOT here yet

No model **weights** are bundled and no accuracy numbers exist: every adapter is ready for the models chosen in Phase 2/3/4 per `docs/architecture/model-selection.md`, but nothing has been measured on real gym footage. No presentation-attack-detection model is selected (`NoLivenessDetector` reports `null`, never a guess). ONVIF stream-URI resolution needs the `cv` extra (`onvif-zeep`) and a real device.
