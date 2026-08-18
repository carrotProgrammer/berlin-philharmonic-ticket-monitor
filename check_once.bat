@echo off
setlocal
cd /d "%~dp0"
title Check Berlin Philharmonic Availability Once
if not exist ".venv\Scripts\python.exe" (
    echo Run start_monitor.bat first to complete setup.
    pause
    exit /b 1
)
set "PYTHONUTF8=1"
".venv\Scripts\python.exe" "monitor.py" --check-once --debug
echo.
echo This check did not send alerts or open a purchase page.
pause
