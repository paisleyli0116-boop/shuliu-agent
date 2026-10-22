@echo off
setlocal
python "%~dp0autopilot.py" emergency-stop
echo.
pause
