@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy RemoteSigned -File "%~dp0autopilot-task.ps1" -Action install
set "rc=%errorlevel%"
echo.
pause
exit /b %rc%
