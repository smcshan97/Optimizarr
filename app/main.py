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
    version="2.0.0"
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
    
    # Create default profile if none exist
    profiles = db.get_profiles()
    if not profiles:
        print("Creating default encoding profile...")
        db.create_profile(
            name="1080p AV1 Balanced (Movies)",
            resolution="1920x1080",
            framerate=24,
            codec="av1",
            encoder="svt_av1",
            quality=28,
            audio_codec="opus",
            container="mkv",
            preset="6",
            two_pass=False,
            custom_args=None,
            is_default=True
        )
        print("✓ Created default profile: 1080p AV1 Balanced (Movies)")
    
    # Initialize scheduler
    initialize_scheduler()
    
    # Log startup
    optimizarr_logger.log_startup("2.0.0", settings.host, settings.port)
    
    print("=" * 60)
    print(f"Server starting on http://{settings.host}:{settings.port}")
    print("=" * 60)


@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup on shutdown."""
    from app.logger import optimizarr_logger
    optimizarr_logger.log_shutdown()
    print("\nShutting down Optimizarr...")
    shutdown_scheduler()


def main():
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
