"""
Main API routes for Optimizarr.
"""
from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks, Query
from typing import List, Optional
from pathlib import Path
import os
import string

from app.api.models import (
    ProfileCreate, ProfileResponse,
    ScanRootCreate, ScanRootResponse,
    QueueItemResponse, QueueUpdateRequest,
    StatsResponse, MessageResponse
)
from app.api.dependencies import get_current_user, get_current_admin_user
from app.database import db
from app.scanner import scanner
from app.encoder import encoder_pool
from app.resources import resource_monitor

router = APIRouter()


# Profile Endpoints
@router.get("/profiles", response_model=List[ProfileResponse])
async def list_profiles(current_user: dict = Depends(get_current_user)):
    """Get all encoding profiles."""
    profiles = db.get_profiles()
    return profiles


@router.post("/profiles", response_model=ProfileResponse, status_code=status.HTTP_201_CREATED)
async def create_profile(
    profile: ProfileCreate,
    current_user: dict = Depends(get_current_admin_user)
):
    """Create a new encoding profile (admin only)."""
    try:
        profile_id = db.create_profile(**profile.model_dump())
        created_profile = db.get_profile(profile_id)
        return created_profile
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to create profile: {str(e)}"
        )


@router.get("/profiles/{profile_id}", response_model=ProfileResponse)
async def get_profile(
    profile_id: int,
    current_user: dict = Depends(get_current_user)
):
    """Get a specific profile by ID."""
    profile = db.get_profile(profile_id)
    
    if not profile:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Profile {profile_id} not found"
        )
    
    return profile


@router.put("/profiles/{profile_id}", response_model=MessageResponse)
async def update_profile(
    profile_id: int,
    profile: ProfileCreate,
    current_user: dict = Depends(get_current_admin_user)
):
    """Update an existing profile (admin only)."""
    # Check if profile exists
    existing = db.get_profile(profile_id)
    if not existing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Profile {profile_id} not found"
        )
    
    # Update profile
    success = db.update_profile(
        profile_id=profile_id,
        name=profile.name,
        resolution=profile.resolution,
        framerate=profile.framerate,
        codec=profile.codec,
        encoder=profile.encoder,
        quality=profile.quality,
        audio_codec=profile.audio_codec,
        container=profile.container,
        audio_handling=profile.audio_handling,
        subtitle_handling=profile.subtitle_handling,
        enable_filters=profile.enable_filters,
        chapter_markers=profile.chapter_markers,
        hw_accel_enabled=profile.hw_accel_enabled,
        preset=profile.preset,
        two_pass=profile.two_pass,
        custom_args=profile.custom_args,
        is_default=profile.is_default
    )
    
    if success:
        return MessageResponse(message="Profile updated successfully")
    else:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update profile"
        )


@router.delete("/profiles/{profile_id}", response_model=MessageResponse)
async def delete_profile(
    profile_id: int,
    current_user: dict = Depends(get_current_admin_user)
):
    """Delete a profile (admin only)."""
    success = db.delete_profile(profile_id)
    
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Profile {profile_id} not found"
        )
    
    return MessageResponse(message=f"Profile {profile_id} deleted")


# Scan Root Endpoints
@router.get("/scan-roots", response_model=List[ScanRootResponse])
async def list_scan_roots(current_user: dict = Depends(get_current_user)):
    """Get all scan roots."""
    roots = db.get_scan_roots()
    return roots


@router.post("/scan-roots", response_model=ScanRootResponse, status_code=status.HTTP_201_CREATED)
async def create_scan_root(
    scan_root: ScanRootCreate,
    current_user: dict = Depends(get_current_admin_user)
):
    """Create a new scan root (admin only)."""
    try:
        root_id = db.create_scan_root(
            path=scan_root.path,
            profile_id=scan_root.profile_id,
            library_type=scan_root.library_type,
            enabled=scan_root.enabled,
            recursive=scan_root.recursive
        )
        
        # Get created scan root
        roots = db.get_scan_roots()
        created_root = next((r for r in roots if r['id'] == root_id), None)
        
        return created_root
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to create scan root: {str(e)}"
        )


