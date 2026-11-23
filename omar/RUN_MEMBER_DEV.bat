@echo off
setlocal enabledelayedexpansion
title KinetiQ - Member Web (HTTPS Port 3001)

set "SCRIPT_DIR=%~dp0"
set "ROOT_DIR=%SCRIPT_DIR%"
set "APP_DIR=%SCRIPT_DIR%apps\member-web"
set "PORT=3001"

echo =======================================================================
echo  KinetiQ Member Web Launcher (Mobile PWA & Phone Camera)
echo =======================================================================

:: Validate App Directory
if not exist "%APP_DIR%" (
    echo [ERROR] Member web directory not found at "%APP_DIR%".
    pause
    exit /b 1
)

:: Validate port 3001 not already owned
for /f "tokens=5" %%a in ('netstat -ano ^| findstr /r /c:":%PORT% .*LISTENING"') do (
    set "OWNER_PID=%%a"
    if defined OWNER_PID (
        echo [ERROR] Port %PORT% is already owned by PID !OWNER_PID!.
        echo KinetiQ Member Web is already running on https://localhost:%PORT%.
        pause
        exit /b 1
    )
)

cd /d "%ROOT_DIR%"
echo [*] Verifying SSL certificates for LAN and phone camera access...
if not exist "apps\member-web\certificates\server.key" (
    call node scripts\generate-ssl-certs.mjs
)

cd /d "%APP_DIR%"
echo [*] Starting Member Web on https://localhost:%PORT% and https://0.0.0.0:%PORT% ...
echo [*] For mobile camera over LAN, open https://^<YOUR_LAN_IP^>:%PORT%/cert on your phone.
echo.

call npm.cmd run dev

set "EXIT_CODE=%ERRORLEVEL%"
echo.
if %EXIT_CODE% neq 0 (
    echo [!] Member Web exited with code %EXIT_CODE%.
)
pause
exit /b %EXIT_CODE%
