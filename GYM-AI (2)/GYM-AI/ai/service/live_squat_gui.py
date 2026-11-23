"""KinetiQ Smart Gym Vision Platform — Multi-Camera, Multi-Person Operational Backend & UI.

Run with:
    python -m ai.service.live_squat_gui --host 0.0.0.0 --port 8080 --https-port 8443
"""
from __future__ import annotations

import argparse
import base64
import math
import os
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from typing import Any, Dict, List, Optional

os.environ.setdefault("MPLCONFIGDIR", os.environ.get("TEMP", "."))

import cv2
import numpy as np
import uvicorn
from fastapi import FastAPI, Header, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
WORKSPACE_ROOT = os.path.abspath(os.path.join(REPO_ROOT, "..", ".."))
EDGE_AI_ROOT = os.path.join(WORKSPACE_ROOT, "omar", "services", "edge-ai")
for _p in (REPO_ROOT, EDGE_AI_ROOT):
    if os.path.exists(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

from ai.core.smart_gym_engine import (
    Detection,
    SmartGymPlatformHub,
)

app = FastAPI(title="KinetiQ Smart Gym — Multi-Camera Vision & Biomechanics Platform")

HUB_LOCK = threading.RLock()
HUB: Optional[SmartGymPlatformHub] = None
STREAM_SOURCE_BY_CAM: Dict[str, str] = {"cam-a": "browser_cam", "cam-b": "browser_cam"}
CAP_BY_CAM: Dict[str, Optional[cv2.VideoCapture]] = {"cam-a": None, "cam-b": None}
CAP_KEY_BY_CAM: Dict[str, str] = {"cam-a": "", "cam-b": ""}


def get_lan_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


LAN_IP = get_lan_ip()

SIM_VIDEOS = {
    "sim_good": os.path.join(
        REPO_ROOT,
        "ai",
        "data",
        "mendeley",
        "Dataset Subject-wise",
        "subject_002",
        "squat",
        "good",
        "front",
        "subject_002_squat_good_front.mp4",
    ),
    "sim_valgus": os.path.join(
        REPO_ROOT,
        "ai",
        "data",
        "mendeley",
        "Dataset Subject-wise",
        "subject_002",
        "squat",
        "bad",
        "front",
        "subject_002_squat_bad_front.mp4",
    ),
    "sim_shallow": os.path.join(
        REPO_ROOT,
        "ai",
        "data",
        "mendeley",
        "Dataset Subject-wise",
        "subject_003",
        "squat",
        "bad",
        "front",
        "subject_003_squat_bad_front.mp4",
    ),
}


def get_hub() -> SmartGymPlatformHub:
    global HUB
    with HUB_LOCK:
        if HUB is None:
            HUB = SmartGymPlatformHub(prefer_detector="yolox_s")
        return HUB


def _open_capture(source_key: str) -> Optional[cv2.VideoCapture]:
    if source_key == "webcam":
        for backend in (cv2.CAP_MSMF, cv2.CAP_ANY, cv2.CAP_DSHOW):
            for idx in (0, 1, 2):
                try:
                    cap = cv2.VideoCapture(idx, backend)
                    if not cap.isOpened():
                        cap.release()
                        continue
                    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
                    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
                    for _ in range(4):
                        ok, frame = cap.read()
                        if ok and frame is not None and frame.mean() > 2.5:
                            return cap
                    cap.release()
                except Exception:
                    pass
        cap = cv2.VideoCapture(0)
        return cap if cap.isOpened() else None

    if source_key in SIM_VIDEOS and os.path.exists(SIM_VIDEOS[source_key]):
        return cv2.VideoCapture(SIM_VIDEOS[source_key])
    return None


def mjpeg_generator(camera_id: str = "cam-a"):
    """Stream annotated MJPEG frames for server-side camera or benchmark test videos."""
    hub = get_hub()
    while True:
        src = STREAM_SOURCE_BY_CAM.get(camera_id, "browser_cam")
        if src in ("paused", "browser_cam"):
            cam_obj = hub.cameras.get(camera_id)
            if cam_obj is not None and cam_obj.last_annotated_bgr is not None:
                ok_enc, jpg = cv2.imencode(".jpg", cam_obj.last_annotated_bgr, [int(cv2.IMWRITE_JPEG_QUALITY), 78])
                if ok_enc:
                    yield (
                        b"--frame\r\n"
                        b"Content-Type: image/jpeg\r\n\r\n" + jpg.tobytes() + b"\r\n"
                    )
            time.sleep(0.08)
            continue

        cap = CAP_BY_CAM.get(camera_id)
        if cap is None or CAP_KEY_BY_CAM.get(camera_id) != src or not cap.isOpened():
            if cap is not None:
                cap.release()
            cap = _open_capture(src)
            CAP_BY_CAM[camera_id] = cap
            CAP_KEY_BY_CAM[camera_id] = src
            hub.reset_camera(camera_id)

        if cap is None or not cap.isOpened():
            canvas = np.zeros((360, 640, 3), dtype=np.uint8)
            cv2.putText(
                canvas,
                f"[{camera_id.upper()}] Camera source '{src}' unavailable — use Browser Stream",
                (28, 180),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.55,
                (100, 195, 255),
                1,
                cv2.LINE_AA,
            )
            ok_enc, jpg = cv2.imencode(".jpg", canvas)
            if ok_enc:
                yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpg.tobytes() + b"\r\n"
            time.sleep(0.25)
            continue

        t_cap0 = time.perf_counter()
        ok, frame = cap.read()
        cap_ms = (time.perf_counter() - t_cap0) * 1000.0
        if not ok or frame is None:
            if src.startswith("sim_"):
                cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                continue
            time.sleep(0.04)
            continue

        hub.process_camera_frame(frame, camera_id=camera_id, capture_ms=cap_ms, decode_ms=0.8, network_ms=0.2)
        cam_obj = hub.cameras[camera_id]
        annotated = cam_obj.last_annotated_bgr if cam_obj.last_annotated_bgr is not None else frame
        ok_enc, jpg = cv2.imencode(".jpg", annotated, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
        if ok_enc:
            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n\r\n" + jpg.tobytes() + b"\r\n"
            )
        time.sleep(0.015)


# ---------------------------------------------------------------------------
# REST API Endpoints
# ---------------------------------------------------------------------------

@app.get("/video_feed")
def video_feed_default():
    return StreamingResponse(
        mjpeg_generator("cam-a"),
        media_type="multipart/x-mixed-replace; boundary=frame",
    )


@app.get("/api/video_feed/{camera_id}")
def video_feed_camera(camera_id: str):
    cid = camera_id if camera_id in ("cam-a", "cam-b") else "cam-a"
    return StreamingResponse(
        mjpeg_generator(cid),
        media_type="multipart/x-mixed-replace; boundary=frame",
    )


@app.post("/api/auth/login")
async def api_login(req: Request):
    body = await req.json()
    email = str(body.get("email", "")).strip()
    password = str(body.get("password", ""))
    user = get_hub().authenticate(email, password)
    if not user:
        return JSONResponse({"ok": False, "error": "Invalid credentials"}, status_code=401)
    return JSONResponse({"ok": True, "user": user})


@app.get("/api/auth/me")
def api_me(authorization: Optional[str] = Header(default=None)):
    token = None
    if authorization and authorization.startswith("Bearer "):
        token = authorization.split(" ", 1)[1].strip()
    return JSONResponse({"ok": True, "user": get_hub().get_user_by_token(token)})


@app.get("/api/telemetry")
def api_telemetry(camera_id: str = "cam-a"):
    hub = get_hub()
    return JSONResponse(hub.get_camera_telemetry(camera_id))


@app.get("/api/cameras/state")
def api_cameras_state():
    hub = get_hub()
    now = time.time()
    return JSONResponse(
        {
            "cam_a": hub.get_camera_telemetry("cam-a", now=now),
            "cam_b": hub.get_camera_telemetry("cam-b", now=now),
            "handoff_log": list(hub.handoff_log)[:25],
            "lan_ip": LAN_IP,
        }
    )


@app.post("/api/analyze_frame")
async def api_analyze_frame(req: Request):
    t_recv = time.perf_counter()
    body = await req.json()
    b64 = body.get("image", "")
    camera_id = str(body.get("camera_id", "cam-a"))
    client_capture_ms = float(body.get("capture_ms", 1.2))
    client_sent_ms = body.get("client_ts_ms")

    if not b64:
        return JSONResponse({"error": "empty image"}, status_code=400)
    if "," in b64:
        b64 = b64.split(",", 1)[1]

    t_dec0 = time.perf_counter()
    raw = base64.b64decode(b64)
    arr = np.frombuffer(raw, dtype=np.uint8)
    frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    decode_ms = (time.perf_counter() - t_dec0) * 1000.0
    if frame is None:
        return JSONResponse({"error": "decode failed"}, status_code=400)

    network_ms = 2.0
    if client_sent_ms is not None:
        try:
            net_diff = (time.time() * 1000.0) - float(client_sent_ms)
            if 0.1 <= net_diff <= 500.0:
                network_ms = float(net_diff)
        except Exception:
            pass

    STREAM_SOURCE_BY_CAM[camera_id] = "browser_cam"
    tel = get_hub().process_camera_frame(
        frame_bgr=frame,
        camera_id=camera_id,
        capture_ms=client_capture_ms,
        decode_ms=decode_ms,
        network_ms=network_ms,
    )
    return JSONResponse(tel)


@app.post("/api/target/select")
async def api_target_select(req: Request):
    body = await req.json()
    camera_id = str(body.get("camera_id", "cam-a"))
    mode = str(body.get("mode", "AUTOMATIC")).upper()
    if mode not in ("AUTOMATIC", "MANUAL", "MEMBER"):
        mode = "AUTOMATIC"
    track_id = body.get("track_id")
    member_id = body.get("member_id")
    x_norm = body.get("x")
    y_norm = body.get("y")

    hub = get_hub()
    cam = hub.cameras.get(camera_id, hub.cameras["cam-a"])
    H, W = (cam.last_frame_bgr.shape[:2]) if cam.last_frame_bgr is not None else (360, 480)
    click_xy = (float(x_norm), float(y_norm)) if (x_norm is not None and y_norm is not None) else None
    sel_tid = cam.select_target(
        mode=mode,  # type: ignore[arg-type]
        track_id=int(track_id) if track_id is not None else None,
        member_id=str(member_id) if member_id else None,
        click_xy_norm=click_xy,
        W=W,
        H=H,
    )
    return JSONResponse(
        {
            "ok": True,
            "camera_id": camera_id,
            "selection_mode": cam.selection_mode,
            "selected_track_id": sel_tid,
            "telemetry": hub.get_camera_telemetry(camera_id),
        }
    )


@app.post("/api/control")
async def api_control(req: Request):
    body = await req.json()
    action = body.get("action", "")
    camera_id = str(body.get("camera_id", "cam-a"))
    hub = get_hub()

    if action == "reset":
        hub.reset_camera(camera_id if body.get("camera_only") else None)
    elif action == "lock_person":
        x_norm = float(body.get("x", 0.5))
        y_norm = float(body.get("y", 0.5))
        cam = hub.cameras.get(camera_id, hub.cameras["cam-a"])
        H, W = (cam.last_frame_bgr.shape[:2]) if cam.last_frame_bgr is not None else (360, 480)
        cam.select_target(mode="MANUAL", click_xy_norm=(x_norm, y_norm), W=W, H=H)
    elif action == "auto_lock":
        cam = hub.cameras.get(camera_id, hub.cameras["cam-a"])
        cam.select_target(mode="AUTOMATIC")
    elif action in ("webcam", "sim_good", "sim_valgus", "sim_shallow", "paused", "browser_cam"):
        STREAM_SOURCE_BY_CAM[camera_id] = action
        if action != "paused":
            hub.reset_camera(camera_id)

    return JSONResponse({"ok": True, "source": STREAM_SOURCE_BY_CAM.get(camera_id, "browser_cam"), "telemetry": hub.get_camera_telemetry(camera_id)})


@app.get("/api/members")
def api_members():
    return JSONResponse({"members": get_hub().list_members()})


@app.post("/api/members/enroll")
async def api_members_enroll(req: Request, authorization: Optional[str] = Header(default=None)):
    token = authorization.split(" ", 1)[1].strip() if (authorization and authorization.startswith("Bearer ")) else None
    user = get_hub().get_user_by_token(token)
    if user.get("role") not in ("Administrator", "Staff"):
        return JSONResponse({"ok": False, "error": "Administrator or Staff role required for biometric enrollment"}, status_code=403)

    body = await req.json()
    member_id = str(body.get("member_id", "member-001"))
    consent_granted = bool(body.get("consent_granted", False))
    camera_id = str(body.get("camera_id", "cam-a"))
    track_id = body.get("track_id")
    try:
        res = get_hub().enroll_member_biometrics(
            member_id=member_id,
            consent_granted=consent_granted,
            camera_id=camera_id,
            track_id=int(track_id) if track_id is not None else None,
        )
        return JSONResponse(res)
    except PermissionError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)


