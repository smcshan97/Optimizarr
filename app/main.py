"""
Main FastAPI application for Optimizarr.
"""
import sys

# Windows consoles default to cp1252, which crashes any print() containing
# emoji status markers or Unicode filenames (third cp1252 incident — see
# NEXT_SESSION.md hard rules). Must run BEFORE imports that print at startup.
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass  # non-reconfigurable streams (e.g. some service wrappers) — never fatal

import uvicorn
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pathlib import Path

from app.config import settings, ensure_data_directories
from app.database import db
from app.auth import auth
from app import __version__
from app.api import routes, auth_routes
from app.scheduler import initialize_scheduler, shutdown_scheduler


# ============================================================
# Lifespan (replaces deprecated @app.on_event)
# ============================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown logic using modern lifespan handler."""
    # ---- STARTUP ----
    from app.logger import optimizarr_logger

    print("=" * 60)
    print("Optimizarr - Automated Media Optimization System")
    print("=" * 60)

    ensure_data_directories()
    print(f"Database: {settings.db_path}")

    auth.create_default_admin()

    # Recover from an unclean shutdown: any item still marked 'processing' or
    # 'paused' has no live encode behind it — re-queue it as 'pending'.
    recovered = db.reset_stale_processing()
    if recovered:
        print(f"  Recovered {recovered} interrupted item(s) → pending")

    from app.devlog import devlog
    devlog('start', v=__version__, rec=recovered or None)

    # Replay webhook events that never finished processing (e.g. Sonarr
    # fired while Optimizarr was restarting). Idempotent — files already
    # queued are skipped. Old processed rows are pruned to cap table growth.
    try:
        import json as _wjson
        from app.api.connection_routes import process_webhook_payload
        pending_hooks = db.get_unprocessed_webhooks(hours=24)
        replay_ok = 0
        for ev in pending_hooks:
            try:
                result = process_webhook_payload(
                    ev['app_type'], _wjson.loads(ev['payload_json'] or '{}')
                )
                db.mark_webhook_processed(
                    ev['id'],
                    error=None if result.get('ok') else result.get('message')
                )
                replay_ok += 1
            except Exception as e:
                db.set_webhook_error(ev['id'], str(e)[:300])
        if pending_hooks:
            print(f"  Replayed {replay_ok}/{len(pending_hooks)} unprocessed webhook event(s)")
            devlog('webhook_replay', n=len(pending_hooks), ok=replay_ok)
        db.prune_webhook_events(days=7)
    except Exception as e:
        print(f"  ⚠ Webhook replay failed: {e}")

    # Seed default profiles on first run
    if not db.get_profiles():
        print("Seeding default encoding profiles…")
        _seed_default_profiles()

    # Start upscaler update checker background thread
    from app.upscaler import start_update_checker
    start_update_checker()

    # Sweep leftover *_optimized.* temp files before anything starts encoding
    # (orphans from a killed/crashed encode). Safe here — encoder isn't running.
    try:
        from app.scanner import cleanup_orphaned_outputs
        n_temp = cleanup_orphaned_outputs()
        if n_temp:
            print(f"  Cleaned {n_temp} orphaned _optimized temp file(s)")
            devlog('temp_cleanup', n=n_temp)
    except Exception as e:
        print(f"  ⚠ Temp cleanup failed: {e}")

    initialize_scheduler()

    # Start where you left off: resume encoding on boot only if the user's
    # last intent was "running" (default true), there's pending work, and the
    # schedule permits. A manual Stop persists intent=false, so a box you
    # deliberately stopped stays stopped across restarts.
    try:
        from app.encoder import encoder_pool
        from app.scheduler import schedule_manager
        import threading as _enc_t
        pending = db.count_pending()
        wants_autostart = db.get_setting('encoder_autostart', 'true') == 'true'
        if (wants_autostart and not encoder_pool.is_running and pending > 0
                and schedule_manager.should_encode_now()):
            _enc_t.Thread(target=encoder_pool.process_queue, daemon=True).start()
            print(f"▶ Resumed encoding on boot ({pending} pending)")
            devlog('autostart', pending=pending)
    except Exception as e:
        print(f"  ⚠ Boot resume check failed: {e}")

    from app.watcher import folder_watcher
    watches = db.get_folder_watches(enabled_only=True)
    if watches:
        folder_watcher.start()
        print(f"✓ Folder watcher started ({len(watches)} active watches)")
    else:
        print("  Folder watcher: no active watches configured")

    optimizarr_logger.log_startup(__version__, settings.host, settings.port)

    print("=" * 60)
    print(f"Server starting on http://{settings.host}:{settings.port}")
    print("=" * 60)

    yield  # Application runs here

    # ---- SHUTDOWN ----
    from app.logger import optimizarr_logger as _log
    from app.watcher import folder_watcher as _fw
    from app.encoder import encoder_pool as _ep
    from app.devlog import devlog as _devlog
    _devlog('stop')
    _log.log_shutdown()
    print("\nShutting down Optimizarr...")
    # Kill any active HandBrakeCLI process tree so it doesn't orphan.
    # mark_cancelled=False leaves the item recoverable on next startup.
    _ep.stop(mark_cancelled=False)
    _fw.stop()
    shutdown_scheduler()


