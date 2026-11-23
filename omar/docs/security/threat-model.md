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
| Processing a non-consenting person's face | `ConsentGate` fails closed (stale cache → deny); server re-checks in `ingest_events` |
| Recording in prohibited areas | Cameras configured in `cameras` have purpose/zones; zones of type `exclusion` suppress processing; policy in `privacy-egypt.md` forbids private areas entirely |

### B3 — Edge → cloud
| Threat | Control |
| --- | --- |
| Forged events | HMAC-SHA256 over device_id, batch_id, sent_at and sha256(events) with per-device secret; constant-time compare; generic 401 |
| Replay | `ingest_batches` PK on batch_id; ±300 s clock window; event_id idempotency |
| Secret compromise | `issue_device_secret()` rotation with 24 h overlap; audit logged; secrets table has **no** RLS policies (service role only) and forced RLS |
| Cross-tenant injection (device claims another branch) | `ingest_events` derives branch from the device row and rejects mismatched `branch_id`, foreign cameras, equipment, members |
| Oversized/malformed payloads | 2 MB body cap, ≤500 events/batch, Zod validation, per-event savepoints |

### B4 — Cloud data
| Threat | Control |
| --- | --- |
| Cross-member data access | RLS: `members.user_id = auth.uid()` chains through sessions → usage → activities → sets → reps; tests in `tests/security/rls.test.ts` |
| Cross-tenant staff access | `staff_roles` scoped by organization + optional branch; helper functions `has_branch_role`/`is_branch_staff` |
| Privilege escalation via role grant | Only owners may grant `owner`; admin writes audited |
| Service-role key in browser | Only used in Edge Functions/worker; `loadServerEnv` refuses `NEXT_PUBLIC_*SERVICE_ROLE*`; CI secrets scan |
| Shared report link guessing | 24-byte random token, 30-day expiry, RPC returns summary + first name only |
| Mass export | PostgREST `max_rows = 1000`; audit log on sensitive tables |

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
- Supabase Vault for device secrets (currently raw in a locked table).
- CSP headers and Subresource Integrity on Vercel.
- Presentation-attack detection model selection and threshold (`presentation_attack_score` is carried but no PAD model is chosen yet).
- Pen test of ingest function and RLS by a third party before pilot expansion.
- Secret scanning (gitleaks) and Bandit run in CI; Dependabot to be enabled on the GitHub repo.
