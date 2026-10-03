@echo off
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\run-docker.ps1"
set EXITCODE=%ERRORLEVEL%
if not "%EXITCODE%"=="0" (
  echo.
  echo AI Quotas did not start. If the bridge failed, see .docker\bridge.err.log
  pause
)
exit /b %EXITCODE%
