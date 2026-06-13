# Optimizarr — Next Session Handoff

**Repo:** https://github.com/smcshan97/Optimizarr
**Stack:** FastAPI · SQLite (WAL) · HandBrakeCLI · Vanilla JS · Local CSS (no framework)
**Style guide:** Sonarr/Radarr dark UI ("Black Glass" theme) — `theme.css` + `utilities.css`
**Version:** 2.3.0 (`app/__init__.py` is the single source of truth)
**Owner:** Shyriq' — Windows, RTX 2060 SUPER, Samsung Odyssey G9 (5120×1440 ultrawide)
**Local path:** `D:\Downloads\optimizarr`

---

## Current State — all roadmap patches (31–38) shipped & pushed

| Commit | What |
|---|---|
| `ede13f9` | **P31** cp1252 Unicode fix — UTF-8 stdout/stderr reconfigure in main.py, UTF-8 log handlers + raw opens, /health uses `__version__` |
| `8b412f8` `83e2a5e` | **P32** Encode-speed stat card + per-item & total queue ETA; per-codec speed breakdown |
| `61c4b39` | **P33** Live encode progress — `parse_hb_progress()` (real FPS/ETA), `current_fps`/`eta_seconds` columns, **2s-throttled** progress writes, `GET /events/encode-progress` SSE |
| `2179dac` | Click-to-edit priority rank (inline number input; refresh guarded mid-edit; `QueueUpdateRequest.priority` cap removed) |
| `3092aeb` | **Developer event log** — `app/devlog.py`, `logs/devlog.jsonl`, 5xx/exception middleware, `python -m app.devlog 24` digest |
| `6e2a39b` | **P34** Webhook dead-letter — `webhook_events` table, persist-before-process, startup replay, `/health` unprocessed count |
| `4090ed7` | **P35** Permission-error re-check — single + bulk endpoints, UI button/chip/filter, self-explaining reasons |
| `4b31833` | **P36** Health completion — HandBrake/ffprobe/ffmpeg versions (cached), connections status, queue depth + ETA hours |
| `b990b7b` | **P37** Auto-sync interval — `sync_interval_hours` col, 15-min `auto_sync_check` tick, `_sync_due()`, modal dropdown + card badge |
| `09aa6d3` | **P38** JSON config export/import — `GET /backup/json` (no keys), `POST /restore/json` (non-destructive merge by name/path) |
| `2d3ef80` | **Frozen-header fix** — `/stats` rewritten as SQL aggregates (138ms→3ms) + moved off the event loop; `/queue` off-loop; corruption-tolerant queue reads (`_safe_json`) |

**Tests:** 68 in `tests/test_core.py` — all must pass before any commit.

**Operational notes:**
- Existing queue items scanned before duration tracking have
  `duration_seconds = 0` (no ETA, sort last in fastest/slowest-first). A
  library re-scan fixes them. As of last audit ~4,870 of 4,900 pending items
  still need this.
- Restart the running server after pulling — the live instance has been
  observed running days-old code while the browser served fresh JS. Restart
  applies migrations and re-aligns.

---

## 🔎 Audit finding — global DB write-lock defeats WAL (top backlog item)

Root cause of the historical "frozen Space Saved header" (fixed for the hot
paths in `2d3ef80`, but the underlying design remains):

`Database.get_connection()` wraps **every** DB access — reads included — in a
single process-global, non-reentrant `threading.Lock` (`_write_lock`), held
for the whole connection. Meanwhile most API handlers are `async def` and call
`db.*` directly, so the blocking `lock.acquire()` runs **on the event loop
thread**. While the encoder's background thread holds the lock, the event loop
stalls and *every* HTTP request freezes.

**Mitigated so far:** the polled/long-lock endpoints now hop to a worker
thread via `asyncio.to_thread` — `/resources/current` (P23), `/stats`,
`/queue`, the SSE stream, `/resources/thresholds`, `/resources/reinit-gpu`.
Encoder progress writes are throttled to 2s (P33).

**Proper fix (do this next, carefully):** SQLite WAL already allows concurrent
readers + one writer, so reads need **no** lock at all. Options:
1. Drop `_write_lock` from the read path; keep it (or rely on `busy_timeout`)
   only for writes. Biggest win, matches WAL's design.
2. Or convert hot handlers to sync `def` so FastAPI threadpools them instead of
   running on the loop.
