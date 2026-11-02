# Phase 0 evidence

Commit on-site evidence here so the feasibility gate is auditable:

- `probe-<YYYY-MM-DD>.json` — output of `edge-ai probe --cameras … --seconds 30` (one entry per camera: ok, resolution, codec, measured fps, read failures, open latency).
- `discover-<YYYY-MM-DD>.json` — output of `edge-ai discover` (ONVIF devices, models, service addresses).
- `coverage/` — annotated photos or frame grabs per machine showing the primary joint is visible at both ends of the movement (no faces; blur people).
- `floor-plan.svg|pdf` — metric floor plan used for `branches.floor_plan` and `equipment.position_*_m`.
- `checklist.md` — the completed `docs/operations/phase0-feasibility-checklist.md`.

Present so far:

- `benchmark-2026-11-02-rtx5050-directml.json` — raw ONNX Runtime latency of the licence-clean candidate models (YOLOX-s, YOLOX-nano, RTMPose-m) on the development laptop's RTX 5050 via DirectML versus CPU, on one public COCO image. It proves the inference path and the speed budget (≈ 15–18 ms detector + ≈ 5–7 ms pose per frame on this GPU), **not** accuracy on gym footage.

Still missing: every on-site artefact above. No authorised camera has been connected; the camera tools exist and are tested, the measurements do not.
