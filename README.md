# Optimizarr

**Automated Media Optimization System**

Optimizarr is an intelligent, automated media optimization tool designed for self-hosted media servers. It systematically converts video files to user-specified formats, codecs, and quality settings while intelligently managing system resources and respecting user-defined schedules.

## Quick Start

### Prerequisites

- Python 3.11+
- HandBrakeCLI (for video encoding)

### Installation

1. **Clone the repository**
   ```bash
   cd /home/claude/optimizarr
   ```

2. **Install Python dependencies**
   ```bash
   pip install -r requirements.txt --break-system-packages
   ```

3. **Configure environment**
   ```bash
   cp .env.example .env
   # Edit .env and change the secret key and admin credentials
   ```

4. **Run the application**
   ```bash
   python -m app.main
   ```

5. **Access the web interface**
   Open your browser to http://localhost:5000

   Default credentials:
   - Username: `admin`
   - Password: `admin` (change this immediately!)

## Features

### Core Features
- ✅ **Automated Media Discovery** - Recursively scan directories for video files
- ✅ **Smart Queue Management** - Priority-based processing with status tracking
- ✅ **Multiple Encoding Profiles** - Configure target codecs, resolutions, and quality
- ✅ **HandBrakeCLI Integration** - Industry-standard video transcoding
- ✅ **Progress Monitoring** - Real-time encoding progress tracking
- ✅ **Web Interface** - Clean, responsive UI for complete control
- ✅ **REST API** - Full programmatic access with JWT authentication

### Planned Features
- ⏳ **Resource-Aware Processing** - CPU/GPU throttling and auto-pause
- ⏳ **Flexible Scheduling** - Time windows and day-of-week encoding
- ⏳ **Hardware Acceleration** - NVENC, QuickSync, VCE support
- ⏳ **Advanced Profiles** - Two-pass encoding, custom HandBrake arguments

## API Documentation

Once running, visit http://localhost:5000/docs for interactive API documentation.

### Key Endpoints

- `POST /api/auth/login` - Authenticate and receive JWT token
- `GET /api/profiles` - List encoding profiles
- `GET /api/scan-roots` - List configured scan directories
- `GET /api/queue` - View encoding queue
- `POST /api/queue/scan` - Trigger media scan
- `POST /api/control/start` - Start encoding
- `GET /api/stats` - System statistics

## Configuration

### Creating an Encoding Profile

```bash
curl -X POST http://localhost:5000/api/profiles \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "1080p H.265",
    "resolution": "1920x1080",
    "codec": "h265",
    "encoder": "x265",
    "quality": 28,
    "audio_codec": "aac",
    "preset": "medium"
  }'
```

### Adding a Scan Root

```bash
curl -X POST http://localhost:5000/api/scan-roots \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "path": "/path/to/media",
    "profile_id": 1,
    "enabled": true,
    "recursive": true
  }'
```

## Development Status

This is **Phase 1** of development - the core backend is functional with:
- Database and models
- Authentication system
- Media scanner
- Basic encoder
- REST API
- Web interface

## Project Structure

```
optimizarr/
├── app/
│   ├── main.py          # FastAPI application
│   ├── config.py        # Configuration management
│   ├── database.py      # SQLite database layer
│   ├── auth.py          # Authentication & JWT
│   ├── scanner.py       # Media file discovery
│   ├── encoder.py       # Video encoding
│   └── api/
│       ├── routes.py    # API endpoints
│       ├── auth_routes.py
│       ├── models.py    # Pydantic models
│       └── dependencies.py
├── web/
│   ├── static/
│   │   └── js/
│   │       └── app.js
│   └── templates/
│       ├── index.html
│       └── login.html
├── data/                # SQLite database (created on first run)
├── requirements.txt
└── .env                # Configuration
```

## Next Steps

1. **Test the basic functionality**
   - Create a profile
   - Add a scan root
   - Scan for files
   - Start encoding

2. **Phase 2: Resource Management**
   - Implement CPU/GPU monitoring
   - Add resource throttling
   - Auto-pause on high system load

3. **Phase 3: Scheduling**
   - Time window configuration
   - Day-of-week selection
   - Automatic start/stop

4. **Phase 4: Docker**
   - Create Dockerfile
   - docker-compose configuration
   - GPU passthrough setup

## License

MIT License - see LICENSE file

## Author

Built with Claude Code
