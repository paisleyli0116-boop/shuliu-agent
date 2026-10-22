@echo off
setlocal
python "%~dp0autopilot.py" approve-current
set "rc=%errorlevel%"
echo.
pause
exit /b %rc%
