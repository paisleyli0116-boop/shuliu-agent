@echo off
setlocal
python "%~dp0autopilot.py" pause
set "rc=%errorlevel%"
echo.
pause
exit /b %rc%
