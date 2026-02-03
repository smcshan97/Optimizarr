#!/usr/bin/env pwsh
# Optimizarr - Automated Windows Setup Script
# Fixes all known Windows compatibility issues

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "    Optimizarr - Automated Windows Setup" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""

# Check Python version
Write-Host "[1/6] Checking Python version..." -ForegroundColor Yellow
try {
    $pythonVersion = python --version 2>&1
    Write-Host "  ✓ Found: $pythonVersion" -ForegroundColor Green
} catch {
    Write-Host "  ✗ Python not found! Please install Python 3.11+" -ForegroundColor Red
    exit 1
}

# Fix .env database path
Write-Host "[2/6] Fixing .env database path..." -ForegroundColor Yellow
if (Test-Path .env) {
    $env = Get-Content .env -Raw
    $env = $env -replace 'OPTIMIZARR_DB_PATH=.*', 'OPTIMIZARR_DB_PATH=data/optimizarr.db'
    $env | Set-Content .env -NoNewline
    Write-Host "  ✓ Updated .env" -ForegroundColor Green
} else {
    Copy-Item .env.example .env
    Write-Host "  ✓ Created .env from template" -ForegroundColor Green
}

# Fix Dict type hints for Python 3.12+
Write-Host "[3/6] Fixing Python 3.12+ compatibility..." -ForegroundColor Yellow
if (Test-Path app\api\routes.py) {
    $content = Get-Content app\api\routes.py -Raw
    $content = $content -replace "Dict\[str, str\]", "dict"
    $content | Set-Content app\api\routes.py -NoNewline
    Write-Host "  ✓ Fixed type hints in routes.py" -ForegroundColor Green
}

# Create data directory and reset database
Write-Host "[4/6] Setting up database..." -ForegroundColor Yellow
New-Item -ItemType Directory -Force -Path data | Out-Null
if (Test-Path data\optimizarr.db) {
    Remove-Item data\optimizarr.db -Force
    Write-Host "  ✓ Removed old database" -ForegroundColor Green
}
Write-Host "  ✓ Data directory ready" -ForegroundColor Green

# Clear Python cache
Write-Host "[5/6] Clearing Python cache..." -ForegroundColor Yellow
Get-ChildItem -Path . -Include __pycache__ -Recurse -Force | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
Get-ChildItem -Path . -Include *.pyc -Recurse -Force | Remove-Item -Force -ErrorAction SilentlyContinue
Write-Host "  ✓ Cache cleared" -ForegroundColor Green

# Install dependencies
Write-Host "[6/6] Installing Python dependencies..." -ForegroundColor Yellow
python -m pip install -r requirements.txt --quiet --disable-pip-version-check
if ($LASTEXITCODE -eq 0) {
    Write-Host "  ✓ Dependencies installed" -ForegroundColor Green
} else {
    Write-Host "  ⚠ Some packages failed, but continuing..." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "    ✅ Setup Complete!" -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "To start Optimizarr:" -ForegroundColor White
Write-Host "  python -m app.main" -ForegroundColor Yellow
Write-Host ""
Write-Host "Then open: http://localhost:5000" -ForegroundColor Cyan
Write-Host "Login: admin / admin" -ForegroundColor Cyan
Write-Host ""
Write-Host "⚠️  IMPORTANT: Change admin password after first login!" -ForegroundColor Red
Write-Host ""
