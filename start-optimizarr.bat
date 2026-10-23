@echo off
REM ---------------------------------------------------------------------------
REM Optimizarr launcher.
REM
REM Self-elevates via UAC, because CPU-temp throttling reads sensors over WMI
REM and that needs admin. GPU temp (pynvml) works unelevated, so if you cancel
REM the UAC prompt the app still runs -- it just loses the CPU-temp trigger.
REM
REM Double-click this file, or make a shortcut to it.
REM ---------------------------------------------------------------------------

net session >nul 2>&1
if %errorlevel% neq 0 (
    echo Requesting administrator privileges for CPU-temp monitoring...
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

cd /d "%~dp0"

REM Prefer a project venv if one exists; otherwise fall back to system Python.
if exist ".venv\Scripts\python.exe" (
    set "PY=.venv\Scripts\python.exe"
) else if exist "venv\Scripts\python.exe" (
    set "PY=venv\Scripts\python.exe"
) else (
    set "PY=python"
)

"%PY%" --version >nul 2>&1
if %errorlevel% neq 0 (
    echo.
    echo ERROR: Python not found ^(tried "%PY%"^).
    echo Install Python 3.11+ and make sure it is on PATH, or create a venv:
    echo     python -m venv .venv
    echo     .venv\Scripts\python.exe -m pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)

echo Starting Optimizarr -- http://localhost:5757
echo Close this window to stop the server.
echo.

"%PY%" -m app.main
set "EXITCODE=%errorlevel%"

echo.
if %EXITCODE% neq 0 (
    echo Optimizarr exited with code %EXITCODE%.
    echo If the port is taken, find the owner with:
    echo     Get-NetTCPConnection -LocalPort 5757 -State Listen ^| ForEach-Object { Get-Process -Id $_.OwningProcess }
) else (
    echo Optimizarr stopped.
)
echo Press any key to close this window.
pause >nul
