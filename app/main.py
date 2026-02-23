"""
Main FastAPI application for Optimizarr.
"""
import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pathlib import Path

from app.config import settings, ensure_data_directories
from app.database import db
from app.auth import auth
from app.api import routes, auth_routes
from app.scheduler import initialize_scheduler, shutdown_scheduler

# Create FastAPI app
app = FastAPI(
    title="Optimizarr",
    description="Automated Media Optimization System",
    version="2.1.0"
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, specify exact origins
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(routes.router, prefix="/api", tags=["api"])
app.include_router(auth_routes.router, prefix="/api")

# Static files
web_dir = Path(__file__).parent.parent / "web"
if web_dir.exists():
    app.mount("/static", StaticFiles(directory=str(web_dir / "static")), name="static")


@app.get("/", response_class=HTMLResponse)
async def root():
    """Serve the main web interface."""
    index_file = web_dir / "templates" / "index.html"
    
    if index_file.exists():
        return FileResponse(index_file)
    
    # Fallback if template doesn't exist yet
    return HTMLResponse("""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Optimizarr</title>
    </head>
    <body>
        <h1>Optimizarr API</h1>
        <p>Welcome to Optimizarr - Automated Media Optimization System</p>
        <ul>
            <li><a href="/docs">API Documentation</a></li>
            <li><a href="/api/health">Health Check</a></li>
        </ul>
    </body>
    </html>
    """)


@app.get("/login", response_class=HTMLResponse)
async def login_page():
    """Serve the login page."""
    login_file = web_dir / "templates" / "login.html"
    
    if login_file.exists():
        return FileResponse(login_file)
    
    # Fallback
    return HTMLResponse("""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Login - Optimizarr</title>
    </head>
    <body>
        <h1>Login</h1>
        <p>Please use the API at /api/auth/login</p>
    </body>
    </html>
    """)


@app.on_event("startup")
async def startup_event():
    """Initialize application on startup."""
    from app.logger import optimizarr_logger
    
    print("=" * 60)
    print("Optimizarr - Automated Media Optimization System")
    print("=" * 60)
    
    # Ensure data directories exist
    ensure_data_directories()
    
    # Initialize database (already done in database.py, but explicit here)
    print(f"Database: {settings.db_path}")
    
    # Create default admin user
    auth.create_default_admin()
    
    # Seed default profiles if none exist
    profiles = db.get_profiles()
    if not profiles:
        print("Seeding default encoding profiles…")
        _seed_default_profiles()

    # Start upscaler update checker background thread
    from app.upscaler import start_update_checker
    start_update_checker()
    
    # Initialize scheduler
    initialize_scheduler()
    
    # Start folder watcher
    from app.watcher import folder_watcher
    watches = db.get_folder_watches(enabled_only=True)
    if watches:
        folder_watcher.start()
        print(f"✓ Folder watcher started ({len(watches)} active watches)")
    else:
        print("  Folder watcher: no active watches configured")
    
    # Log startup
    optimizarr_logger.log_startup("2.1.0", settings.host, settings.port)
    
    print("=" * 60)
    print(f"Server starting on http://{settings.host}:{settings.port}")
    print("=" * 60)


@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup on shutdown."""
    from app.logger import optimizarr_logger
    from app.watcher import folder_watcher
    optimizarr_logger.log_shutdown()
    folder_watcher.stop()
    print("\nShutting down Optimizarr...")
    shutdown_scheduler()


def _seed_default_profiles():
    """Create a set of sensible default profiles covering the most common use cases."""
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



    """Main entry point for running the application."""
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=True,  # Enable auto-reload during development
        log_level=settings.log_level.lower()
    )


if __name__ == "__main__":
    main()