# ============================================================
# App instance
# ============================================================

app = FastAPI(
    title="Optimizarr",
    description="Automated Media Optimization System",
    version=__version__,
    lifespan=lifespan,
)

# Devlog: record every unhandled exception and 5xx response so broken
# endpoints (e.g. a 500ing /queue freezing the UI) leave evidence in
# logs/devlog.jsonl instead of failing silently in the browser.
@app.middleware("http")
async def _devlog_error_middleware(request, call_next):
    from app.devlog import devlog
    try:
        response = await call_next(request)
    except Exception as exc:
        devlog('api_err', p=str(request.url.path), err=str(exc)[:200])
        raise
    if response.status_code >= 500:
        devlog('api_5xx', p=str(request.url.path), code=response.status_code)
    return response


app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()] if settings.cors_origins else [],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(routes.router, prefix="/api", tags=["api"])
app.include_router(auth_routes.router, prefix="/api")

web_dir = Path(__file__).parent.parent / "web"
if web_dir.exists():
    app.mount("/static", StaticFiles(directory=str(web_dir / "static")), name="static")


# ============================================================
# Page routes
# ============================================================

@app.get("/", response_class=HTMLResponse)
async def root():
    """Serve the main web interface."""
    index_file = web_dir / "templates" / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    return HTMLResponse("""
    <!DOCTYPE html><html><head><title>Optimizarr</title></head>
    <body>
        <h1>Optimizarr API</h1>
        <ul>
            <li><a href="/docs">API Documentation</a></li>
            <li><a href="/api/health">Health Check</a></li>
        </ul>
    </body></html>
    """)


@app.get("/login", response_class=HTMLResponse)
async def login_page():
    """Serve the login page."""
    login_file = web_dir / "templates" / "login.html"
    if login_file.exists():
        return FileResponse(login_file)
    return HTMLResponse("""
    <!DOCTYPE html><html><head><title>Login - Optimizarr</title></head>
    <body><h1>Login</h1><p>Use the API at /api/auth/login</p></body></html>
    """)


# ============================================================
# Default profile seeder
# ============================================================

def _seed_default_profiles():
    """Seed sensible default profiles on first run."""
    profiles = [
        {
            "name": "🎬 Movies — AV1 1080p High Quality",
            "resolution": "", "framerate": None,
            "codec": "av1", "encoder": "svt_av1", "quality": 28,
            "audio_codec": "passthrough", "container": "mkv",
            "audio_handling": "preserve_all", "subtitle_handling": "preserve_all",
            "chapter_markers": True, "enable_filters": False,
            "hw_accel_enabled": False, "preset": "6",
            "two_pass": False, "custom_args": None, "is_default": True,
        },
        {
            "name": "📺 TV Shows — AV1 1080p Balanced",
            "resolution": "", "framerate": None,
            "codec": "av1", "encoder": "svt_av1", "quality": 32,
            "audio_codec": "aac", "container": "mkv",
            "audio_handling": "keep_primary", "subtitle_handling": "keep_english",
            "chapter_markers": False, "enable_filters": False,
            "hw_accel_enabled": False, "preset": "8",
            "two_pass": False, "custom_args": None, "is_default": False,
        },
        {
            "name": "⚡ Fast H.265 — GPU Encode (NVENC)",
            "resolution": "", "framerate": None,
            "codec": "h265", "encoder": "nvenc_h265", "quality": 28,
            "audio_codec": "aac", "container": "mkv",
            "audio_handling": "preserve_all", "subtitle_handling": "none",
            "chapter_markers": True, "enable_filters": False,
            "hw_accel_enabled": True, "preset": None,
            "two_pass": False, "custom_args": None, "is_default": False,
        },
        {
            "name": "📦 Archive — HEVC 10-bit Near Lossless",
            "resolution": "", "framerate": None,
            "codec": "h265", "encoder": "x265", "quality": 18,
            "audio_codec": "passthrough", "container": "mkv",
            "audio_handling": "preserve_all", "subtitle_handling": "preserve_all",
            "chapter_markers": True, "enable_filters": False,
            "hw_accel_enabled": False, "preset": "slower",
            "two_pass": False,
            "custom_args": "--encoder-profile main10 --encoder-level 5.1",
            "is_default": False,
        },
        {
            "name": "📱 Mobile — H.264 720p Compatible",
            "resolution": "1280x720", "framerate": 30,
            "codec": "h264", "encoder": "x264", "quality": 23,
            "audio_codec": "aac", "container": "mp4",
            "audio_handling": "stereo_mixdown", "subtitle_handling": "none",
            "chapter_markers": False, "enable_filters": True,
            "hw_accel_enabled": False, "preset": "fast",
            "two_pass": False, "custom_args": None, "is_default": False,
        },
    ]
    for p in profiles:
        try:
            db.create_profile(**p)
            print(f"  ✓ Created profile: {p['name']}")
        except Exception as e:
            print(f"  ⚠ Could not create profile '{p['name']}': {e}")


# ============================================================
# Entry point
# ============================================================

def main():
    """Main entry point for running the application."""
    import os
    is_production = os.environ.get("OPTIMIZARR_ENV", "").lower() == "production"
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=not is_production,
        log_level=settings.log_level.lower()
    )


if __name__ == "__main__":
    main()
