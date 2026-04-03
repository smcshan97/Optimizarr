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


