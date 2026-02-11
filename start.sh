#!/bin/bash
# Optimizarr Startup Script

set -e

echo "======================================================"
echo "Starting Optimizarr - Automated Media Optimization"
echo "======================================================"
echo ""

# Check if .env exists
if [ ! -f .env ]; then
    echo "⚠️  No .env file found. Creating from .env.example..."
    cp .env.example .env
    echo "⚠️  Please edit .env and set a secure SECRET_KEY and ADMIN_PASSWORD"
    echo ""
    read -p "Press Enter to continue with defaults or Ctrl+C to exit and configure .env..."
fi

# Check Python version
if ! command -v python3 &> /dev/null; then
    echo "✗ Python 3 not found. Please install Python 3.11 or higher."
    exit 1
fi

PYTHON_VERSION=$(python3 --version | cut -d' ' -f2 | cut -d'.' -f1,2)
echo "✓ Found Python $PYTHON_VERSION"

# Install dependencies
echo ""
echo "Installing Python dependencies..."
pip install -q -r requirements.txt --break-system-packages || pip install -q -r requirements.txt

# Check for HandBrakeCLI
if command -v HandBrakeCLI &> /dev/null; then
    HANDBRAKE_VERSION=$(HandBrakeCLI --version 2>&1 | head -n1)
    echo "✓ HandBrakeCLI found: $HANDBRAKE_VERSION"
else
    echo "⚠️  HandBrakeCLI not found. Media scanning will be limited."
    echo "   Install with: sudo apt install handbrake-cli (Debian/Ubuntu)"
    echo "                or: brew install handbrake (macOS)"
fi

# Create data directory
mkdir -p data config

echo ""
echo "======================================================"
echo "Starting Optimizarr server..."
echo "======================================================"
echo ""

# Start server (disable auto-reload for stability)
exec python3 -m app.main
