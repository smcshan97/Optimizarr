"""Queue, encoding control, and statistics routes."""
import asyncio
import json as _json
from pathlib import Path as _Path

from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks, Query
from fastapi.responses import StreamingResponse
from typing import List, Optional

from app.api.models import QueueItemResponse, QueueUpdateRequest, StatsResponse, MessageResponse
from app.api.dependencies import get_current_user, get_current_admin_user
from app.database import db
from app.scanner import scanner
from app.encoder import encoder_pool
from app.logger import optimizarr_logger

router = APIRouter()


@router.get("/events/encode-progress")
async def encode_progress_events(token: Optional[str] = None):
    """Server-sent events stream of live encode progress (~every 2s).

    EventSource cannot set an Authorization header, so the JWT is passed as
    a ``token`` query parameter and validated the same way the header is.
    Each event is JSON: {"processing": [{id, progress, fps, eta_seconds,
    file}, ...]}. The stream ends only when the client disconnects.
    """
    from app.auth import auth as _auth
    payload = _auth.decode_token(token) if token else None
    if not payload or not db.get_user_by_id(int(payload.get("sub", 0) or 0)):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Invalid or expired token")

    async def event_stream():
        while True:
            # to_thread: keep the encoder's DB write lock contention off the
            # event loop
            rows = await asyncio.to_thread(db.get_queue_items, 'processing')
            data = [{
                'id': r['id'],
                'progress': round(r.get('progress') or 0, 1),
                'fps': r.get('current_fps') or 0,
                'eta_seconds': r.get('eta_seconds') or 0,
                'file': _Path(r['file_path']).name,
            } for r in rows]
            yield f"data: {_json.dumps({'processing': data})}\n\n"
            await asyncio.sleep(2)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/queue")
async def list_queue(
    status: Optional[str] = None,
    search: Optional[str] = None,
    sort: Optional[str] = None,
    dir: Optional[str] = None,
    page: Optional[int] = Query(None, ge=1),
    page_size: Optional[int] = Query(None, ge=1, le=500),
    current_user: dict = Depends(get_current_user)
):
    """Get queue items with optional pagination, filtering, and sorting.

    When ``page`` is provided, returns a paginated envelope::

        {items: [...], total, page, page_size, total_pages, counts,
         pending_eta_seconds, pending_eta_unknown}

    Pending/paused/processing items carry an ``eta_seconds`` estimate
    (remaining time for processing items). When ``page`` is omitted,
    returns a flat list (backward compat).
    """
    if page is not None:
        # DB work in a thread (Patch 23 pattern): the paginated query + ETA
        # pass acquire the global write-lock and parse JSON for the page;
        # running them here would block the event loop while the encoder
        # holds the lock.
        def _build():
            env = db.get_queue_items_paginated(
                status=status,
                search=search,
                sort_field=sort or 'priority',
                sort_dir=dir or 'asc',
                page=page,
                page_size=page_size or 50,
            )
            _attach_eta(env)
            return env
        return await asyncio.to_thread(_build)
    # Backward compatible — flat list for internal callers
    items = await asyncio.to_thread(db.get_queue_items, status)
    return items


def _attach_eta(envelope: dict):
    """Add encode-time estimates to a paginated queue envelope.

    Per item: ``eta_seconds`` for pending/paused (full estimate) and
    processing (remaining = estimate scaled by progress). Envelope-level:
    ``pending_eta_seconds`` totals ALL pending items (not just this page),
    with ``pending_eta_unknown`` counting items lacking duration data.
    """
    from app.scanner import estimate_encode_seconds

    speed_stats = db.get_encode_speed_stats()

    for item in envelope.get('items', []):
        if item.get('status') not in ('pending', 'paused', 'processing'):
            continue
        # Processing rows carry a REAL ETA parsed live from HandBrake output
        # (Patch 33) — never overwrite it with a history-based estimate.
        if item.get('status') == 'processing' and (item.get('eta_seconds') or 0) > 0:
            continue
        target = item.get('target_specs') or {}
        est = estimate_encode_seconds(
            item.get('duration_seconds') or 0,
            target.get('codec') if isinstance(target, dict) else None,
            speed_stats,
        )
        if est is None:
            continue
        if item.get('status') == 'processing':
            est *= max(0.0, 1.0 - (item.get('progress') or 0) / 100.0)
        item['eta_seconds'] = round(est)

    total_eta, unknown = compute_pending_eta(speed_stats)
    envelope['pending_eta_seconds'] = total_eta
    envelope['pending_eta_unknown'] = unknown


