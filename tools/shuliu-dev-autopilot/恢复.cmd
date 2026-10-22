@echo off
setlocal
python "%~dp0autopilot.py" resume
set "rc=%errorlevel%"
echo.
pause
exit /b %rc%
