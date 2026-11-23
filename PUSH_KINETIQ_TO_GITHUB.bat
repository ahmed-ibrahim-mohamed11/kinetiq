@echo off
title Upload KinetiQ to GitHub (ahmed-ibrahim-mohamed11/kinetiq)
set GHUSER=
for /f "delims=" %%u in ('gh api user --jq .login 2^>nul') do set GHUSER=%%u

if /i not "%GHUSER%"=="ahmed-ibrahim-mohamed11" (
  echo STEP 1 - Sign in to GitHub as ahmed-ibrahim-mohamed11
  echo  - A one-time code appears below. Press Enter to open the browser.
  echo  - In the browser make sure you are signed in as ahmed-ibrahim-mohamed11, NOT omar.
  echo  - Paste the code and click Authorize.
  echo.
  gh auth login --hostname github.com --git-protocol https --web
)

echo.
gh auth status
echo.
echo STEP 2 - Uploading the KinetiQ project...
git -C "C:\Users\mamdo\kinetiq-upload" push -u origin main
echo.
if %errorlevel%==0 (
  echo DONE - open https://github.com/ahmed-ibrahim-mohamed11/kinetiq
) else (
  echo UPLOAD FAILED - exit code %errorlevel%. Copy the messages above to Claude.
)
echo.
pause