def compute_pending_eta(speed_stats: Optional[dict] = None) -> tuple:
    """Total estimated encode seconds across ALL pending items.

    Returns (eta_seconds, unknown_count) where unknown_count is the number
    of pending items lacking duration data (excluded from the total).
    Shared by the queue envelope and the /health endpoint.
    """
    from app.scanner import estimate_encode_seconds

    if speed_stats is None:
        speed_stats = db.get_encode_speed_stats()
    total_eta = 0.0
    unknown = 0
    for group in db.get_pending_duration_by_codec():
        unknown += group['unknown_count'] or 0
        est = estimate_encode_seconds(
            group['total_duration'], group['codec'] or None, speed_stats
        )
        if est:
            total_eta += est
    return round(total_eta), unknown


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

    # Manual start takes control: an enabled schedule must not auto-stop this
    # run at the next window boundary. Cleared when the schedule is next saved.
    try:
        from app.scheduler import schedule_manager
        schedule_manager.enable_manual_override()
    except Exception:
        pass

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

    # Manual stop takes control: an enabled schedule must not auto-restart this
    # window. Cleared when the schedule is next saved.
    try:
        from app.scheduler import schedule_manager
        schedule_manager.enable_manual_override()
    except Exception:
        pass

    encoder_pool.stop()

    return MessageResponse(message="Encoding stopped")


# Statistics Endpoints
def _collect_stats() -> dict:
    """Aggregate header stats entirely in SQL (no per-row Python work).

    The old version loaded ALL queue rows into Python (5k+, with per-row
    JSON parsing) just to count statuses — ~140ms holding the global DB
    write-lock. Two aggregate queries instead, ~5ms.
    """
    with db.get_read_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT status, COUNT(*) FROM queue GROUP BY status")
        counts = {row[0]: row[1] for row in cursor.fetchall()}
        cursor.execute(
            "SELECT COUNT(*), COALESCE(SUM(savings_bytes), 0) FROM history"
        )
        hist = cursor.fetchone()
    total_files = hist[0] if hist else 0
    total_saved = hist[1] if hist else 0
    return {
        "total_files_processed": total_files,
        "total_space_saved_bytes": total_saved,
        "total_space_saved_gb": round(total_saved / (1024**3), 2),
        "queue_pending": counts.get('pending', 0),
        "queue_processing": counts.get('processing', 0),
        "queue_completed": counts.get('completed', 0),
        "queue_failed": counts.get('failed', 0),
    }


@router.get("/stats", response_model=StatsResponse)
async def get_statistics(current_user: dict = Depends(get_current_user)):
    """Get system statistics for the always-visible header (polled every 5s).

    Runs the DB work in a thread (like /resources/current, Patch 23) so the
    blocking write-lock acquire never stalls the event loop while the
    encoder holds the lock — that loop-stall was the frozen-header cause.
    """
    return StatsResponse(**await asyncio.to_thread(_collect_stats))


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
    sort_by: 'file_size' | 'estimated_savings' | 'filename'
           | 'fastest_first' | 'slowest_first' | 'default'
    order: 'desc' | 'asc'  (default: desc = largest first; ignored by the
           fastest/slowest strategies, which embed their own direction)

    fastest_first / slowest_first sort by estimated encode time, derived from
    each item's video duration and the average encode speed in history for
    its target codec. Fastest-first suits short encoding windows; slowest-
    first suits overnight runs. Items with unknown duration sort last.
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
    elif sort_by in ("fastest_first", "slowest_first"):
        from app.scanner import estimate_encode_seconds
        speed_stats = db.get_encode_speed_stats()
        slowest = (sort_by == "slowest_first")

        def _sort_key(item):
            target = item.get("target_specs") or {}
            est = estimate_encode_seconds(
                item.get("duration_seconds") or 0,
                target.get("codec") if isinstance(target, dict) else None,
                speed_stats,
            )
            if est is None:
                return (1, 0)  # unknown duration → always last
            return (0, -est if slowest else est)

        items.sort(key=_sort_key)
    # default: leave current order but we still write priorities below

    # Assign rank-based priorities: 1 = first to encode, 2 = second, etc.
    # Matches DB ORDER BY priority ASC
    total = len(items)
    for rank, item in enumerate(items):
        db.update_queue_item(item["id"], priority=rank + 1)

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


