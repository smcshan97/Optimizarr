# Optimizarr - Automated Media Optimization

**Part of the \*arr Stack Family** 🎬

Optimizarr is an intelligent, automated media optimization tool for self-hosted media servers. Systematically convert video files to modern codecs (AV1, H.265) with intelligent resource management and scheduling.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)

## 🚀 Quick Start

### Windows

```powershell
# Extract archive, then:
.\setup-windows.ps1
python -m app.main
```

### Linux / macOS

```bash
# Extract archive, then:
./setup.sh
python3 -m app.main
```

**Open:** http://localhost:5000  
**Login:** admin / admin

⚠️ **Change admin password immediately!**

---

## ✨ Features

- ✅ **Video Scanning** - Find all media files recursively
- ✅ **HandBrakeCLI** - Industry-standard transcoding
- ✅ **Profiles** - AV1, H.265, H.264 presets
- ✅ **Queue** - Priority-based with filters & search
- ✅ **Scheduling** - Time windows & days
- ✅ **Web UI** - Dark theme, responsive design

---

## 🐛 Troubleshooting

### "500 Error" on Scan/Edit/Delete

**You're running an OLD version!**

1. Stop server (Ctrl+C)
2. Extract **v1.0-FIXED.zip**
3. Run `setup-windows.ps1` or `./setup.sh`
4. Start: `python -m app.main`

### Database Errors

```powershell
# Delete old database
rm data/optimizarr.db
python -m app.main
```

---

## 📚 Full Documentation

- **API:** http://localhost:5000/docs
- **Design:** See `ARR_STACK_DESIGN.md`
- **Technical:** See `Optimizarr_Technical_Documentation.docx`

---

## 📄 License

MIT © 2026 Shyriq' McShan
