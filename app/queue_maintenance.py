"""Queue upkeep between encodes.

A pending row is a snapshot taken at scan time. Files get deleted, replaced
by Sonarr/Radarr upgrades or re-encoded by other tools while they wait, and
older scans mis-read embedded cover art as the video (a 1080p file queued as
"960x540 png" got a 45-hour AI upscale plan). After every encode:

  * rows whose file is gone are removed;
  * rows whose file changed size, or whose specs look misread, are
    re-probed — specs, duration, upscale plan and savings are recomputed,
    and rows that no longer need encoding are removed;
  * a remembered "Prioritize by" choice is re-applied, so a smart sort
    (fastest/slowest first) stays right as new items arrive and the speed
    estimates it's built on keep learning.

The same pass also runs on a background timer, so an idle queue stays clean.
"""
import json
import os
import threading
import time
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from app.database import db
from app.logger import optimizarr_logger

# Setting holding the last applied sort as {"sort_by": ..., "order": ...}.
# Absent/empty = no auto re-sort (Default, or a manual drag reorder).
AUTO_SORT_SETTING = "queue_auto_sort"

SORT_MODES = ("file_size", "estimated_savings", "filename",
              "fastest_first", "slowest_first")

# Still-image codecs: a "video" with one of these is cover art that an older
# scan picked up instead of the real stream.
_IMAGE_CODECS = {"png", "mjpeg", "jpeg", "bmp", "gif", "webp", "tiff"}

# Re-probes are ~0.1-1 s each; cap per pass so a big backlog of misread rows
# doesn't stall the next encode. Later passes pick up the rest.
MAX_REPROBES_PER_PASS = 50

_lock = threading.Lock()


def _as_dict(raw) -> Dict:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw:
        try:
            val = json.loads(raw)
            return val if isinstance(val, dict) else {}
        except ValueError:
            return {}
    return {}


def _plan_enabled(raw) -> bool:
    return bool(_as_dict(raw).get("enabled"))


def needs_reprobe(item: Dict, size_on_disk: int) -> bool:
    """True when a row's stored specs can't be trusted any more."""
    specs = _as_dict(item.get("current_specs"))
    if specs.get("codec") in _IMAGE_CODECS:
        return True
    stored = item.get("file_size_bytes") or 0
    return bool(stored) and stored != size_on_disk


def revised_upscale_plan(plan_raw, specs: Dict) -> Optional[str]:
    """The row's upscale plan re-checked against fresh specs.

    Uses the scanner's own eligibility rule (source < 85% of target). None
    when the source no longer qualifies; the plan is kept when the height
    can't be read.
    """
    plan = _as_dict(plan_raw)
    if not plan.get("enabled"):
        return plan_raw if plan_raw else None
    height = specs.get("height") or 0
    target = plan.get("target_height") or 0
    if not height or not target:
        return json.dumps(plan)
    if height >= target * 0.85:
        return None
    plan["source_height"] = height
    return json.dumps(plan)


def _recheck_item(item: Dict, scanner) -> str:
    """Re-probe one row. Returns 'updated', 'removed' or 'failed'."""
    path = item["file_path"]
    specs = scanner.analyze_file(path)
    if not specs or specs.get("codec", "unknown") == "unknown":
        return "failed"
    target_specs = _as_dict(item.get("target_specs"))
    name = Path(path).name
    if target_specs and not scanner._needs_encoding(specs, target_specs):
        db.delete_queue_item(item["id"])
        optimizarr_logger.app_logger.info(
            "Queue check: removed %s — file changed and is already optimized", name)
        return "removed"

    old_plan = item.get("upscale_plan")
    upscale_plan = revised_upscale_plan(old_plan, specs)
    if _plan_enabled(old_plan) and not _plan_enabled(upscale_plan):
        optimizarr_logger.app_logger.info(
            "Queue check: dropped upscale for %s — source is %sp, already at target",
            name, specs.get("height"))

    size = Path(path).stat().st_size
    profile = db.get_profile(item["profile_id"]) or {}
    savings = scanner._estimate_savings(
        size, specs.get("codec", "unknown"), target_specs.get("codec", "unknown"),
        current_specs=specs, profile=profile,
        upscale_plan=upscale_plan, stereo_plan=item.get("stereo_plan"),
    )
    db.update_queue_item(
        item["id"], current_specs=json.dumps(specs),
        duration_seconds=specs.get("duration", 0), file_size_bytes=size,
        estimated_savings_bytes=savings, upscale_plan=upscale_plan,
    )
    return "updated"


