@echo off
title KinetiQ Smart Gym — Multi-Camera Vision & Biomechanics Platform
cd /d "%~dp0GYM-AI (2)\GYM-AI"
echo Starting KinetiQ Smart Gym Multi-Camera Vision Platform (ONNX YOLOX + RTMPose-m + ByteTrack)...
"%~dp0omar\services\edge-ai\.venv\Scripts\python.exe" -m ai.service.live_squat_gui --host 0.0.0.0 --port 8080 --https-port 8443
pause
