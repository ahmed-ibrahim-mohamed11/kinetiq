# Threat model and security controls

Scope: cameras, edge server, cloud (Supabase, Vercel), web apps, notification worker, operators. Method: STRIDE per trust boundary, controls mapped to code.

## Assets (in order of sensitivity)

1. Biometric templates (edge only).
2. Live and historical camera video (edge, transient in memory; never stored).
3. Member identity + attendance + workout facts (cloud).
4. Camera credentials, device secrets, service-role key, VAPID private key.
5. Staff accounts.

## Trust boundaries and controls

### B1 — Cameras → edge (camera VLAN)
| Threat | Control |
| --- | --- |
| Default/weak camera passwords, credential sniffing | Credentials only in `camera-credentials.yaml` (0600, read-only mount); rotation at commissioning; RTSP over TCP; RTSPS/SRTP where the camera supports it; VLAN isolated from internet and member Wi-Fi |
| Rogue device on camera VLAN | Switch port security / 802.1X where available; edge host firewall allows RTSP only to known camera IPs |
| Spoofed stream (replaying a recording) | Out of scope for v1; mitigated operationally by camera tamper alarms; `camera.status` anomalies alert |

### B2 — Edge server
| Threat | Control |
| --- | --- |
| Template theft from disk | AES-256-GCM with device key from `EDGE_BIOMETRIC_KEY_B64` (TPM/KMS-held in production); AAD binds ciphertext to template_ref+member |
| Credential leakage in logs | `redact_url()`; structured logging never prints URLs with credentials or secrets |
| Container escape / privilege | Non-root user, `no-new-privileges`, read-only rootfs, tmpfs, only health port bound to localhost |
| Processing a non-consenting person's face | `ConsentGate` holds only granted biometric/CCTV consents from the device sync and denies when its cache is older than its max age (the age must be measured from the snapshot's `generated_at`, not from the last local re-apply — edge item R1-18). Server side, `ingest_events` refuses face check-ins and enrollments without a granted, unexpired biometric consent and attaches no CCTV-derived fact without a CCTV consent (migration 0008). Embeddings are still computed transiently for everyone passing an entrance camera before the gate decides (see `privacy-egypt.md`, open question 7). |
| Recording in prohibited areas | Policy in `privacy-egypt.md` forbids private areas entirely and the commissioning checklist signs off camera positions. Zones of type `exclusion` can be configured and are synced, but the edge pipeline does **not** yet suppress processing inside them (edge item R1-16): do not rely on them. |
| Templates of people who left | Revocation reaches the device through `revoked_template_refs` for withdrawn or expired biometric consent, cancelled/expired membership and deleted members (tombstones in `biometric_revocations`); the device snapshot also lists `active_template_refs` so the edge can delete anything else |

### B3 — Edge → cloud
| Threat | Control |
| --- | --- |
| Forged events | HMAC-SHA256 over device_id, batch_id, sent_at and sha256(events) with per-device secret; constant-time compare; generic 401 |
| Replay | `ingest_batches` PK on batch_id; ±300 s clock window; event_id idempotency |
| Secret compromise | `issue_device_secret()` rotation with 24 h overlap; rotations are audited **without** the secret (id, device, 12-character hash fingerprint, timestamps only; older audit copies were scrubbed in migration 0009); the secrets table has RLS enabled with **no** policies and all anon/authenticated grants revoked, so only the service role and SECURITY DEFINER functions read it (RLS is not FORCEd) |
| Cross-tenant injection (device claims another branch) | `ingest_events` derives branch from the device row and rejects mismatched `branch_id`, foreign cameras, equipment, members |
| Oversized/malformed payloads | 2 MB body cap, ≤500 events/batch, Zod validation, per-event savepoints |

### B4 — Cloud data
| Threat | Control |
| --- | --- |
| Cross-member data access | RLS: `members.user_id = auth.uid()` chains through sessions → usage → activities → sets → reps; tests in `tests/security/rls.test.ts` |
| Cross-tenant staff access | `staff_roles` scoped by organization + optional branch; helper functions `has_branch_role`/`is_branch_staff`; organization-level powers (`has_org_role`) count only org-wide roles (`branch_id is null`). A member's or staff role's organization must be the organization of its branch (trigger), so rows and audit entries cannot be planted in another tenant. |
| Privilege escalation via role grant | Only org-wide owners may grant, change or remove `owner` rows; admins cannot delete or demote owners; the last owner of an organization cannot be removed; branch-scoped owners/admins manage only non-owner, non-admin roles of their own branch. Every change is audited. |
| Staff taking over a member account | `members.user_id` cannot be set or changed from the browser (trigger); accounts are linked by the service-role activation flow |
| Forged or resurrected biometric references | Browser roles can only read `biometric_enrollments` (owner/admin) and revoke through `revoke_biometric_enrollment`; inserts come from ingest only; a revocation can never be cleared and the reference columns cannot change |
| Consent tampering | `consent_records` rows are immutable except granted → withdrawn (server time) or granted → expired; `captured_by_staff_id` is always the inserting staff user; deleted records are archived for 5 years |
| RLS helper oracle | Internal helpers (`session_member`, `branch_org`, …) are not executable by anon |
| Service-role key in browser | Only used in Edge Functions/worker; `loadServerEnv` refuses `NEXT_PUBLIC_*SERVICE_ROLE*`; CI secrets scan |
| Shared report link guessing | Links exist only when the member creates one: 24-byte random token, 1–30 days (default 7), revocable, creation and revocation audited; the RPC returns the summary and first name only |
| Mass export | PostgREST `max_rows = 1000`; audit log on sensitive tables |
| Undetected changes | `audit_logs` covers members, consents, biometric references, staff roles, cameras, devices and device secrets, with the organization and branch resolved per table. Org-wide owners and auditors read their organization's entries; branch-scoped owners/auditors read only their branch's entries; members and other staff read none. |

### B5 — Web apps
| Threat | Control |
| --- | --- |
| Session hijacking | Supabase SSR cookies (httpOnly), refresh rotation enabled, HSTS, `X-Frame-Options: DENY` |
| XSS → token theft | React escaping; no `dangerouslySetInnerHTML`; CSP to be added at Vercel edge in Phase 8 |
| CSRF on server actions | Next.js server actions are origin-checked; destructive actions confirm in UI |
| Brute force | Supabase Auth rate limits; no self sign-up; magic link alternative |
| QR token replay | 1-minute HMAC tokens ±1 min, verified server-side under the **staff** session so branch role is enforced |

### B6 — Notifications
| Threat | Control |
| --- | --- |
| Push payload leaking workout data | Payload carries title + deep link only; data loads behind auth |
| VAPID key leak | Server env only; rotate by regenerating keys and re-subscribing |

## Residual risks / planned work (Phase 8)
- Supabase Vault for device secrets (currently raw in a locked table; the audit log no longer holds copies).
- Exclusion-zone suppression in the edge pipeline (configured zones are not enforced yet).
- `informed_acknowledged_at` is stored when the admin app sends it; make it mandatory for browser inserts once the app does.
- Hosting regions: Supabase eu-west-2 (London, UK) and Vercel fra1 (Frankfurt, EU) — cross-border transfer review with counsel.
- CSP headers and Subresource Integrity on Vercel.
- Presentation-attack detection model selection and threshold (`presentation_attack_score` is carried but no PAD model is chosen yet).
- Pen test of ingest function and RLS by a third party before pilot expansion.
- Secret scanning (gitleaks) and Bandit run in CI; Dependabot to be enabled on the GitHub repo.
