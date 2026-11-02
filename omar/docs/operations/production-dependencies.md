# Production dependencies outside the codebase

Last updated: 2026-11-02. Audience: the gym owner, the engineering owner and legal counsel.

This document lists everything the AI Smart Gym platform still needs from the physical site, from model vendors, from infrastructure providers and from lawyers before members can be tracked by cameras. The software side (database, cloud functions, web apps, edge service, notification delivery) is built and verified; its evidence lives in `PROGRESS.md`. Nothing below can be completed by writing code.

Each item says what is needed, why, who owns it, and how we will know it is done.

## 1. Cameras and the gym floor

The edge service has only ever processed synthetic data and public test images. No camera of the gym has been opened.

| Needed | Why | Owner | Done when |
| --- | --- | --- | --- |
| Written authorisation to connect to the existing CCTV, plus the NVR/camera make and model | Legal basis for access, and the RTSP/ONVIF paths differ per vendor | Gym owner | Signed note filed with the project |
| Per camera: RTSP URL, a read-only account, position, field of view, resolution and frame rate | The probe tool needs them; credentials stay on the edge device only (`camera-credentials.yaml`, never in the cloud) | Gym IT | `edge-ai probe` JSON for every camera committed under `docs/phase0/` |
| Minimum stream quality: 1280×720, 12 fps or better, H.264/H.265, stable night lighting | Below this, person detection and pose are unreliable | Gym IT | Probe shows `ok: true`, `measured_fps ≥ 12`, read failures under 1 % |
| Floor plan with equipment positions; every tracked machine visible to at least one camera (two for cross-camera rep de-duplication) | Equipment zones and exercise attribution | Gym management | Zones drawn in the admin app for every machine |
| Entrance camera for face check-in, mounted at face height, 1.5–3 m from the door, even front lighting | Face identification needs a frontal, well-lit face | Gym IT | Enrollment of staff volunteers matches reliably at the door |
| No camera covering changing rooms, toilets, showers or prayer rooms; exclusion zones drawn for any partial view of such doors | Privacy law and member trust; the edge drops people inside exclusion zones before tracking | Gym owner | Walk-through signed off; exclusion polygons configured |
| Camera VLAN reachable from the edge node, and the edge node's clock synchronised (NTP) | Streams must stay on the local network; event times must be correct | Gym IT | Probe succeeds from the edge node; `chrony` reports sync |
| Signage at the entrance and in covered areas (Arabic and English) | Notice obligation for CCTV and biometric processing | Gym owner with counsel | Signs installed, wording approved by counsel |

## 2. Model weights

The inference code supports ONNX models on CPU and GPU. Licence-clean candidates are downloaded for verification only; none is "chosen" until it has been measured on footage from this gym.

| Component | Candidate | Licence | Status | Blocking question |
| --- | --- | --- | --- | --- |
| Person detector | YOLOX-s / YOLOX-nano (Megvii official ONNX release 0.1.1rc0) | Apache-2.0 | Downloaded, SHA-256 recorded below, verified on public test images | Accuracy on gym footage (occlusion by machines, mirrors) |
| Pose | RTMPose-m body7 256×192 (OpenMMLab ONNX SDK) | Apache-2.0 | Downloaded, SHA-256 recorded below, verified on public test images | Joint-angle accuracy per exercise on gym footage |
| Person re-identification across cameras | OSNet x1.0 | Code MIT; the public weights are trained on research datasets (Market-1501, MSMT17) whose licences restrict commercial use | Not downloaded | Need weights trained on commercially usable data, or our own fine-tune |
| Face embedding | ArcFace-family model | The popular InsightFace `buffalo_l` weights are licensed for non-commercial research only | Not downloaded | Buy a commercial licence (InsightFace or a vendor SDK) or choose another provider |
| Face anti-spoofing (presentation-attack detection) | None selected | — | Not started | Required before face check-in goes live; vendor choice |
| Exercise classifier (beyond the equipment default) | None | — | Needs labelled footage | Requires recorded, consented training clips |
| Thresholds (face match, re-identification, rep profiles) | Defaults in code | — | Uncalibrated | Calibrate on enrolled staff volunteers and labelled sets |

Recorded artefacts (downloaded 2026-11-02, kept out of git under `services/edge-ai/models/`):