@router.delete("/scan-roots/{root_id}", response_model=MessageResponse)
async def delete_scan_root(
    root_id: int,
    current_user: dict = Depends(get_current_admin_user)
):
    """Delete a scan root (admin only)."""
    success = db.delete_scan_root(root_id)
    
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Scan root {root_id} not found"
        )
    
    return MessageResponse(message=f"Scan root {root_id} deleted")


@router.get("/scan-roots/{root_id}")
async def get_scan_root(
    root_id: int,
    current_user: dict = Depends(get_current_user)
):
    """Get a specific scan root by ID."""
    root = db.get_scan_root(root_id)
    
    if not root:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Scan root {root_id} not found"
        )
    
    return root


@router.put("/scan-roots/{root_id}", response_model=MessageResponse)
async def update_scan_root(
    root_id: int,
    root_data: ScanRootCreate,
    current_user: dict = Depends(get_current_admin_user)
):
    """Update an existing scan root (admin only)."""
    # Check if exists
    existing = db.get_scan_root(root_id)
    if not existing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Scan root {root_id} not found"
        )
    
    # Update
    success = db.update_scan_root(
        root_id=root_id,
        path=root_data.path,
        profile_id=root_data.profile_id,
        library_type=root_data.library_type,
        recursive=root_data.recursive,
        enabled=root_data.enabled
    )
    
    if success:
        return MessageResponse(message="Scan root updated successfully")
    else:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update scan root"
        )


@router.post("/scan-roots/{root_id}/scan", response_model=MessageResponse)
async def scan_single_root(
    root_id: int,
    current_user: dict = Depends(get_current_user)
):
    """Trigger scan for a specific scan root."""
    from app.scanner import MediaScanner
    
    # Get the root
    root = db.get_scan_root(root_id)
    if not root:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Scan root {root_id} not found"
        )
    
    # Initialize scanner and scan
    scanner = MediaScanner()
    try:
        files_added = scanner.scan_root(root_id)
        return MessageResponse(
            message=f"Scan complete! Found {files_added} video file(s) and added to queue."
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Scan failed: {str(e)}"
        )


# Queue Endpoints
@router.get("/queue", response_model=List[QueueItemResponse])
async def list_queue(
    status: Optional[str] = None,
    current_user: dict = Depends(get_current_user)
):
    """Get queue items, optionally filtered by status."""
    items = db.get_queue_items(status=status)
    return items


@router.post("/queue/scan", response_model=MessageResponse)
async def trigger_scan(
    background_tasks: BackgroundTasks,
    root_id: Optional[int] = None,
    current_user: dict = Depends(get_current_user)
):
    """Trigger a scan of all scan roots or a specific root."""
    def run_scan():
        if root_id:
            added = scanner.scan_root(root_id)
        else:
            added = scanner.scan_all_roots()
        print(f"Scan completed: {added} files added to queue")
    
    background_tasks.add_task(run_scan)
    
    return MessageResponse(
        message=f"Scan started for {'root ' + str(root_id) if root_id else 'all roots'}"
    )


@router.patch("/queue/{item_id}", response_model=MessageResponse)
async def update_queue_item(
    item_id: int,
    update: QueueUpdateRequest,
    current_user: dict = Depends(get_current_user)
):
    """Update a queue item (e.g., change priority or status)."""
    try:
        update_data = update.model_dump(exclude_none=True)
        db.update_queue_item(item_id, **update_data)
        
        return MessageResponse(message=f"Queue item {item_id} updated")
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to update queue item: {str(e)}"
        )


@router.delete("/queue/{item_id}", response_model=MessageResponse)
async def delete_queue_item(
    item_id: int,
    current_user: dict = Depends(get_current_user)
):
    """Remove an item from the queue."""
    success = db.delete_queue_item(item_id)
    
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Queue item {item_id} not found"
        )
    
    return MessageResponse(message=f"Queue item {item_id} deleted")


@router.post("/queue/clear", response_model=MessageResponse)
async def clear_queue(
    status: Optional[str] = None,
    current_user: dict = Depends(get_current_admin_user)
):
    """Clear the queue (admin only). Optionally filter by status."""
    db.clear_queue(status=status)
    
    message = f"Queue cleared"
    if status:
        message = f"Cleared queue items with status '{status}'"
    
    return MessageResponse(message=message)


