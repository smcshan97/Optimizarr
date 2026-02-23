# External Connections — Design Document
## Sonarr, Radarr, and Stash Integration

**Status:** Planned — not yet implemented  
**Priority:** Sonarr/Radarr first, Stash after privacy toggles are solid  
**Prerequisite for Stash:** `show_in_stats` privacy toggle must be fully working ✅

---

## Why Connect to Sonarr/Radarr?

Sonarr and Radarr have already done the hard work of cataloguing every file in your library — they know the file path, codec, resolution, video quality, audio tracks, language, and episode/movie metadata. Instead of running ffprobe on 1,493 files ourselves (slow, sometimes wrong), we can ask them directly via their REST API.

Benefits:
- **Instant codec/resolution data** — no ffprobe scan needed for existing libraries
- **Smarter queue decisions** — know if a file is a Blu-ray remux vs a web-dl vs an HDTV rip
- **Import library directly** — pull Radarr's whole movie list into Optimizarr queue in one click
- **Event-driven updates** — Sonarr/Radarr can call Optimizarr webhook when a new download completes
- **Skip files already at target quality** — check quality profile before queuing

---

## Architecture

### Connection Storage (database)

New table: `external_connections`

```sql
CREATE TABLE external_connections (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,           -- e.g. "Radarr - Movies"
    app_type    TEXT NOT NULL,           -- 'sonarr' | 'radarr' | 'stash'
    base_url    TEXT NOT NULL,           -- http://localhost:7878
    api_key     TEXT NOT NULL,           -- stored encrypted at rest (AES-256)
    enabled     BOOLEAN DEFAULT 1,
    show_in_stats BOOLEAN DEFAULT 1,     -- privacy: hide this connection from stats
    last_tested TIMESTAMP,
    last_synced TIMESTAMP,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

**Security:** API keys must be encrypted at rest using Fernet symmetric encryption (Python `cryptography` package). The encryption key is derived from the instance's `SECRET_KEY` setting. Keys are never logged or returned in API responses — only a masked preview (last 4 chars) is shown in the UI.

### Scan Root Linkage

When a user links a Sonarr/Radarr connection to a scan root, that root's files can be enriched with metadata from the external app. New column on `scan_roots`:

```sql
ALTER TABLE scan_roots ADD COLUMN external_connection_id INTEGER REFERENCES external_connections(id);
```

---

## Sonarr / Radarr API

Both use the same v3 API structure. Base URL + `/api/v3/` prefix.

### Authentication
All requests: `X-Api-Key: {api_key}` header.

### Key Endpoints

**Test connection:**
```
GET /api/v3/system/status
→ { "appName": "Radarr", "version": "5.x.x", ... }
```

**Get all media (Radarr):**
```
GET /api/v3/movie
→ [{ "id", "title", "year", "movieFile": { "path", "size", "mediaInfo": { "videoCodec", "videoResolution", "videoBitrate", "audioCodecs" } } }]
```

**Get all media (Sonarr):**
```
GET /api/v3/series
→ [{ "id", "title", "episodeFileCount", "path" }]

