# Optimizarr Deployment Guide

## 🚀 Quick Start (Local Development)

### Prerequisites
- Python 3.11 or higher
- HandBrakeCLI (optional, but recommended for full functionality)

### Installation & Running

```bash
# Navigate to project directory
cd /home/claude/optimizarr

# Run the startup script
./start.sh

# Alternatively, run manually:
pip install -r requirements.txt --break-system-packages
python3 -m app.main
```

The server will start on **http://localhost:5000**

**Default Credentials:**
- Username: `admin`
- Password: `admin`

⚠️ **IMPORTANT:** Change the admin password immediately after first login!

---

## 📦 Pushing to GitHub

The repository is ready to push to https://github.com/smcshan97/Optimizarr.git

### First-Time Push

```bash
cd /home/claude/optimizarr

# Verify remote is configured
git remote -v
# Should show: origin  https://github.com/smcshan97/Optimizarr.git

# Check current status
git status

# If there are uncommitted changes, commit them:
git add .
git commit -m "Update: [describe your changes]"

# Push to GitHub (main branch)
git push -u origin main
```

### Subsequent Updates

```bash
# Stage changes
git add .

# Commit with message
git commit -m "feat: add new feature" 
# or
git commit -m "fix: resolve bug"
# or  
git commit -m "docs: update documentation"

# Push to GitHub
git push
```

---

## 🔐 Security Configuration

### Generate Secure Secret Key

```bash
# Generate a secure random key
openssl rand -hex 32

# Update .env file
OPTIMIZARR_SECRET_KEY=<paste-generated-key-here>
```

### Change Admin Password

Via Web UI:
1. Login as admin
2. Navigate to Settings → Users (coming soon)
3. Change password

Via API:
```bash
curl -X POST http://localhost:5000/api/auth/change-password \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "current_password": "admin",
    "new_password": "your_new_secure_password"
  }'
```

---

## 🐳 Docker Deployment (Phase 4 - Coming Soon)

Docker support is planned for Phase 4. The Dockerfile will include:
- HandBrakeCLI pre-installed
- Proper PUID/PGID user mapping
- Volume mounts for media, config, and data
- GPU passthrough support (NVIDIA)

---

## 🌐 Production Deployment

### Using Nginx Reverse Proxy

```nginx
server {
    listen 80;
    server_name optimizarr.yourdomain.com;

    location / {
        proxy_pass http://localhost:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

### Using systemd Service

Create `/etc/systemd/system/optimizarr.service`:

```ini
[Unit]
Description=Optimizarr Media Optimization Service
After=network.target

[Service]
Type=simple
User=your_user
WorkingDirectory=/path/to/optimizarr
ExecStart=/usr/bin/python3 -m app.main
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Enable and start:
```bash
sudo systemctl daemon-reload
sudo systemctl enable optimizarr
sudo systemctl start optimizarr
sudo systemctl status optimizarr
```

---

## 📊 Monitoring & Logs

### View Logs
```bash
# If running directly
tail -f optimizarr_server.log

# If running as systemd service
sudo journalctl -u optimizarr -f
```

### Check Server Status
```bash
# Test health endpoint
curl http://localhost:5000/api/health

# Should return: {"status":"ok","service":"optimizarr"}
```

---

## 🧪 Testing

### Run API Tests
```bash
./test_api.sh
```

### Manual Testing
```bash
# Login and get token
TOKEN=$(curl -s -X POST http://localhost:5000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"admin"}' \
  | grep -o '"access_token":"[^"]*"' | cut -d'"' -f4)

# List profiles
curl -H "Authorization: Bearer $TOKEN" http://localhost:5000/api/profiles

# Get statistics
curl -H "Authorization: Bearer $TOKEN" http://localhost:5000/api/stats
```

---

## 🔧 Troubleshooting

### Server Won't Start

**Issue:** Port 5000 already in use
```bash
# Find process using port 5000
lsof -i :5000
# or
netstat -tlnp | grep 5000

# Kill the process
kill -9 <PID>
```

**Issue:** Module not found errors
```bash
# Reinstall dependencies
pip install -r requirements.txt --break-system-packages --force-reinstall
```

### Database Issues

**Reset Database:**
```bash
# WARNING: This deletes all data!
rm -f data/optimizarr.db

# Restart server to recreate
./start.sh
```

### HandBrakeCLI Not Found

**Ubuntu/Debian:**
```bash
sudo apt update
sudo apt install handbrake-cli
```

**macOS:**
```bash
brew install handbrake
```

**Verify Installation:**
```bash
HandBrakeCLI --version
```

---

## 📁 Directory Structure

```
/home/claude/optimizarr/
├── app/                    # Application code
├── web/                    # Frontend templates
├── data/                   # SQLite database (auto-created)
├── config/                 # Configuration files
├── .env                    # Environment configuration
├── start.sh                # Startup script
└── requirements.txt        # Python dependencies
```

---

## 🆘 Getting Help

- **GitHub Issues:** https://github.com/smcshan97/Optimizarr/issues
- **Documentation:** See PROJECT_STATUS.md and GETTING_STARTED.md
- **API Docs:** http://localhost:5000/docs (when server is running)

---

## ✅ Pre-Flight Checklist

Before pushing to production:

- [ ] Changed default admin password
- [ ] Generated secure SECRET_KEY in .env
- [ ] Tested all API endpoints with test_api.sh
- [ ] Verified HandBrakeCLI is installed
- [ ] Configured reverse proxy with HTTPS
- [ ] Set up systemd service for auto-start
- [ ] Configured firewall rules
- [ ] Set up regular database backups
- [ ] Reviewed security notes in PROJECT_STATUS.md

---

**Last Updated:** February 2, 2026  
**Version:** 1.0.0 (Phase 1)
