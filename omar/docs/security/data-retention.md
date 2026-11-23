# Data retention rules

Defaults live in `public.retention_policies` and are enforced nightly by `apply_retention()` (pg_cron `smartgym-apply-retention`, 03:30). Change a value with a migration or an admin SQL update — never by editing the function.

| Data | Where | Default | Notes |
| --- | --- | --- | --- |
| Raw video | — | **never stored** | Frames exist only in edge RAM during inference |
| Face images | — | **never stored** | Only embeddings are derived |
| Biometric templates (encrypted) | edge SQLite | until revoked | Revoked when the biometric consent is withdrawn or expires, when the membership becomes `cancelled` or `expired`, or when the member is deleted. The device deletes the template when the reference appears in `revoked_template_refs` of its next device sync. |
| Biometric enrollment references | cloud `biometric_enrollments` | lifetime of the member | `revoked_at` mirrors the revocation and can never be cleared; removed with the member |
| Biometric revocation tombstones | cloud `biometric_revocations` | 365 days | Written when an enrollment row disappears (member deleted, row deleted) so the device still learns to delete the template |
| Consent records | cloud `consent_records` | lifetime of membership | Immutable legal evidence; never deleted by the retention job |
| Consent evidence after member deletion | cloud `consent_records_archive` | 5 years (1825 days, `archived_at`) | Copied automatically before any consent record is deleted, including by member deletion; keeps the member id, name, member code, branch and organization with the full consent row. Readable by organization owners/auditors only. |
| Attendance sessions, sets, reports | cloud | lifetime of membership | Deleted with the member (cascade) |
| Repetition events | cloud | 365 days | Aggregates remain in `exercise_sets` and `workout_reports.summary` |
| Person tracks / uncertain segments | cloud | 180 days | Diagnostic |
| Device & camera health logs | cloud | 90 days | Operational |
| Event ingest log / batches | cloud | 30 days | Idempotency window — the edge never retries anything older |
| Audit logs | cloud | 730 days | Extendable under legal hold. Device secrets are audited without the secret (only a 12-character fingerprint of its hash). |
| Edge event queue | edge SQLite | until acked | Alert when depth > 5k |
| Report share links | cloud | none by default | A link exists only after the member creates one (1–30 days, default 7); the member can revoke it; the report itself remains for the member |

## Consent expiry

`expire_consents()` moves granted consents whose `expires_at` has passed to `expired` (revoking biometric references, and ending an active visit when it is the CCTV consent) and revokes the references of ended memberships. pg_cron runs it hourly as `smartgym-expire-consents` where pg_cron is enabled; elsewhere schedule `select public.expire_consents()` at least daily. Consent checks do not wait for the sweep: ingest and the device snapshot compare `expires_at` with the current time.

## Member deletion

Deleting a `members` row:

1. copies every consent record of the member to `consent_records_archive` (reason `member_deleted`, with the deleting user),
2. writes a tombstone in `biometric_revocations` for every enrollment reference and removes those references, so each device deletes the templates on its next sync,
3. cascades to consents, sessions, usage, sets, reps, reports and notification jobs.

Audit log entries referencing the member are retained (they contain the old row data) for the audit period; they keep the organization and branch, so the organization's owners and auditors can still read them. This is a deliberate accountability trade-off to be confirmed by counsel.
