@echo off
setlocal enabledelayedexpansion
title KinetiQ - Edge AI Vision Service (Port 8088)

set "SCRIPT_DIR=%~dp0"
set "SERVICE_DIR=%SCRIPT_DIR%services\edge-ai"
set "PYTHON_EXE=%SERVICE_DIR%\.venv\Scripts\python.exe"
set "PORT=8088"

echo =======================================================================
echo  KinetiQ Edge AI Vision Service Launcher
echo =======================================================================

:: Validate Python Environment
if not exist "%PYTHON_EXE%" (
    echo [ERROR] Python virtualenv not found at "%PYTHON_EXE%".
    echo Please configure services\edge-ai\.venv first.
    pause
    exit /b 1
)

:: Validate port 8088 not already owned
for /f "tokens=5" %%a in ('netstat -ano ^| findstr /r /c:":%PORT% .*LISTENING"') do (
    set "OWNER_PID=%%a"
    if defined OWNER_PID (
        echo [ERROR] Port %PORT% is already owned by PID !OWNER_PID!.
        echo KinetiQ Edge AI cannot start because another instance is already running.
        echo Health URL: http://localhost:%PORT%/healthz
        pause
        exit /b 1
    )
)

:: Ensure logs directory
if not exist "%SERVICE_DIR%\logs" (
    mkdir "%SERVICE_DIR%\logs" 2>nul
)

cd /d "%SERVICE_DIR%"
echo [*] Working directory: %CD%
echo [*] Starting Edge AI Vision Service on port %PORT%...
echo [*] Press Ctrl+C in this window to stop cleanly.
echo.

"%PYTHON_EXE%" -m edge_ai run

set "EXIT_CODE=%ERRORLEVEL%"
echo.
if %EXIT_CODE% neq 0 (
    echo [!] Edge AI service exited with code %EXIT_CODE%.
) else (
    echo [*] Edge AI service stopped cleanly.
)
pause
exit /b %EXIT_CODE%
