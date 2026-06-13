"""System routes: resources, settings, health, logs, backup, schedule, watches."""
from fastapi import APIRouter, Depends, HTTPException, status, Query, UploadFile, File
from fastapi.responses import FileResponse
from typing import List, Optional
from pathlib import Path
import asyncio
import os

from app.api.models import MessageResponse
from app.api.dependencies import get_current_user, get_current_admin_user
from app.database import db
from app.resources import resource_monitor
from app.logger import optimizarr_logger

router = APIRouter()


@router.get("/resources/current")
async def get_current_resources(current_user: dict = Depends(get_current_user)):
    """Get current system resource usage.

    Runs in a thread executor so blocking psutil / pynvml calls never
    stall the FastAPI event loop.  If the call takes longer than 8s
    (e.g. hung WMI or pynvml), return a degraded snapshot.
    """
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(resource_monitor.get_all_resources),
            timeout=8.0
        )
    except asyncio.TimeoutError:
        # Return a degraded response so the dashboard doesn't break
        return {
            'timestamp': None,
            'cpu': {'percent': 0, 'per_core': [], 'count': 0,
                    'temperature_c': None, 'temp_available': False},
            'memory': {'total_mb': 0, 'available_mb': 0, 'used_mb': 0, 'percent': 0},
            'disk_io': {'read_bytes': 0, 'write_bytes': 0, 'read_count': 0, 'write_count': 0},
            'gpu': None,
            '_degraded': True,
            '_error': 'Resource monitoring timed out — GPU driver may need reinit'
        }


@router.get("/resources/thresholds")
async def check_resource_thresholds(
    current_user: dict = Depends(get_current_user)
):
    """Check if current resource usage exceeds any enabled thresholds.

    Reads the saved settings from the database to determine which
    triggers are active and what their limits are.

    Runs in a thread executor because check_thresholds calls pynvml /
    nvidia-smi / psutil which can block.
    """
    # Load current settings
    with db.get_read_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT key, value FROM settings WHERE key LIKE 'resource_%'")
        settings = {row[0]: row[1] for row in cursor.fetchall()}

    def _check():
        return resource_monitor.check_thresholds(
            pause_on_cpu_temp=settings.get('resource_pause_on_cpu_temp', 'true').lower() == 'true',
            cpu_temp_threshold=float(settings.get('resource_cpu_temp_threshold', '85.0')),
            pause_on_gpu_temp=settings.get('resource_pause_on_gpu_temp', 'true').lower() == 'true',
            gpu_temp_threshold=float(settings.get('resource_gpu_temp_threshold', '83.0')),
            pause_on_memory=settings.get('resource_pause_on_memory', 'false').lower() == 'true',
            memory_threshold=float(settings.get('resource_memory_threshold', '85.0')),
            pause_on_cpu_usage=settings.get('resource_pause_on_cpu_usage', 'false').lower() == 'true',
            cpu_usage_threshold=float(settings.get('resource_cpu_usage_threshold', '95.0')),
        )

    return await asyncio.to_thread(_check)


@router.post("/resources/reinit-gpu", response_model=MessageResponse)
async def reinit_gpu(current_user: dict = Depends(get_current_user)):
    """Re-initialize GPU monitoring after a driver update or failure.

    Resets the circuit breaker, creates a fresh thread pool, and retries
    pynvml / nvidia-smi detection.
    """
    success = await asyncio.to_thread(resource_monitor.reinit_gpu)
    if success:
        return MessageResponse(message=f"GPU monitoring re-initialized ({resource_monitor._gpu_method})")
    return MessageResponse(message="GPU monitoring not available — no NVIDIA GPU detected", success=False)


