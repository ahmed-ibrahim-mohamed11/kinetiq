# KinetiQ Mobile Camera LAN & HTTPS Setup Guide

## Purpose
Mobile browsers (Apple Safari on iOS and Google Chrome on Android) strictly require a **Secure Context** (`window.isSecureContext === true`) before exposing camera hardware via `navigator.mediaDevices.getUserMedia`. When accessing the Smart Gym web application over a local gym Wi-Fi network (e.g. `https://192.168.1.3:3001`), untrusted self-signed certificates or plain HTTP will trigger security blocks, disabling mobile camera access.

This guide provides the complete setup, certificate trust instructions for iOS and Android devices, and verification procedures.

---

## 1. Architecture Overview

- **Local Host IP:** `192.168.1.3` (or current LAN host IP)
- **HTTPS Port:** `3001` (`apps/member-web`)
- **Root Certificate Authority (CA):** `kinetiq-ca.crt` (Subject Alternative Names include `127.0.0.1`, `localhost`, `192.168.1.3`, `*.local`)
- **Server Certificate:** `server.crt` signed directly by `kinetiq-ca.crt`
- **Zero Loopback Rule:** Handsets communicating over LAN never query `127.0.0.1`. All Edge AI broker endpoints dynamically resolve to `window.location.hostname` (`192.168.1.3:8088`).
- **Decoupled Architecture:** "Phone Camera" mode runs completely client-side in the browser via WebRTC/MediaPipe Pose and has zero dependency on central Edge AI camera brokers.

---

## 2. In-App Mobile Setup Portal

Navigate directly from any handset on the gym Wi-Fi to:
```text
https://<LAN_IP>:3001/cert
Example: https://192.168.1.3:3001/cert
```

This page provides:
1. **Interactive QR Code:** Quickly scan with your phone camera to open the setup URL.
2. **Direct Certificate Download:** One-click download button for `kinetiq-ca.crt`.
3. **Live Hardware Camera Probe:** "Test Handset Camera" button verifying permissions, active stream acquisition, and resolution.
4. **Platform-Specific Instructions:** Guided steps for iOS Safari and Android Chrome.

---

## 3. iOS Installation & Trust Instructions (iPhone / iPad)

### Step 1: Download the Profile
1. On your iPhone, open **Safari** (do not use Chrome for profile installation on iOS).
2. Browse to `https://192.168.1.3:3001/cert`.
3. Tap **Download Root CA Certificate (`kinetiq-ca.crt`)**.
4. Safari displays a prompt: *"This website is trying to download a configuration profile. Do you want to allow this?"* Tap **Allow**.
5. Once downloaded, tap **Close**.

### Step 2: Install Profile
1. Open the iOS **Settings** app.
2. Near the top, tap **Profile Downloaded** (or go to **General** > **VPN & Device Management**).
3. Tap **KinetiQ Gym Root CA**.
4. Tap **Install** in the top right corner.
5. Enter your iPhone passcode and tap **Install** to confirm.

### Step 3: Enable Full SSL Trust (Critical)
Apple requires manual root trust enablement:
1. In **Settings**, go to **General** > **About**.
2. Scroll to the bottom and tap **Certificate Trust Settings**.
3. Under **Enable full trust for root certificates**, find **KinetiQ Gym Root CA**.
4. Toggle the switch to **ON** (Green).
5. A confirmation dialog appears: *"Root Certificate - Enabling full trust for a certificate will enable any third party using it to trust the certificates issued by it"*. Tap **Continue**.

### Step 4: Verify Camera Access
1. Return to Safari and refresh `https://192.168.1.3:3001/cert`.
2. Notice the lock icon in the address bar is secure with no certificate warnings.
3. Tap **Test Handset Camera** to confirm `getUserMedia` opens without errors.
4. Open `https://192.168.1.3:3001/workout` and start your AI workout session.

---

## 4. Android Installation & Trust Instructions (Chrome / Samsung Internet)

### Step 1: Download Certificate
1. On your Android phone, open **Chrome**.
2. Browse to `https://192.168.1.3:3001/cert`.
3. Tap **Download Root CA Certificate (`kinetiq-ca.crt`)**.
4. The file `kinetiq-ca.crt` will be saved in your Downloads folder.

### Step 2: Install as a Trusted CA
1. Open Android **Settings**.
2. Tap **Security** or **Security & Privacy** > **More security settings** > **Encryption & credentials**.
3. Tap **Install a certificate** > **CA certificate**.
4. Tap **Install anyway** on the security warning.
5. Select `kinetiq-ca.crt` from your Downloads folder.
6. Enter your PIN or fingerprint if prompted.
7. Confirmation *"CA certificate installed"* appears.

### Step 3: Verify Camera Access
1. Return to Chrome and navigate to `https://192.168.1.3:3001/cert`.
2. Tap **Test Handset Camera**.
3. When prompted, select **Allow** for camera permissions.
4. Verify the success banner appears: `Camera probe PASSED: ... Ready for AI workout!`.

---

## 5. Development & Production Run Commands

### Starting Member Web with HTTPS LAN Binding:
```batch
cd apps\member-web
npx next dev --experimental-https --experimental-https-key ./certificates/server.key --experimental-https-cert ./certificates/server.crt -H 0.0.0.0 -p 3001
```
Or use the convenience root launcher:
```batch
RUN_MEMBER_DEV.bat
```

### Starting Central Edge AI Service (Optional for Member Mobile Camera):
```batch
RUN_EDGE_AI.bat
```
*(Note: Phone Camera operates completely independently from the Edge AI broker).*

---

## 6. Automated Handset Test Drill

To verify all 8 mobile handset requirements automatically in headless Chromium mobile handset emulation:
```bash
node tests/live/test_mobile_handset_verification.mjs
```

### Verified Checks in Drill:
1. **Secure Context over LAN:** Validates `window.isSecureContext === true` on `https://192.168.1.3:3001/cert`.
2. **Valid CA Endpoint:** Validates `GET /kinetiq-ca.crt` delivers valid PEM payload.
3. **Camera Probe UI:** Tests in-browser `getUserMedia` probe execution and green status.
4. **Member Handset Authentication:** Performs 1-click mobile login and redirects to `/workout`.
5. **Phone Camera Independence:** Initiates phone camera with 1280x720 video, playsinline, and `readyState === 'live'`.
6. **Front/Rear Camera Switching:** Executes `flipCamera()` and confirms active video stream.
7. **Hardware Track Cleanup:** Calls `stopCamera()` and confirms `video.srcObject === null` and tracks stopped.
8. **Zero Loopback Enforcement:** Verifies 0 requests made to `127.0.0.1` during the handset session.

