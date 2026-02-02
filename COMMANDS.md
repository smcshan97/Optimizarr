# Optimizarr - Quick Command Reference

## 🚀 Starting the Server

```bash
# Quick start
cd /home/claude/optimizarr
./start.sh

# Manual start (recommended for stability)
python3 -m app.main

# With auto-reload (development only)
python3 -m app.main --reload
```

## 🔄 Git Commands

```bash
# Push to GitHub for the first time
cd /home/claude/optimizarr
git push -u origin main

# Regular workflow
git add .
git commit -m "your message"
git push

# Check status
git status
git log --oneline -10

# View remote
git remote -v
```

## 🧪 Testing

```bash
# Run API test suite
./test_api.sh

# Manual health check
curl http://localhost:5000/api/health

# Login and get token
curl -X POST http://localhost:5000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"admin"}'

# Use token (replace YOUR_TOKEN)
TOKEN="YOUR_TOKEN_HERE"
curl -H "Authorization: Bearer $TOKEN" http://localhost:5000/api/profiles
curl -H "Authorization: Bearer $TOKEN" http://localhost:5000/api/stats
curl -H "Authorization: Bearer $TOKEN" http://localhost:5000/api/queue
```

## 📦 Dependencies

```bash
# Install dependencies
pip install -r requirements.txt --break-system-packages

# Install HandBrakeCLI (Ubuntu/Debian)
sudo apt update && sudo apt install handbrake-cli

# Install HandBrakeCLI (macOS)
brew install handbrake

# Verify installation
HandBrakeCLI --version
```

## 🗄️ Database

```bash
# View database location
ls -lh data/optimizarr.db

# Backup database
cp data/optimizarr.db data/optimizarr.db.backup

# Reset database (WARNING: Deletes all data!)
rm -f data/optimizarr.db
./start.sh  # Will recreate
```

## 🔐 Security

```bash
# Generate secure secret key
openssl rand -hex 32

# Update .env file
nano .env
# Set OPTIMIZARR_SECRET_KEY=<generated-key>

# Change admin password via API
curl -X POST http://localhost:5000/api/auth/change-password \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "current_password": "admin",
    "new_password": "your_new_secure_password"
  }'
```

## 🐛 Troubleshooting

```bash
# Check if server is running
ps aux | grep "python.*app.main"

# Find process on port 5000
lsof -i :5000
netstat -tlnp | grep 5000

# Kill server
pkill -f "python.*app.main"

# View logs
tail -f optimizarr_server.log

# Clear Python cache
find . -type d -name __pycache__ -exec rm -rf {} +
find . -type f -name "*.pyc" -delete
```

## 📊 API Endpoints Quick Reference

```bash
# Authentication
POST   /api/auth/login              # Login
GET    /api/auth/me                 # Current user
POST   /api/auth/logout             # Logout
POST   /api/auth/change-password    # Change password

# Profiles
GET    /api/profiles                # List all
POST   /api/profiles                # Create
DELETE /api/profiles/{id}           # Delete

# Scan Roots
GET    /api/scan-roots              # List all
POST   /api/scan-roots              # Add
DELETE /api/scan-roots/{id}         # Delete

# Queue
GET    /api/queue                   # List items
POST   /api/queue/scan              # Trigger scan
PATCH  /api/queue/{id}              # Update
DELETE /api/queue/{id}              # Remove
POST   /api/queue/clear             # Clear all

# Control
POST   /api/control/start           # Start encoding
POST   /api/control/stop            # Stop encoding

# System
GET    /api/stats                   # Statistics
GET    /api/health                  # Health check
GET    /docs                        # API documentation
```

## 🌐 URLs

```bash
# Web Interface
http://localhost:5000

# Login Page
http://localhost:5000/login

# API Documentation
http://localhost:5000/docs

# API Base
http://localhost:5000/api
```

## 🔍 Examples

### Create Profile via API

```bash
curl -X POST http://localhost:5000/api/profiles \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "4K AV1",
    "resolution": "3840x2160",
    "codec": "av1",
    "encoder": "svt_av1",
    "quality": 30,
    "audio_codec": "opus",
    "preset": "6"
  }'
```

### Add Scan Root via API

```bash
curl -X POST http://localhost:5000/api/scan-roots \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "path": "/path/to/media",
    "profile_id": 1,
    "enabled": true,
    "recursive": true
  }'
```

### Trigger Media Scan

```bash
curl -X POST http://localhost:5000/api/queue/scan \
  -H "Authorization: Bearer $TOKEN"
```

### Start Encoding

```bash
curl -X POST http://localhost:5000/api/control/start \
  -H "Authorization: Bearer $TOKEN"
```

---

**Quick Reference Guide**  
Optimizarr v1.0.0  
Last Updated: February 2, 2026
