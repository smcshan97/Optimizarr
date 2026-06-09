"""Scan root management routes."""
from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks
from typing import List

from app.api.models import ScanRootCreate, ScanRootResponse, MessageResponse
from app.api.dependencies import get_current_user, get_current_admin_user
from app.database import db
from app.scanner import scanner

router = APIRouter()


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
            recursive=scan_root.recursive,
            show_in_stats=scan_root.show_in_stats,
            upscale_enabled=scan_root.upscale_enabled,
            upscale_trigger_below=scan_root.upscale_trigger_below,
            upscale_target_height=scan_root.upscale_target_height,
            upscale_model=scan_root.upscale_model,
            upscale_factor=scan_root.upscale_factor,
            upscale_key=scan_root.upscale_key,
        )
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
        enabled=root_data.enabled,
        show_in_stats=root_data.show_in_stats,
        upscale_enabled=root_data.upscale_enabled,
        upscale_trigger_below=root_data.upscale_trigger_below,
        upscale_target_height=root_data.upscale_target_height,
        upscale_model=root_data.upscale_model,
        upscale_factor=root_data.upscale_factor,
        upscale_key=root_data.upscale_key,
        stereo_enabled=root_data.stereo_enabled,
        stereo_mode=root_data.stereo_mode,
        stereo_format=root_data.stereo_format,
        stereo_divergence=root_data.stereo_divergence,
        stereo_convergence=root_data.stereo_convergence,
        stereo_depth_model=root_data.stereo_depth_model,
    )
    
    if success:
        return MessageResponse(message="Scan root updated successfully")
    else:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update scan root"
        )


@router.post("/scan-roots/{root_id}/watch", response_model=MessageResponse)
async def toggle_scan_root_watch(
    root_id: int,
    data: dict,
    current_user: dict = Depends(get_current_admin_user)
):
    """Enable/disable folder watching for a library (admin only).

    Enabling auto-creates a folder watch bound to the scan root (inheriting its
    path, profile, and recursion). Disabling deletes the linked watch. Newly
    queued files come from *future* additions only — existing files are picked
    up by a manual scan.
    """
    root = db.get_scan_root(root_id)
    if not root:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Scan root {root_id} not found"
        )

    enabled = bool(data.get('enabled', False))
    existing = db.get_folder_watch_by_scan_root(root_id)

    from app.watcher import folder_watcher

    if enabled:
        if existing:
            db.update_folder_watch(existing['id'], enabled=True)
        else:
            db.create_folder_watch(
                path=root['path'],
                profile_id=root['profile_id'],
                enabled=True,
                recursive=bool(root.get('recursive', True)),
                auto_queue=True,
                scan_root_id=root_id,
            )
        # Ensure the watcher thread is running (idempotent)
        folder_watcher.start()
        return MessageResponse(message="Folder watching enabled for this library")

    # Disable — remove the linked watch and forget its known-file cache
    if existing:
        folder_watcher.forget_watch(existing['id'])
        db.delete_folder_watch(existing['id'])
    return MessageResponse(message="Folder watching disabled for this library")


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


