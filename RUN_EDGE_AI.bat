@echo off
title KinetiQ - Edge AI Vision Service (Port 8088)
cd /d "%~dp0\omar\services\edge-ai"
echo Starting KinetiQ Edge AI Vision Service on http://localhost:8088 ...
call .venv\Scripts\python.exe -m edge_ai run
pause

