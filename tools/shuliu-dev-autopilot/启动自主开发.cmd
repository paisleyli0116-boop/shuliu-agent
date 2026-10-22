@echo off
setlocal
python "%~dp0autopilot.py" start
set "rc=%errorlevel%"
echo.
pause
exit /b %rc%
