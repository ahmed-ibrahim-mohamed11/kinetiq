# Phase 0 evidence

Commit on-site evidence here so the feasibility gate is auditable:

- `probe-<YYYY-MM-DD>.json` — output of `edge-ai probe --cameras … --seconds 30` (one entry per camera: ok, resolution, codec, measured fps, read failures, open latency).
- `discover-<YYYY-MM-DD>.json` — output of `edge-ai discover` (ONVIF devices, models, service addresses).
- `coverage/` — annotated photos or frame grabs per machine showing the primary joint is visible at both ends of the movement (no faces; blur people).
- `floor-plan.svg|pdf` — metric floor plan used for `branches.floor_plan` and `equipment.position_*_m`.
- `checklist.md` — the completed `docs/operations/phase0-feasibility-checklist.md`.

Nothing is here yet: no authorised camera has been connected. The tools exist and are tested; the measurements do not.