# Control Endpoints
@router.post("/control/start", response_model=MessageResponse)
async def start_encoding(
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(get_current_user)
):
    """Start processing the encoding queue."""
    if encoder_pool.is_running:
        return MessageResponse(
            message="Encoder is already running",
            success=False
        )
    
    background_tasks.add_task(encoder_pool.process_queue)
    
    return MessageResponse(message="Encoding started")


@router.post("/control/stop", response_model=MessageResponse)
async def stop_encoding(current_user: dict = Depends(get_current_user)):
    """Stop processing the encoding queue."""
    if not encoder_pool.is_running:
        return MessageResponse(
            message="Encoder is not running",
            success=False
        )
    
    encoder_pool.stop()
    
    return MessageResponse(message="Encoding stopped")


# Statistics Endpoints
@router.get("/stats", response_model=StatsResponse)
async def get_statistics(current_user: dict = Depends(get_current_user)):
    """Get system statistics."""
    queue_items = db.get_queue_items()
    
    # Count by status
    pending = len([i for i in queue_items if i['status'] == 'pending'])
    processing = len([i for i in queue_items if i['status'] == 'processing'])
    completed = len([i for i in queue_items if i['status'] == 'completed'])
    failed = len([i for i in queue_items if i['status'] == 'failed'])
    
    # Get history stats
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT 
                COUNT(*) as total_files,
                COALESCE(SUM(savings_bytes), 0) as total_saved
            FROM history
        """)
        row = cursor.fetchone()
        total_files = row[0] if row else 0
        total_saved = row[1] if row else 0
    
    return StatsResponse(
        total_files_processed=total_files,
        total_space_saved_bytes=total_saved,
        total_space_saved_gb=round(total_saved / (1024**3), 2),
        queue_pending=pending,
        queue_processing=processing,
        queue_completed=completed,
        queue_failed=failed
    )


# Resource Monitoring Endpoints
@router.get("/resources/current")
async def get_current_resources(current_user: dict = Depends(get_current_user)):
    """Get current system resource usage."""
    return resource_monitor.get_all_resources()


@router.get("/resources/thresholds")
async def check_resource_thresholds(
    cpu_threshold: float = 90.0,
    memory_threshold: float = 85.0,
    gpu_threshold: float = 90.0,
    current_user: dict = Depends(get_current_user)
):
    """Check if current resource usage exceeds thresholds."""
    return resource_monitor.check_thresholds(
        cpu_threshold=cpu_threshold,
        memory_threshold=memory_threshold,
        gpu_threshold=gpu_threshold
    )


# Settings Endpoints
@router.get("/settings/resources")
async def get_resource_settings(current_user: dict = Depends(get_current_user)):
    """Get resource management settings."""
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT key, value FROM settings 
            WHERE key LIKE 'resource_%'
        """)
        settings = {row[0]: row[1] for row in cursor.fetchall()}
    
    # Default settings if none exist
    if not settings:
        settings = {
            'resource_cpu_threshold': '90.0',
            'resource_memory_threshold': '85.0',
            'resource_gpu_threshold': '90.0',
            'resource_nice_level': '10',
            'resource_enable_throttling': 'true'
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


# Library Types Endpoint
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

@router.get("/health")
async def health_check():
    """Comprehensive health check — no auth required."""
    import shutil
    import time
    from pathlib import Path
    
    health = {
        "status": "ok",
        "service": "optimizarr",
        "version": "2.1.0",
        "uptime_info": "running",
    }
    
    # HandBrakeCLI check
    hb_path = shutil.which("HandBrakeCLI")
    health["handbrake"] = {
        "installed": hb_path is not None,
        "path": hb_path or "not found"
    }
    
    # Database check
    try:
        with db.get_connection() as conn:
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
    
    return health


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


# ============================================================
# FOLDER WATCH ENDPOINTS
# ============================================================

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


# ============================================================
# PRESET IMPORT / EXPORT
# ============================================================

@router.get("/profiles/export")
async def export_profiles(current_user: dict = Depends(get_current_user)):
    """Export all profiles as JSON for backup/sharing."""
    profiles = db.export_profiles()
    return {
        "version": "2.1.0",
        "export_type": "optimizarr_profiles",
        "count": len(profiles),
        "profiles": profiles
    }


@router.post("/profiles/import")
async def import_profiles(
    data: dict,
    current_user: dict = Depends(get_current_admin_user)
):
    """Import profiles from JSON (admin only)."""
    if 'profiles' not in data or not isinstance(data['profiles'], list):
        raise HTTPException(status_code=400, detail="Invalid format: expected {profiles: [...]}")
    
    result = db.import_profiles(data['profiles'])
    return {
        "message": f"Imported {result['imported']} profile(s), skipped {result['skipped']}",
        **result
    }


# ============================================================
# AI UPSCALER ENDPOINTS
# ============================================================

@router.get("/upscalers")
async def get_upscalers(current_user: dict = Depends(get_current_user)):
    """Get AI upscaler info and detection results."""
    from app.upscaler import get_upscaler_info
    return get_upscaler_info()


@router.get("/upscalers/detect")
async def detect_upscalers_endpoint(current_user: dict = Depends(get_current_user)):
    """Re-detect available AI upscalers."""
    from app.upscaler import detect_upscalers
    return detect_upscalers()


# Schedule Endpoints
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


# ============================================================
# UPSCALER DOWNLOAD ENDPOINTS
# ============================================================

@router.post("/upscalers/{key}/download")
async def start_upscaler_download(
    key: str,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(get_current_admin_user)
):
    """Start downloading an AI upscaler binary (admin only)."""
    from app.upscaler import UPSCALERS, download_upscaler
    if key not in UPSCALERS:
        raise HTTPException(status_code=404, detail=f"Unknown upscaler: {key}")
    state = download_upscaler(key)
    return {"message": f"Download started for {UPSCALERS[key]['name']}", "state": state}


@router.get("/upscalers/{key}/download/status")
async def get_upscaler_download_status(
    key: str,
    current_user: dict = Depends(get_current_user)
):
    """Get download progress for an upscaler."""
    from app.upscaler import UPSCALERS, get_download_status
    if key not in UPSCALERS:
        raise HTTPException(status_code=404, detail=f"Unknown upscaler: {key}")
    return get_download_status(key)


@router.post("/upscalers/check-updates")
async def check_upscaler_updates(current_user: dict = Depends(get_current_user)):
    """Check GitHub for newer versions of installed upscalers."""
    from app.upscaler import check_for_updates
    return check_for_updates()


# ============================================================
# QUEUE PRIORITIZATION
# ============================================================

@router.post("/queue/prioritize")
async def prioritize_queue(
    data: dict,
    current_user: dict = Depends(get_current_user)
):
    """
    Re-prioritize pending queue items by a given criterion.
    sort_by: 'file_size' | 'estimated_savings' | 'filename' | 'default'
    order: 'desc' | 'asc'  (default: desc = largest first)
    """
    sort_by = data.get("sort_by", "default")
    order = data.get("order", "desc")
    reverse = (order == "desc")

    items = db.get_queue_items(status="pending")
    if not items:
        return {"message": "No pending items to prioritize", "count": 0}

    # Sort by chosen criterion
    if sort_by == "file_size":
        items.sort(key=lambda i: i.get("file_size_bytes", 0), reverse=reverse)
    elif sort_by == "estimated_savings":
        items.sort(key=lambda i: i.get("estimated_savings_bytes", 0), reverse=reverse)
    elif sort_by == "filename":
        items.sort(key=lambda i: Path(i.get("file_path", "")).name.lower(), reverse=reverse)
    # default: leave current order but we still write priorities below

    # Assign priority values: highest item gets priority = len(items)*10, etc.
    # This preserves relative order in DB ORDER BY priority DESC
    total = len(items)
    for rank, item in enumerate(items):
        new_priority = (total - rank) * 10   # 1st item → highest priority
        db.update_queue_item(item["id"], priority=new_priority)

    return {
        "message": f"Prioritized {total} pending items by {sort_by} ({order})",
        "count": total,
        "sort_by": sort_by,
        "order": order,
    }


# ============================================================
# WINDOWS ACTIVE HOURS
# ============================================================

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
