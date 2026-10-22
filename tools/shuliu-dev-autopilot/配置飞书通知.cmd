@echo off
setlocal
python "%~dp0autopilot.py" configure-feishu
set "rc=%errorlevel%"
echo.
pause
exit /b %rc%
