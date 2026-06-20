# Optimizarr Deployment Guide

Optimizarr is a **native Windows** application. It talks directly to host
hardware (NVENC, GPU temp via pynvml, CPU temp via WMI), reads Windows Active
Hours from the registry, and works against local/removable/network drive
paths. Run it as a normal Windows process — see *Packaging* below. Docker is
**not** recommended on this host (see *Why not Docker*).

---

## Prerequisites

- **Windows 10/11**
- **Python 3.11+** (3.14 is fine)
- **HandBrakeCLI** on `PATH` — the encoder (`HandBrakeCLI --version` should work)
- **ffmpeg / ffprobe** on `PATH` — probing, stereo/3D, duration backfill
- **NVIDIA GPU + current driver** for NVENC (H.264/H.265). AV1 falls back to
  software SVT-AV1 on pre-RTX-40 cards.

Optional: the AI upscalers (Real-ESRGAN / Real-CUGAN / Waifu2x) are downloaded
on demand from the Upscaler tab.

---

## Install & run

```powershell
cd D:\Downloads\optimizarr
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m app.main
```

Server starts on **http://localhost:5000**.

First-run credentials are `admin` / `admin` — you are forced to change the
password on first login. The `SECRET_KEY` is auto-generated and persisted to
`data\.secret_key` on first run.

Config is via `OPTIMIZARR_`-prefixed environment variables (or a `.env` file):
`OPTIMIZARR_PORT`, `OPTIMIZARR_HOST`, `OPTIMIZARR_DB_PATH`,
`OPTIMIZARR_CORS_ORIGINS`, `OPTIMIZARR_JWT_EXPIRATION_HOURS`, etc.

Keep it bound to localhost (default) unless you intend to expose it; auth is
JWT but this is a single-user home tool.

---

## Packaging — launch when you want, not at Windows boot

By design the heavy startup work (orphan-temp sweep, encode resume, duration
backfill) runs in background threads at **program start**, so launching is
quick and it does **not** belong in Windows boot. Pick whichever launcher fits:

**A. Shortcut / batch file (simplest).** A `start-optimizarr.bat`:

```bat
@echo off
cd /d D:\Downloads\optimizarr
call .venv\Scripts\activate.bat
python -m app.main
```

Double-click it (or pin a shortcut). To get **CPU-temp throttling**, run it
elevated: shortcut → Properties → Advanced → *Run as administrator*. (GPU temp
via pynvml works without elevation; CPU temp via WMI needs admin. The app
degrades gracefully — GPU temp is the primary trigger on RTX hardware.)

**B. PyInstaller elevated .exe.** Build a single launchable exe that requests
admin via its manifest, so a normal double-click gets CPU-temp monitoring:

```powershell
pip install pyinstaller
pyinstaller --name Optimizarr --onefile --uac-admin ^
  --add-data "web;web" app\main.py
```

Launch `dist\Optimizarr.exe` when you want it running. (Bundle/point it at
HandBrakeCLI + ffmpeg on `PATH`.)

**C. Start at login (optional, still not a boot service).** If you want it up
after *you* log in (not slowing the OS boot): Task Scheduler → Create Task →
Trigger *At log on* → Action run the bat/exe → check *Run with highest
privileges*. This keeps it off the Windows boot path while still auto-starting
your session.

> Avoid registering it as an auto-start Windows **Service** (NSSM/WinSW) if you
> don't want it touching Windows boot — that's exactly the "bog down boot"
> case. The login-trigger task above gives auto-start without it.

"Start where you left off" complements this: if the encoder was running when
you closed it, it resumes on the next launch; if you stopped it manually, it
stays stopped (intent persisted as the `encoder_autostart` setting).

---

## Why not Docker (on this host)

Containerizing fights the app's core host integrations and buys nothing on a
single Windows box:

| Needs | In a Windows container |
|---|---|
| CPU temp via WMI | ❌ no host sensor access |
| Windows Active Hours via registry | ❌ no host registry |
| NVENC + GPU temp (pynvml) | ⚠️ requires WSL2 + NVIDIA Container Toolkit, fiddly |
| Local/removable/network drives | ⚠️ each must be bind-mounted |

If you ever run a **headless Linux** box with mounted storage and don't need
host temp sensors or Active Hours, a container is reasonable — temperature
safety would then lean on GPU temp only. On Windows, native is the answer.

---

## Operations

- **Health:** `GET /api/health` (no auth) — versions of HandBrake/ffprobe/
  ffmpeg, DB + disk + queue depth + estimated hours remaining, connection
  status, unprocessed webhook count.
- **Dev log:** `python -m app.devlog 24` prints a compact digest of the last
  24h (encodes, problems deduped, recent events). Run it at the start of a
  troubleshooting session.
- **Backups:** raw DB via `GET /api/backup` (and restore), or portable
  human-readable config via `GET /api/backup/json` / `POST /api/restore/json`
  (profiles, scan roots, settings — no API keys).
- **Stray processes:** if you ever hard-kill the app mid-encode, end any
  leftover `HandBrakeCLI.exe` in Task Manager before relaunching; orphaned
  `*_optimized.*` partials are swept automatically on next start.

---

## Updating

```powershell
cd D:\Downloads\optimizarr
git pull
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt   # if deps changed
python -m pytest tests -q          # optional sanity check
python -m app.main
```

DB schema migrations run automatically on start (`PRAGMA table_info` →
`ALTER TABLE`), so pulling and relaunching is enough.
