# Optimizarr — Next Session Handoff

**Repo:** https://github.com/smcshan97/Optimizarr  
**Stack:** FastAPI · SQLite · HandBrakeCLI · Vanilla JS · Local CSS (no framework)  
**Style guide:** Sonarr/Radarr dark UI ("Black Glass" theme) — `theme.css` + `utilities.css`  
**Version:** 2.3.0  
**Owner:** Shyriq' — Windows, RTX 2060 SUPER, Samsung Odyssey G9 (5120×1440 ultrawide)

---

## What Was Shipped (Patches 15–22, this session)

| # | Patch | Files | Summary |
|---|---|---|---|
| 15 | Temperature-Based Throttling | 7 | Replaced broken CPU/GPU utilization-based pause with temperature triggers. Dashboard expanded to 4 resource cards. Settings rebuilt with per-trigger toggle rows. CPU temp via WMI (needs admin), GPU temp via pynvml (works without admin). |
| 16 | README Rewrite | 1 | Full GitHub landing page with Tdarr comparison, features, quick start, tech stack. |
| 17 | Windows Unicode Fix | 5 | Added `encoding='utf-8', errors='replace'` to ALL 16 subprocess call sites across scanner, encoder, upscaler, stereo, resources. Fixes cp1252 crash on Unicode filenames. |
| 18 | CDN Tailwind Removal | 2 | Replaced `cdn.tailwindcss.com` with local `utilities.css` (~296 lines, ~10KB vs 300KB CDN). |
| 19 | Routes Decomposition | 7 | Split 1,873-line `routes.py` into 6 focused sub-routers: profile, scan, queue, connection, system, upscaler. Orchestrator is now 32 lines. |
| 20 | Security + Button Unify | 6 | Auto-generate SECRET_KEY on first run (`data/.secret_key`). `must_change_password` flag on default admin. Disable uvicorn reload when `OPTIMIZARR_ENV=production`. All inline-styled buttons converted to `.btn` classes. Login page uses theme.css. |
| 21 | Basic Test Suite | 2 | 32 pytest tests: codec normalisation, needs_encoding logic, savings math, DB schema, secret key gen, stereo plan structure. All pass. |
| 22 | Queue Drag Reorder + Notifications | 7 | Drag handles (⠿) on queue rows with HTML5 drag-and-drop → batch `POST /queue/reorder`. Outgoing notifications module (Discord embeds, Slack blocks, generic JSON). Fires on encode_complete, encode_failed, queue_empty. Full CRUD routes + Settings UI with add modal, test, enable/disable, delete. |

**Projected score after all patches: ~95/100**

---

## Architecture Overview

```
app/
  __init__.py              — Version constant (2.3.0)
  main.py                  — FastAPI app, lifespan startup, default profile seeder
  config.py                — Pydantic settings + auto SECRET_KEY generation
  database.py              — SQLite via context managers; all CRUD, schema + migrations
  encoder.py               — HandBrakeCLI wrapper, three-phase pipeline (stereo→upscale→encode)
  scanner.py               — ffprobe/HandBrake probing, _needs_encoding, _estimate_savings
  watcher.py               — Watchdog folder watcher, queues new files
  scheduler.py             — APScheduler, should_encode_now(), check_and_trigger()
  upscaler.py              — Real-ESRGAN / Real-CUGAN / Waifu2x download + run
  stereo.py                — iw3 2D→3D and ffmpeg 3D→2D conversion
  resources.py             — CPU/GPU temp, memory, throttle logic (temperature-first)
  notifications.py         — Outgoing webhooks (Discord/Slack/generic), fires on encode events
  external_connections.py  — Sonarr/Radarr REST + Stash GraphQL, encryption
  auth.py                  — Bcrypt + JWT auth
  logger.py                — Structured logging
  api/
    routes.py              — Thin orchestrator, includes 6 sub-routers
    profile_routes.py      — Profiles CRUD, import/export, seed defaults
    scan_routes.py         — Scan roots CRUD, scanning
    queue_routes.py        — Queue CRUD, control, stats, prioritize, reprobe, reorder
    connection_routes.py   — Sonarr/Radarr/Stash connections, sync, webhooks
    system_routes.py       — Resources, settings, health, logs, backup, schedule, watches, notifications
    upscaler_routes.py     — AI upscaler + stereo 3D detection, downloads
    auth_routes.py         — Login, logout, change password, user info
    models.py              — Pydantic request/response models
    dependencies.py        — Auth middleware
    filesystem.py          — File system utilities
web/
  templates/index.html     — Single-page app (~1,610 lines)
  templates/login.html     — Login page (uses theme.css, no CDN)
  static/js/app.js         — All frontend logic (~3,530 lines)
  static/css/theme.css     — Black Glass design system (~695 lines)
  static/css/utilities.css — Tailwind replacement utilities (~296 lines)
tests/
  test_core.py             — 32 unit tests (pytest)
```

---

## Known Bugs (must fix next session)

### 🔴 BUG: Stop button doesn't actually stop encoding
**File:** `app/encoder.py` lines 763–783, 876–882  
**Problem:** Hitting "Stop" on the dashboard calls `encoder_pool.stop()` which sets `is_running = False` and calls `job.stop()`. But `process_queue()` is blocked on `job.start()` (synchronous), so the loop doesn't see `is_running = False` until the current job's start() returns. The `job.stop()` calls `process.terminate()` but:
1. On Windows, `terminate()` may not kill HandBrakeCLI's child processes
2. The job is immediately marked `status='failed'` with `error_message='Manually stopped'` — this should be a distinct `'cancelled'` status, not `'failed'`
3. The `_optimized.mkv` temp output file is NOT cleaned up — left behind on disk