@app.post("/api/members/revoke")
async def api_members_revoke(req: Request, authorization: Optional[str] = Header(default=None)):
    token = authorization.split(" ", 1)[1].strip() if (authorization and authorization.startswith("Bearer ")) else None
    user = get_hub().get_user_by_token(token)
    body = await req.json()
    member_id = str(body.get("member_id", "member-001"))
    if user.get("role") == "Member" and user.get("member_id") != member_id:
        return JSONResponse({"ok": False, "error": "Members may only revoke their own consent"}, status_code=403)
    res = get_hub().revoke_member_consent(member_id)
    return JSONResponse(res)


@app.get("/api/analytics")
def api_analytics(member_id: Optional[str] = None):
    return JSONResponse(get_hub().get_analytics(member_id=member_id))


@app.get("/api/diagnostics")
def api_diagnostics():
    return JSONResponse(get_hub().get_diagnostics())


@app.post("/api/simulate/scenario")
async def api_simulate_scenario(req: Request):
    """Execute deterministic validation scenarios directly through the live SmartGymPlatformHub so the operator
    can inspect multi-person separation, 4-squat counting, and cross-camera handoff in the UI."""
    from ai.tests.test_smart_gym_recovery import (
        make_distinct_embedding,
        make_seated_closeup_keypoints,
        make_standing_or_squat_keypoints,
    )

    body = await req.json()
    scenario = str(body.get("scenario", "multi_person_3"))
    hub = get_hub()
    hub.reset_camera(None)

    base_canvas = np.full((360, 480, 3), (18, 22, 30), dtype=np.uint8)
    t = time.time() - 12.0
    dt = 0.05

    if scenario == "4_squat_reference":
        emb_a = make_distinct_embedding(1)
        for step in range(24 + 4 * 44):
            depth = 0.0 if step < 24 else (0.94 * math.sin(math.pi * ((step - 24) % 44) / 31.0) if ((step - 24) % 44) < 32 else 0.0)
            box_a, kpts_a = make_standing_or_squat_keypoints(cx=240.0, foot_y=330.0, depth_fraction=depth)
            hub.step_detections_for_camera("cam-a", base_canvas.copy(), [Detection(box_a, 0.95, 0)], [kpts_a], [emb_a], t)
            t += dt

    elif scenario == "multi_person_3":
        emb_a = make_distinct_embedding(1)
        emb_b = make_distinct_embedding(2)
        emb_c = make_distinct_embedding(3)
        for step in range(24 + 4 * 44):
            depth_a = 0.0 if step < 24 else (0.93 * math.sin(math.pi * ((step - 24) % 44) / 31.0) if ((step - 24) % 44) < 32 else 0.0)
            box_a, kpts_a = make_standing_or_squat_keypoints(cx=275.0, foot_y=330.0, depth_fraction=depth_a)
            box_b, kpts_b = make_seated_closeup_keypoints(cx=100.0, sho_y=295.0, head_bob_px=12.0 * math.sin(step * 0.25))
            walk_cx = 405.0 + 35.0 * math.sin(step * 0.12)
            box_c, kpts_c = make_standing_or_squat_keypoints(cx=walk_cx, foot_y=310.0, depth_fraction=0.14 * abs(math.sin(step * 0.25)))
            hub.step_detections_for_camera(
                "cam-a",
                base_canvas.copy(),
                [Detection(box_a, 0.94, 0), Detection(box_b, 0.88, 0), Detection(box_c, 0.85, 0)],
                [kpts_a, kpts_b, kpts_c],
                [emb_a, emb_b, emb_c],
                t,
            )
            t += dt

    elif scenario == "cross_camera_handoff":
        emb_m1 = make_distinct_embedding(77)
        hub.enroll_member_biometrics("member-001", consent_granted=True, camera_id="cam-a", explicit_embedding=emb_m1)
        # 4 squats on Camera A
        for step in range(24 + 4 * 44):
            depth = 0.0 if step < 24 else (0.93 * math.sin(math.pi * ((step - 24) % 44) / 31.0) if ((step - 24) % 44) < 32 else 0.0)
            box_a, kpts_a = make_standing_or_squat_keypoints(cx=240.0, foot_y=330.0, depth_fraction=depth)
            hub.step_detections_for_camera("cam-a", base_canvas.copy(), [Detection(box_a, 0.94, 0)], [kpts_a], [emb_m1], t)
            t += dt
        # Handoff to Camera B + 2 more squats on Camera B
        for step in range(24 + 2 * 44):
            depth = 0.0 if step < 24 else (0.93 * math.sin(math.pi * ((step - 24) % 44) / 31.0) if ((step - 24) % 44) < 32 else 0.0)
            box_b, kpts_b = make_standing_or_squat_keypoints(cx=235.0, foot_y=330.0, depth_fraction=depth)
            hub.step_detections_for_camera("cam-b", base_canvas.copy(), [Detection(box_b, 0.95, 0)], [kpts_b], [emb_m1], t)
            t += dt

    return JSONResponse(
        {
            "ok": True,
            "scenario": scenario,
            "cam_a": hub.get_camera_telemetry("cam-a"),
            "cam_b": hub.get_camera_telemetry("cam-b"),
            "analytics": hub.get_analytics(),
            "handoff_log": list(hub.handoff_log)[:15],
        }
    )


