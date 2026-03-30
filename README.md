# Optimizarr

**Automated media optimization for the *arr stack.**

Optimizarr scans your media libraries, identifies files that can be re-encoded to modern codecs, queues them intelligently, and encodes them with HandBrakeCLI — all while managing system resources so your server stays responsive. Think of it as the missing optimization layer between Sonarr/Radarr and your media player.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![GitHub release](https://img.shields.io/github/v/release/smcshan97/Optimizarr?include_prereleases)](https://github.com/smcshan97/Optimizarr/releases)

---

## Why Optimizarr?

Most media libraries accumulate files from different sources — old DVD rips in H.264, downloads in varying quality, home videos in ancient codecs. Re-encoding everything to AV1 or H.265 can cut storage by 40–60% with no visible quality loss, but doing it manually is tedious and error-prone.

Optimizarr automates the entire pipeline: scan → analyze → queue → encode → replace. Set it up once and let it work through your library overnight.

### Optimizarr vs Tdarr

| | Tdarr | Optimizarr |
|---|---|---|
| **Installation** | Complex multi-component setup | Single Python app, one setup script |
| **Open Source** | v2 is closed-source | 100% open source, always will be |
| **Language** | Node.js | Python — easy to extend and debug |
| **AI Upscaling** | Not available | Integrated (Real-ESRGAN, Real-CUGAN, Waifu2x) |
| **3D Conversion** | Not available | iw3 stereo 2D→3D and 3D→2D |
| **Statistics** | Paid pro feature | Free, built-in |
| **Integrations** | Limited | Sonarr, Radarr, Stash (GraphQL) |
| **Resource Control** | Basic | Temperature-based throttling with per-trigger toggles |

---

## Features

**Encoding Pipeline** — Scan directories, detect files that don't match your target profile, queue them by priority, and encode with HandBrakeCLI. Multi-pass encoding, per-file savings estimates, and automatic original replacement.

**Encoding Profiles** — Define target codec (AV1, H.265, H.264, VP9), resolution, framerate, quality (CRF), preset, audio passthrough rules, subtitle handling, and container format. Assign profiles to scan roots or apply manually.

**AI Upscaling** — Upscale SD content to HD before encoding using Real-ESRGAN, Real-CUGAN, or Waifu2x. Configurable per scan root or per profile. The upscale runs as a pre-processing step, then HandBrake encodes the upscaled file.

**3D Stereo Conversion** — Convert 2D video to stereoscopic 3D using iw3 (nunif), or extract 2D from existing 3D content with ffmpeg. Runs as a pipeline stage before upscaling and encoding.

**External Connections** — Pull libraries from Sonarr and Radarr via REST API, or from Stash via GraphQL. Webhook endpoints receive events and auto-queue new imports. API keys are encrypted at rest with Fernet.

**Scheduling** — Define encoding windows by day-of-week and time range. Optimizarr only encodes during your schedule and pauses outside it. Manual override available.

**Resource Management** — Temperature-based throttling prevents thermal damage without deadlocking encodes. GPU temperature (NVIDIA via pynvml) and CPU temperature (WMI/psutil) as primary pause triggers. Memory % and CPU usage % available as optional secondary triggers. Process priority control.

**Folder Watcher** — Watchdog-based filesystem monitoring auto-queues new files as they land in scan roots. Probes codec info and applies the appropriate profile.

**Queue Management** — Sort, filter, search, bulk select, prioritize, pause, resume, cancel. Per-job progress tracking with real-time updates.

**Statistics Dashboard** — Space saved, encoding time, file counts, codec distribution, all with date filtering.

**Dark UI** — Sonarr/Radarr-inspired dark theme with glass cards, sidebar navigation, and responsive layout.

---

## Quick Start

### Prerequisites

- **Python 3.11+** — [python.org/downloads](https://www.python.org/downloads/)
- **HandBrakeCLI** — [handbrake.fr/downloads](https://handbrake.fr/downloads.php)
- **ffmpeg / ffprobe** — [ffmpeg.org/download](https://ffmpeg.org/download.html)
- **NVIDIA GPU** (optional) — for NVENC encoding and GPU temperature monitoring

Make sure `HandBrakeCLI`, `ffmpeg`, and `ffprobe` are in your system PATH.

### Windows

```powershell
git clone https://github.com/smcshan97/Optimizarr.git
cd Optimizarr
.\setup-windows.ps1
python -m app.main
```

### Linux / macOS

```bash
git clone https://github.com/smcshan97/Optimizarr.git
cd Optimizarr
chmod +x setup.sh && ./setup.sh
python3 -m app.main
```

### First Login

Open **http://localhost:5000** and log in with `admin` / `admin`. **Change the default password immediately** in Settings → Account.

---

## Configuration

### Encoding Profiles

Create profiles in the UI under the Profiles tab. Key settings:

- **Codec**: `svt_av1` (best compression), `x265` (wide compatibility), `x264` (universal)
- **Quality (CRF)**: 20–28 for AV1, 18–24 for H.265 — lower = better quality, larger files
- **Preset**: Speed vs. quality tradeoff — `8` is a good starting point for SVT-AV1
- **Resolution**: Target output resolution (original, 1080p, 720p, etc.)

### Scan Roots

Add directories to scan under the Scan Roots tab. Each scan root is linked to a profile and can have its own AI upscaling and 3D conversion settings.

### External Connections

Configure Sonarr, Radarr, or Stash connections in Settings → External Connections. Provide the URL and API key, then use the Sync button to pull libraries.

### Resource Throttling

Settings → Resource Management lets you configure temperature-based pause triggers. Defaults: pause if GPU exceeds 83°C. Memory % and CPU usage % are available as optional secondary triggers (encoding pegs CPU/GPU to 100% by design, so usage-based triggers will pause most jobs).

---

## Tech Stack

| Component | Technology |
|---|---|
| Backend | Python, FastAPI, SQLite (WAL mode) |
| Frontend | Vanilla JavaScript, Tailwind CSS |
| Encoding | HandBrakeCLI, ffmpeg |
| AI Upscaling | Real-ESRGAN, Real-CUGAN, Waifu2x |
| 3D Conversion | iw3 (nunif) |
| GPU Monitoring | pynvml, nvidia-smi |
| Integrations | Sonarr/Radarr REST, Stash GraphQL |
| Auth | bcrypt + JWT |
| Scheduling | APScheduler |

---

## API

Optimizarr exposes a full REST API. Interactive docs are available at `http://localhost:5000/docs` (Swagger UI) when the server is running.

---

## Contributing

Optimizarr is a solo project but contributions are welcome. Open an issue first to discuss what you'd like to change.

---

## License

MIT © 2026 Shyriq' McShan
