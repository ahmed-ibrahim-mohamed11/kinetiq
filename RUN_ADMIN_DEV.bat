@echo off
title KinetiQ - Admin Web (Port 3000)
cd /d "%~dp0\omar\apps\admin-web"
echo Starting KinetiQ Admin Web on http://localhost:3000 ...
call npm.cmd run dev
pause

