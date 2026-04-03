"""Queue, encoding control, and statistics routes."""
from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks, Query
from typing import List, Optional

from app.api.models import QueueItemResponse, QueueUpdateRequest, StatsResponse, MessageResponse
from app.api.dependencies import get_current_user, get_current_admin_user
from app.database import db
from app.scanner import scanner
from app.encoder import encoder_pool
from app.logger import optimizarr_logger

router = APIRouter()


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


@router.post("/stats/clear-names", response_model=MessageResponse)
async def clear_history_file_names(current_user: dict = Depends(get_current_admin_user)):
    """Anonymize file paths in history while preserving all statistical data (admin only)."""
    count = db.clear_history_file_names()
    return MessageResponse(message=f"Cleared file names from {count} history records")


@router.post("/stats/purge", response_model=MessageResponse)
async def purge_history(current_user: dict = Depends(get_current_admin_user)):
    """Delete all history records. Aggregate stats will reset to zero (admin only)."""
    count = db.purge_history()
    return MessageResponse(message=f"Purged {count} history records")

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


@router.post("/queue/reprobe")
async def reprobe_queue_items(
    current_user: dict = Depends(get_current_user)
):
    """
    Re-run ffprobe on all queue items with UNKNOWN codec or resolution.
    Useful after updating the scanner or adding new media.
    """
    import json
    from app.scanner import MediaScanner

    scanner = MediaScanner()
    items = db.get_queue_items()

    # Filter to items where codec is unknown/missing
    to_probe = []
    for item in items:
        specs = item.get("current_specs") or {}
        if isinstance(specs, str):
            try:
                specs = json.loads(specs)
            except Exception:
                specs = {}
        codec = specs.get("codec", "unknown")
        if codec in ("unknown", "", None):
            to_probe.append(item)

    if not to_probe:
        return {"message": "No items with unknown codec found", "updated": 0, "total_checked": len(items)}

    updated = 0
    failed = 0
    for item in to_probe:
        try:
            probe = scanner.probe_file(item["file_path"])
            if probe and probe.get("codec", "unknown") != "unknown":
                db.update_queue_item(item["id"], current_specs=json.dumps(probe))
                updated += 1
        except Exception:
            failed += 1

    return {
        "message": f"Re-probed {len(to_probe)} items: {updated} updated, {failed} failed",
        "updated": updated,
        "failed": failed,
        "total_checked": len(items),
        "items_probed": len(to_probe),
    }


@router.post("/queue/clear-completed")
async def clear_completed_queue(
    current_user: dict = Depends(get_current_user)
):
    """Remove all completed items from the queue."""
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM queue WHERE status = 'completed'")
        count = cursor.fetchone()[0]
        cursor.execute("DELETE FROM queue WHERE status = 'completed'")

    return {"message": f"Cleared {count} completed item(s)", "count": count}


