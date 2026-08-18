@echo off
setlocal
cd /d "%~dp0"
title Test Berlin Philharmonic Notifications
if not exist ".venv\Scripts\python.exe" (
    echo Run start_monitor.bat first to complete setup.
    pause
    exit /b 1
)
if not exist ".env" (
    echo The .env file is missing. Run start_monitor.bat first.
    pause
    exit /b 1
)
set "PYTHONUTF8=1"
".venv\Scripts\python.exe" "monitor.py" --test-notification
echo.
pause
