# PRIVACY.md — privacy requirements for the AI side (Phase 5, P5.7)

**Status: requirements only.** No backend, database, accounts or deployment exist yet (deferred
to the closing phases). This document states what any future backend must do with the data the
AI pipeline touches, and records what the local service already does. It is engineering guidance,
not legal advice. Data-protection law (e.g. the EU GDPR, Egypt's Personal Data Protection Law
No. 151 of 2020) may apply depending on where users and servers are; get a legal review before
any deployment.

## 1. What data the AI side handles

| data | example | sensitivity |
|---|---|---|
| Raw video | a phone recording of a set | **High**: identifies the person (face, body, surroundings, sometimes other people) |
| Derived 2D/3D pose | `(T,17,3)` keypoints | **High**: body shape and motion are personal data even without the video |
| Analysis result | rep timings, form indicators, guard status | Medium: performance data; becomes personal once linked to an account |
| Technical metadata | fps, resolution, content hash, model versions, timings | Low on its own |

The form indicators are coach-attention prompts, **not** medical diagnoses. Every result carries
that warning, and no feature may present them as injury or health assessments.

## 2. Requirements for any future backend

1. **Video retention.** Delete the uploaded video once analysis finishes, by default. Keeping it
   (e.g. so a user can replay a set) needs explicit opt-in, a stated retention period, and
   user-initiated deletion that also removes backups within a bounded time.
2. **Derived poses** get the same treatment as video. Store results by default, not pose arrays.
3. **Results** linked to an account must be exportable and deletable on request.
4. **Logs** carry job ids, statuses, durations and error categories only. Never video content,
   frames, original file names (they often contain people's names), or pose data. Access logs
   with IP addresses are kept only briefly.
5. **Transport and storage.** TLS in transit; encryption at rest for anything stored;
   least-privilege access. The deferred Supabase design already plans row-level security
   (`supabase/migrations/*_rls.sql`), to be reviewed at the closing phase.
6. **No training on user data** without separate, explicit consent. The rule that protects the
   final-test cohort applies to all user data by default: it is never used for training, tuning,
   threshold or model selection.
7. **No third-party processing.** Videos and poses are processed only by this pipeline on
   infrastructure the project controls. No external vision APIs.
8. **Recordings made for validation** (the Phase 6 fresh-athlete dataset): written consent per
   person covering purpose, who sees the footage, retention, and the right to withdraw. Store
   consent records separately from the footage, and keep footage out of git (already enforced by
   `.gitignore`; see UNSEEN_ATHLETE_DATASET_SPEC.md).
9. **Third parties in frame.** The tracker locks onto one subject, but other people can appear in
   the video, so retention rules apply to the whole file, not just the athlete.

## 3. What the local service already does (docs/AI_SERVICE.md)

- Never uses or stores the client's file name; uploads are saved under random names.
- Deletes each uploaded video when its job finishes (success or failure). Queued uploads are
  deleted on DELETE and at shutdown, and the private upload directory is removed at shutdown.
  `GYM_AI_KEEP_UPLOADS=1` exists for debugging only.
- Results contain a content hash, never a path or name (asserted by a test). They are held in
  memory only and expire after `GYM_AI_JOB_TTL_S` (default 1 h). Clients can DELETE them earlier.
- Error messages returned to clients never contain server paths. Unexpected errors are opaque to
  the client and logged server-side with the job id only.
- Binds to localhost by default and warns when bound elsewhere, because there is no
  authentication.
- **No telemetry from the product path.** Ultralytics posts anonymous usage events (Google
  Analytics: task, model name, image counts, timings, CPU/GPU names; no images or keypoints) after
  every predict call while its global `sync` setting is on, which is the default. Found 2026-10-10:
  it was on in this environment, so earlier CLI/service runs sent such events.
  `ai.core.pose.YoloTopK` now switches it off for its process (`disable_ultralytics_analytics`;
  the user's global settings are untouched; regression test in `ai/tests/test_pose.py`). The Docker
  image also sets `YOLO_OFFLINE=1`.
- **No network calls at all from the product detector (2026-10-10).** A socket-level audit found
  one more outbound call: importing Ultralytics runs an online check (DNS lookups of
  `one.one.one.one` / `dns.google`; no payload). `YoloTopK` now defaults `YOLO_OFFLINE=1` before
  that import (an explicit user setting wins), so the product path makes **zero** socket calls,
  DNS included; `test_product_detector_makes_no_network_calls` checks this in a fresh interpreter.
- **Development machine (owner decision 2026-10-10)**: Ultralytics' global `sync` is now **off**
  (`yolo settings sync=False`, in `%APPDATA%\Ultralytics\settings.json`), so research scripts
  that call Ultralytics directly send no analytics either. Verified by the same socket audit on
  the product, research (`from ultralytics import YOLO`) and legacy (`pose.yolo_keypoints`) paths:
  `events.enabled == False` and nothing queued on all three. Research scripts still make the one
  import-time DNS lookup (no payload) unless `YOLO_OFFLINE=1` is set in the shell. On any OTHER
  machine, run the settings command once before using research scripts (docs/REPRODUCIBILITY.md).

Not applicable yet (no persistence or network exposure): encryption at rest, accounts, export
and erasure endpoints, consent management. These are closing-phase work.
