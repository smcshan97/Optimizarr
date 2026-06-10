# Optimizarr — Next Session Handoff

**Repo:** https://github.com/smcshan97/Optimizarr
**Stack:** FastAPI · SQLite (WAL) · HandBrakeCLI · Vanilla JS · Local CSS (no framework)
**Style guide:** Sonarr/Radarr dark UI ("Black Glass" theme) — `theme.css` + `utilities.css`
**Version:** 2.3.0 (`app/__init__.py` is the single source of truth)
**Owner:** Shyriq' — Windows, RTX 2060 SUPER, Samsung Odyssey G9 (5120×1440 ultrawide)
**Local path:** `D:\Downloads\optimizarr`

---

## ⚠️ Patch Numbering Reconciliation

Two parallel handoff documents diverged after Patch 30. The June 2026 session
shipped its own "Patches 26–31" while a separate audit document assigned
different numbers. **The audit document's numbering is canonical going
forward.** Mapping of the collision zone:

| Audit # | Feature | Commit | Status |
|---|---|---|---|
| 31 | Windows cp1252 Unicode fix (console + log handles) | `ede13f9` | ✅ shipped |
| 32 | Encode speed display + per-item/total queue ETA | `8b412f8` + follow-up | ✅ shipped |

Next up is **Patch 33**.

## Current State (all committed and pushed)