**Fix needed:**
- Use `psutil.Process(pid).kill()` + kill children on Windows (like the pause/resume logic already does)
- Add a `'cancelled'` status distinct from `'failed'`
- In `job.stop()`, clean up the `_optimized.*` temp file if it exists
- The finally block in `start()` should check `self.stop_monitoring` to know if this was a manual stop vs a real failure

### 🔴 BUG: Failed encode handling is poor
**Problem:** When an encode fails, the queue item stays at `status='failed'` forever with no way to retry. No retry logic exists.
**Fix needed:**
- Add `retry_count` column to queue table (default 0)
- Add `max_retries` setting (default 3)
- On failure, increment retry_count; if < max_retries, set status back to `pending`
- Add a "Retry" button per failed item and a "Retry All Failed" bulk action
- Similar to JDownloader's retry model

---

## Feature Requests (from owner)

### 1. Folder Watch → Library Toggle
**Current:** Folder watches have their own section in Settings with separate CRUD.  
**Requested:** Merge folder watch into Libraries (Scan Roots). Add an eye-shaped toggle (👁) on each scan root card that enables/disables folder watching for that root. Remove the standalone Folder Watches section.  
**Impact:** Remove `folder_watches` table? Or link watches to scan_roots via foreign key and auto-create/delete watches when toggling the eye icon.

### 2. Smart Encoding Prioritization
**Requested:** Two new queue sort strategies:
- **Fastest First** — sort by estimated encode time ascending (considers file size, duration, codec complexity). Good for knocking out quick wins during short active-hours windows.
- **Slowest First** — sort by estimated encode time descending. Good for starting long jobs during overnight windows (e.g. 2am–8am inactive hours → start 16-hour encodes first so they finish by morning).

**Consideration:** Needs encode time estimation, which could use: `duration × (bitrate / target_bitrate) × preset_speed_factor`. The Windows Active Hours API already exists (`/schedule/windows-active-hours`). Could auto-switch strategy based on whether we're in active hours or not.

### 3. Priority System Improvements
**Requested:**
- Priority should start at 1 (not 50) — easier to understand "1 is first, 2 is second"
- Support both ascending and descending priority sort
- The drag reorder (Patch 22) already renumbers priorities on drop — just needs the numbering scheme changed

### 4. Ascending/Descending on All Queue Sorts
**Current:** Queue sorting exists for file, codec, resolution, size, savings, status, progress, priority — but the sort direction toggle may not work correctly on all columns.  
**Requested:** Verify and fix ascending/descending toggle on all sortable columns.

---

## Key Patterns & Gotchas

- **Patch workflow:** Sequential `.patch` files. Applied via `git apply`, committed, pushed. Owner confirms each patch works before proceeding.
- **Validation before delivery:** All patches validated with `python ast.parse()` and `node --check` for syntax, and `git apply --check` before delivery.
- **subprocess on Windows:** ALWAYS use `encoding='utf-8', errors='replace'` — Python defaults to cp1252 which crashes on Unicode filenames.
- **HandBrakeCLI pegs CPU to 100%** — utilization-based throttling is fundamentally broken; temperature-based triggers are the correct approach.
- **Hardware encoder mapping:** Only H.264/H.265 should map to NVENC on RTX 2060 SUPER. AV1 NVENC requires RTX 40-series — fall back to SVT-AV1 software.
- **HandBrake 1.8+** renamed `--two-pass` to `--multi-pass`.
- **`app_logger` vs `app`:** `app_logger` was a broken attribute; correct attribute is `app` — fixed with alias in `logger.py`.
- **Video on ultrawides:** Players don't stretch to fill 32:9. Content plays pillarboxed at native 16:9. Vertical resolution matters — 1440p source → encode at 1440p; 1080p source → leave at 1080p.
- **DB settings:** Stored as key-value strings in `settings` table. Resource settings prefixed with `resource_`. All parsed with defaults at load time.

---

## Suggested Next Patch Order

| # | Patch | Priority | Scope |
|---|---|---|---|
| 23 | Stop Button Fix + Cancelled Status + Temp Cleanup | 🔴 Bug | encoder.py, database.py, app.js |
| 24 | Failed Encode Retry Logic | 🔴 Bug | encoder.py, database.py, queue_routes.py, app.js |
| 25 | Folder Watch → Library Eye Toggle | 🟡 UX | scan_routes.py, app.js, index.html, database.py |
| 26 | Priority Renumbering (start at 1) + Asc/Desc Sort Fix | 🟡 UX | app.js, queue_routes.py |
| 27 | Smart Encode Prioritization (fastest/slowest first) | 🟢 Feature | scanner.py, queue_routes.py, app.js |

---

## How to Start the Next Chat

Clone the repo first, then read this document:

```
I'm continuing development of Optimizarr, a Python/FastAPI media transcoding automation tool.
Repo: https://github.com/smcshan97/Optimizarr

Please clone the repo, read the NEXT_SESSION.md handoff document in the project knowledge,
and begin with the bugs listed under "Known Bugs (must fix next session)".
Think like a software developer — plan first, then code. Keep UI design close to Sonarr/Radarr.
Patch workflow: one patch at a time as .patch files, validated with ast.parse() and git apply --check.
```
