@echo off
setlocal enabledelayedexpansion
title KinetiQ - Admin Web (Port 3000)

set "SCRIPT_DIR=%~dp0"
set "APP_DIR=%SCRIPT_DIR%apps\admin-web"
set "PORT=3000"

echo =======================================================================
echo  KinetiQ Admin Web Launcher (Staff/Kiosk Console)
echo =======================================================================

:: Validate App Directory
if not exist "%APP_DIR%" (
    echo [ERROR] Admin web directory not found at "%APP_DIR%".
    pause
    exit /b 1
)

:: Validate port 3000 not already owned
for /f "tokens=5" %%a in ('netstat -ano ^| findstr /r /c:":%PORT% .*LISTENING"') do (
    set "OWNER_PID=%%a"
    if defined OWNER_PID (
        echo [ERROR] Port %PORT% is already owned by PID !OWNER_PID!.
        echo KinetiQ Admin Web is already running on http://localhost:%PORT%.
        pause
        exit /b 1
    )
)

cd /d "%APP_DIR%"
echo [*] Working directory: %CD%
echo [*] Starting Admin Web on http://localhost:%PORT% ...
echo.

call npm.cmd run dev

set "EXIT_CODE=%ERRORLEVEL%"
echo.
if %EXIT_CODE% neq 0 (
    echo [!] Admin Web exited with code %EXIT_CODE%.
)
pause
exit /b %EXIT_CODE%