GET /api/v3/episodefile?seriesId={id}
→ [{ "id", "path", "size", "mediaInfo": { "videoCodec", "resolution", "videoBitrate" } }]
```

**Register webhook (Sonarr/Radarr):**
```
POST /api/v3/notification
{
  "name": "Optimizarr",
  "implementation": "Webhook",
  "fields": [
    { "name": "url", "value": "http://optimizarr:5000/api/webhooks/sonarr" },
    { "name": "method", "value": 1 }   // POST
  ],
  "onDownload": true,
  "onUpgrade": true
}
```

### `mediaInfo.videoCodec` mapping to Optimizarr codecs

| Sonarr/Radarr value | Optimizarr codec |
|---------------------|-----------------|
| `x264` / `AVC`      | `h264`          |
| `x265` / `HEVC`     | `h265`          |
| `AV1`               | `av1`           |
| `VP9`               | `vp9`           |
| `XviD` / `DivX`     | `mpeg4`         |
| `MPEG-2`            | `mpeg2`         |

---

## Stash Integration

> ⚠️ **Stash contains adult content by default. Privacy toggles (`show_in_stats`) must be working and tested before implementing this connection.**

Stash is a self-hosted media organiser for personal video collections. It uses a **GraphQL API** rather than REST.

### Stash GraphQL Endpoint
```
POST http://localhost:9999/graphql
Authorization: ApiKey {api_key}
Content-Type: application/json
```

### Privacy Requirements (mandatory before implementation)

1. **`show_in_stats` must default to `false` for Stash connections** — opt-in visibility only
2. File paths from Stash must never appear in logs when the connection has `show_in_stats = false`
3. Stash-sourced queue items must display as `[Private]` in the file column when stats privacy is off
4. The Statistics tab codec/resolution/savings charts must exclude Stash items by default
5. Any Stash connection must show a clear warning: *"Files from this source will appear in your encoding queue. Ensure no one else has access to this Optimizarr instance before connecting Stash."*

### Key GraphQL Queries

**Test connection:**
```graphql
query { version { version build_time } }
```

**Get all scenes with file info:**
```graphql
query FindScenes($filter: FindFilterType) {
  findScenes(filter: $filter) {
    scenes {
      id
      title
      files { path size width height video_codec duration bit_rate }
    }
  }
}
```

**Get scene count:**
```graphql
query { findScenes { count } }
```

---

## Implementation Plan

### Phase 1 — Sonarr/Radarr (no privacy requirements)

**Backend:**
- [ ] `external_connections` table + migrations
- [ ] `ExternalConnectionManager` class (app/external_connections.py)
  - `test_connection(conn)` — GET /system/status, returns version
  - `fetch_radarr_library(conn)` — GET /movie, maps to Optimizarr queue format
  - `fetch_sonarr_library(conn)` — GET /series + paginated episodefile
  - `enrich_queue_item(item, conn)` — fill codec/resolution from mediaInfo
  - `register_webhook(conn, optimizarr_url)` — POST /notification
- [ ] Webhook receiver: `POST /api/webhooks/{app_type}` — receives download events, auto-adds to queue
- [ ] Background sync: optional scheduled pull (configurable interval, default: off)

**API endpoints:**
- `GET  /api/connections` — list configured connections
- `POST /api/connections` — add new connection (test before saving)
- `PUT  /api/connections/{id}` — update
- `DEL  /api/connections/{id}` — remove
- `POST /api/connections/{id}/test` — test connectivity
- `POST /api/connections/{id}/sync` — pull library → queue
- `POST /api/connections/{id}/enrich-queue` — fill UNKNOWN items using this connection's mediaInfo

**UI (Settings tab — new "Connections" section):**
- Connection cards (similar to upscaler cards)
- Add Connection modal: App Type dropdown, URL, API Key (password field), Name
- Test button shows version/status before saving
- Sync button with progress: "Importing 1,247 movies…"
- Per-connection: show_in_stats toggle
- Link to Scan Root: dropdown to associate connection with a folder

### Phase 2 — Stash (after privacy validated)

- Same architecture but GraphQL client instead of REST
- `show_in_stats` defaults to false, locked until user explicitly enables
- `[Private]` placeholders in all public-facing queue views
- Warning banner when adding Stash connection
- Option to use a display alias instead of real file paths

---

## Notes on API Key Security

- Never store API keys in plaintext in SQLite
- Use `cryptography.fernet.Fernet` for symmetric encryption
- Derive key from `settings.SECRET_KEY` using PBKDF2
- Return only masked key (`****{last4}`) in GET responses
- Full key only sent once: on creation confirmation

```python
from cryptography.fernet import Fernet
import base64, hashlib

def _get_fernet():
    key_bytes = hashlib.sha256(settings.SECRET_KEY.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key_bytes))

def encrypt_api_key(key: str) -> str:
    return _get_fernet().encrypt(key.encode()).decode()

def decrypt_api_key(encrypted: str) -> str:
    return _get_fernet().decrypt(encrypted.encode()).decode()
```

---

## Open Questions

1. Should "Sync from Radarr" replace existing queue items or add alongside?
   - Recommendation: add/update only, never delete existing items
2. Webhook events — should a completed Radarr download auto-start encoding immediately or just queue it?
   - Recommendation: queue it, respect the scheduler's rest hours
3. For Stash: should we support tag-based filtering? (e.g. only process scenes tagged "needs-encoding")
   - Yes, implement as optional tag filter in connection settings
4. Multiple instances? (e.g. two Radarr instances — 1080p and 4K libraries)
   - Yes, the `external_connections` table supports multiple rows of the same `app_type`