# ---------------------------------------------------------------------------
# Premium Operational Platform HTML/JS Single-Page Application
# ---------------------------------------------------------------------------

HTML_PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no" />
<title>KinetiQ Smart Gym — Multi-Camera AI Vision & Biomechanics Command Center</title>
<style>
  :root {
    --bg-base: #07090e;
    --bg-surface: #0d1119;
    --bg-elevated: #131924;
    --bg-card: #161e2c;
    --border-subtle: #1f2a3d;
    --border-active: #06b6d4;
    --text-primary: #f1f5f9;
    --text-secondary: #94a3b8;
    --text-muted: #64748b;
    --accent-cyan: #06b6d4;
    --accent-blue: #3b82f6;
    --status-valid: #10b981;
    --status-warn: #f59e0b;
    --status-fault: #ef4444;
    --font-sans: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    --font-mono: 'JetBrains Mono', 'Fira Code', Consolas, monospace;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    background: var(--bg-base);
    color: var(--text-primary);
    font-family: var(--font-sans);
    min-height: 100vh;
    display: flex;
    flex-direction: column;
    overflow-x: hidden;
  }

  /* Top Command Header */
  .top-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    flex-wrap: wrap;
    gap: 12px;
    padding: 10px 18px;
    background: var(--bg-surface);
    border-bottom: 1px solid var(--border-subtle);
  }
  .brand-cluster {
    display: flex;
    align-items: center;
    gap: 12px;
  }
  .brand-logo {
    width: 32px;
    height: 32px;
    border-radius: 8px;
    background: linear-gradient(135deg, var(--accent-cyan), var(--accent-blue));
    display: flex;
    align-items: center;
    justify-content: center;
    font-weight: 900;
    font-size: 0.95rem;
    color: #041016;
    letter-spacing: -0.5px;
  }
  .brand-title {
    font-size: 1rem;
    font-weight: 800;
    letter-spacing: 0.3px;
    color: var(--text-primary);
  }
  .brand-subtitle {
    font-size: 0.72rem;
    color: var(--text-secondary);
    display: block;
  }

  /* Navigation Tabs */
  .nav-tabs {
    display: flex;
    gap: 6px;
    background: var(--bg-base);
    padding: 4px;
    border-radius: 9px;
    border: 1px solid var(--border-subtle);
  }
  .nav-tab {
    background: transparent;
    color: var(--text-secondary);
    border: none;
    padding: 7px 13px;
    border-radius: 6px;
    font-size: 0.78rem;
    font-weight: 600;
    cursor: pointer;
    transition: all 0.15s ease;
  }
  .nav-tab:hover { color: var(--text-primary); }
  .nav-tab.active {
    background: var(--bg-card);
    color: var(--accent-cyan);
    box-shadow: 0 1px 3px rgba(0,0,0,0.4);
  }

  /* Right Telemetry & Role Bar */
  .header-meta {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
  }
  .pill {
    font-family: var(--font-mono);
    font-size: 0.72rem;
    padding: 4px 10px;
    border-radius: 999px;
    background: var(--bg-elevated);
    border: 1px solid var(--border-subtle);
    color: var(--text-secondary);
  }
  .pill.cyan { color: var(--accent-cyan); border-color: rgba(6, 182, 212, 0.35); }
  .pill.green { color: var(--status-valid); border-color: rgba(16, 185, 129, 0.35); }
  .pill.amber { color: var(--status-warn); border-color: rgba(245, 158, 11, 0.35); }

  /* Action Bar */
  .control-bar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    flex-wrap: wrap;
    gap: 8px;
    padding: 8px 18px;
    background: var(--bg-elevated);
    border-bottom: 1px solid var(--border-subtle);
  }
  .btn-group {
    display: flex;
    align-items: center;
    gap: 6px;
    flex-wrap: wrap;
  }
  .group-label {
    font-size: 0.68rem;
    text-transform: uppercase;
    letter-spacing: 0.8px;
    color: var(--text-muted);
    font-weight: 700;
    margin-right: 4px;
  }
  button.btn {
    background: var(--bg-card);
    color: var(--text-primary);
    border: 1px solid var(--border-subtle);
    padding: 6px 11px;
    border-radius: 6px;
    font-size: 0.76rem;
    font-weight: 600;
    cursor: pointer;
    transition: all 0.14s ease;
  }
  button.btn:hover { border-color: var(--accent-cyan); }
  button.btn.active {
    background: rgba(6, 182, 212, 0.15);
    border-color: var(--accent-cyan);
    color: #67e8f9;
  }
  button.btn.danger {
    background: rgba(239, 68, 68, 0.14);
    border-color: rgba(239, 68, 68, 0.45);
    color: #fca5a5;
  }
  button.btn.success {
    background: rgba(16, 185, 129, 0.15);
    border-color: rgba(16, 185, 129, 0.45);
    color: #6ee7b7;
  }

  /* View Containers */
  .view-section { display: none; padding: 14px 18px; flex: 1; }
  .view-section.active { display: block; }

  /* View A: Live Command Center Grid */
  .command-grid {
    display: grid;
    grid-template-columns: 1.55fr 1fr;
    gap: 14px;
    align-items: start;
  }
  @media (max-width: 1080px) {
    .command-grid { grid-template-columns: 1fr; }
  }

  .video-panel {
    background: #030508;
    border: 1px solid var(--border-subtle);
    border-radius: 12px;
    overflow: hidden;
    position: relative;
  }
  .video-panel-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 8px 14px;
    background: var(--bg-surface);
    border-bottom: 1px solid var(--border-subtle);
    font-size: 0.78rem;
  }
  .viewport-wrap {
    position: relative;
    width: 100%;
    aspect-ratio: 16 / 10;
    background: #04060a;
    display: flex;
    align-items: center;
    justify-content: center;
    overflow: hidden;
  }
  #localVideo, #camBVideo {
    position: absolute;
    width: 100%;
    height: 100%;
    object-fit: contain;
    display: none;
  }
  #hudCanvas {
    position: absolute;
    width: 100%;
    height: 100%;
    object-fit: contain;
    cursor: crosshair;
    z-index: 3;
  }
  #serverStreamImg {
    width: 100%;
    height: 100%;
    object-fit: contain;
    display: none;
    cursor: crosshair;
    z-index: 2;
  }
  .status-strip {
    padding: 10px 14px;
    font-size: 0.84rem;
    font-weight: 700;
    display: flex;
    align-items: center;
    justify-content: space-between;
    background: rgba(16, 185, 129, 0.12);
    border-top: 1px solid rgba(16, 185, 129, 0.35);
    color: #a7f3d0;
  }
  .status-strip.warn {
    background: rgba(245, 158, 11, 0.15);
    border-top-color: rgba(245, 158, 11, 0.45);
    color: #fde68a;
  }
  .status-strip.fault {
    background: rgba(239, 68, 68, 0.18);
    border-top-color: rgba(239, 68, 68, 0.55);
    color: #fecaca;
  }

  /* Right Telemetry Column */
  .telemetry-col {
    display: flex;
    flex-direction: column;
    gap: 12px;
  }
  .panel-card {
    background: var(--bg-surface);
    border: 1px solid var(--border-subtle);
    border-radius: 10px;
    padding: 12px 14px;
  }
  .panel-title {
    font-size: 0.72rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.8px;
    color: var(--text-secondary);
    margin-bottom: 10px;
    display: flex;
    justify-content: space-between;
    align-items: center;
  }

  /* 4-Counter KPI Row */
  .kpi-grid-4 {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 8px;
  }
  .kpi-box {
    background: var(--bg-elevated);
    border: 1px solid var(--border-subtle);
    border-radius: 8px;
    padding: 10px 8px;
    text-align: center;
  }
  .kpi-box .lbl {
    font-size: 0.65rem;
    text-transform: uppercase;
    letter-spacing: 0.6px;
    color: var(--text-secondary);
    font-weight: 700;
  }
  .kpi-box .val {
    font-family: var(--font-mono);
    font-size: 1.95rem;
    font-weight: 800;
    margin-top: 3px;
    line-height: 1.05;
  }
  .kpi-box.total .val { color: var(--text-primary); }
  .kpi-box.valid { border-color: rgba(16, 185, 129, 0.35); }
  .kpi-box.valid .val { color: var(--status-valid); }
  .kpi-box.imperfect { border-color: rgba(239, 68, 68, 0.35); }
  .kpi-box.imperfect .val { color: var(--status-fault); }
  .kpi-box.partial { border-color: rgba(245, 158, 11, 0.35); }
  .kpi-box.partial .val { color: var(--status-warn); }

  /* Selected Trainee Identity & State Grid */
  .trainee-meta-grid {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 8px;
  }
  .meta-cell {
    background: var(--bg-elevated);
    border: 1px solid var(--border-subtle);
    border-radius: 7px;
    padding: 7px 10px;
  }
  .meta-cell .k {
    font-size: 0.66rem;
    color: var(--text-muted);
    text-transform: uppercase;
  }
  .meta-cell .v {
    font-family: var(--font-mono);
    font-size: 0.85rem;
    font-weight: 700;
    color: var(--text-primary);
    margin-top: 2px;
  }

  /* Depth Bar */
  .depth-meter-wrap { margin-top: 10px; }
  .depth-meter-bg {
    width: 100%;
    height: 9px;
    background: var(--bg-base);
    border-radius: 999px;
    overflow: hidden;
    border: 1px solid var(--border-subtle);
    margin-top: 4px;
  }
  .depth-meter-fill {
    height: 100%;
    width: 0%;
    background: linear-gradient(90deg, var(--accent-blue), var(--accent-cyan), var(--status-valid));
    transition: width 0.08s linear;
  }

  /* Active Tracks List (Multi-Person) */
  .track-list {
    display: flex;
    flex-direction: column;
    gap: 6px;
    max-height: 175px;
    overflow-y: auto;
  }
  .track-item {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 7px 10px;
    border-radius: 7px;
    background: var(--bg-elevated);
    border: 1px solid var(--border-subtle);
    font-size: 0.76rem;
    cursor: pointer;
  }
  .track-item:hover { border-color: var(--accent-cyan); }
  .track-item.selected {
    border-color: var(--accent-cyan);
    background: rgba(6, 182, 212, 0.10);
  }

  /* Rep History Log */
  .rep-list {
    display: flex;
    flex-direction: column;
    gap: 6px;
    max-height: 190px;
    overflow-y: auto;
  }
  .rep-row {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 8px 10px;
    border-radius: 7px;
    background: var(--bg-elevated);
    border-left: 3px solid var(--status-valid);
    font-size: 0.76rem;
  }
  .rep-row.incorrect { border-left-color: var(--status-fault); }
  .badge {
    font-family: var(--font-mono);
    font-size: 0.66rem;
    font-weight: 700;
    padding: 2px 7px;
    border-radius: 4px;
    text-transform: uppercase;
  }
  .badge.valid { background: rgba(16, 185, 129, 0.18); color: #6ee7b7; }
  .badge.fault { background: rgba(239, 68, 68, 0.20); color: #fca5a5; }

  /* Dual Camera Grid (View B) */
  .dual-cam-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 14px;
  }
  @media (max-width: 960px) {
    .dual-cam-grid { grid-template-columns: 1fr; }
  }

  /* Data Tables (Views C & D) */
  table.data-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 0.78rem;
  }
  table.data-table th, table.data-table td {
    padding: 8px 10px;
    text-align: left;
    border-bottom: 1px solid var(--border-subtle);
  }
  table.data-table th {
    color: var(--text-secondary);
    font-size: 0.68rem;
    text-transform: uppercase;
    letter-spacing: 0.6px;
  }
  table.data-table td { font-family: var(--font-mono); }
