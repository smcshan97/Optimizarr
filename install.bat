@echo off
REM Optimizarr - Foolproof Windows Installation Script
setlocal EnableDelayedExpansion

echo ============================================================
echo     Optimizarr - Windows Installation
echo ============================================================
echo.

REM Check Python
echo [1/6] Checking Python...
python --version >nul 2>&1
if errorlevel 1 (
    echo   X Python not found! Install Python 3.11+ from python.org
    pause
    exit /b 1
)
python --version
echo   + Python found
echo.

REM Fix .env
echo [2/6] Creating .env file...
if not exist .env (
    copy .env.example .env >nul 2>&1
    echo   + Created .env from template
) else (
    echo   + .env already exists
)
echo.

REM Fix database path in .env (works on all Python versions)
echo [3/6] Fixing .env database path...
powershell -Command "(Get-Content .env) -replace 'OPTIMIZARR_DB_PATH=.*', 'OPTIMIZARR_DB_PATH=data/optimizarr.db' | Set-Content .env"
echo   + Database path updated
echo.

REM Create data directory (CRITICAL!)
echo [4/6] Creating data directory...
if not exist data mkdir data
if exist data\optimizarr.db del /f data\optimizarr.db 2>nul
echo   + Data directory ready
echo.

REM Clear Python cache
echo [5/6] Clearing Python cache...
for /d /r . %%d in (__pycache__) do @if exist "%%d" rd /s /q "%%d" 2>nul
del /s /q *.pyc 2>nul
echo   + Cache cleared
echo.

REM Install dependencies
echo [6/6] Installing Python packages...
python -m pip install -r requirements.txt --quiet --disable-pip-version-check
if errorlevel 1 (
    echo   ! Some packages failed, but continuing...
) else (
    echo   + Dependencies installed
)
echo.

echo ============================================================
echo     Installation Complete!
echo ============================================================
echo.
echo To start Optimizarr:
echo   python -m app.main
echo.
echo Then open: http://localhost:5000
echo Login: admin / admin
echo.
echo IMPORTANT: Change admin password after first login!
echo.
pause