| File | Source | SHA-256 |
| --- | --- | --- |
| `yolox_s.onnx` (34.2 MB, input 1×3×640×640, output 1×8400×85 raw) | github.com/Megvii-BaseDetection/YOLOX releases 0.1.1rc0 | `c5c2d13e59ae883e6af3b45daea64af4833a4951c92d116ec270d9ddbe998063` |
| `yolox_nano.onnx` (3.5 MB, input 1×3×416×416, output 1×3549×85 raw) | same release | `c789161ed43c8269fcd4e67c67eeeb4e80c622da2eb296a20bc6007bd18a0b7d` |
| `rtmpose-m_simcc-body7_256x192.onnx` (51.8 MB, outputs simcc_x 384 bins, simcc_y 512 bins) | download.openmmlab.com rtmposev1 onnx_sdk zip `rtmpose-m_simcc-body7_pt-body7_420e-256x192-e48f03d0_20230504.zip` (zip SHA-256 `f7fbb6c5c11a1bb70f3d445e4ddec5d144ea89ad5649c081ef976f9b24a0b741`) | `5c0a4bf67953e6d2ac43ce15e77dc9d5d354ae18430a47d2c5963a7bc5683e3c` |

## 3. Infrastructure and accounts

| Needed | Why | Owner | Done when |
| --- | --- | --- | --- |
| Edge GPU computer per branch (for about 8 cameras: an NVIDIA RTX 4060/4070-class desktop or a Jetson Orin NX 16 GB), Ubuntu 22.04, NVIDIA driver 550 or newer, Docker with the NVIDIA container toolkit, UPS, full-disk encryption, ideally a TPM for the biometric key | Video never leaves the gym; inference runs on site | Gym owner (purchase), engineering (setup) | `docs/deployment/edge-device.md` checklist completed and the published image `ghcr.io/omarahmedvibe210-droid/ai-smart-gym-edge-ai` running with `/healthz` green |
| Vercel **Pro** plan | The Hobby plan the apps run on today is limited to non-commercial personal use under Vercel's terms | Gym owner (billing) | Team plan shows Pro |
| Supabase paid plan (Pro) with daily backups, and point-in-time recovery if the budget allows | Free projects pause after a period of inactivity and have weaker backup guarantees; confirm the current plan in Dashboard → Billing | Gym owner (billing) | Plan upgraded; first restore drill recorded in `docs/operations/runbook.md` |
| Custom SMTP for Supabase Auth (for example Resend SMTP) with a verified sending domain | Supabase's built-in mailer only delivers to the project's own team members and is heavily rate-limited, so member invitations, magic links and password resets will not reach members without it | Engineering + domain owner | A test member receives an invitation e-mail |
| E-mail notification channel: Resend API key and `EMAIL_FROM` on a verified domain | Web Push is live; e-mail is the fallback for members without push | Engineering | `RESEND_API_KEY` and `EMAIL_FROM` set as function secrets |
| Custom domains for both apps | Professional URLs and stable Auth redirect allow-list | Gym owner | DNS records added in Vercel; Supabase Auth URLs updated |
| Staging Supabase project | Preview deployments currently have no backend on purpose, so they cannot be used for testing | Engineering | Preview deployments point at staging |
| Monitoring and on-call: who receives edge-offline and error alerts | Operations | Gym owner + engineering | Contacts listed in `docs/operations/runbook.md` |

## 4. Legal (Egypt — Personal Data Protection Law No. 151 of 2020)

These questions are for counsel. The engineering design assumes the strictest reasonable reading and keeps biometric templates on the gym's own edge device, but it cannot replace a legal opinion.

1. **Biometric data is sensitive data.** Confirm the licence or permit the gym needs from the Personal Data Protection Center to process it, and the status of the law's executive regulations.
2. **Consent wording.** Approve the consent texts shown in the apps (face identification, CCTV workout tracking, data processing, marketing), in Arabic and English, and the policy version strings stored with each consent record.
3. **Cross-border transfer.** Member and workout data is stored by Supabase in eu-west-2 (London, United Kingdom); the web apps run on Vercel with region fra1 (Frankfurt, EU). Confirm whether a transfer licence is required, or whether hosting must move.
4. **Retention schedule.** Approve the periods in `docs/security/data-retention.md`, including how long consent records are kept as evidence.
5. **Minors.** Confirm the rule for members under 18 (parental consent; face identification disabled).
6. **Staff on camera.** Employees also appear in footage; confirm notice and consent obligations towards them.
7. **Processor agreements.** Data-processing agreements with Supabase, Vercel and the e-mail provider.
8. **Impact assessment.** A data-protection impact assessment before the pilot.

## 5. What is verified versus what is not

The live verification status of every software component (database, cloud functions, web apps, owner sign-in, GitHub and automatic Vercel deployments, notification delivery, end-to-end member session, real-model inference on public test images) is recorded with its evidence in `PROGRESS.md`; this document only covers what code cannot provide.

Not verifiable until the dependencies above are met: camera streams, accuracy on gym footage, face identification with a commercially licensed model and liveness check, multi-camera tracking with licensed re-identification weights, and legal approval.