</style>
</head>
<body>

<!-- Top Operational Header -->
<header class="top-header">
  <div class="brand-cluster">
    <div class="brand-logo">KQ</div>
    <div>
      <span class="brand-title">KINETIQ SMART GYM VISION PLATFORM</span>
      <span class="brand-subtitle">Multi-Camera • Multi-Person ByteTrack • 9-State Biomechanics Engine</span>
    </div>
  </div>

  <nav class="nav-tabs">
    <button class="nav-tab active" onclick="switchView('command', this)">A. Live Vision Command Center</button>
    <button class="nav-tab" onclick="switchView('multicam', this)">B. Multi-Camera View (Cam A + Cam B)</button>
    <button class="nav-tab" onclick="switchView('analytics', this)">C. Workout Analytics</button>
    <button class="nav-tab" onclick="switchView('admin', this)">D. Administration & Diagnostics</button>
  </nav>

  <div class="header-meta">
    <span class="pill cyan" id="fpsPill">20.0 FPS | 42 ms</span>
    <span class="pill green" id="backendPill">ONNX YOLOX + RTMPose-m</span>
    <span class="pill amber" id="rolePill">Role: Administrator</span>
    <span class="pill">Phone HTTPS: https://__LAN_IP__:8443</span>
  </div>
</header>

<!-- Global Action & Target Control Bar -->
<div class="control-bar">
  <div class="btn-group">
    <span class="group-label">Active Camera:</span>
    <button class="btn active" id="btnCamA" onclick="selectActiveCamera('cam-a')">Camera A (Rack Main / Mobile)</button>
    <button class="btn" id="btnCamB" onclick="selectActiveCamera('cam-b')">Camera B (Secondary / Laptop)</button>
    <span class="group-label" style="margin-left:8px;">Source:</span>
    <button class="btn active" id="btnFrontCam" onclick="startDeviceCam('user', this)">Browser Front Cam</button>
    <button class="btn" id="btnBackCam" onclick="startDeviceCam('environment', this)">Phone Rear Cam</button>
    <button class="btn" onclick="setServerSource('webcam', this)">Server Host Webcam</button>
    <button class="btn" onclick="setServerSource('sim_good', this)">Recorded Good Squat</button>
    <button class="btn" onclick="setServerSource('sim_valgus', this)">Recorded Valgus Squat</button>
  </div>

  <div class="btn-group">
    <span class="group-label">Target Lock:</span>
    <button class="btn active" id="btnModeAuto" onclick="setSelectionMode('AUTOMATIC')">Auto (Zone + Activity)</button>
    <button class="btn" id="btnModeManual" onclick="setSelectionMode('MANUAL')">Manual (Click Person)</button>
    <button class="btn" id="btnModeMember" onclick="setSelectionMode('MEMBER', 'member-001')">Lock Member: Omar</button>
    <button class="btn success" onclick="runValidationScenario('multi_person_3')">Verify 3-Person Independence</button>
    <button class="btn success" onclick="runValidationScenario('cross_camera_handoff')">Verify Cam A→B Handoff</button>
    <button class="btn danger" onclick="resetCounters()">Reset Session</button>
  </div>
</div>

