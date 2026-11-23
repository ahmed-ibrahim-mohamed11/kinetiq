# SMART GYM — DEMO ACCOUNTS & STAGING FIXTURES

This document provides the reference test accounts, credentials, tenant identifiers, and membership plans configured in the Smart Gym staging and development environment.

---

## 1. INSTANT DEMO ACCOUNTS (READY TO LOGIN)

### Account 1: Admin / Staff Portal (Port 3000)
- **Portal URL:** `http://localhost:3000/login`
- **Email:** `admin@smartgym.demo`
- **Password:** `DemoAdmin123!`
- **Role:** `owner` (Full administrative access: Dashboard, Reception Kiosk, Member Directory, Subscriptions, AI Diagnostics, Cameras)
- **Organization:** DEMO Smart Gym (Nasr City Branch)

### Account 2: Member / PWA Portal (Port 3001)
- **Portal URL:** `http://localhost:3001/login`
- **Email:** `member@smartgym.demo`
- **Password:** `DemoMember123!`
- **Member Name:** Youssef Hassan
- **Member Code:** `GYM-YH24K9`
- **Status:** Active Member with VIP subscription
- **Features:** AI Live Workout (`/workout`), QR Code Check-in (`/dashboard`), Workout History (`/history`), Settings & Privacy Consent (`/settings`)

---

## 2. HOW TO RUN ALL 3 SERVICES SAFELY

To run the full stack without PowerShell script execution errors (`npm.ps1 cannot be loaded`) and with optimized CPU consumption:

```bash
# Option A: One-click launcher (starts all 3 in dedicated windows)
START_ALL_DEV.bat

# Option B: Run via command line (using npm.cmd directly)
# Window 1 - Edge AI:
cd services/edge-ai
.venv\Scripts\python.exe -m edge_ai run

# Window 2 - Admin Web (Port 3000):
cd apps/admin-web
npm.cmd run dev

# Window 3 - Member Web (Port 3001):
cd apps/member-web
npm.cmd run dev
```

---

## 3. DEFAULT TENANT & BRANCH

| Entity | Identifier (UUID) | Name / Details |
|---|---|---|
| **Organization** | `a0000000-0000-4000-8000-000000000001` | DEMO Smart Gym (test data) |
| **Branch** | `b0000000-0000-4000-8000-000000000001` | DEMO Branch — Nasr City (Cairo, EG) |
| **Edge Device** | `d0000000-0000-4000-8000-000000000001` | edge-01 (dev laptop) |
| **Device Secret** | `dev-only-device-secret-change-me` | SHA-256 hashed in `edge_device_secrets` |

---

## 2. SEEDED MEMBERS

### Demo Member 1 (Full Consent & Active Membership)
- **Member ID:** `33333333-3333-4333-8333-333333333333`
- **Member Code:** `GYM-TEST-M2A7`
- **Full Name:** Test Member (Ahmed Trainee)
- **Phone:** `+201000000000`
- **Status:** `active`
- **Consents Granted:**
  - `biometric_face_identification`: `55555555-5555-4555-8555-555555555555`
  - `cctv_workout_tracking`: `55555555-5555-4555-8555-555555555556`
  - `data_processing`: `55555555-5555-4555-8555-555555555557`

### Demo Member 2 (Omar — Staging Member)
- **Member ID:** `44444444-4444-4444-8444-444444444444`
- **Member Code:** `GYM-TEST-OMAR`
- **Full Name:** Omar Hassan
- **Phone:** `+201100000001`
- **Status:** `active`
- **Consents Granted:** `biometric_face_identification`, `cctv_workout_tracking`

### Demo Member 3 (Ali — Walk-in / Unenrolled)
- **Member ID:** `55555555-5555-4444-8555-555555555555`
- **Member Code:** `GYM-TEST-ALI3`
- **Full Name:** Ali Mostafa
- **Phone:** `+201200000002`
- **Status:** `active`
- **Consents Granted:** `data_processing` only (No biometric consent; test case for biometric refusal)

---

## 3. SEEDED MEMBERSHIP PLANS (Migration 0013)

| Plan Code | Plan Name | Price (EGP) | Duration | AI Training Included? | Freeze Allowance |
|---|---|---|---|---|---|
| `PLAN_1M` | 1 Month Standard | 1,200.00 | 30 days | ❌ No (Base access) | 0 days |
| `PLAN_3M` | 3 Months Standard | 3,200.00 | 90 days | ❌ No | 7 days (1x) |
| `PLAN_6M` | 6 Months Pro | 5,800.00 | 180 days | 🟢 **YES (AI Included)** | 15 days (2x) |
| `PLAN_12M`| 12 Months VIP | 10,500.00 | 365 days | 🟢 **YES (AI Included)** | 30 days (4x) |
| `PLAN_DAY`| Day Pass | 150.00 | 1 day | ❌ No | 0 days |
| `PLAN_PT_12`| PT (12 Sessions) | 3,600.00 | 45 days | 🟢 **YES (AI Included)** | 5 days (1x) |

---

## 4. SEEDED PROMOTIONS

| Promo Code | Name | Type | Value | Minimum Amount | Status |
|---|---|---|---|---|---|
| `WELCOME10` | New Member Welcome | Percentage | 10.00% | 1,000.00 EGP | Active (`approved`) |
| `AI_FREE_TRIAL` | AI Vision Add-on Trial | AI Add-on Free | Free AI | N/A | Active (`approved`) |
| `ANNUAL_CASH_500`| Annual Cash Discount | Fixed Amount | 500.00 EGP | 9,000.00 EGP | Active (`approved`) |

---

## 5. CAMERA CONFIGURATIONS (`config/cameras.yaml`)

```yaml
cameras:
  - id: "c0000000-0000-4000-8000-000000000001"
    name: "Entrance Kiosk Camera"
    url: "webcam://0" # Uses cv2.CAP_DSHOW on Windows
    purpose: "entrance"
    fps: 20
    inference_fps: 15
    zones: [] # Zone-free entrance mode

  - id: "c0000000-0000-4000-8000-000000000002"
    name: "Gym Floor Workout Camera"
    url: "rtsp://192.0.2.11:554/Streaming/Channels/101"
    purpose: "floor"
    fps: 20
    inference_fps: 20
    zones: [] # Zone-free squat floor mode
```

