@echo off
setlocal
cd /d "%~dp0"
title Berlin Philharmonic Ticket Monitor

set "PYTHON_LAUNCHER="

rem Prefer the Python currently active in PowerShell/Conda.
where python >nul 2>nul
if not errorlevel 1 (
    python -c "import sys; raise SystemExit(sys.version_info[:2] not in [(3, n) for n in range(11, 100)])" >nul 2>nul
    if not errorlevel 1 set "PYTHON_LAUNCHER=python"
)

rem Fall back to the Windows Python launcher when needed.
if not defined PYTHON_LAUNCHER (
    where py >nul 2>nul
    if not errorlevel 1 (
        py -3 -c "import sys; raise SystemExit(sys.version_info[:2] not in [(3, n) for n in range(11, 100)])" >nul 2>nul
        if not errorlevel 1 set "PYTHON_LAUNCHER=py -3"
    )
)

if not defined PYTHON_LAUNCHER (
    echo [ERROR] Python 3.11 or newer was not found.
    echo Install a supported version from:
    echo https://www.python.org/downloads/windows/
    echo Select "Add python.exe to PATH" during installation.
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo [FIRST RUN] Creating the .venv environment...
    %PYTHON_LAUNCHER% -m venv ".venv"
    if errorlevel 1 goto :setup_error
)

if not exist ".venv\setup_complete" (
    echo [FIRST RUN] Installing Python dependencies...
    ".venv\Scripts\python.exe" -m pip install --upgrade pip
    if errorlevel 1 goto :setup_error
    ".venv\Scripts\python.exe" -m pip install -r "requirements.txt"
    if errorlevel 1 goto :setup_error
    echo [FIRST RUN] Installing Playwright Chromium...
    ".venv\Scripts\python.exe" -m playwright install chromium
    if errorlevel 1 goto :setup_error
    type nul > ".venv\setup_complete"
)

if not exist ".env" (
    copy /y ".env.example" ".env" >nul
    echo.
    echo [ACTION REQUIRED] A new .env file was created.
    echo Enter your Gmail address and Google app password in Notepad.
    echo Save and close the file, then run start_monitor.bat again.
    echo Never paste the app password into chat or monitor.py.
    start "" notepad ".env"
    pause
    exit /b 0
)

set "PYTHONUTF8=1"
echo Starting the monitor. Press Ctrl+C to stop safely.
echo.
".venv\Scripts\python.exe" "monitor.py"
set "MONITOR_EXIT=%errorlevel%"
if not "%MONITOR_EXIT%"=="0" (
    echo.
    echo The monitor exited with code %MONITOR_EXIT%.
    echo See logs\monitor.log for details.
    pause
)
exit /b %MONITOR_EXIT%

:setup_error
echo.
echo [ERROR] Initial setup failed.
echo Check the network connection, free disk space, and Python installation.
pause
exit /b 1
