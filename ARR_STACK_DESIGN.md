# Optimizarr - *arr Stack Design Principles

## Following the *arr Stack Convention

Optimizarr follows design principles from Sonarr, Radarr, and the *arr ecosystem:

### 1. Naming Convention
- **Format**: `*arr` suffix (Sonarr, Radarr, Lidarr, Optimizarr)
- **Purpose**: Clearly identifies as part of the self-hosted media stack

### 2. API Design
- **RESTful endpoints** with clear resource hierarchy
- **Consistent response formats** (JSON)
- **Proper HTTP status codes** (200, 201, 400, 404, 500)
- **API versioning** (e.g., `/api/v1/`)

### 3. UI/UX Patterns
- **Dark theme** as default (matches *arr apps)
- **Tabbed navigation** (Series/Movies/Queue/System in Sonarr → Queue/Profiles/Scan Roots/Schedule in Optimizarr)
- **Status indicators** with emoji/icons
- **Real-time updates** and auto-refresh
- **Responsive design** for mobile/desktop

### 4. Queue Management
- **Priority-based processing**
- **Status tracking** (pending, processing, completed, failed)
- **Progress indicators**
- **Bulk actions** (select multiple, delete, modify)
- **Filters and search**

### 5. Settings Organization
- **Media Management** → Profile settings, quality definitions
- **Indexers** → Scan roots (where to find media)
- **Download Clients** → Encoder configuration
- **General** → Resource limits, scheduling

### 6. Background Processing
- **Task scheduling** (cron-like with APScheduler)
- **Resource throttling** (CPU/GPU limits)
- **Automatic pause/resume** based on system load
- **Queue processing** with configurable concurrency

### 7. Database Design
- **SQLite for simplicity** (Sonarr uses SQLite)
- **Clear schema** with foreign keys
- **History tracking** for all operations
- **Settings table** for key-value configuration

### 8. Docker Deployment
- **PUID/PGID support** for file permissions
- **Volume mounts** for media, config, data
- **Health checks**
- **GPU passthrough** for hardware encoding

### 9. Authentication & Security
- **API keys** for automation/scripts
- **JWT tokens** for web UI sessions
- **Role-based access** (admin vs. user)
- **Secure password hashing** (bcrypt)

### 10. Logging & Diagnostics
- **Structured logging** with levels (DEBUG, INFO, WARNING, ERROR)
- **Health check endpoint** (`/api/health`)
- **System diagnostics** page
- **Permission checker** for media files

## Optimizarr-Specific Features

While following *arr conventions, Optimizarr adds:

1. **Encoding Profiles** - Similar to Quality Profiles in Sonarr
2. **Scan Roots** - Similar to Root Folders in *arr apps
3. **Resource Management** - CPU/GPU monitoring and throttling
4. **Scheduling** - Time-based encoding windows
5. **Progress Tracking** - Real-time encoding progress

## File Structure (Following *arr Pattern)

```
optimizarr/
├── app/                    # Backend (like src/ in Sonarr)
│   ├── main.py            # Application entry
│   ├── config.py          # Configuration
│   ├── database.py        # Database ORM
│   ├── scanner.py         # Media scanner
│   ├── encoder.py         # Encoding engine
│   ├── scheduler.py       # Task scheduling
│   ├── resources.py       # Resource monitoring
│   └── api/               # REST API
│       ├── routes.py
│       └── models.py
├── web/                   # Frontend (like UI/ in Sonarr)
│   ├── static/
│   │   └── js/
│   └── templates/
├── docker/                # Docker configuration
├── config/                # Runtime configuration
└── data/                  # Database and runtime data
```

## Next Steps for *arr Alignment

To fully align with *arr stack:

1. **Add notifications** (Discord, Telegram, Email) - like Sonarr's Connect settings
2. **Implement indexer system** - more sophisticated media discovery
3. **Add calendar view** - show encoding schedule visually
4. **Create system/status page** - health monitoring dashboard
5. **Add backup/restore** - for database and configuration
6. **Implement webhooks** - on encoding start/complete/fail
7. **Add custom scripts** - run before/after encoding
8. **Create mobile app** - like nzb360 for *arr apps

## Reference

- Sonarr: https://github.com/Sonarr/Sonarr
- Radarr: https://github.com/Radarr/Radarr
- *arr Wiki: https://wiki.servarr.com/