def _recheck_permission_item(item: dict) -> bool:
    """Re-run the permission check for one queue item.

    Returns True when the file is accessible now (item flipped to pending).
    On continued failure the row keeps status permission_error but gets the
    current reason written to permission_status/error_message so the UI can
    show WHY (the original enqueue paths never stored it).
    """
    perm_status, perm_message = scanner.check_file_permissions(item['file_path'])
    if perm_status == 'ok':
        db.update_queue_item(
            item['id'],
            status='pending',
            permission_status='ok',
            permission_message=None,
            error_message=None,
            retry_after=None,
        )
        return True
    db.update_queue_item(
        item['id'],
        permission_status=perm_status,
        permission_message=perm_message,
        error_message=perm_message,
    )
    return False


@router.post("/queue/recheck-permissions", response_model=MessageResponse)
async def recheck_all_permissions(current_user: dict = Depends(get_current_user)):
    """Re-check every permission_error item; accessible files go pending."""
    items = db.get_queue_items(status='permission_error')
    if not items:
        return MessageResponse(message="No permission-error items to check")
    fixed = sum(1 for item in items if _recheck_permission_item(item))
    return MessageResponse(
        message=f"Re-checked {len(items)} item(s): {fixed} now pending, "
                f"{len(items) - fixed} still blocked"
    )


@router.post("/queue/{item_id}/recheck-permissions", response_model=MessageResponse)
async def recheck_item_permissions(
    item_id: int,
    current_user: dict = Depends(get_current_user)
):
    """Re-check a single permission_error item."""
    item = db.get_queue_item(item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Queue item not found")
    if item['status'] != 'permission_error':
        return MessageResponse(
            message=f"Item is '{item['status']}' — nothing to re-check",
            success=False
        )
    if _recheck_permission_item(item):
        return MessageResponse(message="File is accessible now — re-queued as pending")
    refreshed = db.get_queue_item(item_id) or {}
    return MessageResponse(
        message=f"Still blocked: {refreshed.get('permission_message') or 'permission error'}",
        success=False
    )


@router.post("/queue/retry-failed", response_model=MessageResponse)
async def retry_all_failed(current_user: dict = Depends(get_current_user)):
    """Re-queue every failed item (resets retry counters)."""
    failed = db.get_queue_items(status='failed')
    for it in failed:
        db.update_queue_item(
            it['id'], status='pending', retry_count=0,
            progress=0.0, error_message=None, retry_after=None
        )
    return MessageResponse(message=f"Re-queued {len(failed)} failed item(s)")


@router.post("/queue/{item_id}/retry", response_model=MessageResponse)
async def retry_queue_item(item_id: int, current_user: dict = Depends(get_current_user)):
    """Re-queue a single failed or cancelled item (resets its retry counter)."""
    item = db.get_queue_item(item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Queue item not found")
    if item['status'] not in ('failed', 'cancelled'):
        return MessageResponse(
            message=f"Item is '{item['status']}' — only failed/cancelled items can be retried",
            success=False
        )
    db.update_queue_item(
        item_id, status='pending', retry_count=0,
        progress=0.0, error_message=None, retry_after=None
    )
    return MessageResponse(message="Item re-queued")


@router.post("/queue/reorder", response_model=MessageResponse)
async def reorder_queue(
    data: dict,
    current_user: dict = Depends(get_current_user)
):
    """Batch-update queue item priorities after a drag-and-drop reorder.

    Body: { "items": [ {"id": 1, "priority": 90}, {"id": 2, "priority": 80}, ... ] }
    """
    items = data.get('items', [])
    if not items:
        raise HTTPException(status_code=400, detail="No items provided")

    for entry in items:
        item_id = entry.get('id')
        priority = entry.get('priority')
        if item_id is not None and priority is not None:
            db.update_queue_item(int(item_id), priority=int(priority))

    return MessageResponse(message=f"Reordered {len(items)} items")