# Settings Endpoints
@router.get("/settings/resources")
async def get_resource_settings(current_user: dict = Depends(get_current_user)):
    """Get resource management settings."""
    with db.get_read_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT key, value FROM settings
            WHERE key LIKE 'resource_%'
        """)
        settings = {row[0]: row[1] for row in cursor.fetchall()}
    
    # Default settings if none exist — temperature-first model
    if not settings:
        settings = {
            'resource_enable_throttling': 'true',
            'resource_nice_level': '10',
            'resource_pause_on_cpu_temp': 'true',
            'resource_cpu_temp_threshold': '85.0',
            'resource_pause_on_gpu_temp': 'true',
            'resource_gpu_temp_threshold': '83.0',
            'resource_pause_on_memory': 'false',
            'resource_memory_threshold': '85.0',
            'resource_pause_on_cpu_usage': 'false',
            'resource_cpu_usage_threshold': '95.0',
        }
    
    return settings


@router.post("/settings/resources")
async def update_resource_settings(
    settings: dict,
    current_user: dict = Depends(get_current_admin_user)
):
    """Update resource management settings (admin only)."""
    with db.get_connection() as conn:
        cursor = conn.cursor()
        
        for key, value in settings.items():
            if not key.startswith('resource_'):
                continue
                
            cursor.execute("""
                INSERT INTO settings (key, value, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(key) DO UPDATE SET 
                    value = excluded.value,
                    updated_at = CURRENT_TIMESTAMP
            """, (key, value))
    
    return MessageResponse(message="Resource settings updated")


@router.get("/settings/encoding")
async def get_encoding_settings(current_user: dict = Depends(get_current_user)):
    """Get general encoding settings (e.g. retry behavior)."""
    return {
        'max_retries': db.get_setting('max_retries', '3'),
    }


@router.post("/settings/encoding")
async def update_encoding_settings(
    settings: dict,
    current_user: dict = Depends(get_current_admin_user)
):
    """Update general encoding settings (admin only)."""
    if 'max_retries' in settings:
        try:
            val = max(0, min(20, int(settings['max_retries'])))
        except (ValueError, TypeError):
            val = 3
        db.set_setting('max_retries', str(val))
    return MessageResponse(message="Encoding settings updated")


# Directory Browser Endpoint
@router.get("/browse")
async def browse_directories(
    path: str = Query("", description="Directory path to browse. Empty returns root drives/mount points."),
    current_user: dict = Depends(get_current_user)
):
    """Browse server directories for scan root selection."""
    dirs = []

    if not path:
        # Return root directories / drives
        if os.name == 'nt':
            # Windows: list available drive letters
            for letter in string.ascii_uppercase:
                drive = f"{letter}:\\"
                if os.path.isdir(drive):
                    dirs.append({
                        'name': f"{letter}:",
                        'path': drive,
                    })
        else:
            # Linux/Mac: start at /
            try:
                for entry in sorted(os.scandir('/'), key=lambda e: e.name):
                    if entry.is_dir():
                        dirs.append({
                            'name': entry.name,
                            'path': entry.path,
                        })
            except PermissionError:
                pass
        return {'path': '', 'parent': None, 'dirs': dirs}

    # Normalize and resolve the path
    target = Path(path).resolve()

    if not target.is_dir():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Not a valid directory: {path}"
        )

    # Compute parent path
    parent = str(target.parent) if target.parent != target else None

    try:
        for entry in sorted(os.scandir(str(target)), key=lambda e: e.name.lower()):
            if entry.is_dir() and not entry.name.startswith('.'):
                dirs.append({
                    'name': entry.name,
                    'path': entry.path,
                })
    except PermissionError:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Permission denied: {path}"
        )

    return {
        'path': str(target),
        'parent': parent,
        'dirs': dirs,
    }


@router.get("/library-types")
async def get_library_types():
    """Get all available library types with recommended settings."""
    from app.api.models import LIBRARY_TYPES
    return LIBRARY_TYPES


# Audio/Subtitle Strategy Endpoints
@router.get("/audio-strategies")
async def get_audio_strategies():
    """Get available audio handling strategies."""
    from app.api.models import AUDIO_STRATEGIES
    return AUDIO_STRATEGIES


@router.get("/subtitle-strategies")
async def get_subtitle_strategies():
    """Get available subtitle handling strategies."""
    from app.api.models import SUBTITLE_STRATEGIES
    return SUBTITLE_STRATEGIES


@router.get("/video-filters")
async def get_video_filters():
    """Get available video filter definitions."""
    from app.api.models import VIDEO_FILTERS
    return VIDEO_FILTERS


# Hardware Acceleration Endpoint
@router.get("/hardware-detect")
async def detect_hardware(current_user: dict = Depends(get_current_user)):
    """Detect available hardware encoders (NVENC, QSV, VCE, VideoToolbox)."""
    from app.encoder import detect_hardware_acceleration
    return detect_hardware_acceleration()


# Log Endpoints
@router.get("/logs")
async def get_logs(
    log_type: str = Query("app", description="Log type: app, handbrake, errors"),
    lines: int = Query(100, ge=1, le=1000, description="Number of lines to return"),
    level: str = Query("ALL", description="Filter by log level"),
    current_user: dict = Depends(get_current_user)
):
    """Get application logs."""
    from app.logger import optimizarr_logger
    return optimizarr_logger.get_logs(log_type=log_type, lines=lines, level=level)


@router.get("/logs/statistics")
async def get_log_statistics(
    days: int = Query(7, ge=1, le=90, description="Number of days of statistics"),
    current_user: dict = Depends(get_current_user)
):
    """Get structured encoding statistics from logs."""
    from app.logger import optimizarr_logger
    return optimizarr_logger.get_statistics(days=days)


@router.post("/logs/clear")
async def clear_logs(
    log_type: str = Query("app", description="Log type to clear"),
    current_user: dict = Depends(get_current_admin_user)
):
    """Clear a log file (admin only)."""
    from app.logger import optimizarr_logger
    success = optimizarr_logger.clear_log(log_type)
    if success:
        return MessageResponse(message=f"Cleared {log_type} logs")
    return MessageResponse(message=f"Failed to clear {log_type} logs", success=False)


# ============================================================
# ENHANCED HEALTH CHECK
# ============================================================
# Tool version strings, probed once per process — the binaries don't change
# while we're running, and /health is no-auth so it must stay cheap to poll.
_tool_versions: dict = {}


def _get_tool_version(cmd: list, cache_key: str) -> Optional[str]:
    """First line of a tool's version output, cached. None on any failure."""
    if cache_key in _tool_versions:
        return _tool_versions[cache_key]
    version = None
    try:
        import subprocess
        r = subprocess.run(
            cmd, capture_output=True, text=True,
            encoding='utf-8', errors='replace', timeout=5
        )
        lines = ((r.stdout or '') + (r.stderr or '')).strip().splitlines()
        if lines:
            version = lines[0].strip()
    except Exception:
        version = None
    _tool_versions[cache_key] = version
    return version


@router.get("/health")
async def health_check():
    """Comprehensive health check — no auth required."""
    import shutil
    import time
    from pathlib import Path

    from app import __version__
    health = {
        "status": "ok",
        "service": "optimizarr",
        "version": __version__,
        "uptime_info": "running",
    }

    # HandBrakeCLI check
    hb_path = shutil.which("HandBrakeCLI")
    health["handbrake"] = {
        "installed": hb_path is not None,
        "path": hb_path or "not found",
        "version": _get_tool_version([hb_path, "--version"], "handbrake") if hb_path else None,
    }
    
    # Database check
    try:
        with db.get_read_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM profiles")
            profiles = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM queue")
            queue_items = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM history")
            history = cursor.fetchone()[0]
        health["database"] = {
            "status": "ok",
            "profiles": profiles,
            "queue_items": queue_items,
            "history_records": history
        }
    except Exception as e:
        health["database"] = {"status": "error", "error": str(e)}
        health["status"] = "degraded"
    
    # Disk space for data directory
    try:
        data_path = Path("data")
        if data_path.exists():
            usage = shutil.disk_usage(str(data_path))
            health["disk"] = {
                "total_gb": round(usage.total / (1024**3), 1),
                "used_gb": round(usage.used / (1024**3), 1),
                "free_gb": round(usage.free / (1024**3), 1),
                "percent_used": round((usage.used / usage.total) * 100, 1)
            }
            if health["disk"]["percent_used"] > 95:
                health["status"] = "warning"
                health["disk"]["warning"] = "Low disk space!"
    except Exception:
        pass
    
    # Folder watcher status
    try:
        from app.watcher import folder_watcher
        health["folder_watcher"] = {
            "running": folder_watcher.running,
            "active_watches": len(db.get_folder_watches(enabled_only=True))
        }
    except Exception:
        health["folder_watcher"] = {"running": False}
    
    # ffprobe / ffmpeg check
    ffprobe_path = shutil.which("ffprobe")
    ffmpeg_path  = shutil.which("ffmpeg")
    health["ffprobe"] = {
        "installed": ffprobe_path is not None,
        "path": ffprobe_path or "not found",
        "version": _get_tool_version([ffprobe_path, "-version"], "ffprobe") if ffprobe_path else None,
    }
    health["ffmpeg"] = {
        "installed": ffmpeg_path is not None,
        "path": ffmpeg_path or "not found",
        "version": _get_tool_version([ffmpeg_path, "-version"], "ffmpeg") if ffmpeg_path else None,
    }
    if not ffprobe_path or not ffmpeg_path:
        health["status"] = "degraded"

    # Upscaler binaries
    try:
        from app.upscaler import detect_upscalers
        upscaler_status = detect_upscalers()
        health["upscalers"] = upscaler_status
    except Exception:
        health["upscalers"] = {}

    # Disk space per enabled scan root
    try:
        roots = db.get_scan_roots()
        root_disk = []
        seen_mounts = set()
        for root in roots:
            if not root.get('enabled'):
                continue
            rpath = Path(root['path'])
            if not rpath.exists():
                root_disk.append({"path": root['path'], "status": "missing"})
                continue
            try:
                usage = shutil.disk_usage(str(rpath))
                mount_key = (usage.total, usage.used)   # deduplicate same mount
                pct = round((usage.used / usage.total) * 100, 1)
                entry = {
                    "path": root['path'],
                    "free_gb": round(usage.free / (1024**3), 1),
                    "total_gb": round(usage.total / (1024**3), 1),
                    "percent_used": pct,
                }
                if pct > 95:
                    entry["warning"] = "Low disk space!"
                    health["status"] = "warning"
                if mount_key not in seen_mounts:
                    root_disk.append(entry)
                    seen_mounts.add(mount_key)
            except Exception:
                root_disk.append({"path": root['path'], "status": "error"})
        health["scan_root_disk"] = root_disk
    except Exception:
        pass

    # Scheduler status
    try:
        from app.scheduler import schedule_manager
        sched = db.get_schedule()
        health["scheduler"] = {
            "enabled": sched.get("enabled", False),
            "running": schedule_manager.running if schedule_manager else False,
            "next_window": f"{sched.get('start_time','?')} – {sched.get('end_time','?')}",
        }
    except Exception:
        health["scheduler"] = {"enabled": False, "running": False}

    # External connections — status only (no URLs/keys: endpoint is no-auth)
    try:
        health["connections"] = [{
            "name": c.get("name"),
            "app_type": c.get("app_type"),
            "enabled": bool(c.get("enabled")),
            "last_tested": c.get("last_tested"),
            "last_synced": c.get("last_synced"),
        } for c in db.get_external_connections()]
    except Exception:
        health["connections"] = []

    # Queue summary — counted in SQL (the old version loaded every row into
    # Python; this endpoint is no-auth and must stay cheap)
    try:
        with db.get_read_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT status, COUNT(*) FROM queue GROUP BY status")
            counts = {row[0]: row[1] for row in cursor.fetchall()}
        # Estimated time to clear the pending backlog (Patch 32 ETA math).
        # Called OUTSIDE the connection block — get_connection's lock is
        # not reentrant.
        from app.api.queue_routes import compute_pending_eta
        eta_seconds, eta_unknown = compute_pending_eta()
        health["queue"] = {
            "total": sum(counts.values()),
            "pending":    counts.get("pending", 0),
            "processing": counts.get("processing", 0),
            "completed":  counts.get("completed", 0),
            "failed":     counts.get("failed", 0),
            "permission_error": counts.get("permission_error", 0),
            "estimated_hours_remaining": round(eta_seconds / 3600, 1),
            "eta_unknown_items": eta_unknown,
        }
    except Exception:
        pass

    # Incoming webhook dead-letter queue — unprocessed events await the
    # next startup replay; a non-zero count after a replay means failures
    try:
        unprocessed = db.count_unprocessed_webhooks()
        health["webhooks"] = {"unprocessed": unprocessed}
        if unprocessed > 0:
            health["webhooks"]["note"] = "Events will be replayed at next startup"
    except Exception:
        health["webhooks"] = {"unprocessed": -1}

    return health

@router.get("/backup")
async def download_backup(current_user: dict = Depends(get_current_admin_user)):
    """Download the full SQLite database as a backup file."""
    import shutil as _shutil
    from app.config import settings

    db_path = Path(settings.db_path)
    if not db_path.exists():
        raise HTTPException(status_code=404, detail="Database file not found")

    # Copy to a temp file so we don't stream a live write
    import tempfile
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    _shutil.copy2(str(db_path), tmp.name)

    from datetime import datetime   # not imported at module level
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"optimizarr_backup_{timestamp}.db"
    return FileResponse(
        path=tmp.name,
        media_type="application/octet-stream",
        filename=filename,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/restore")
async def restore_backup(
    current_user: dict = Depends(get_current_admin_user),
):
    """Use POST /restore-upload with multipart/form-data instead."""
    raise HTTPException(status_code=405, detail="Use POST /restore-upload with multipart/form-data, field 'file'")


@router.post("/restore-upload")
async def restore_backup_upload(
    file: UploadFile = File(...),
    current_user: dict = Depends(get_current_admin_user),
):
    """Restore a database backup. Upload .db file as multipart field 'file'. Restart required after."""
    import shutil as _shutil
    import tempfile
    from app.config import settings

    db_path = Path(settings.db_path)
    contents = await file.read()

    if len(contents) < 100 or not contents.startswith(b"SQLite format 3\x00"):
        raise HTTPException(status_code=400, detail="File does not appear to be a valid SQLite database")

    # Atomic replace via temp file on same filesystem
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False, dir=db_path.parent)
    try:
        tmp.write(contents)
        tmp.close()
        _shutil.move(tmp.name, str(db_path))
    except Exception as e:
        import os
        try: os.unlink(tmp.name)
        except Exception: pass
        raise HTTPException(status_code=500, detail=f"Restore failed: {e}")

    return {"message": "Database restored successfully. Please restart Optimizarr for changes to take effect.", "size_bytes": len(contents)}


# Profile columns safe to round-trip through JSON config (excludes id).
_PROFILE_EXPORT_FIELDS = [
    "name", "resolution", "framerate", "codec", "encoder", "quality",
    "audio_codec", "container", "audio_handling", "subtitle_handling",
    "enable_filters", "chapter_markers", "hw_accel_enabled", "preset",
    "two_pass", "custom_args", "is_default",
    "upscale_enabled", "upscale_trigger_below", "upscale_target_height",
    "upscale_model", "upscale_factor", "upscale_key",
    "stereo_enabled", "stereo_mode", "stereo_format",
    "stereo_divergence", "stereo_convergence", "stereo_depth_model",
]
# Scan-root columns. external_connection_id is intentionally omitted —
# connection IDs are instance-specific and connections aren't imported.
_SCANROOT_EXPORT_FIELDS = [
    "library_type", "enabled", "recursive", "show_in_stats",
    "upscale_enabled", "upscale_trigger_below", "upscale_target_height",
    "upscale_model", "upscale_factor", "upscale_key",
    "stereo_enabled", "stereo_mode", "stereo_format",
    "stereo_divergence", "stereo_convergence", "stereo_depth_model",
]


@router.get("/backup/json")
async def export_config_json(current_user: dict = Depends(get_current_admin_user)):
    """Export profiles, scan roots, settings as human-readable JSON.

    Unlike the raw .db backup this is portable across instances: scan roots
    carry their profile by NAME (not instance-specific id), and connections
    are listed for reference only WITHOUT api keys (keys are encrypted with
    this instance's SECRET_KEY and aren't portable). Restore re-adds keys.
    """
    from datetime import datetime
    from app import __version__

    profiles = db.get_profiles()
    profiles_by_id = {p["id"]: p for p in profiles}

    export_profiles = [
        {k: p.get(k) for k in _PROFILE_EXPORT_FIELDS} for p in profiles
    ]

    export_roots = []
    for r in db.get_scan_roots():
        entry = {k: r.get(k) for k in _SCANROOT_EXPORT_FIELDS}
        entry["path"] = r["path"]
        prof = profiles_by_id.get(r.get("profile_id"))
        entry["profile_name"] = prof["name"] if prof else None
        export_roots.append(entry)

    # Connections: metadata only, no secrets. Informational on import.
    export_connections = [{
        "name": c.get("name"),
        "app_type": c.get("app_type"),
        "base_url": c.get("base_url"),
        "enabled": bool(c.get("enabled")),
        "sync_interval_hours": c.get("sync_interval_hours", 0),
    } for c in db.get_external_connections()]

    return {
        "optimizarr_config": True,
        "version": __version__,
        "exported_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
        "profiles": export_profiles,
        "scan_roots": export_roots,
        "settings": db.get_all_settings(),
        "connections": export_connections,
    }


@router.post("/restore/json")
async def import_config_json(
    config: dict,
    current_user: dict = Depends(get_current_admin_user),
):
    """Merge a JSON config export into the current instance.

    Non-destructive: profiles are skipped if the name already exists, scan
    roots if the path exists; settings are upserted (overwrite by key).
    Connections are NOT imported (api keys aren't portable) — re-add them
    manually. Takes effect immediately; no restart needed.
    """
    if not config.get("optimizarr_config"):
        raise HTTPException(
            status_code=400,
            detail="Not an Optimizarr config export (missing optimizarr_config flag)"
        )

    summary = {"profiles_added": 0, "profiles_skipped": 0,
               "scan_roots_added": 0, "scan_roots_skipped": 0,
               "settings_applied": 0, "connections_skipped": 0}

    # ---- Profiles (skip by name) ----
    existing_profile_names = {p["name"] for p in db.get_profiles()}
    for p in config.get("profiles", []):
        name = p.get("name")
        if not name or name in existing_profile_names:
            summary["profiles_skipped"] += 1
            continue
        fields = {k: p.get(k) for k in _PROFILE_EXPORT_FIELDS if k in p}
        # Don't let an import silently steal the default flag
        fields["is_default"] = False
        db.create_profile(**fields)
        existing_profile_names.add(name)
        summary["profiles_added"] += 1

    # ---- Scan roots (skip by path; resolve profile by name) ----
    name_to_profile_id = {p["name"]: p["id"] for p in db.get_profiles()}
    default_profile = next((p["id"] for p in db.get_profiles() if p.get("is_default")), None)
    existing_paths = {r["path"] for r in db.get_scan_roots()}
    for r in config.get("scan_roots", []):
        path = r.get("path")
        if not path or path in existing_paths:
            summary["scan_roots_skipped"] += 1
            continue
        profile_id = name_to_profile_id.get(r.get("profile_name")) or default_profile
        if not profile_id:
            summary["scan_roots_skipped"] += 1
            continue
        fields = {k: r.get(k) for k in _SCANROOT_EXPORT_FIELDS if k in r}
        db.create_scan_root(path=path, profile_id=profile_id, **fields)
        existing_paths.add(path)
        summary["scan_roots_added"] += 1

    # ---- Settings (upsert by key) ----
    for key, value in (config.get("settings") or {}).items():
        db.set_setting(key, value)
        summary["settings_applied"] += 1

    # ---- Connections are not imported (keys aren't portable) ----
    summary["connections_skipped"] = len(config.get("connections", []))

    msg = (f"Imported {summary['profiles_added']} profile(s), "
           f"{summary['scan_roots_added']} scan root(s), "
           f"{summary['settings_applied']} setting(s).")
    if summary["connections_skipped"]:
        msg += (f" {summary['connections_skipped']} connection(s) in the file "
                f"were NOT imported — re-add them with their API keys.")
    return {"message": msg, "summary": summary}


# ============================================================
# STATISTICS DASHBOARD
# ============================================================

@router.get("/stats/dashboard")
async def get_stats_dashboard(
    days: int = Query(default=30, ge=1, le=365),
    current_user: dict = Depends(get_current_user)
):
    """Get comprehensive statistics for the dashboard."""
    return db.get_stats_dashboard(days=days)


@router.get("/watches")
async def list_folder_watches(current_user: dict = Depends(get_current_user)):
    """Get all folder watches."""
    return db.get_folder_watches()


@router.post("/watches")
async def create_folder_watch(
    watch: dict,
    current_user: dict = Depends(get_current_admin_user)
):
    """Create a new folder watch (admin only)."""
    required = ['path', 'profile_id']
    for field in required:
        if field not in watch:
            raise HTTPException(status_code=400, detail=f"Missing required field: {field}")
    
    # Verify path exists
    if not Path(watch['path']).exists():
        raise HTTPException(status_code=400, detail=f"Path does not exist: {watch['path']}")
    
    try:
        watch_id = db.create_folder_watch(
            path=watch['path'],
            profile_id=watch['profile_id'],
            enabled=watch.get('enabled', True),
            recursive=watch.get('recursive', True),
            auto_queue=watch.get('auto_queue', True),
            extensions=watch.get('extensions', '.mkv,.mp4,.avi,.mov,.wmv,.flv,.webm,.m4v,.ts,.mpg,.mpeg')
        )
        return db.get_folder_watch(watch_id)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to create watch: {str(e)}")


@router.put("/watches/{watch_id}")
async def update_folder_watch(
    watch_id: int, watch: dict,
    current_user: dict = Depends(get_current_admin_user)
):
    """Update a folder watch (admin only)."""
    success = db.update_folder_watch(watch_id, **watch)
    if not success:
        raise HTTPException(status_code=404, detail="Watch not found")
    return db.get_folder_watch(watch_id)


@router.delete("/watches/{watch_id}")
async def delete_folder_watch(
    watch_id: int,
    current_user: dict = Depends(get_current_admin_user)
):
    """Delete a folder watch (admin only)."""
    success = db.delete_folder_watch(watch_id)
    if not success:
        raise HTTPException(status_code=404, detail="Watch not found")
    return MessageResponse(message="Folder watch deleted")


@router.get("/watches/status")
async def get_watcher_status(current_user: dict = Depends(get_current_user)):
    """Get folder watcher status."""
    from app.watcher import folder_watcher
    return folder_watcher.get_status()


@router.post("/watches/check")
async def force_watcher_check(
    watch_id: Optional[int] = None,
    current_user: dict = Depends(get_current_admin_user)
):
    """Force an immediate check for new files (admin only)."""
    from app.watcher import folder_watcher
    result = folder_watcher.force_check(watch_id)
    return result


@router.get("/schedule")
async def get_schedule(current_user: dict = Depends(get_current_user)):
    """Get current schedule configuration."""
    from app.scheduler import schedule_manager
    return schedule_manager.get_status()


@router.post("/schedule")
async def update_schedule(
    config: dict,
    current_user: dict = Depends(get_current_admin_user)
):
    """Update schedule configuration (admin only)."""
    from app.scheduler import schedule_manager
    
    success = schedule_manager.save_schedule(config)
    
    if success:
        return MessageResponse(message="Schedule updated successfully")
    else:
        raise HTTPException(status_code=500, detail="Failed to update schedule")


@router.post("/schedule/enable")
async def enable_schedule(current_user: dict = Depends(get_current_admin_user)):
    """Enable scheduled encoding (admin only)."""
    from app.scheduler import schedule_manager
    
    config = schedule_manager.schedule_config or {}
    config['enabled'] = True
    
    success = schedule_manager.save_schedule(config)
    
    if success:
        return MessageResponse(message="Schedule enabled")
    else:
        raise HTTPException(status_code=500, detail="Failed to enable schedule")


@router.post("/schedule/disable")
async def disable_schedule(current_user: dict = Depends(get_current_admin_user)):
    """Disable scheduled encoding (admin only)."""
    from app.scheduler import schedule_manager
    
    config = schedule_manager.schedule_config or {}
    config['enabled'] = False
    
    success = schedule_manager.save_schedule(config)
    
    if success:
        return MessageResponse(message="Schedule disabled")
    else:
        raise HTTPException(status_code=500, detail="Failed to disable schedule")


@router.get("/schedule/windows-active-hours")
async def get_windows_active_hours(current_user: dict = Depends(get_current_user)):
    """
    Read Windows Active Hours from the registry (Windows only).
    Returns the detected rest window suitable for scheduled encoding.
    """
    from app.scheduler import schedule_manager
    hours = schedule_manager.get_windows_active_hours()
    if hours is None:
        return {
            "available": False,
            "reason": "Windows Active Hours not available on this OS or registry key not found"
        }
    return {"available": True, **hours}



# ============================================================
# NOTIFICATION WEBHOOK ENDPOINTS
# ============================================================

@router.get("/notifications")
async def list_notifications(current_user: dict = Depends(get_current_user)):
    """List all notification webhooks."""
    import json as _json
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM notifications ORDER BY created_at DESC")
        cols = [d[0] for d in cursor.description]
        rows = [dict(zip(cols, row)) for row in cursor.fetchall()]
    for r in rows:
        r['events'] = _json.loads(r['events']) if r.get('events') else []
    return rows


@router.post("/notifications", status_code=201)
async def create_notification(
    data: dict,
    current_user: dict = Depends(get_current_admin_user)
):
    """Create a notification webhook."""
    import json as _json
    name = data.get('name', '').strip()
    url = data.get('webhook_url', '').strip()
    wtype = data.get('webhook_type', 'discord')
    events = data.get('events', ['encode_complete', 'encode_failed', 'queue_empty'])
    enabled = data.get('enabled', True)

    if not name or not url:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="Name and webhook_url are required")

    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO notifications (name, webhook_url, webhook_type, events, enabled)
            VALUES (?, ?, ?, ?, ?)
        """, (name, url, wtype, _json.dumps(events), 1 if enabled else 0))
        new_id = cursor.lastrowid

    return {"id": new_id, "message": f"Notification '{name}' created"}


@router.put("/notifications/{notif_id}")
async def update_notification(
    notif_id: int,
    data: dict,
    current_user: dict = Depends(get_current_admin_user)
):
    """Update a notification webhook."""
    import json as _json
    fields = []
    values = []
    for key in ('name', 'webhook_url', 'webhook_type', 'enabled'):
        if key in data:
            fields.append(f"{key} = ?")
            val = data[key]
            if key == 'enabled':
                val = 1 if val else 0
            values.append(val)
    if 'events' in data:
        fields.append("events = ?")
        values.append(_json.dumps(data['events']))

    if not fields:
        return MessageResponse(message="Nothing to update")

    values.append(notif_id)
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(f"UPDATE notifications SET {', '.join(fields)} WHERE id = ?", values)

    return MessageResponse(message="Notification updated")


@router.delete("/notifications/{notif_id}")
async def delete_notification(
    notif_id: int,
    current_user: dict = Depends(get_current_admin_user)
):
    """Delete a notification webhook."""
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM notifications WHERE id = ?", (notif_id,))

    return MessageResponse(message="Notification deleted")


@router.post("/notifications/{notif_id}/test")
async def test_notification(
    notif_id: int,
    current_user: dict = Depends(get_current_admin_user)
):
    """Send a test notification to verify webhook connectivity."""
    import json as _json
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM notifications WHERE id = ?", (notif_id,))
        row = cursor.fetchone()
        if not row:
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail="Notification not found")
        cols = [d[0] for d in cursor.description]
        webhook = dict(zip(cols, row))

    from app.notifications import _send_webhook
    payload = {
        'event': 'test',
        'title': '🔔 Test Notification',
        'message': f"This is a test from Optimizarr webhook **{webhook['name']}**.",
        'fields': {'Status': 'Working!', 'Type': webhook['webhook_type']},
    }
    _send_webhook(webhook, payload)
    return MessageResponse(message="Test notification sent")
