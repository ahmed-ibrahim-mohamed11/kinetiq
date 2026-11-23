@echo off
setlocal enabledelayedexpansion
title KinetiQ - Launch All Services

echo =======================================================================
echo  KinetiQ Commercial Smart Gym - Master Service Launcher
echo  1. Edge AI Vision Service -> http://localhost:8088
echo  2. Admin Web (Staff/Kiosk) -> http://localhost:3000
echo  3. Member Web (Mobile PWA) -> https://localhost:3001
echo =======================================================================

set "ROOT_DIR=%~dp0"
cd /d "%ROOT_DIR%"

:: 1. Validate certificates for Mobile LAN HTTPS
if not exist "apps\member-web\certificates\server.key" (
    echo [*] Generating HTTPS SSL certificates for mobile camera LAN access...
    call node scripts\generate-ssl-certs.mjs
)

:: 2. Launch Edge AI (Port 8088)
set "EDGE_AI_RUNNING=0"
for /f "tokens=5" %%a in ('netstat -ano ^| findstr /r /c:":8088 .*LISTENING"') do (
    set "EDGE_AI_RUNNING=1"
    set "EDGE_PID=%%a"
)
if "!EDGE_AI_RUNNING!"=="1" (
    echo [i] Edge AI Vision Service is already running (PID: !EDGE_PID!). Skipping launch.
) else (
    echo [*] Starting Edge AI Vision Service on port 8088...
    start "KinetiQ Edge AI" cmd /k "cd /d %ROOT_DIR%services\edge-ai && call %ROOT_DIR%RUN_EDGE_AI.bat"
    timeout /t 3 /nobreak > nul
)

:: 3. Launch Admin Web (Port 3000)
set "ADMIN_RUNNING=0"
for /f "tokens=5" %%a in ('netstat -ano ^| findstr /r /c:":3000 .*LISTENING"') do (
    set "ADMIN_RUNNING=1"
    set "ADMIN_PID=%%a"
)
if "!ADMIN_RUNNING!"=="1" (
    echo [i] Admin Web is already running (PID: !ADMIN_PID!). Skipping launch.
) else (
    echo [*] Starting Admin Web on port 3000...
    start "KinetiQ Admin Web" cmd /k "cd /d %ROOT_DIR%apps\admin-web && call %ROOT_DIR%RUN_ADMIN_DEV.bat"
    timeout /t 2 /nobreak > nul
)

:: 4. Launch Member Web (Port 3001)
set "MEMBER_RUNNING=0"
for /f "tokens=5" %%a in ('netstat -ano ^| findstr /r /c:":3001 .*LISTENING"') do (
    set "MEMBER_RUNNING=1"
    set "MEMBER_PID=%%a"
)
if "!MEMBER_RUNNING!"=="1" (
    echo [i] Member Web is already running (PID: !MEMBER_PID!). Skipping launch.
) else (
    echo [*] Starting Member Web on port 3001 (HTTPS)...
    start "KinetiQ Member Web" cmd /k "cd /d %ROOT_DIR%apps\member-web && call %ROOT_DIR%RUN_MEMBER_DEV.bat"
)

echo.
echo =======================================================================
echo  Services Active:
echo  - Admin Web   : http://localhost:3000
echo  - Member Web  : https://localhost:3001
echo  - Edge AI     : http://localhost:8088/healthz  (Readiness: /readyz)
echo  - Phone Cert  : https://^<YOUR_LAN_IP^>:3001/cert
echo.
echo  To stop all services cleanly, run: STOP_ALL_DEV.bat
echo =======================================================================
echo.
pause
