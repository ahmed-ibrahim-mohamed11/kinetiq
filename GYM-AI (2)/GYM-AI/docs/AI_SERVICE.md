# AI_SERVICE.md — local analysis service (Phase 5, P5.4)

A small FastAPI layer over the Mode A (after-set) pipeline, `ai/core/pipeline.py`. It is **local
only**: no authentication, no database, no UI, no deployment. Those belong to the closing phases,
which start only on the owner's "START CLOSING PHASE". This document is the contract those phases
will plug into.

## Run

```powershell
python -m ai.service                      # 127.0.0.1:8000, CPU
python -m ai.service --device cuda        # or set GYM_AI_DEVICE=cuda
```

Startup loads the detector, the lifter and the fingerprint-verified rep-segmentation artifact
once. A missing checkpoint or a tampered artifact makes startup fail with an explicit error
(run `python -m ai.scripts.download_checkpoints` first). Interactive API docs:
`http://127.0.0.1:8000/docs`.

## Endpoints

| method | path | success | errors |
|---|---|---|---|
| POST | `/v1/analyze` (multipart field `video`) | **202** `JobInfo`, state `queued` | 400 empty upload · 413 over the size limit · 415 non-video content type · 422 missing `video` field · 503 queue full (`Retry-After: 30`) |
| GET | `/v1/jobs/{job_id}` | 200 `JobInfo`; `result` is an `AnalysisResult` v1.0 once `state == "succeeded"` | 404 unknown, deleted or expired |
| DELETE | `/v1/jobs/{job_id}` | 204 | 404 unknown · 409 while running |
| GET | `/v1/health` | 200 `{status, device, queue_depth}` | — |
| GET | `/v1/model` | 200 `{schema_version, exercise, modes, versions}` (artifact version + fingerprint, detector/lifter names + sha256, code commit) | — |

`JobInfo` = `{job_id, state: queued|running|succeeded|failed, created_at, started_at,
finished_at, error, result}` (strict schema, unknown keys rejected).

## Semantics

- **succeeded** means a valid `AnalysisResult` was produced. That includes explicit abstentions
  (`NO_PERSON`, `VIDEO_TOO_SHORT`, `INSUFFICIENT_CONFIDENCE`, `LOST_TRACK`,
  `AMBIGUOUS_SUBJECT`, `UNSUPPORTED_EXERCISE`): an abstention is a correct answer, not a failure.
  Same contract as the CLI's exit code 0.
- **failed** means the input could not be analysed: undecodable video, invalid frame rate, no
  frames, or longer than the after-set limit. `error` gives the reason, never a server path.
  Unexpected exceptions are logged server-side and reported only as
  `"internal error while analysing the video"`.
- One worker thread processes jobs in submission order. Model instances are never shared across
  threads, and the queue is bounded (`GYM_AI_MAX_PENDING`).
- The result schema is `ai/schemas/analysis_result.v1.json` (generated from `ai/core/schema.py`).
  `ai.core.schema.to_edge_events` maps a result onto the existing edge-event contract
  (`shared/event-schemas`, `rep_completed`) for the future DB.

## Settings (environment variables)

| variable | default | meaning |
|---|---|---|
| `GYM_AI_DEVICE` | `cpu` | `cpu` or `cuda` (CPU is the validated reference; CUDA differs numerically within the bounds in LIVE_MODE.md) |
| `GYM_AI_MAX_UPLOAD_MB` | 500 | request body limit, enforced by Content-Length and while streaming |
| `GYM_AI_MAX_VIDEO_S` | 600 | longer videos fail the job (protects the single worker) |
| `GYM_AI_MAX_PENDING` | 4 | queued jobs beyond this get 503 |
| `GYM_AI_JOB_TTL_S` | 3600 | finished jobs (and their results) are forgotten after this |
| `GYM_AI_KEEP_UPLOADS` | `0` | `1` keeps uploaded videos after processing (debugging only) |
| `GYM_AI_UPLOAD_DIR` | private temp dir | where uploads are written; the private default is deleted at shutdown |

## Privacy behaviour (requirements: docs/PRIVACY.md)

- The client's file name is never used or stored. Uploads are saved as `<random>.mp4`.
- The uploaded video is deleted as soon as its job finishes (success or failure) unless
  `GYM_AI_KEEP_UPLOADS=1`. Queued uploads are deleted on DELETE and at shutdown.
- Results carry a content hash (`video.source_sha256`), never a path or name. Logs carry job ids
  and durations only.
- Results live in memory only and expire after `GYM_AI_JOB_TTL_S`.

## Not implemented (deliberately)

- **Authentication / network exposure.** Binding to anything but localhost prints a warning. Auth
  belongs with deployment (deferred).
- **Live-mode streaming endpoint** (optional in the master prompt). Not built, for three reasons:
  (1) Mode B realtime capability is not measured yet (P4.3–P4.5 are blocked on AC power);
  (2) a live session would need its own detector/lifter instances, since the single-worker design
  never shares model instances across threads, roughly doubling memory;
  (3) the product's live mode is expected to run in-process next to the camera
  (`ai.core.live.LiveStreamer`), where shipping frames over HTTP would only add latency.
  Revisit after P4.5. Recorded in DECISION_LOG.md.

## Tests

`ai/tests/test_service.py`. Endpoint behaviour is tested with an injected analyzer (fast):
success, upload deletion, keep-uploads, input-error and internal-error jobs, 404/204/409, bad
uploads (400/415/422), the size limit (Content-Length and chunked), queue full (503) and drain, and
private-dir cleanup at shutdown. One slow test runs the real pipeline over HTTP on a dev video and
requires the result to equal a direct `analyze_video` call.
