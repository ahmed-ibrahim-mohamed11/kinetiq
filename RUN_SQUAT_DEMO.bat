@echo off
title KinetiQ Squat Assessment Demo
echo ============================================
echo   KinetiQ Squat Assessment System Demo
echo   AI Powered Movement Analysis
echo ============================================
echo.

cd /d "%~dp0GYM-AI (2)\GYM-AI"

REM Try the GYM-AI venv first, then the edge-ai venv
if exist ".venv\Scripts\python.exe" (
    echo Using GYM-AI Python environment...
    .venv\Scripts\python.exe squat_demo_gui.py
) else if exist "%~dp0omar\services\edge-ai\.venv\Scripts\python.exe" (
    echo Using edge-ai Python environment...
    "%~dp0omar\services\edge-ai\.venv\Scripts\python.exe" squat_demo_gui.py
) else (
    echo ERROR: No Python virtual environment found.
    echo Please ensure .venv exists in GYM-AI or omar\services\edge-ai
    echo.
    echo Try: python squat_demo_gui.py
)

echo.
pause
