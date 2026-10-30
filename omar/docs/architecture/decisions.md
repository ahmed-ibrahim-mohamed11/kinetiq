# Architecture decision records

## ADR-001 — Edge GPU server processes video; cloud never sees frames
**Decision**: all video decoding and inference run on an on-premises NVIDIA server. Only derived events leave the building.
**Why**: 4–8 continuous 1080p streams are ~20–40 Mbit/s of video and need a resident GPU; serverless (Vercel/Supabase Edge Functions) cannot hold GPU state or long-lived RTSP sockets. It also makes the privacy story simple: no video in the cloud, ever.
**Consequences**: edge hardware per branch; local durability (SQLite queue); device authentication; remote operations tooling (health, metrics, secret rotation).

## ADR-002 — Idempotent, versioned event stream instead of RPC-per-fact
**Decision**: the edge emits `events.v1` envelopes with a UUID `event_id` and monotonic `sequence`; the cloud applies them idempotently in SQL.
**Why**: at-least-once delivery over unreliable internet is far simpler to make correct than exactly-once; a single ingest path means every business rule (consent check, branch ownership) lives once in `ingest_events`.
**Consequences**: edge-local ids (`usage_local_id` etc.) with UNIQUE constraints; replaying a batch is a no-op; schema changes are additive or bump the version.

## ADR-003 — Biometric templates stay on the edge, encrypted; cloud holds references only
**Decision**: face embeddings are AES-256-GCM encrypted in a local SQLite store keyed by a device key; `biometric_enrollments` stores `template_ref` + `key_id` only.
**Why**: Egyptian PDPL treats biometric data as sensitive; keeping templates off the cloud removes cross-border transfer questions for the most sensitive class of data and shrinks breach impact. Consent withdrawal deletes ciphertext immediately.
**Consequences**: enrollment happens at the branch; multi-branch recognition requires explicit, consented template replication (not built).

## ADR-004 — Refuse ambiguous identity merges
**Decision**: when the best and second-best ReID candidates are within `ambiguity_margin`, start a new identity and flag `identity_ambiguous` instead of merging.
**Why**: attributing someone's workout to the wrong member is the worst user-facing failure; a split track merely yields an "unverified" segment.
**Consequences**: more fragments in crowded gyms; the report's `uncertain_segments` surfaces them honestly.

## ADR-005 — Cross-camera rep deduplication at the fusion layer, not per camera
**Decision**: every camera runs its own rep counter; `RepDeduplicator` admits one rep per (identity, equipment) per 700 ms and tracks a primary camera with failover.
**Why**: letting each camera count independently gives redundancy (Camera A occluded → Camera B keeps counting) without double counting.
**Consequences**: window tuned for max human cadence (~1 rep/s); very fast exercises (jump rope) need a per-profile window — supported by extending `RepProfile`.

## ADR-006 — Supabase RLS as the authorization boundary; service role only in trusted processes
**Decision**: browsers use the anon key; staff/member scope is enforced by SQL policies; the service role exists only in Edge Functions and the notification worker.
**Why**: one authorization model enforced at the data layer; UI bugs cannot leak data.
**Consequences**: helper functions (`has_branch_role` etc.) must stay `security definer` + `stable`; RLS tests run against a real database in CI.

## ADR-007 — SQL functions for finalisation and reporting
**Decision**: `finalize_attendance_session` and `build_workout_summary` are PL/pgSQL.
**Why**: finalisation must be atomic with the report insert and the notification enqueue; doing it in one transaction removes a class of partial-failure bugs.
**Consequences**: report schema evolution is a migration; heavy analytics later move to a worker reading the same tables.

## ADR-008 — Rep detection = pose-derived signal + validated state machine (not an end-to-end rep model) for v1
**Decision**: per-exercise hysteresis state machines over joint angles, with plausibility windows and visibility gating.
**Why**: explainable, tunable per machine, no training data needed to start, and the uncertainty behaviour (never count what we did not see) is explicit. Temporal action models are added in Phase 6 behind the same `RepCounter` interface once labelled footage exists.
**Consequences**: camera angle matters (frontal/side views of the primary joint); zone-to-camera assignment is part of commissioning.

## ADR-009 — npm workspaces, not pnpm/turbo
**Decision**: plain npm workspaces.
**Why**: pnpm was unavailable in the build environment; npm 11 workspaces are sufficient for 10 packages and Vercel supports them natively.
**Consequences**: no remote build cache; acceptable at this size.