Either removes the architectural footgun rather than papering over each
endpoint. ~90 async handlers still call blocking `db.*` on the loop, but the
rest are user-triggered one-offs (create/update/delete) that complete in a few
ms — low impact, fix opportunistically.

---

## Roadmap — 🟢 Backlog (unsequenced)

- **DB locking model** (see audit finding above) — highest-value structural fix.
- **Retry backoff:** auto-retries fire immediately (re-queued item can be
  picked again within ~1s); consider a delay or send-to-back.
- **Watches API pruning:** standalone `/watches` CRUD routes still exist but
  have no UI (eye toggle replaced it); `/watches/status` still used by /health.
- **Auto-switch fastest/slowest** by Windows Active Hours
  (`/schedule/windows-active-hours`).
- **Two-pass audit:** `--multi-pass` is passed but SVT-AV1 vs x265 handle it
  differently — verify with real test encodes on each encoder.
- **Stash size field:** `external_connections` uses `f.get("size")` — confirm
  against Stash v0.24+ GraphQL VideoFile schema.
- **Webhook replay timing:** startup replay is synchronous before serving; a
  large stranded backlog would delay startup a few seconds (acceptable today).
- **Multi-instance support:** docs-only (separate data dirs + ports).
- Skip dark/light mode — Sonarr/Radarr are dark-only; stay consistent.

---

## Architecture Map

```
app/
  __init__.py              — __version__ = "2.3.0" (single source of truth)
  main.py                  — FastAPI app; UTF-8 stdout reconfigure (top); 5xx/exception
                             devlog middleware; lifespan (stale-processing recovery,
                             webhook replay, encoder kill on shutdown)
  config.py                — Pydantic settings + auto SECRET_KEY (data/.secret_key)
  database.py              — SQLite WAL; global _write_lock (see audit finding);
                             _safe_json corruption-tolerant parsing; migrations;
                             paginated queue query; get_encode_speed_stats;
                             get_pending_duration_by_codec; get/set/get_all_settings;
                             reset_stale_processing; webhook_events CRUD
  devlog.py                — Compact JSONL event log (logs/devlog.jsonl); devlog(),
                             read_events(), summarize(); `python -m app.devlog [hours]`
  encoder.py               — HandBrakeCLI wrapper, three-phase pipeline
                             (stereo→upscale→encode), psutil kill tree on stop,
                             _handle_failure retry, parse_hb_progress, 2s write throttle
  scanner.py               — ffprobe probing, _needs_encoding, _estimate_savings
                             (plan-aware, negative allowed), estimate_encode_seconds
  watcher.py               — Polling folder watcher; watches linked to scan roots,
                             new watches SEEDED not queued, forget_watch()
  scheduler.py             — APScheduler; schedule_check (encode windows) +
                             auto_sync_check (every 15m) + _sync_due()
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
    queue_routes.py        — Queue CRUD, pagination (+_attach_eta, off-loop),
                             /stats (SQL, off-loop), prioritize (incl. fastest/
                             slowest), reorder, retry, recheck-permissions, SSE,
                             compute_pending_eta()
    connection_routes.py   — Sonarr/Radarr/Stash, sync (+process_webhook_payload),
                             incoming webhooks, auto-sync interval
    system_routes.py       — Resources (+reinit-gpu, all off-loop), settings,
                             health (versions/connections/queue ETA), logs, backup
                             (.db + JSON config export/import), schedule
    upscaler_routes.py / auth_routes.py / models.py / dependencies.py / filesystem.py
web/
  templates/index.html     — SPA; templates/login.html
  static/js/app.js         — All frontend (~3,900 lines); token key is 'token';
                             SSE live-progress stream; inline priority edit
  static/css/theme.css     — Black Glass (~695 lines)
  static/css/utilities.css — Tailwind-replacement utilities (xl:grid-cols-7)
tests/
  test_core.py             — 68 pytest tests
```

---

## Hard Rules & Gotchas (violations have caused real bugs)

1. **subprocess + file I/O on Windows:** ALWAYS `encoding='utf-8',
   errors='replace'` on every subprocess call, `open()`, `read_text()`,
   `write_text()`, and logging FileHandler. Three cp1252 incidents so far.
   (Binary-mode `"wb"` opens are exempt.)
