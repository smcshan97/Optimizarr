@echo off
REM Quick fix for Python 3.14 compatibility
echo Fixing Python 3.14 compatibility...
echo.

REM Uninstall old pydantic that requires Rust
python -m pip uninstall -y pydantic pydantic-core pydantic-settings 2>nul

REM Install compatible versions
echo Installing Python 3.14 compatible packages...
python -m pip install --upgrade fastapi==0.115.0 uvicorn[standard]==0.32.0 pydantic==2.10.3 pydantic-settings==2.6.1 psutil==6.1.0 pynvml==11.5.3 passlib[bcrypt]==1.7.4 pyjwt==2.9.0 apscheduler==3.10.4 python-multipart==0.0.12 jinja2==3.1.4 python-dotenv==1.0.1 --quiet --disable-pip-version-check

echo.
echo ============================================================
echo   Python 3.14 Fix Complete!
echo ============================================================
echo.
echo Now run: python -m app.main
echo.
pause
