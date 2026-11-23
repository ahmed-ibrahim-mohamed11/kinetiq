<h1 align="center">KinetiQ — AI Smart Gym 🏋️‍♂️</h1>
<p align="center"><b>Real-time computer-vision squat assessment + a hybrid edge-AI / cloud platform that turns gym cameras into an automatic workout tracker.</b></p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10-3776AB?style=flat-square&logo=python&logoColor=white" />
  <img src="https://img.shields.io/badge/MediaPipe-Pose-0097A7?style=flat-square&logo=google&logoColor=white" />
  <img src="https://img.shields.io/badge/YOLO11-Pose-111F68?style=flat-square" />
  <img src="https://img.shields.io/badge/OpenCV-5C3EE8?style=flat-square&logo=opencv&logoColor=white" />
  <img src="https://img.shields.io/badge/ONNX_Runtime-005CED?style=flat-square&logo=onnx&logoColor=white" />
  <img src="https://img.shields.io/badge/Next.js-000000?style=flat-square&logo=nextdotjs&logoColor=white" />
  <img src="https://img.shields.io/badge/TypeScript-3178C6?style=flat-square&logo=typescript&logoColor=white" />
  <img src="https://img.shields.io/badge/Supabase-3FCF8E?style=flat-square&logo=supabase&logoColor=white" />
  <img src="https://img.shields.io/badge/PostgreSQL-4169E1?style=flat-square&logo=postgresql&logoColor=white" />
  <img src="https://img.shields.io/badge/Docker-2496ED?style=flat-square&logo=docker&logoColor=white" />
</p>

> 🎓 **Graduation Project** — CSE493 / AIE493 Graduation Project 1, **Alamein International University (AIU)**, built by a six-person team.

---

## ✨ What it does

**1. Squat assessment (AI core)** — a single camera watches a barbell squat, estimates the athlete's pose in real time, segments every repetition and flags form errors instantly, with a live desktop GUI for demos.

**2. Smart-gym platform** — a production-style system around the AI:
- 🎥 **Edge AI service** — RTSP camera ingest, detection, tracking, multi-camera identity fusion, rep & set counting, encrypted on-device biometrics, durable offline queue with HMAC-signed sync to the cloud
- ☁️ **Cloud** — Supabase (PostgreSQL + Row-Level Security, Edge Functions) with an idempotent event-ingest engine, workout reports and notifications
- 🖥️ **Admin web** (Next.js) — members, consents, cameras & zones, equipment, devices, live sessions, reception kiosk, audit log
- 📱 **Member web / PWA** (Next.js) — QR check-in, live workout, history, reports, push notifications, privacy & consent management

## 🏗️ Architecture

```
 Gym cameras (RTSP/ONVIF)
        │
        ▼
 Edge server (GPU, Docker)
   detection ─► tracking ─► identity fusion ─► pose ─► rep/set state machine
        │                                   (consent-gated, encrypted on device)
        ▼  HMAC-signed sync
 Supabase  ─► ingest RPC ─► PostgreSQL (RLS) ─► workout reports ─► notifications
        │
        ▼
 Next.js admin-web (staff)  ·  Next.js member-web (members, PWA)
```

## 📁 Repository layout

| Path | What it is |
|---|---|
| `GYM-AI (2)/GYM-AI/` | **AI research core** — pose estimation (MediaPipe / YOLO11-pose / MotionBERT 3D), repetition segmentation, evaluation harness, squat demo GUI (`squat_demo_gui.py`) |
| `omar/apps/admin-web` | Next.js staff console |
| `omar/apps/member-web` | Next.js member app (PWA) |
| `omar/services/edge-ai` | Python edge pipeline (camera → AI → signed cloud sync) |
| `omar/supabase/` | Database schema, RLS, migrations and Edge Functions |
| `omar/packages/` | Shared contracts, types, validation, UI kit |
| `omar/infrastructure/` | Dockerfiles, edge compose, monitoring |
| `docs/`, `omar/docs/`, `GYM-AI (2)/GYM-AI/docs/` | Architecture, evaluation, security & privacy, deployment, audits |
| `Fitness-AQA/` | Third-party reference: *Fitness-AQA* (ECCV 2022) dataset/code, used for research |
| `*.bat` / `*.ps1` (root) | One-click Windows launchers |

## 🚀 Quick start (Windows)

```powershell
# Squat assessment demo (desktop GUI)
RUN_SQUAT_DEMO.bat

# Full platform: Edge AI (:8088) + Admin web (:3000) + Member web (:3001)
START_ALL_DEV.bat
```

Manual setup — AI core:
```powershell
cd "GYM-AI (2)\GYM-AI"
py -3.10 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python squat_demo_gui.py
```

Platform: Node 22+, Python 3.10+, Docker and the Supabase CLI — see [`omar/README.md`](omar/README.md). Copy `.env.example` → `.env` and fill in your own keys (real secrets are never committed).

## 📊 Status

- ✅ Squat assessment pipeline and live demo GUI
- ✅ Platform software layers built and verified by automated tests (migrations, RLS, ingest engine, edge → cloud end-to-end)
- 🟡 Real-gym validation (authorised cameras, labelled footage, model calibration) in progress

Accuracy figures are reported only where they have been measured — see the evaluation docs.

## 👥 Team & credits

KinetiQ is a **team graduation project** at Alamein International University (six members).
Third-party work used for research is credited in its own folder (e.g. Fitness-AQA, MotionBERT) under its original license.

---
<p align="center">Maintained by <a href="https://github.com/ahmed-ibrahim-mohamed11">Ahmed Ibrahim</a> · AI Engineering, AIU</p>
