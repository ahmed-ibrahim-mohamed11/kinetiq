# Data retention rules

Defaults live in `public.retention_policies` and are enforced nightly by `apply_retention()` (pg_cron `smartgym-apply-retention`, 03:30). Change a value with a migration or an admin SQL update — never by editing the function.

| Data | Where | Default | Notes |
| --- | --- | --- | --- |
| Raw video | — | **never stored** | Frames exist only in edge RAM during inference |
| Face images | — | **never stored** | Only embeddings are derived |
| Biometric templates (encrypted) | edge SQLite | until consent withdrawal or membership end | Deleted (not flagged) on withdrawal; `biometric_enrollments.revoked_at` mirrors it |
| Consent records | cloud | lifetime of membership + 5 years | Legal evidence; never deleted by retention job |
| Attendance sessions, sets, reports | cloud | lifetime of membership | Deleted with the member (cascade) |
| Repetition events | cloud | 365 days | Aggregates remain in `exercise_sets` and `workout_reports.summary` |
| Person tracks / uncertain segments | cloud | 180 days | Diagnostic |
| Device & camera health logs | cloud | 90 days | Operational |
| Event ingest log / batches | cloud | 30 days | Idempotency window — the edge never retries anything older |
| Audit logs | cloud | 730 days | Extendable under legal hold |
| Edge event queue | edge SQLite | until acked | Alert when depth > 5k |
| Report share links | cloud | 30 days | Token expiry; report itself remains for the member |

Member deletion: deleting a `members` row cascades to consents, sessions, usage, sets, reps, reports, notification jobs; the edge deletes templates on the next consent sync. Audit log entries referencing the member are retained (they contain the old row data) for the audit period — this is a deliberate accountability trade-off to be confirmed by counsel.
