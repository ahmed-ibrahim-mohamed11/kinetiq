# Privacy and legal considerations (Egypt) — engineering notes, not legal advice

This document records the privacy requirements the system is engineered to meet and the open questions that **must be answered by qualified legal counsel before production use**. It is an input to legal review, not a substitute for it.

## Applicable frameworks to evaluate

- **Law No. 151 of 2020 (Personal Data Protection Law, PDPL)** and its executive regulations (pending/issued status to be confirmed by counsel). Biometric data is expressly *sensitive personal data*: processing requires explicit written consent and a license/permit from the Personal Data Protection Centre; cross-border transfer of personal data requires a license and an adequate level of protection in the destination.
- **Constitution of Egypt (2014) Art. 57** — privacy of private life; relevant to CCTV monitoring.
- **Civil Code / Penal Code provisions** on image rights and unlawful recording.
- **Labour law** implications for staff who are also recorded.
- **Consumer protection** rules on disclosure of automated processing in membership contracts.

## How the system is engineered for these requirements

| Requirement | Implementation |
| --- | --- |
| Explicit, informed, revocable consent for biometrics | `consent_records` with `policy_version`, who captured it, timestamp; UI requires an explicit acknowledgment (`informed_acknowledged: true`); withdrawal is one action for the member and triggers template deletion on the edge (`on_consent_withdrawn` + `EncryptedTemplateStore.revoke_member`) |
| Non-biometric alternative | QR (member app) and NFC/staff check-in produce identical sessions and reports |
| Separate consent for CCTV-based workout tracking | `cctv_workout_tracking` consent is checked by `ingest_events` for every check-in; without it no session is created |
| Data minimisation | No video stored; no face images stored; templates are embeddings, encrypted, edge-only; the cloud holds derived workout facts |
| Purpose limitation | Events carry only exercise facts; `marketing` consent is separate and unused by the pipeline |
| Cross-border transfer minimisation | Biometrics never leave Egypt (edge). Workout/identity data is hosted in Supabase — **region must be selected with counsel**; the nearest regions are EU (Frankfurt) — a transfer license may be required. Vercel region pinned to `fra1`. |
| Retention and deletion | `retention_policies` table + `apply_retention()`; member deletion cascades; reports expire from shared links after 30 days |
| Transparency | Member app privacy page; report shows uncertain segments rather than hiding automated inferences |
| Access and portability | Members see all their data through RLS; export endpoint planned in Phase 7 |
| Security of processing | See `threat-model.md` |
| Prohibited areas | Cameras in changing rooms, bathrooms, prayer rooms, treatment rooms or other private spaces are **never** to be connected. Commissioning checklist requires sign-off on camera positions; `camera_zones.zone_type = 'exclusion'` masks any partially visible private doorway |
| Children | Members under 18 require guardian consent; the UI should block biometric enrollment for minors (date of birth field to be added in Phase 3) |

## Signage and notices

Physical signage at the entrance must state that CCTV-based workout tracking operates, who the controller is, how to opt out (QR mode), and where the policy is published. The signage version should match `policy_version` in consents.

## Open questions for counsel (blocking before pilot)

1. Is a PDPL license required for the gym (controller) and for the platform operator (processor) for biometric processing? Timeline?
2. Supabase hosting region and whether a cross-border transfer license is required for non-biometric member data; alternatives (self-hosted Supabase in Egypt) if so.
3. Required content and language (Arabic) of consent forms and signage.
4. Retention maxima for CCTV-derived data under Egyptian law vs. the 365-day default here.
5. DPIA (data protection impact assessment) requirement and format.
6. Staff monitoring: are trainers/front-desk staff visible to cameras considered data subjects requiring separate notice/consent?
