@echo off
title KinetiQ - Member Web (Port 3001)
cd /d "%~dp0\omar\apps\member-web"
echo Starting KinetiQ Member Web on http://localhost:3001 ...
call npm.cmd run dev
pause

