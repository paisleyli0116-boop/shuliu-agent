@echo off
setlocal
python "%~dp0autopilot.py" status
set "rc=%errorlevel%"
echo.
pause
exit /b %rc%