<!-- ===================================================================== -->
<!-- VIEW A: LIVE VISION COMMAND CENTER                                    -->
<!-- ===================================================================== -->
<section id="view-command" class="view-section active">
  <div class="command-grid">
    <!-- Left: Interactive Video & Anatomical Skeleton Viewport -->
    <div class="video-panel">
      <div class="video-panel-header">
        <div>
          <strong id="activeCamTitle">Camera A — Squat Rack Main</strong>
          <span style="color:var(--text-muted); margin-left:8px;" id="selectionModeBadge">Mode: AUTOMATIC (Zone + Activity Evidence)</span>
        </div>
        <div style="font-family:var(--font-mono); font-size:0.72rem; color:var(--accent-cyan);">
          Click any person bounding box to lock target
        </div>
      </div>

      <div class="viewport-wrap" id="viewportContainer">
        <video id="localVideo" playsinline autoplay muted></video>
        <img id="serverStreamImg" alt="Annotated Server Stream" onclick="onViewportClick(event)" />
        <canvas id="hudCanvas" onclick="onViewportClick(event)"></canvas>
      </div>

      <div id="statusStrip" class="status-strip">
        <span id="statusBannerText">READY — Stand in exercise zone so shoulders, hips, and knees are visible</span>
        <span id="visibilityBadge" class="badge valid">FULL_BODY</span>
      </div>
    </div>

    <!-- Right: Multi-Person & Biomechanical Telemetry -->
    <div class="telemetry-col">
      <!-- 4 Separated Repetition Counters -->
      <div class="panel-card">
        <div class="panel-title">
          <span>ISOLATED PERSON REPETITION COUNTERS</span>
          <span id="setCounterLabel" style="color:var(--accent-cyan); font-family:var(--font-mono);">SET #1 • FORM: 100%</span>
        </div>
        <div class="kpi-grid-4">
          <div class="kpi-box total">
            <div class="lbl">Completed Reps</div>
            <div class="val" id="valCompletedReps">0</div>
          </div>
          <div class="kpi-box valid">
            <div class="lbl">Valid (Right)</div>
            <div class="val" id="valValidReps">0</div>
          </div>
          <div class="kpi-box imperfect">
            <div class="lbl">Imperfect (Wrong)</div>
            <div class="val" id="valImperfectReps">0</div>
          </div>
          <div class="kpi-box partial">
            <div class="lbl">Partial / Rejected</div>
            <div class="val" id="valPartialReps">0</div>
          </div>
        </div>
      </div>

      <!-- Selected Person Identity, 9-State Phase & Biomechanics -->
      <div class="panel-card">
        <div class="panel-title">
          <span>SELECTED TRAINEE STATE & BIOMECHANICS</span>
          <span id="phaseBadge" class="pill cyan">STATE: NO_PERSON</span>
        </div>
        <div class="trainee-meta-grid">
          <div class="meta-cell">
            <div class="k">Selected Track / ID</div>
            <div class="v" id="metaTrackIdentity">None</div>
          </div>
          <div class="meta-cell">
            <div class="k">Identity Status</div>
            <div class="v" id="metaIdentityStatus">ANONYMOUS</div>
          </div>
          <div class="meta-cell">
            <div class="k">Activity State</div>
            <div class="v" id="metaActivity">UNKNOWN</div>
          </div>
          <div class="meta-cell">
            <div class="k">Knee Flexion Angle</div>
            <div class="v" id="metaKneeAngle">180.0°</div>
          </div>
          <div class="meta-cell">
            <div class="k">Hip Flexion / Trunk</div>
            <div class="v" id="metaHipTrunk">180.0° / 0.0°</div>
          </div>
          <div class="meta-cell">
            <div class="k">Valgus Ratio / View</div>
            <div class="v" id="metaValgusView">1.00 (front)</div>
          </div>
        </div>
        <div class="depth-meter-wrap">
          <div style="display:flex; justify-content:space-between; font-size:0.72rem; color:var(--text-secondary);">
            <span>Normalized Hip-to-Knee Squat Excursion (≥44% Bottom, ≥70% Parallel)</span>
            <span id="metaDepthPct" style="font-family:var(--font-mono); color:var(--accent-cyan);">0%</span>
          </div>
          <div class="depth-meter-bg">
            <div id="depthFill" class="depth-meter-fill"></div>
          </div>
        </div>
      </div>

      <!-- All Tracked People in Current Camera Frame -->
      <div class="panel-card">
        <div class="panel-title">
          <span>INDEPENDENT MULTI-PERSON TRACKS IN FRAME</span>
          <span id="peopleCountBadge" style="font-family:var(--font-mono);">0 Tracked</span>
        </div>
        <div id="activeTracksList" class="track-list">
          <div style="color:var(--text-muted); font-size:0.76rem;">No tracked individuals in current frame.</div>
        </div>
      </div>

      <!-- Traceable Rep-by-Rep History for Selected Trainee -->
      <div class="panel-card">
        <div class="panel-title">
          <span>SELECTED TRAINEE REP LOG (TRACEABLE EVENTS)</span>
          <span style="font-family:var(--font-mono); font-size:0.68rem; color:var(--text-muted);">IDEMPOTENT EVENT ID</span>
        </div>
        <div id="repHistoryList" class="rep-list">
          <div style="color:var(--text-muted); font-size:0.76rem;">Complete a full squat cycle (Standing → Descent → Bottom → Ascent → Standing) to log events.</div>
        </div>
      </div>
    </div>
  </div>
</section>

<!-- ===================================================================== -->
<!-- VIEW B: MULTI-CAMERA VIEW (CAMERA A + CAMERA B + HANDOFF LOG)          -->
<!-- ===================================================================== -->
<section id="view-multicam" class="view-section">
  <div class="dual-cam-grid">
    <div class="panel-card">
      <div class="panel-title">
        <span>CAMERA A — SQUAT RACK MAIN (FIXED / MOBILE WI-FI STREAM)</span>
        <span class="pill green" id="camAStatusPill">CONNECTED</span>
      </div>
      <div style="aspect-ratio:16/10; background:#04060a; border-radius:8px; overflow:hidden; margin-bottom:10px;">
        <img id="multiCamAImg" src="/api/video_feed/cam-a" style="width:100%; height:100%; object-fit:contain;" />
      </div>
      <div id="camATracksSummary" style="font-family:var(--font-mono); font-size:0.76rem; color:var(--text-secondary);">
        Waiting for Camera A tracks...
      </div>
    </div>

    <div class="panel-card">
      <div class="panel-title">
        <span>CAMERA B — SECONDARY STATION (LAPTOP WEBCAM)</span>
        <div style="display:flex; gap:6px;">
          <button class="btn" onclick="toggleDualLaptopCamB()">Stream This Browser to Cam B</button>
          <span class="pill green" id="camBStatusPill">CONNECTED</span>
        </div>
      </div>
      <div style="aspect-ratio:16/10; background:#04060a; border-radius:8px; overflow:hidden; margin-bottom:10px; position:relative;">
        <video id="camBVideo" playsinline autoplay muted></video>
        <img id="multiCamBImg" src="/api/video_feed/cam-b" style="width:100%; height:100%; object-fit:contain;" />
      </div>
      <div id="camBTracksSummary" style="font-family:var(--font-mono); font-size:0.76rem; color:var(--text-secondary);">
        Waiting for Camera B tracks...
      </div>
    </div>
  </div>

  <div class="panel-card" style="margin-top:14px;">
    <div class="panel-title">
      <span>GLOBAL IDENTITY HIERARCHY & CROSS-CAMERA HANDOFF LOG (Camera Track ID → Global ID → Verified Member ID → Active Session)</span>
      <button class="btn success" onclick="runValidationScenario('cross_camera_handoff')">Simulate Member A Handoff (Cam A → Cam B)</button>
    </div>
    <table class="data-table">
      <thead>
        <tr>
          <th>Handoff ID</th>
          <th>Verified Member</th>
          <th>Global Identity</th>
          <th>Active Gym Session</th>
          <th>Transition</th>
          <th>Re-ID Confidence</th>
          <th>Timestamp</th>
        </tr>
      </thead>
      <tbody id="handoffTableBody">
        <tr><td colspan="7" style="color:var(--text-muted);">No cross-camera handoffs recorded yet.</td></tr>
      </tbody>
    </table>
  </div>
</section>

<!-- ===================================================================== -->
<!-- VIEW C: WORKOUT ANALYTICS & PERSISTED SESSION EVENTS                   -->
<!-- ===================================================================== -->
<section id="view-analytics" class="view-section">
  <div class="kpi-grid-4" style="margin-bottom:14px;">
    <div class="kpi-box total"><div class="lbl">Persisted SQLite Reps</div><div class="val" id="anTotalReps">0</div></div>
    <div class="kpi-box valid"><div class="lbl">Validated Form Reps</div><div class="val" id="anValidReps">0</div></div>
    <div class="kpi-box imperfect"><div class="lbl">Imperfect Form Reps</div><div class="val" id="anImperfectReps">0</div></div>
    <div class="kpi-box partial"><div class="lbl">Partial / Rejected</div><div class="val" id="anPartialReps">0</div></div>
  </div>

  <div class="panel-card">
    <div class="panel-title">
      <span>IDEMPOTENT PERSISTED REPETITION EVENTS (SQLITE WAL DATABASE)</span>
      <button class="btn" onclick="refreshAnalytics()">Refresh Analytics</button>
    </div>
    <table class="data-table">
      <thead>
        <tr>
          <th>Event ID</th>
          <th>Rep / Set</th>
          <th>Camera / Track</th>
          <th>Member / Session</th>
          <th>Classification</th>
          <th>Knee / Excursion</th>
          <th>Duration</th>
          <th>Confidence / Quality</th>
          <th>Biomechanical Feedback</th>
        </tr>
      </thead>
      <tbody id="analyticsEventsBody">
        <tr><td colspan="9" style="color:var(--text-muted);">No persisted repetition events yet.</td></tr>
      </tbody>
    </table>
  </div>
</section>

<!-- ===================================================================== -->
<!-- VIEW D: ADMINISTRATION, CONSENT BIOMETRICS & 10-STAGE DIAGNOSTICS      -->
<!-- ===================================================================== -->
<section id="view-admin" class="view-section">
  <div class="dual-cam-grid">
    <!-- Left: RBAC Auth & Member Consent-Gated Biometrics -->
    <div class="panel-card">
      <div class="panel-title">
        <span>ROLE-BASED ACCESS CONTROL & CONSENT-GATED BIOMETRIC ENROLLMENT (AES-256-GCM)</span>
      </div>
      <div style="display:flex; gap:8px; margin-bottom:12px; flex-wrap:wrap;">
        <button class="btn" onclick="quickLogin('admin@kinetiq.ai', 'admin123')">Switch: Administrator</button>
        <button class="btn" onclick="quickLogin('staff@kinetiq.ai', 'staff123')">Switch: Staff Coach</button>
        <button class="btn" onclick="quickLogin('omar@kinetiq.ai', 'member123')">Switch: Member (Omar)</button>
      </div>
      <table class="data-table">
        <thead>
          <tr>
            <th>Member ID</th>
            <th>Full Name</th>
            <th>Tier</th>
            <th>GDPR Biometric Consent</th>
            <th>AES-256 Templates</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody id="membersTableBody"></tbody>
      </table>
    </div>

    <!-- Right: 10-Stage Real-Time Pipeline Latency Benchmark -->
    <div class="panel-card">
      <div class="panel-title">
        <span>10-STAGE END-TO-END PIPELINE LATENCY & HARDWARE DIAGNOSTICS</span>
        <button class="btn" onclick="refreshDiagnostics()">Refresh Metrics</button>
      </div>
      <div id="hardwareSummaryBox" style="font-family:var(--font-mono); font-size:0.74rem; color:var(--text-secondary); margin-bottom:10px; line-height:1.5;"></div>
      <table class="data-table">
        <thead>
          <tr>
            <th>Pipeline Stage</th>
            <th>p50 Latency (ms)</th>
            <th>p95 Latency (ms)</th>
            <th>Mean (ms)</th>
          </tr>
        </thead>
        <tbody id="diagnosticsStagesBody"></tbody>
      </table>
    </div>
  </div>
