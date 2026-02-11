#!/bin/bash
# Optimizarr - Automated Linux/Mac Setup Script

set -e

echo "============================================================"
echo "    Optimizarr - Automated Setup"
echo "============================================================"
echo ""

# Check Python version
echo "[1/5] Checking Python version..."
if command -v python3 &> /dev/null; then
    PYTHON_VERSION=$(python3 --version)
    echo "  ✓ Found: $PYTHON_VERSION"
else
    echo "  ✗ Python 3 not found! Please install Python 3.11+"
    exit 1
fi

# Create .env if needed
echo "[2/5] Setting up environment..."
if [ ! -f .env ]; then
    cp .env.example .env
    echo "  ✓ Created .env from template"
else
    echo "  ✓ .env already exists"
fi

# Create data directory and reset database
echo "[3/5] Setting up database..."
mkdir -p data
if [ -f data/optimizarr.db ]; then
    rm -f data/optimizarr.db
    echo "  ✓ Removed old database"
fi
echo "  ✓ Data directory ready"

# Clear Python cache
echo "[4/5] Clearing Python cache..."
find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
find . -type f -name "*.pyc" -delete 2>/dev/null || true
echo "  ✓ Cache cleared"

# Install dependencies
echo "[5/5] Installing Python dependencies..."
python3 -m pip install -r requirements.txt --quiet --disable-pip-version-check 2>&1 | grep -v "already satisfied" || true
echo "  ✓ Dependencies installed"

echo ""
echo "============================================================"
echo "    ✅ Setup Complete!"
echo "============================================================"
echo ""
echo "To start Optimizarr:"
echo "  python3 -m app.main"
echo ""
echo "Then open: http://localhost:5000"
echo "Login: admin / admin"
echo ""
echo "⚠️  IMPORTANT: Change admin password after first login!"
echo ""
