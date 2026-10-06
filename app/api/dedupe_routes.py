"""Duplicate-detection routes: scan control, results, safe file deletion."""
import time
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from app.api.models import MessageResponse
from app.api.dependencies import get_current_user, get_current_admin_user
from app.database import db

router = APIRouter()


@router.post("/duplicates/scan", response_model=MessageResponse)
async def start_duplicate_scan(
    data: dict = None,
    current_user: dict = Depends(get_current_user)
):
    """Start a duplicate scan.

    body {"mode": "quick"} → hash-only pass (minutes), exact copies only.
    body {"mode": "full"}  → also fingerprints content (slow), finding
                             re-encodes and clips inside longer videos.
    """
    from app.dedupe import start_scan, get_scan_status
    if get_scan_status().get('status') == 'running':
        return MessageResponse(message="A scan is already running", success=False)
    mode = ((data or {}).get('mode') or 'full').lower()
    start_scan(mode)
    return MessageResponse(
        message=("Quick scan started — finding exact duplicates"
                 if mode == 'quick' else
                 "Full scan started — this can take a long time; "
                 "progress is saved as it goes"))


@router.post("/duplicates/scan/cancel", response_model=MessageResponse)
async def cancel_duplicate_scan(current_user: dict = Depends(get_current_user)):
    """Stop a running scan. Completed fingerprints are kept, so restarting
    the scan resumes rather than starting over."""
    from app.dedupe import cancel_scan, get_scan_status
    if get_scan_status().get('status') != 'running':
        return MessageResponse(message="No scan is running", success=False)
    cancel_scan()
    return MessageResponse(message="Cancelling scan…")


@router.get("/duplicates/status")
async def duplicate_scan_status(current_user: dict = Depends(get_current_user)):
    from app.dedupe import get_scan_status
    return get_scan_status()


@router.get("/duplicates")
async def list_duplicates(current_user: dict = Depends(get_current_user)):
    """All match results, grouped by the kept (longer/original) file.

    Each group: {file_a, size_a, duration_a, matches: [{file_b, size_b,
    kind, score, offset_seconds, overlap_seconds}]}. Sizes come from the
    fingerprint cache; files deleted since the scan are filtered out.
    """
    matches = db.get_duplicate_matches()
    fps = {f['file_path']: f for f in db.get_all_fingerprints()}

    groups: dict = {}
    for m in matches:
        a, b = m['file_a'], m['file_b']
        if not (Path(a).exists() and Path(b).exists()):
            continue
        g = groups.setdefault(a, {
            'file_a': a,
            'size_a': (fps.get(a) or {}).get('file_size', 0),
            'duration_a': (fps.get(a) or {}).get('duration', 0),
            'matches': [],
        })
        g['matches'].append({
            'file_b': b,
            'size_b': (fps.get(b) or {}).get('file_size', 0),
            'duration_b': (fps.get(b) or {}).get('duration', 0),
            'kind': m['kind'],
            'score': m['score'],
            'offset_seconds': m['offset_seconds'],
            'overlap_seconds': m['overlap_seconds'],
        })

    out = sorted(groups.values(),
                 key=lambda g: sum(x['size_b'] or 0 for x in g['matches']),
                 reverse=True)
    reclaimable = sum(x['size_b'] or 0
                      for g in out for x in g['matches']
                      if x['kind'] in ('exact', 'duplicate'))
    return {'groups': out, 'total_groups': len(out),
            'reclaimable_bytes': reclaimable}


@router.post("/duplicates/delete-file", response_model=MessageResponse)
async def delete_duplicate_file(
    data: dict,
    current_user: dict = Depends(get_current_admin_user)
):
    """Delete a duplicate file from disk (admin only).

    Safety rails: the path must be one we fingerprinted (i.e. it came from a
    configured library scan — arbitrary paths are refused), and a file that
    is currently encoding can't be deleted.
    """
    file_path = (data or {}).get('file_path') or ''
    fp = db.get_fingerprint(file_path)
    if not fp:
        raise HTTPException(status_code=400,
                            detail="Path is not a fingerprinted library file")

    for item in db.get_queue_items(status='processing'):
        if item['file_path'] == file_path:
            return MessageResponse(
                message="File is currently encoding — stop the encode first",
                success=False)

    p = Path(file_path)
    freed = 0
    if p.exists():
        freed = p.stat().st_size
        # Windows can hold handles briefly (thumbnailers, the scanner) —
        # retry a few times before reporting failure.
        deleted = False
        for _ in range(5):
            try:
                p.unlink()
                deleted = True
                break
            except PermissionError:
                time.sleep(0.5)
            except FileNotFoundError:
                deleted = True
                break
        if not deleted:
            return MessageResponse(
                message="Could not delete — file is in use", success=False)

    removed_queue = db.delete_queue_items_by_path(file_path)
    db.delete_fingerprint(file_path)

    from app.logger import optimizarr_logger
    optimizarr_logger.app_logger.info(
        "Duplicate deleted by %s: %s (freed %.1f MB, %d queue row(s) removed)",
        current_user.get('username', '?'), file_path, freed / 1e6, removed_queue)
    return MessageResponse(
        message=f"Deleted ({freed / (1024**2):.1f} MB freed)")