| Commit | What |
|---|---|
| `990ec9b` | Duration tracking + savings math rewrite (upscale/stereo aware, negative savings allowed) |
| `3ded7ea` | Stop button: psutil kill tree, `cancelled` status, temp output cleanup |
| `75e479c` | Failed-encode retry logic (`retry_count`, `max_retries` setting, retry endpoints/UI) + unclean-shutdown recovery (`reset_stale_processing` on startup, encoder kill on lifespan shutdown) |
| `4a22481` | Folder watches merged into Libraries via per-root 👁 eye toggle (`folder_watches.scan_root_id` FK, watch seeding so toggling on doesn't flood the queue) |
| `6321bfc` | Rank-based priority: **1 = first**, `ORDER BY priority ASC`; codec/resolution/duration server-side sortable (json_extract); drag-reorder page-offset ranks |
| `a89f112` | Smart prioritization: ⚡ fastest-first / 🐢 slowest-first via `get_encode_speed_stats()` + `estimate_encode_seconds()` |
| `8b412f8` | Avg Encode Speed stat card, per-item ETA in queue rows (remaining-time for processing), total Queue ETA chip, `_attach_eta()` on paginated envelope |
| `ede13f9` | cp1252 Unicode fix: UTF-8 stdout/stderr reconfigure in main.py, UTF-8 log handlers + raw opens, /health uses `__version__` |
| (next commit) | Per-codec encode speed in dashboard payload (`encode_speed` key) + codec breakdown rows show "N.N× realtime" |

**Tests:** 46 in `tests/test_core.py` — all must pass before any commit.

**Operational note:** existing queue items scanned before duration tracking
have `duration_seconds = 0` — no ETA, sort last in fastest/slowest-first.
A library re-scan fixes them.

---

## Roadmap — Build These In Order

### 🔴 Patch 33 — Live Encode Progress (SSE)
The active-encode card estimates ETA by linear extrapolation in JS — the last
fake math in the app. The encoder does NOT write FPS to the DB.
- Parse FPS + HandBrake's own ETA from stdout lines:
  `Encoding: task 1 of 1, 23.45 % (113.06 fps, avg 102.33 fps, ETA 00h12m34s)`
  — the regex currently only captures the percentage
- Write `current_fps`, `eta_seconds` to the queue row alongside progress
  (migration, same pattern as `duration_seconds`)
- `GET /events/encode-progress` SSE endpoint (text/event-stream, JSON every
  ~2s while a job is active)
- Frontend: EventSource replaces 5s polling for the active card ONLY; falls
  back to polling if SSE unsupported. Rest of queue stays on 5s poll.

### 🔴 Patch 34 — Webhook Reliability (Dead Letter)
If Sonarr/Radarr fires a webhook while Optimizarr is restarting, the event is
lost forever.
- `webhook_events` table: id, app_type, payload_json, received_at,
  processed_at, error
- `receive_webhook` in connection_routes.py: insert row FIRST, then process,
  then mark processed (idempotent — dedupe on file path already in queue)
- Startup (main.py lifespan): replay unprocessed rows from last 24h
- Health endpoint: include unprocessed webhook count

### 🟡 Patch 35 — Permission Error Re-check
`permission_error` queue items sit forever with no recovery path.
- `POST /queue/{id}/recheck-permissions` — re-runs
  `check_file_permissions()`, flips to pending if ok now
- Bulk: `POST /queue/recheck-permissions` for all permission_error items
- UI: "Re-check" button on permission_error rows + bulk action

### 🟡 Patch 36 — Health Page Completion
`/health` (system_routes.py) has DB counts + disk space. Add:
- `HandBrakeCLI --version` and `ffprobe -version` strings (subprocess with
  `encoding='utf-8', errors='replace'`, 5s timeout)
- Upscaler binary presence + version per tool (upscaler.py has helpers)
- Per-connection last_tested / last_synced from external_connections
- Queue depth + estimated hours remaining (reuse Patch 32 ETA math)

### 🟡 Patch 37 — Auto-Sync Interval
Sync with Sonarr/Radarr is currently manual-only.
- Migration: `sync_interval_hours INTEGER DEFAULT 0` on external_connections
- Scheduler picks up connections with interval > 0, calls `_sync_connection_task`
- UI: interval dropdown on connection modal (Off / 1h / 6h / 12h / 24h)

### 🟢 Patch 38 — JSON Config Export/Import
- `GET /backup/json` — profiles + scan roots + connections (API keys
  EXCLUDED or re-encrypted) + settings as human-readable JSON
- `POST /restore/json` — merges (doesn't wipe); skip duplicates by name/path

### 🟢 Backlog (unsequenced)
- **Retry backoff:** auto-retries currently fire immediately (re-queued item
  can be picked up within ~1s); consider delay or send-to-back
- **Watches API pruning:** standalone `/watches` CRUD routes still exist but
  have no UI (eye toggle replaced it) — health/status routes still used
- **Auto-switch fastest/slowest** based on Windows Active Hours
  (`/schedule/windows-active-hours`)
- **Two-pass audit:** `--multi-pass` passed but SVT-AV1 vs x265 handle it
  differently — verify with real test encodes
- **Stash size field:** `f.get("size")` — confirm against Stash v0.24+
  GraphQL VideoFile schema
- **Multi-instance support:** docs-only (separate data dirs + ports)
- Skip dark/light mode — Sonarr/Radarr are dark-only; stay consistent

---

## Architecture Map

```
app/
  __init__.py              — __version__ = "2.3.0" (single source of truth)
  main.py                  — FastAPI app, UTF-8 stdout reconfigure (top), lifespan
                             (startup recovery + encoder kill on shutdown)
  config.py                — Pydantic settings + auto SECRET_KEY (data/.secret_key)
  database.py              — SQLite WAL; CRUD, migrations, paginated queue query,
                             get_encode_speed_stats, get_pending_duration_by_codec,
                             get/set_setting, reset_stale_processing
  encoder.py               — HandBrakeCLI wrapper, three-phase pipeline
                             (stereo→upscale→encode), psutil kill tree on stop,
                             _handle_failure retry logic, --multi-pass
  scanner.py               — ffprobe probing, _needs_encoding, _estimate_savings
                             (plan-aware, negative allowed), estimate_encode_seconds
  watcher.py               — Polling folder watcher; watches linked to scan roots,
                             new watches SEEDED not queued, forget_watch()
  scheduler.py             — APScheduler, should_encode_now(), check_and_trigger()
  upscaler.py              — Real-ESRGAN / Real-CUGAN / Waifu2x download + run
  stereo.py                — iw3 2D→3D and ffmpeg 3D→2D
  resources.py             — Temp-based throttling; persistent GPU thread pool +
                             circuit breaker + reinit; NEVER 'with' on the pool
  notifications.py         — Outgoing webhooks (Discord/Slack/generic)
  external_connections.py  — Sonarr/Radarr REST + Stash GraphQL, Fernet encryption
  auth.py / logger.py      — Bcrypt+JWT; rotating logs, all handlers UTF-8
  api/
    routes.py              — Thin orchestrator → 6 sub-routers
    profile_routes.py      — Profiles CRUD, import/export
    scan_routes.py         — Scan roots CRUD + POST /scan-roots/{id}/watch toggle
    queue_routes.py        — Queue CRUD, pagination (+_attach_eta), prioritize
                             (incl. fastest/slowest), reorder, retry, control
    connection_routes.py   — Sonarr/Radarr/Stash, sync, incoming webhooks
    system_routes.py       — Resources (+reinit-gpu), settings (resources +
                             encoding/max_retries), health, logs, backup, schedule
    upscaler_routes.py / auth_routes.py / models.py / dependencies.py / filesystem.py
web/
  templates/index.html     — SPA; templates/login.html
  static/js/app.js         — All frontend (~3,800 lines); token key is 'token'
  static/css/theme.css     — Black Glass (~695 lines)
  static/css/utilities.css — Tailwind-replacement utilities
tests/
  test_core.py             — 46 pytest tests
```

---

## Hard Rules & Gotchas (violations have caused real bugs)

1. **subprocess + file I/O on Windows:** ALWAYS `encoding='utf-8',
   errors='replace'` on every subprocess call, `open()`, `read_text()`,
   `write_text()`, and logging FileHandler. Three cp1252 incidents so far.
   (Binary-mode `"wb"` opens are exempt.)
2. **Never `with ThreadPoolExecutor()`** around code that can hang (pynvml).
   `__exit__` calls `shutdown(wait=True)` and blocks forever. Use the
   persistent pool pattern in resources.py.
3. **HandBrakeCLI pegs CPU to 100% by design** — temperature-based throttling
   only; never utilization-based.
4. **AV1 NVENC requires RTX 40-series.** On RTX 2060 SUPER only H.264/H.265
   map to NVENC; AV1 falls back to SVT-AV1 software.
5. **HandBrake 1.8+:** flag is `--multi-pass`, not `--two-pass`.
6. **HandBrakeCLI JSON scan output goes to stderr**, not stdout.
7. **Queue API backward compat:** `GET /queue` without `page` returns a flat
   list — encoder, scanner, watcher, sync depend on this. With `page` it
   returns the paginated envelope (now incl. `pending_eta_seconds`).
8. **Savings can be negative** (upscale/stereo increases size). Never re-add
   `max(0, ...)` clamps. Frontend shows orange `+X GB (↑N%)`.
9. **Savings calculated AFTER upscale/stereo plans are built** — plan JSON
   feeds `_estimate_savings`.
10. **Priority is rank-based: 1 = first** (`ORDER BY priority ASC`). New items
    append at end (`MAX+1`). One-time migration guarded by `priority_scheme`
    settings flag.
11. **localStorage token key is `'token'`** — `'auth_token'` was a bug.
12. **Speed stats exclude `duration_seconds = 0` rows** (pre-duration-tracking
    history) via SQL WHERE — keep it that way.
13. **DB settings are key-value strings** in `settings`; resource settings
    prefixed `resource_`; parse with defaults. Use `db.get_setting`/`set_setting`.
14. **Version string lives ONLY in `app/__init__.py`.** Never hardcode.
15. **Watch toggling must not flood the queue:** the watcher SEEDS newly-seen
    watches (existing files ignored — those are for manual scan) and only
    queues future additions.
16. **Git push on this machine:** the GitHub key is passphrase-protected in
    the Windows ssh-agent; repo has `core.sshCommand` pointed at
    `C:/Windows/System32/OpenSSH/ssh.exe`. Don't remove that config.

---

## Workflow

- One focused change at a time; each is a single git commit with a structured
  message. Owner confirms each works before the next.
- Validate before committing:
  `python -c "import ast; ast.parse(open('FILE', encoding='utf-8').read())"`
  per Python file, `node --check web/static/js/app.js`
- Run the test suite: `python -m pytest tests/ -q` (all 46 must pass)
- DB schema changes via the migration pattern in database.py
  (`PRAGMA table_info` check → `ALTER TABLE` → `↳ Migrated:` print)
- UI: Sonarr/Radarr aesthetic, CSS variables (`var(--card-bg)`,
  `var(--accent)`, `var(--success)`, `var(--danger)`, `var(--warning)`,
  `var(--text-primary)`, `var(--text-muted)`, `var(--border)`), button
  classes (`.btn .btn-primary .btn-secondary .btn-xs`)
- After the session: update this file with what shipped and what's next.
