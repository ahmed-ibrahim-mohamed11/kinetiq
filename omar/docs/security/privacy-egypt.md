# Privacy and legal considerations (Egypt) — engineering notes, not legal advice

This document records the privacy requirements the system is engineered to meet and the open questions that **must be answered by qualified legal counsel before production use**. It is an input to legal review, not a substitute for it. Where a control is not implemented yet, the table says so.

## Applicable frameworks to evaluate

- **Law No. 151 of 2020 (Personal Data Protection Law, PDPL)** and its executive regulations (pending/issued status to be confirmed by counsel). Biometric data is expressly *sensitive personal data*: processing requires explicit written consent and a license/permit from the Personal Data Protection Centre; cross-border transfer of personal data requires a license and an adequate level of protection in the destination.
- **Constitution of Egypt (2014) Art. 57** — privacy of private life; relevant to CCTV monitoring.
- **Civil Code / Penal Code provisions** on image rights and unlawful recording.
- **Labour law** implications for staff who are also recorded.
- **Consumer protection** rules on disclosure of automated processing in membership contracts.

## How the system is engineered for these requirements

| Requirement | Implementation |
| --- | --- |
| Explicit, informed, revocable consent for biometrics | `consent_records` holds `policy_version`, the capturing staff user (`captured_by_staff_id`, always set to the signed-in staff user by the database) and server-side `granted_at`. The admin form requires an explicit acknowledgment checkbox; the record has an `informed_acknowledged_at` column for it (the admin app must send it — until then the checkbox is enforced in the UI only). Records are immutable: the only changes the database accepts are granted → withdrawn (with server time) and granted → expired. Withdrawal is one action for the member. |
| Biometric template deletion | The cloud holds only enrollment *references* (`biometric_enrollments`). A reference is revoked when the biometric consent is withdrawn or expires, when the membership becomes `cancelled`/`expired`, or when the member is deleted (a tombstone in `biometric_revocations` survives the deletion). Every revoked reference is sent to the device in `revoked_template_refs` on its next sync, and the device deletes the template (`EncryptedTemplateStore`). Revocations cannot be undone, and staff can only revoke references (RPC `revoke_biometric_enrollment`), not create or edit them. |
| Non-biometric alternative | QR (member app) and NFC/staff check-in produce identical sessions and reports |
| Separate consent for CCTV-based workout tracking | A face/edge check-in (`identity.checkin`) is refused by `ingest_events` without a granted, unexpired `cctv_workout_tracking` consent. Staff, NFC and QR check-ins do **not** require it (the visit is recorded), but since migration 0008 `ingest_events` attaches no CCTV-derived fact (tracks, equipment usage, sets, reps) to the session of a member without that consent. Withdrawing it during a visit ends the visit immediately as `abandoned`, with no workout report and no notification. |
| Data minimisation | No video stored; no face images stored; templates are embeddings, encrypted, edge-only; the cloud holds derived workout facts. The device sync snapshot carries no member names or member codes: only member ids with their biometric/CCTV consent record ids and expiry. |
| Purpose limitation | Events carry only exercise facts; `marketing` and `data_processing` consents are separate and never sent to the edge |
| Cross-border transfer | Biometrics never leave the gym (edge). Member identity, attendance and workout data are stored in Supabase project region **eu-west-2 (London, United Kingdom)**; the web apps run on Vercel region **`fra1` (Frankfurt, EU)**, so personal data is processed in two jurisdictions outside Egypt (UK and EU). Counsel must assess both destinations (see open question 2). |
| Retention and deletion | `retention_policies` + `apply_retention()`; member deletion cascades to sessions and reports, but consent records are first copied to `consent_records_archive` and kept 5 years as evidence. Report share links exist only when the member creates one (1–30 days, revocable). See `data-retention.md`. |
| Accountability | `audit_logs` records changes to members, consents, biometric references, staff roles, cameras, devices and device credentials (never the raw device secret). Organization owners and auditors read their organization's entries; branch-scoped owners/auditors read their branch's entries. |
| Transparency | Member app privacy page; report shows uncertain segments rather than hiding automated inferences |
| Access and portability | Members see all their data through RLS; export endpoint planned in Phase 7 |
| Security of processing | See `threat-model.md` |
| Prohibited areas | Cameras in changing rooms, bathrooms, prayer rooms, treatment rooms or other private spaces are **never** to be connected. The commissioning checklist requires sign-off on camera positions. `camera_zones.zone_type = 'exclusion'` can be drawn and is synced to the edge, but **the edge pipeline does not yet suppress detection, tracking or face matching inside exclusion zones**; until it does, a camera must be positioned so that no private doorway is visible at all. |
| Children | Members under 18 require guardian consent; the UI should block biometric enrollment for minors (date of birth field to be added in Phase 3) |

## Signage and notices

Physical signage at the entrance must state that CCTV-based workout tracking operates, who the controller is, how to opt out (QR mode), and where the policy is published. The signage version should match `policy_version` in consents.

## Open questions for counsel (blocking before pilot)

1. Is a PDPL license required for the gym (controller) and for the platform operator (processor) for biometric processing? Timeline?
2. Cross-border transfer: the database is in the UK (Supabase eu-west-2, London) and the web tier in the EU (Vercel fra1, Frankfurt). Is a transfer licence required for each, does either destination offer an adequate level of protection, and should the project move to an EU region (one jurisdiction) or to hosting in Egypt?
3. Required content and language (Arabic) of consent forms and signage.
4. Retention maxima for CCTV-derived data under Egyptian law vs. the 365-day default here, and whether the 5-year consent evidence archive (which keeps the member's name and member code) is the right period.
5. DPIA (data protection impact assessment) requirement and format.
6. Staff monitoring: are trainers/front-desk staff visible to cameras considered data subjects requiring separate notice/consent?
7. Face matching on entrance cameras computes an embedding for every person passing (including non-members) before the consent gate discards non-consenting matches; nothing is stored. Is signage a sufficient basis for this transient processing?
