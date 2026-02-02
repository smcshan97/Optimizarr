# Getting Started with Optimizarr

## Quick Setup (Development)

### 1. Clone the Repository

```bash
git clone <YOUR_GITHUB_URL>
cd optimizarr
```

### 2. Install Dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure Environment

```bash
cp .env.example .env
```

Edit `.env` and set:
- `OPTIMIZARR_SECRET_KEY` - Generate with: `openssl rand -hex 32`
- `OPTIMIZARR_ADMIN_USERNAME` - Your admin username
- `OPTIMIZARR_ADMIN_PASSWORD` - Your admin password

### 4. Run the Application

```bash
python -m app.main
```

The server will start on `http://localhost:5000`

### 5. Access the Web Interface

Open your browser to: `http://localhost:5000`

**Default credentials:**
- Username: `admin`
- Password: `admin` (or whatever you set in .env)

**⚠️ IMPORTANT:** Change the default password immediately after first login!

## API Documentation

Once running, visit `http://localhost:5000/docs` for interactive API documentation (Swagger UI).

## Basic Workflow

### 1. Create an Encoding Profile

**Via Web UI:**
- Login → Profiles tab → Create Profile

**Via API:**
```bash
curl -X POST http://localhost:5000/api/profiles \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "1080p H.265 Balanced",
    "resolution": "1920x1080",
    "codec": "h265",
    "encoder": "x265",
    "quality": 28,
    "audio_codec": "aac",
    "preset": "medium"
  }'
```

### 2. Add a Scan Root

**Via Web UI:**
- Scan Roots tab → Add Scan Root

**Via API:**
```bash
curl -X POST http://localhost:5000/api/scan-roots \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "path": "/path/to/your/media",
    "profile_id": 1,
    "enabled": true,
    "recursive": true
  }'
```

### 3. Scan for Files

**Via Web UI:**
- Queue tab → Click "Scan for Files"

**Via API:**
```bash
curl -X POST http://localhost:5000/api/queue/scan \
  -H "Authorization: Bearer YOUR_TOKEN"
```

### 4. Start Encoding

**Via Web UI:**
- Queue tab → Click "Start Encoding"

**Via API:**
```bash
curl -X POST http://localhost:5000/api/control/start \
  -H "Authorization: Bearer YOUR_TOKEN"
```

## Troubleshooting

### HandBrakeCLI Not Found

If you see "HandBrakeCLI not found", install it:

**Ubuntu/Debian:**
```bash
sudo apt install handbrake-cli
```

**macOS:**
```bash
brew install handbrake
```

**Docker:**
HandBrakeCLI will be included in the Docker image (Phase 4).

### Permission Errors

Ensure the application has read/write access to your media directories:

```bash
# Check permissions
ls -la /path/to/media

# Fix if needed
chmod -R 755 /path/to/media
```

### Database Issues

If you encounter database errors:

```bash
# Remove the database (⚠️ This will delete all data)
rm data/optimizarr.db

# Restart the application (it will recreate the database)
python -m app.main
```

## Development

### Running Tests

```bash
# Run the test script
./test_api.sh
```

### Accessing the Database

```bash
sqlite3 data/optimizarr.db

# Useful queries:
.tables                          # Show all tables
SELECT * FROM profiles;          # Show all profiles
SELECT * FROM queue;             # Show queue items
SELECT * FROM users;             # Show users
.quit                            # Exit
```

### Project Structure

```
optimizarr/
├── app/                    # Main application code
│   ├── main.py            # FastAPI app and startup
│   ├── config.py          # Configuration management
│   ├── database.py        # SQLite database layer
│   ├── auth.py            # Authentication (JWT, bcrypt)
│   ├── scanner.py         # Media file discovery
│   ├── encoder.py         # Video encoding logic
│   └── api/               # API endpoints
│       ├── routes.py      # Main API routes
│       ├── auth_routes.py # Authentication routes
│       ├── models.py      # Pydantic models
│       └── dependencies.py # Auth middleware
├── web/                    # Frontend
│   ├── static/js/         # JavaScript files
│   └── templates/         # HTML templates
├── data/                   # SQLite database (created on first run)
├── requirements.txt        # Python dependencies
└── .env                    # Configuration (create from .env.example)
```

## What's Next?

Current implementation is **Phase 1** - the core backend. Upcoming phases:

- **Phase 2:** Resource monitoring and throttling (CPU/GPU management)
- **Phase 3:** Scheduling system (time windows, auto-start/stop)
- **Phase 4:** Docker packaging and deployment
- **Phase 5:** Advanced features (hardware acceleration, two-pass encoding)

## Getting Help

- Check the logs in `data/` directory
- Visit the API docs at `/docs` for detailed endpoint information
- Review the main documentation in `Optimizarr_Technical_Documentation.docx`

## License

MIT License - see LICENSE file for details.
