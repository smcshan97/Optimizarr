# Optimizarr Windows Setup Script
# Automatically fixes common Windows compatibility issues

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Optimizarr Windows Setup" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Fix 1: Database path in .env
Write-Host "[1/4] Fixing database path in .env..." -ForegroundColor Yellow
if (Test-Path .env) {
    $env = Get-Content .env -Raw
    $env = $env -replace 'OPTIMIZARR_DB_PATH=.*', 'OPTIMIZARR_DB_PATH=data/optimizarr.db'
    $env | Set-Content .env -NoNewline
    Write-Host "  ✓ Fixed .env database path" -ForegroundColor Green
} else {
    Write-Host "  ⚠ .env file not found, skipping" -ForegroundColor Yellow
}

# Fix 2: Dict type hint for Python 3.14
Write-Host ""
Write-Host "[2/4] Fixing Dict type hint for Python 3.14..." -ForegroundColor Yellow
if (Test-Path app\api\routes.py) {
    $content = Get-Content app\api\routes.py -Raw
    if ($content -match 'Dict\[str, str\]') {
        $content = $content -replace "Dict\[str, str\]", "dict"
        $content | Set-Content app\api\routes.py -NoNewline
        Write-Host "  ✓ Fixed Dict type hints" -ForegroundColor Green
    } else {
        Write-Host "  ✓ Already fixed" -ForegroundColor Green
    }
} else {
    Write-Host "  ✗ routes.py not found!" -ForegroundColor Red
}

# Fix 3: Create data directory
Write-Host ""
Write-Host "[3/4] Creating data directory..." -ForegroundColor Yellow
New-Item -ItemType Directory -Force -Path "data" | Out-Null
Write-Host "  ✓ Data directory ready" -ForegroundColor Green

# Fix 4: Check Python version
Write-Host ""
Write-Host "[4/4] Checking Python version..." -ForegroundColor Yellow
$pythonVersion = python --version 2>&1
Write-Host "  → $pythonVersion" -ForegroundColor Cyan

# Summary
Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Setup Complete!" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Next steps:" -ForegroundColor Yellow
Write-Host "  1. Install dependencies: pip install apscheduler" -ForegroundColor White
Write-Host "  2. Start server: python -m app.main" -ForegroundColor White
Write-Host "  3. Open browser: http://localhost:5000" -ForegroundColor White
Write-Host "  4. Login with: admin / admin" -ForegroundColor White
Write-Host ""
Write-Host "Press any key to continue..." -ForegroundColor Gray
$null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
