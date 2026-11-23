@echo off
setlocal enabledelayedexpansion
title KinetiQ - Safe Stop All Services

set "ROOT_DIR=%~dp0"
cd /d "%ROOT_DIR%"

set "PYTHON_EXE=services\edge-ai\.venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" (
    set "PYTHON_EXE=python.exe"
)

echo =======================================================================
echo  Stopping KinetiQ Services (Port 8088, 3000, 3001)
echo =======================================================================

"%PYTHON_EXE%" scripts\safe-stop-services.py

echo.
pause