2. **Polled / DB-heavy `async def` handlers MUST run blocking work via
   `asyncio.to_thread`** (or be sync `def`). `db.get_connection()` takes a
   blocking global lock; doing that on the event loop freezes the whole
   server while the encoder holds the lock. This was the frozen-header bug.
3. **No nested `db.get_connection()` calls** — the `_write_lock` is NOT
   reentrant; calling one `db.*` method inside another's `with get_connection()`
   block self-deadlocks the whole instance. (Bit us in the stats dashboard.)
4. **Never `with ThreadPoolExecutor()`** around code that can hang (pynvml).
   `__exit__` calls `shutdown(wait=True)` and blocks forever. Use the
   persistent pool pattern in resources.py.
5. **HandBrakeCLI pegs CPU to 100% by design** — temperature-based throttling
   only; never utilization-based.
6. **AV1 NVENC requires RTX 40-series.** On RTX 2060 SUPER only H.264/H.265
   map to NVENC; AV1 falls back to SVT-AV1 software.
7. **HandBrake 1.8+:** flag is `--multi-pass`, not `--two-pass`.
8. **HandBrakeCLI JSON scan output goes to stderr**, not stdout.
9. **Queue API backward compat:** `GET /queue` without `page` returns a flat
   list — encoder, scanner, watcher, sync depend on this. With `page` it
   returns the paginated envelope (incl. `pending_eta_seconds`, per-item ETAs).
10. **Savings can be negative** (upscale/stereo increases size). Never re-add
    `max(0, ...)` clamps. Frontend shows orange `+X GB (↑N%)`.
11. **Savings calculated AFTER upscale/stereo plans are built** — plan JSON
    feeds `_estimate_savings`.
12. **Priority is rank-based: 1 = first** (`ORDER BY priority ASC`). New items
    append at end (`MAX+1`). One-time migration guarded by `priority_scheme`
    settings flag.
13. **localStorage token key is `'token'`** — `'auth_token'` was a bug.
14. **Speed stats exclude `duration_seconds = 0` rows** (pre-duration-tracking
    history) via SQL WHERE — keep it that way.
15. **DB settings are key-value strings** in `settings`; resource settings
    prefixed `resource_`; parse with defaults. Use `db.get_setting`/`set_setting`.
16. **Version string lives ONLY in `app/__init__.py`.** Never hardcode.
17. **Watch toggling must not flood the queue:** the watcher SEEDS newly-seen
    watches (existing files ignored — those are for manual scan) and only
    queues future additions.
18. **EventSource can't set headers** — the SSE endpoint validates the JWT
    from a `?token=` query param. Keep that.
19. **Git push on this machine:** the GitHub key is passphrase-protected in
    the Windows ssh-agent; repo has `core.sshCommand` pointed at
    `C:/Windows/System32/OpenSSH/ssh.exe`. If push fails with
    `Permission denied (publickey)`, the agent stopped — `Start-Service
    ssh-agent; ssh-add` (user types the passphrase).

---

## Session-start ritual (catch regressions early)

Run `python -m app.devlog 24` (or ask Claude to) and paste/skim the digest —
it shows event counts, encode totals, **deduped problems with counts**, and the
last 15 events. The 5xx/exception middleware means a silently-failing endpoint
leaves evidence there instead of only in the browser console.

## Workflow

- One focused change at a time; each is a single git commit with a structured
  message. Owner confirms each works before the next.
- Validate before committing:
  `python -c "import ast; ast.parse(open('FILE', encoding='utf-8').read())"`
  per Python file, `node --check web/static/js/app.js`.
- Run the test suite: `python -m pytest tests/ -q` (all 68 must pass). NOTE: a
  running encoder pegs the CPU and can stretch the suite from ~30s to ~2.5min —
  that's the machine, not a hang.
- Prefer verifying against a COPY of the production DB (`data/optimizarr.db`)
  on a throwaway port with `OPTIMIZARR_DB_PATH` — never test against the live DB.
- DB schema changes via the migration pattern in database.py
  (`PRAGMA table_info` check → `ALTER TABLE` → `↳ Migrated:` print).
- UI: Sonarr/Radarr aesthetic, CSS variables (`var(--card-bg)`, `var(--accent)`,
  `var(--success)`, `var(--danger)`, `var(--warning)`, `var(--text-primary)`,
  `var(--text-muted)`, `var(--border)`), button classes
  (`.btn .btn-primary .btn-secondary .btn-xs`).
- After the session: update this file with what shipped and what's next.