</section>

<script>
let activeCameraId = "cam-a";
let authToken = null;
let currentRole = "Administrator";
let mediaStream = null;
let camBStream = null;
let pumpActive = false;
let inFlightFrame = false;
const workCanvas = document.createElement("canvas");
const workCtx = workCanvas.getContext("2d");

const SKELETON_EDGES = [
  [5,6], [5,7], [7,9], [6,8], [8,10],
  [5,11], [6,12], [11,12],
  [11,13], [13,15], [12,14], [14,16]
];

function switchView(viewName, btn) {
  document.querySelectorAll(".view-section").forEach(s => s.classList.remove("active"));
  document.querySelectorAll(".nav-tab").forEach(b => b.classList.remove("active"));
  document.getElementById("view-" + viewName).classList.add("active");
  if (btn) btn.classList.add("active");
  if (viewName === "analytics") refreshAnalytics();
  if (viewName === "admin") { refreshMembers(); refreshDiagnostics(); }
}

function selectActiveCamera(camId) {
  activeCameraId = camId;
  document.getElementById("btnCamA").classList.toggle("active", camId === "cam-a");
  document.getElementById("btnCamB").classList.toggle("active", camId === "cam-b");
  document.getElementById("activeCamTitle").textContent =
    camId === "cam-a"
      ? "Camera A — Squat Rack Main (Mobile / Fixed Angle)"
      : "Camera B — Secondary Station (Laptop Webcam)";
}

async function setSelectionMode(mode, memberId = null, trackId = null, clickXY = null) {
  document.getElementById("btnModeAuto").classList.toggle("active", mode === "AUTOMATIC");
  document.getElementById("btnModeManual").classList.toggle("active", mode === "MANUAL");
  document.getElementById("btnModeMember").classList.toggle("active", mode === "MEMBER");
  const payload = { camera_id: activeCameraId, mode };
  if (memberId) payload.member_id = memberId;
  if (trackId !== null) payload.track_id = trackId;
  if (clickXY) { payload.x = clickXY[0]; payload.y = clickXY[1]; }
  const r = await fetch("/api/target/select", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });
  const data = await r.json();
  if (data.telemetry) renderTelemetry(data.telemetry);
}

async function onViewportClick(ev) {
  const rect = ev.currentTarget.getBoundingClientRect();
  const xNorm = Math.max(0, Math.min(1, (ev.clientX - rect.left) / Math.max(1, rect.width)));
  const yNorm = Math.max(0, Math.min(1, (ev.clientY - rect.top) / Math.max(1, rect.height)));
  await setSelectionMode("MANUAL", null, null, [xNorm, yNorm]);
}

async function startDeviceCam(facingMode, btn) {
  document.querySelectorAll(".control-bar .btn").forEach(b => {
    if (b.id === "btnFrontCam" || b.id === "btnBackCam") b.classList.remove("active");
  });
  if (btn) btn.classList.add("active");
  pumpActive = false;
  if (mediaStream) {
    mediaStream.getTracks().forEach(t => t.stop());
    mediaStream = null;
  }
  const video = document.getElementById("localVideo");
  const srvImg = document.getElementById("serverStreamImg");
  const hud = document.getElementById("hudCanvas");
  srvImg.style.display = "none";
  srvImg.src = "";
  video.style.display = "block";
  hud.style.display = "block";

  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    setServerSource("webcam");
    return;
  }
  try {
    mediaStream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: { ideal: facingMode }, width: { ideal: 480 }, height: { ideal: 360 } },
      audio: false
    });
    video.srcObject = mediaStream;
    await video.play();
    pumpActive = true;
    requestAnimationFrame(pumpBrowserFrame);
  } catch (err) {
    setServerSource("webcam");
  }
}

async function pumpBrowserFrame() {
  if (!pumpActive) return;
  const video = document.getElementById("localVideo");
  if (!inFlightFrame && video.readyState >= 2 && video.videoWidth > 0) {
    inFlightFrame = true;
    const tCap0 = performance.now();
    const scale = Math.min(1.0, 440 / Math.max(video.videoWidth, video.videoHeight));
    workCanvas.width = Math.max(64, Math.round(video.videoWidth * scale));
    workCanvas.height = Math.max(64, Math.round(video.videoHeight * scale));
    workCtx.drawImage(video, 0, 0, workCanvas.width, workCanvas.height);
    const dataUrl = workCanvas.toDataURL("image/jpeg", 0.72);
    const capMs = performance.now() - tCap0;

    try {
      const r = await fetch("/api/analyze_frame", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          camera_id: activeCameraId,
          image: dataUrl,
          capture_ms: Number(capMs.toFixed(2)),
          client_ts_ms: Date.now()
        })
      });
      const tel = await r.json();
      if (!tel.error) {
        renderTelemetry(tel);
        drawClientOverlay(tel);
      }
    } catch (e) {}
    inFlightFrame = false;
  }
  if (pumpActive) setTimeout(() => requestAnimationFrame(pumpBrowserFrame), 38);
}

async function setServerSource(sourceKey, btn) {
  pumpActive = false;
  if (mediaStream) {
    mediaStream.getTracks().forEach(t => t.stop());
    mediaStream = null;
  }
  const video = document.getElementById("localVideo");
  const srvImg = document.getElementById("serverStreamImg");
  const hud = document.getElementById("hudCanvas");
  video.style.display = "none";
  hud.style.display = "none";
  srvImg.style.display = "block";

  await fetch("/api/control", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action: sourceKey, camera_id: activeCameraId })
  });
  srvImg.src = `/api/video_feed/${activeCameraId}?t=${Date.now()}`;
}

function drawClientOverlay(tel) {
  const video = document.getElementById("localVideo");
  const canvas = document.getElementById("hudCanvas");
  if (!video.videoWidth) return;
  canvas.width = video.videoWidth;
  canvas.height = video.videoHeight;
  const ctx = canvas.getContext("2d");
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  const W = canvas.width, H = canvas.height;

  // Draw Exercise Zone Polygon
  if (tel.zone_polygon && tel.zone_polygon.length >= 3) {
    ctx.save();
    ctx.strokeStyle = "rgba(6, 182, 212, 0.35)";
    ctx.lineWidth = 1.5;
    ctx.setLineDash([6, 4]);
    ctx.beginPath();
    tel.zone_polygon.forEach((pt, idx) => {
      if (idx === 0) ctx.moveTo(pt[0] * W, pt[1] * H);
      else ctx.lineTo(pt[0] * W, pt[1] * H);
    });
    ctx.closePath();
    ctx.stroke();
    ctx.restore();
  }

  // Draw all independent person tracks
  (tel.tracks || []).forEach(tr => {
    if (!tr.box) return;
    const [x1, y1, x2, y2] = [tr.box[0] * W, tr.box[1] * H, tr.box[2] * W, tr.box[3] * H];
    const isSel = tr.is_selected;
    let stroke = "#06b6d4";
    if (tr.activity_state === "SEATED_IDLE") stroke = "#64748b";
    else if (isSel && tr.alarm_level === "red_alarm") stroke = "#ef4444";
    else if (isSel) stroke = "#10b981";

    ctx.strokeStyle = stroke;
    ctx.lineWidth = isSel ? 3 : 1.5;
    ctx.strokeRect(x1, y1, x2 - x1, y2 - y1);

    // Header pill
    const tag = `${isSel ? "★ " : ""}${tr.member_name} | ${tr.activity_state} | ${tr.completed_reps} REPS`;
    ctx.font = "bold 12px Inter, sans-serif";
    const tw = ctx.measureText(tag).width + 14;
    const ly = Math.max(22, y1);
    ctx.fillStyle = "rgba(13, 17, 25, 0.88)";
    ctx.fillRect(x1, ly - 22, tw, 22);
    ctx.strokeRect(x1, ly - 22, tw, 22);
    ctx.fillStyle = "#f8fafc";
    ctx.fillText(tag, x1 + 7, ly - 7);

    // Skeleton (only confident anatomical landmarks 5..16, no face clutter)
    const kpts = tr.keypoints || [];
    const faulty = new Set(tr.faulty_coco || []);
    SKELETON_EDGES.forEach(([a, b]) => {
      if (kpts[a] && kpts[b] && kpts[a][2] >= 0.16 && kpts[b][2] >= 0.16) {
        ctx.beginPath();
        ctx.moveTo(kpts[a][0] * W, kpts[a][1] * H);
        ctx.lineTo(kpts[b][0] * W, kpts[b][1] * H);
        ctx.strokeStyle = (faulty.has(a) || faulty.has(b)) ? "#ef4444" : (isSel ? "#22d3ee" : "#94a3b8");
        ctx.lineWidth = isSel ? 3 : 1.5;
        ctx.stroke();
      }
    });
    for (let j = 5; j < 17; j++) {
      if (kpts[j] && kpts[j][2] >= 0.16) {
        ctx.beginPath();
        ctx.arc(kpts[j][0] * W, kpts[j][1] * H, isSel ? 4.5 : 3, 0, Math.PI * 2);
        ctx.fillStyle = faulty.has(j) ? "#ef4444" : (isSel ? "#10b981" : "#cbd5e1");
        ctx.fill();
      }
    }
  });
}