def revalidate_pending(scanner=None) -> Dict[str, int]:
    """Drop vanished files and refresh stale rows among pending items."""
    if scanner is None:
        from app.scanner import scanner
    counts = {"removed_missing": 0, "updated": 0, "removed_optimized": 0, "failed": 0}
    reprobes = 0
    for item in db.get_queue_items(status="pending"):
        path = item.get("file_path") or ""
        try:
            size = os.stat(path).st_size
        except FileNotFoundError:
            db.delete_queue_item(item["id"])
            counts["removed_missing"] += 1
            optimizarr_logger.app_logger.info(
                "Queue check: removed %s — file no longer exists", Path(path).name)
            continue
        except OSError:
            continue  # drive offline / permission blip — leave the row alone
        if reprobes >= MAX_REPROBES_PER_PASS or not needs_reprobe(item, size):
            continue
        reprobes += 1
        try:
            outcome = _recheck_item(item, scanner)
        except Exception as e:
            optimizarr_logger.app_logger.warning(
                "Queue check: re-probe failed for %s: %s", Path(path).name, e)
            outcome = "failed"
        key = "removed_optimized" if outcome == "removed" else outcome
        counts[key] += 1
    return counts


def sort_pending(items: List[Dict], sort_by: str, order: str) -> List[Dict]:
    """Order pending items by a "Prioritize by" mode (first = encode first)."""
    reverse = (order == "desc")
    if sort_by == "file_size":
        items.sort(key=lambda i: i.get("file_size_bytes", 0), reverse=reverse)
    elif sort_by == "estimated_savings":
        items.sort(key=lambda i: i.get("estimated_savings_bytes", 0), reverse=reverse)
    elif sort_by == "filename":
        items.sort(key=lambda i: Path(i.get("file_path", "")).name.lower(), reverse=reverse)
    elif sort_by in ("fastest_first", "slowest_first"):
        from app.scanner import estimate_item_seconds
        speed_stats = db.get_encode_speed_stats()
        slowest = (sort_by == "slowest_first")

        # Calibrate seconds-per-byte from items that HAVE a duration, then use
        # it to estimate a cost for items that don't — so unknowns order by
        # size instead of all piling at the end. Only plain encodes calibrate
        # it: an upscale's cost has nothing to do with its source file size,
        # and letting one in would skew every unknown item's estimate.
        spb_samples = []
        for it in items:
            size = it.get("file_size_bytes") or 0
            preprocessed = (_plan_enabled(it.get("upscale_plan"))
                            or _plan_enabled(it.get("stereo_plan")))
            if size > 0 and not preprocessed:
                est = estimate_item_seconds(it, speed_stats)
                if est:
                    spb_samples.append(est / size)
        avg_spb = (sum(spb_samples) / len(spb_samples)) if spb_samples else None

        def _cost(item):
            est = estimate_item_seconds(item, speed_stats)
            if est is not None:
                return est
            size = item.get("file_size_bytes") or 0
            # Calibrated proxy when we have samples; else raw size keeps a
            # consistent big-after-small ordering among unknowns.
            return size * avg_spb if avg_spb is not None else size * 1e-6

        items.sort(key=_cost, reverse=slowest)
    # default: leave current order
    return items


def apply_sort(sort_by: str, order: str) -> int:
    """Re-rank all pending items. Returns the number re-ranked."""
    items = db.get_queue_items(status="pending")
    if not items:
        return 0
    sort_pending(items, sort_by, order)
    # Rank-based priorities: 1 = first to encode. Matches ORDER BY priority ASC.
    return db.set_queue_priorities(
        (item["id"], rank + 1) for rank, item in enumerate(items))


def get_auto_sort() -> Optional[Tuple[str, str]]:
    cfg = _as_dict(db.get_setting(AUTO_SORT_SETTING, ""))
    if cfg.get("sort_by") in SORT_MODES:
        return cfg["sort_by"], cfg.get("order", "desc")
    return None


def set_auto_sort(sort_by: Optional[str], order: str = "desc"):
    """Remember a sort to re-apply after each encode (None/default clears)."""
    value = json.dumps({"sort_by": sort_by, "order": order}) if sort_by in SORT_MODES else ""
    db.set_setting(AUTO_SORT_SETTING, value)


def after_encode():
    """One upkeep pass — after each finished job and on the background timer.
    Never raises; skips if a pass is already running."""
    if not _lock.acquire(blocking=False):
        return
    try:
        counts = revalidate_pending()
        if any(counts.values()):
            optimizarr_logger.app_logger.info(
                "Queue check: %d missing removed, %d refreshed, %d already "
                "optimized removed, %d re-probe failures",
                counts["removed_missing"], counts["updated"],
                counts["removed_optimized"], counts["failed"])
        mode = get_auto_sort()
        if mode:
            apply_sort(*mode)
    except Exception as e:
        optimizarr_logger.app_logger.warning("Queue check failed: %s", e)
    finally:
        _lock.release()


# Background cadence. The pass is an os.stat per pending row plus a few
# capped re-probes, so this is cheap even for a large queue.
CHECK_INTERVAL_SECONDS = 15 * 60


def start_background_checker():
    """Run after_encode()'s checks every CHECK_INTERVAL_SECONDS (daemon)."""
    def _loop():
        while True:
            time.sleep(CHECK_INTERVAL_SECONDS)
            after_encode()

    threading.Thread(target=_loop, daemon=True, name="queue-checker").start()
