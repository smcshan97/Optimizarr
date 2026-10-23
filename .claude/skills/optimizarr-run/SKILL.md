---
name: optimizarr-run
description: Launch Optimizarr against a throwaway database and drive its web UI — use to run, start or screenshot the app, or to confirm a UI, settings or API change works in the real app rather than only in tests.
---

# Run Optimizarr

## Start

Use the built-in browser's `preview_start` with name **`optimizarr-test`** (defined in `.claude/launch.json`). That config runs `app.main` on `http://127.0.0.1:5758` with a fresh temp database and a fixed admin login, so it never touches the user's real queue in `data/optimizarr.db` or their running instance (which uses a different port). The admin username and password are the `OPTIMIZARR_ADMIN_*` values in `launch.json` — read them from there and keep them out of chat.

Never start the server with Bash, and never point a test run at `data/optimizarr.db`.

## Drive it

1. Open the page, sign in through the login form with the launch.json credentials. The first login may ask for a password change (`must_change_password`); pick a new test value and record nothing.
2. Go to the screen the change affects (Settings tabs, Queue, Profiles). The fresh database is empty: create a profile or scan root through the UI when the screen needs data. Pointing a scan root at real media is fine for reading, but don't start encodes on real files from a test run.
3. Check with `read_page` / `get_page_text` first, `read_console_messages` for JS errors, and `preview_logs` for server tracebacks. API calls go to `/api/...` (auth at `/api/auth/login`), visible in `read_network_requests`.
4. For a settings change: change the value, save, reload the page, and confirm the value survived the round trip.

Done when the changed behaviour is observed in the app (screenshot for visual changes) and console/server logs are free of new errors.

## Environment notes

- The app runs on system Python `C:\Python314` (no venv). Commands handed to the user go in PowerShell syntax: `& "C:\Python314\python.exe" ...`.
- Tests: `"/c/Python314/python.exe" -m pytest -q tests` from the repo root (Bash), ~40 s.
- Stop the preview with `preview_stop` when finished.
