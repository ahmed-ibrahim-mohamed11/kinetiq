@echo off
title KinetiQ - Launch All 3 Services
echo ==========================================================
echo  KinetiQ Commercial Gym Platform - Development Launcher
echo  1. Edge AI Vision Service -> http://localhost:8088
echo  2. Admin Web (Staff/Kiosk) -> http://localhost:3000
echo  3. Member Web (Mobile/PWA) -> http://localhost:3001
echo ==========================================================

echo [*] Starting Edge AI Vision Service in background window...
start "KinetiQ Edge AI" cmd /k "cd /d %~dp0\omar\services\edge-ai && .venv\Scripts\python.exe -m edge_ai run"
timeout /t 3 /nobreak > nul

echo [*] Starting Admin Web in background window...
start "KinetiQ Admin Web" cmd /k "cd /d %~dp0\omar\apps\admin-web && npm.cmd run dev"
timeout /t 2 /nobreak > nul

echo [*] Starting Member Web in background window...
start "KinetiQ Member Web" cmd /k "cd /d %~dp0\omar\apps\member-web && npm.cmd run dev"

echo.
echo ==========================================================
echo  All 3 services are running!
echo  Admin Web  : http://localhost:3000
echo  Member Web : http://localhost:3001
echo  Edge AI    : http://localhost:8088/healthz
echo ==========================================================
echo.
pause

