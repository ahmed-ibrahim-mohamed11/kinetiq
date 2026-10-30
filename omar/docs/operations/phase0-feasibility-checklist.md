# Phase 0 — technical feasibility and discovery checklist

Fill in on site. Each row needs evidence (probe JSON, photo, drawing) committed under `docs/phase0/`.

## Cameras

| # | Item | Result | Evidence |
| --- | --- | --- | --- |
| C1 | Make/model/firmware of every camera | | |
| C2 | RTSP URL template, codec (H.264/H.265), resolution, fps, bitrate | | `edge-ai probe` JSON |
| C3 | ONVIF supported? (Profile S/T) | | |
| C4 | NVR in the path? Sub-stream available? | | |
| C5 | Camera credentials rotated from defaults; viewer-only account created | | |
| C6 | Camera VLAN isolated from internet and member Wi-Fi | | network diagram |
| C7 | Time sync source for cameras (NTP) | | |

## Coverage

| # | Item | Result |
| --- | --- | --- |
| V1 | Entrance camera: frontal face view at 1.5–1.9 m height, ≥ 80 px inter-ocular at the choke point, lighting ≥ 200 lux, no backlight | |
| V2 | Each tracked machine visible from ≥ 1 camera with the primary joint (elbow/knee) unoccluded at the start and end positions | |
| V3 | Overlapping cameras per machine (for redundancy) | |
| V4 | Blind spots between entrance and floor | |
| V5 | Private areas (changing rooms, bathrooms, prayer rooms) **not** covered — confirm and photograph | |
| V6 | Mirrors / reflections that create phantom people | |

## Floor and equipment

| # | Item | Result |
| --- | --- | --- |
| F1 | Floor plan with metric dimensions (→ `branches.floor_plan`) | |
| F2 | Equipment inventory with positions (→ `equipment`) and catalogue exercise mapping | |
| F3 | Peak concurrent members and peak per camera view | |
| F4 | Machines where two users share a frame (e.g. dual cable stations) | |

## Compute and network

| # | Item | Result |
| --- | --- | --- |
| N1 | Edge host spec (GPU model/VRAM, CPU, RAM, disk, NICs) | |
| N2 | Internet uplink bandwidth and typical outage frequency | |
| N3 | Where the host sits physically; power protection (UPS) | |

## Compliance

| # | Item | Result |
| --- | --- | --- |
| P1 | Legal review started (see `docs/security/privacy-egypt.md` open questions) | |
| P2 | Consent form text (Arabic/English) and `policy_version` | |
| P3 | Signage locations | |
| P4 | Supabase region decision | |

## Exit criteria

- All cameras probe `ok` at ≥ 12 fps decode, ≥ 720p.
- Every target machine has at least one qualifying camera view (V2).
- No camera covers a private area (V5 signed off by gym management).
- Hardware sizing agreed (N1) and legal review in progress (P1).