function renderTelemetry(tel) {
  if (!tel) return;
  document.getElementById("fpsPill").textContent = `${tel.fps || 20.0} FPS | ${tel.latency_ms || 42.0} ms`;
  if (tel.detector_backend) document.getElementById("backendPill").textContent = tel.detector_backend;
  document.getElementById("selectionModeBadge").textContent =
    `Mode: ${tel.selection_mode || 'AUTOMATIC'} | Selected Track: ${tel.selected_track_id !== null ? '#' + tel.selected_track_id : 'None'}`;

  const sel = tel.selected_person || {};
  document.getElementById("valCompletedReps").textContent = sel.completed_reps ?? 0;
  document.getElementById("valValidReps").textContent = sel.valid_reps ?? 0;
  document.getElementById("valImperfectReps").textContent = sel.imperfect_reps ?? 0;
  document.getElementById("valPartialReps").textContent = (sel.partial_reps ?? 0) + (sel.rejected_movements ?? 0);
  document.getElementById("setCounterLabel").textContent =
    `SET #${sel.set_count || 1} • FORM SCORE: ${tel.form_score_pct ?? 100}%`;

  document.getElementById("phaseBadge").textContent = `STATE: ${sel.exercise_state || 'NO_PERSON'}`;
  document.getElementById("metaTrackIdentity").textContent =
    sel.track_id !== null ? `T#${sel.track_id} (${sel.member_name})` : "No Active Trainee";
  document.getElementById("metaIdentityStatus").textContent =
    `${sel.identity_status || 'ANONYMOUS'} (${Math.round((sel.identity_confidence || 0) * 100)}%)`;
  document.getElementById("metaActivity").textContent = sel.activity_state || "UNKNOWN";
  document.getElementById("metaKneeAngle").textContent = `${sel.knee_angle ?? 180}°`;
  document.getElementById("metaHipTrunk").textContent = `${sel.hip_angle ?? 180}° / ${sel.trunk_angle ?? 0}°`;
  document.getElementById("metaValgusView").textContent = `${sel.valgus_ratio ?? 1.0} (${sel.view || 'front'})`;

  const depth = Math.max(0, Math.min(100, Math.round(sel.excursion_pct || 0)));
  document.getElementById("metaDepthPct").textContent = `${depth}%`;
  document.getElementById("depthFill").style.width = `${depth}%`;

  // Status banner
  const strip = document.getElementById("statusStrip");
  strip.className = "status-strip";
  if (sel.visibility_status === "INSUFFICIENT_POSE_VISIBILITY" || sel.alarm_level === "yellow") {
    strip.classList.add("warn");
  } else if (sel.alarm_level === "red_alarm") {
    strip.classList.add("fault");
  }
  document.getElementById("statusBannerText").textContent = sel.status_banner || tel.status_banner || "";
  const visBadge = document.getElementById("visibilityBadge");
  visBadge.textContent = sel.visibility_status || "UNKNOWN";
  visBadge.className = "badge " + (sel.visibility_status === "INSUFFICIENT_POSE_VISIBILITY" ? "fault" : "valid");

  // Multi-Person Track List
  const tracks = tel.tracks || [];
  document.getElementById("peopleCountBadge").textContent = `${tracks.length} Tracked`;
  const tList = document.getElementById("activeTracksList");
  if (tracks.length === 0) {
    tList.innerHTML = `<div style="color:var(--text-muted); font-size:0.76rem;">No tracked individuals in current frame.</div>`;
  } else {
    tList.innerHTML = tracks.map(tr => `
      <div class="track-item ${tr.is_selected ? 'selected' : ''}" onclick="setSelectionMode('MANUAL', null, ${tr.track_id})">
        <div>
          <strong>${tr.is_selected ? '★ ' : ''}Track #${tr.track_id} — ${tr.member_name}</strong>
          <span style="color:var(--text-secondary); margin-left:6px;">[${tr.identity_status}]</span>
          <div style="color:var(--text-muted); font-size:0.70rem;">
            Activity: ${tr.activity_state} • State: ${tr.exercise_state} • Visibility: ${tr.visibility_status}
          </div>
        </div>
        <div style="text-align:right; font-family:var(--font-mono);">
          <div style="font-weight:800; color:var(--accent-cyan);">${tr.completed_reps} Reps</div>
          <div style="font-size:0.68rem; color:var(--text-secondary);">Valid:${tr.valid_reps} | Fault:${tr.imperfect_reps} | Part:${tr.partial_reps}</div>
        </div>
      </div>
    `).join("");
  }

  // Rep History Log
  const reps = (sel.rep_history || []).slice().reverse();
  const rList = document.getElementById("repHistoryList");
  if (reps.length === 0) {
    rList.innerHTML = `<div style="color:var(--text-muted); font-size:0.76rem;">No completed repetitions for this person yet.</div>`;
  } else {
    rList.innerHTML = reps.map(r => `
      <div class="rep-row ${r.verdict}">
        <div>
          <strong>Rep #${r.rep_number} (${r.camera_id.toUpperCase()} • T#${r.track_id})</strong> — ${r.explanation}
          <div style="font-family:var(--font-mono); font-size:0.68rem; color:var(--text-secondary);">
            Event: ${r.event_id.slice(0,8)} • Knee: ${r.min_knee_angle_deg}° • Excursion: ${r.peak_excursion_pct}% • Dur: ${r.duration_s}s • Conf: ${Math.round(r.confidence*100)}%
          </div>
        </div>
        <span class="badge ${r.verdict === 'correct' ? 'valid' : 'fault'}">${r.verdict === 'correct' ? 'VALID' : 'IMPERFECT'}</span>
      </div>
    `).join("");
  }
}

async function runValidationScenario(scenarioName) {
  const r = await fetch("/api/simulate/scenario", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ scenario: scenarioName })
  });
  const data = await r.json();
  if (scenarioName === "cross_camera_handoff") {
    selectActiveCamera("cam-b");
    renderTelemetry(data.cam_b);
  } else {
    selectActiveCamera("cam-a");
    renderTelemetry(data.cam_a);
  }
  // Display the server-rendered HUD image so the simulated tracks are immediately visible
  pumpActive = false;
  document.getElementById("localVideo").style.display = "none";
  document.getElementById("hudCanvas").style.display = "none";
  const srvImg = document.getElementById("serverStreamImg");
  srvImg.style.display = "block";
  srvImg.src = `/api/video_feed/${activeCameraId}?t=${Date.now()}`;
  pollMultiCamState();
}

async function resetCounters() {
  await fetch("/api/control", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action: "reset", camera_id: activeCameraId })
  });
}

async function toggleDualLaptopCamB() {
  const vidB = document.getElementById("camBVideo");
  if (camBStream) {
    camBStream.getTracks().forEach(t => t.stop());
    camBStream = null;
    return;
  }
  try {
    camBStream = await navigator.mediaDevices.getUserMedia({ video: { width: 400, height: 300 }, audio: false });
    vidB.srcObject = camBStream;
    await vidB.play();
    setInterval(async () => {
      if (!camBStream || vidB.readyState < 2) return;
      workCanvas.width = 320;
      workCanvas.height = 240;
      workCtx.drawImage(vidB, 0, 0, 320, 240);
      await fetch("/api/analyze_frame", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ camera_id: "cam-b", image: workCanvas.toDataURL("image/jpeg", 0.7) })
      });
    }, 180);
  } catch (e) {}
}

