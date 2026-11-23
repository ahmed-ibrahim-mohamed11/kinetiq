@echo off
title Upload KinetiQ to GitHub
cd /d "%~dp0omar"
echo Uploading KinetiQ to https://github.com/ahmed-ibrahim-mohamed11/kinetiq ...
echo If a GitHub sign-in window opens, sign in as ahmed-ibrahim-mohamed11.
echo.
where git > "%~dp0push_log.txt" 2>&1
git --version >> "%~dp0push_log.txt" 2>&1
git push https://github.com/ahmed-ibrahim-mohamed11/kinetiq.git HEAD:main >> "%~dp0push_log.txt" 2>&1
echo EXIT %errorlevel% >> "%~dp0push_log.txt"
type "%~dp0push_log.txt"
echo.
echo Tell Claude: done
pause
