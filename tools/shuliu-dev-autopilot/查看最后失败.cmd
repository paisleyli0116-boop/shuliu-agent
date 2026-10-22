@echo off
setlocal
python "%~dp0autopilot.py" last-failure
set "rc=%errorlevel%"
echo.
pause
exit /b %rc%