async function pollMultiCamState() {
  try {
    const r = await fetch("/api/cameras/state");
    const st = await r.json();
    if (!pumpActive) {
      renderTelemetry(activeCameraId === "cam-a" ? st.cam_a : st.cam_b);
    }
    const fmtCam = (c) => {
      const trs = c.tracks || [];
      if (!trs.length) return "0 Active Tracks";
      return trs.map(t => `T#${t.track_id} (${t.member_name} • ${t.identity_status}): ${t.completed_reps} reps [${t.activity_state}]`).join("  |  ");
    };
    document.getElementById("camATracksSummary").textContent = fmtCam(st.cam_a);
    document.getElementById("camBTracksSummary").textContent = fmtCam(st.cam_b);

    const hBody = document.getElementById("handoffTableBody");
    const hLog = st.handoff_log || [];
    if (hLog.length > 0) {
      hBody.innerHTML = hLog.map(h => `
        <tr>
          <td>${h.handoff_id}</td>
          <td style="color:var(--status-valid); font-weight:700;">${h.member_name} (${h.member_id || 'Anon'})</td>
          <td>${h.global_id.slice(0, 10)}</td>
          <td>${h.session_id || '-'}</td>
          <td><span class="badge valid">${h.from_camera_id.toUpperCase()} → ${h.to_camera_id.toUpperCase()}</span></td>
          <td>${Math.round(h.similarity * 100)}%</td>
          <td>${new Date(h.occurred_at * 1000).toLocaleTimeString()}</td>
        </tr>
      `).join("");
    }
  } catch (e) {}
}

async function refreshAnalytics() {
  const r = await fetch("/api/analytics");
  const d = await r.json();
  document.getElementById("anTotalReps").textContent = d.total_persisted_reps ?? 0;
  document.getElementById("anValidReps").textContent = d.valid_reps ?? 0;
  document.getElementById("anImperfectReps").textContent = d.imperfect_reps ?? 0;
  document.getElementById("anPartialReps").textContent = (d.partial_reps ?? 0) + (d.rejected_movements ?? 0);

  const tbody = document.getElementById("analyticsEventsBody");
  const evs = d.recent_rep_events || [];
  if (!evs.length) {
    tbody.innerHTML = `<tr><td colspan="9" style="color:var(--text-muted);">No persisted repetition events yet.</td></tr>`;
    return;
  }
  tbody.innerHTML = evs.map(e => `
    <tr>
      <td>${e.event_id.slice(0, 10)}</td>
      <td>Rep #${e.rep_number} (Set ${e.set_index})</td>
      <td>${e.camera_id.toUpperCase()} / T#${e.track_id}</td>
      <td>${e.member_id || 'Anonymous'} / ${(e.session_id || '').slice(0, 10)}</td>
      <td><span class="badge ${e.verdict === 'correct' ? 'valid' : 'fault'}">${e.classification}</span></td>
      <td>${e.min_knee_angle_deg}° / ${e.peak_excursion_pct}%</td>
      <td>${e.duration_s}s</td>
      <td>${Math.round(e.confidence * 100)}% / ${Math.round(e.pose_quality * 100)}%</td>
      <td style="font-family:var(--font-sans);">${e.explanation}</td>
    </tr>
  `).join("");
}

async function quickLogin(email, password) {
  const r = await fetch("/api/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password })
  });
  const d = await r.json();
  if (d.ok) {
    authToken = d.user.token;
    currentRole = d.user.role;
    document.getElementById("rolePill").textContent = `Role: ${d.user.role} (${d.user.full_name})`;
    refreshMembers();
  }
}

async function refreshMembers() {
  const r = await fetch("/api/members");
  const d = await r.json();
  const tbody = document.getElementById("membersTableBody");
  tbody.innerHTML = (d.members || []).map(m => `
    <tr>
      <td>${m.member_id}</td>
      <td style="font-family:var(--font-sans); font-weight:600;">${m.full_name}</td>
      <td>${m.membership_tier}</td>
      <td><span class="badge ${m.consent_granted ? 'valid' : 'fault'}">${m.consent_granted ? 'GRANTED (' + m.consent_version + ')' : 'REVOKED / NONE'}</span></td>
      <td>${m.enrolled_templates} encrypted</td>
      <td>
        <button class="btn success" onclick="enrollMember('${m.member_id}')">Consent & Enroll</button>
        <button class="btn danger" onclick="revokeMember('${m.member_id}')">Revoke & Purge</button>
      </td>
    </tr>
  `).join("");
}

async function enrollMember(memberId) {
  const headers = { "Content-Type": "application/json" };
  if (authToken) headers["Authorization"] = `Bearer ${authToken}`;
  const r = await fetch("/api/members/enroll", {
    method: "POST",
    headers,
    body: JSON.stringify({ member_id: memberId, consent_granted: true, camera_id: activeCameraId })
  });
  const d = await r.json();
  if (!d.ok) alert(d.error || "Enrollment failed");
  refreshMembers();
}

async function revokeMember(memberId) {
  const headers = { "Content-Type": "application/json" };
  if (authToken) headers["Authorization"] = `Bearer ${authToken}`;
  await fetch("/api/members/revoke", {
    method: "POST",
    headers,
    body: JSON.stringify({ member_id: memberId })
  });
  refreshMembers();
}

async function refreshDiagnostics() {
  const r = await fetch("/api/diagnostics");
  const d = await r.json();
  const hw = d.hardware || {};
  const tp = d.throughput || {};
  document.getElementById("hardwareSummaryBox").innerHTML = `
    <div><strong>CPU:</strong> ${hw.cpu} &nbsp;|&nbsp; <strong>GPU Detected:</strong> ${hw.gpu_detected}</div>
    <div><strong>Active Inference Provider:</strong> ${hw.active_execution_provider} &nbsp;|&nbsp; <strong>CUDA Active:</strong> ${hw.gpu_acceleration_active ? 'YES' : 'NO (CPU Verified)'}</div>
    <div><strong>Detector Backend:</strong> ${hw.detector_backend} &nbsp;|&nbsp; <strong>Processed Frames:</strong> ${tp.processed_frames} &nbsp;|&nbsp; <strong>Queue Depth:</strong> ${tp.event_queue_depth}</div>
  `;
  const stBody = document.getElementById("diagnosticsStagesBody");
  const stages = d.stages || {};
  stBody.innerHTML = Object.entries(stages).map(([k, v]) => `
    <tr>
      <td>${k}</td>
      <td style="color:var(--accent-cyan); font-weight:700;">${v.p50_ms} ms</td>
      <td>${v.p95_ms} ms</td>
      <td>${v.mean_ms} ms</td>
    </tr>
  `).join("");
}

setInterval(pollMultiCamState, 220);

window.addEventListener("DOMContentLoaded", () => {
  startDeviceCam("user", document.getElementById("btnFrontCam"));
  refreshMembers();
  refreshDiagnostics();
});
</script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
def index():
    return HTMLResponse(HTML_PAGE.replace("__LAN_IP__", LAN_IP))


def _ensure_self_signed_cert(cert_path: str, key_path: str) -> bool:
    if os.path.exists(cert_path) and os.path.exists(key_path):
        return True
    os.makedirs(os.path.dirname(cert_path), exist_ok=True)
    openssl_bin = r"C:\Program Files\Git\usr\bin\openssl.exe"
    if not os.path.exists(openssl_bin):
        return False
    try:
        subprocess.run(
            [
                openssl_bin,
                "req",
                "-x509",
                "-newkey",
                "rsa:2048",
                "-keyout",
                key_path,
                "-out",
                cert_path,
                "-days",
                "365",
                "-nodes",
                "-subj",
                f"/CN={LAN_IP}",
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return os.path.exists(cert_path) and os.path.exists(key_path)
    except Exception:
        return False


def main():
    ap = argparse.ArgumentParser(description="KinetiQ Smart Gym Multi-Camera Vision & Biomechanics Platform")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--https-port", type=int, default=8443)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()

    print("[KinetiQ Smart Gym] Initializing Top-Down Multi-Person Detector + RTMPose + Multi-Camera Hub...")
    get_hub()

    cert_dir = os.path.join(REPO_ROOT, "ai", "outputs", "squat_assessment")
    cert_path = os.path.join(cert_dir, "cert.pem")
    key_path = os.path.join(cert_dir, "key.pem")
    has_ssl = _ensure_self_signed_cert(cert_path, key_path)

    if has_ssl:
        def _run_https():
            uvicorn.run(
                app,
                host=args.host,
                port=args.https_port,
                ssl_certfile=cert_path,
                ssl_keyfile=key_path,
                log_level="warning",
            )
        threading.Thread(target=_run_https, daemon=True).start()
        print(f"[Mobile Phone Camera Link (HTTPS)]  https://{LAN_IP}:{args.https_port}")

    print(f"[Mobile Phone & Laptop Link (HTTP)] http://{LAN_IP}:{args.port}")
    print(f"[Laptop Localhost Link]             http://127.0.0.1:{args.port}")

    if not args.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(f"http://127.0.0.1:{args.port}")).start()
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
