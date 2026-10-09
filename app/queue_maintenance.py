"""Queue upkeep between encodes.

A queued row is a snapshot taken at scan time. Files get deleted, replaced
by Sonarr/Radarr upgrades or re-encoded by other tools while they wait,
settings change under them, and older scans mis-read embedded cover art as
the video (a 1080p file queued as "960x540 png" got a 45-hour AI upscale
plan). After every encode, and on a timer (queue_check_interval_minutes):

  * waiting rows (pending/failed/permission_error/quarantined) whose file is
    gone are removed — unless the whole folder is gone (offline drive);
  * a second row for the same file (another path spelling) is removed;
  * quarantined rows are re-tried and go back to pending once readable;
  * pending rows whose file changed size, or whose specs look misread, are
    re-probed (specs, duration, savings recomputed);
  * pending rows follow their profile's current target codec/resolution and
    the current upscale rule (engine/model, trigger and target heights,
    upscaling on/off) — rows that no longer need encoding are removed;
  * a remembered "Prioritize by" choice is re-applied, so a smart sort
    (fastest/slowest first) stays right as new items arrive and the speed
    estimates it's built on keep learning.
"""
import json
import os
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from app.database import db
from app.logger import optimizarr_logger

# Setting holding the last applied sort as {"sort_by": ..., "order": ...}.
# Absent/empty = no auto re-sort (Default, or a manual drag reorder).
AUTO_SORT_SETTING = "queue_auto_sort"
LAST_CHECK_SETTING = "queue_last_check"
INTERVAL_SETTING = "queue_check_interval_minutes"
DEFAULT_INTERVAL_MINUTES = 15

SORT_MODES = ("file_size", "estimated_savings", "filename",
              "fastest_first", "slowest_first")

# Still-image codecs: a "video" with one of these is cover art that an older
# scan picked up instead of the real stream.
_IMAGE_CODECS = {"png", "mjpeg", "jpeg", "bmp", "gif", "webp", "tiff"}

# Re-probes are ~0.1-1 s each; cap per pass so a big backlog of misread rows
# doesn't stall the next encode. Later passes pick up the rest.
MAX_REPROBES_PER_PASS = 50

_PLAN_KEYS = ("enabled", "upscaler_key", "model", "factor", "source_height",
              "target_height")

# Statuses whose rows are checked for vanished files and duplicates. Not
# 'processing'/'paused' (a live job owns those) nor finished rows (history).
_WAITING = ("pending", "failed", "permission_error", "quarantined")

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


def _settings_for(item: Dict, cache: Dict) -> Tuple[Dict, Optional[Dict]]:
    """(profile, upscale settings source) for an item, cached per pass."""
    from app.scanner import upscale_source_for
    if "roots" not in cache:
        cache["roots"] = {r["id"]: r for r in db.get_scan_roots()}
        cache["profiles"] = {}
    pid = item.get("profile_id")
    if pid not in cache["profiles"]:
        cache["profiles"][pid] = db.get_profile(pid) or {}
    profile = cache["profiles"][pid]
    root = cache["roots"].get(item.get("root_id"))
    return profile, upscale_source_for(root, profile)


def _desired_plan(item: Dict, specs: Dict, cache: Dict) -> Optional[str]:
    """The plan the item should have under the CURRENT settings — the same
    rule the scanner applies. Follows engine/model changes, adds a plan when
    a raised trigger height now covers the source, drops it when upscaling
    was turned off or the source no longer qualifies. Unknown height keeps
    whatever the row has."""
    from app.scanner import upscale_plan_for
    height = specs.get("height") or 0
    if not height:
        return item.get("upscale_plan") or None
    _, source = _settings_for(item, cache)
    return upscale_plan_for(source, height)


def _same_plan(a, b) -> bool:
    pa, pb = _as_dict(a), _as_dict(b)
    if not pa.get("enabled") and not pb.get("enabled"):
        return True
    return all(pa.get(k) == pb.get(k) for k in _PLAN_KEYS)


def _desired_target(item: Dict, cache: Dict) -> Dict:
    """target_specs refreshed from the profile's current codec/resolution/
    audio (and framerate, when the row carries one)."""
    profile, _ = _settings_for(item, cache)
    target = dict(_as_dict(item.get("target_specs")))
    if not profile:
        return target
    for key in ("codec", "resolution", "audio_codec"):
        if key in profile:
            target[key] = profile.get(key) or ""
    if "framerate" in target and "framerate" in profile:
        target["framerate"] = profile["framerate"]
    return target


def _file_identity(path: str, st) -> tuple:
    """Same file under two spellings (drive letter vs UNC, case, ..) has the
    same volume + file index. Falls back to the normalised path where the
    filesystem reports no index."""
    if getattr(st, "st_ino", 0):
        return ("ino", st.st_dev, st.st_ino)
    return ("path", os.path.normcase(os.path.abspath(path)))


def _refresh_pending(item: Dict, size: int, scanner, cache: Dict,
                     budget: List[int]) -> str:
    """Bring one pending row up to date. Returns the outcome key."""
    path = item["file_path"]
    name = Path(path).name
    specs = _as_dict(item.get("current_specs"))
    reprobed = False
    if needs_reprobe(item, size) and budget[0] > 0:
        budget[0] -= 1
        fresh = scanner.analyze_file(path)
        if not fresh or fresh.get("codec", "unknown") == "unknown":
            return "failed"
        specs, reprobed = fresh, True

    target = _desired_target(item, cache)
    if (target.get("codec") and specs.get("codec")
            and not scanner._needs_encoding(specs, target)):
        db.delete_queue_item(item["id"])
        optimizarr_logger.app_logger.info(
            "Queue check: removed %s — already optimized for its profile", name)
        return "removed_optimized"

    plan = _desired_plan(item, specs, cache)
    old_plan = item.get("upscale_plan")
    plan_changed = not _same_plan(old_plan, plan)
    target_changed = target != _as_dict(item.get("target_specs"))
    if not (reprobed or plan_changed or target_changed):
        return "unchanged"
    if plan_changed:
        if not _plan_enabled(plan):
            how = "removed"
        elif not _plan_enabled(old_plan):
            how = "added"
        else:
            how = "updated to current settings"
        optimizarr_logger.app_logger.info("Queue check: upscale plan for %s %s", name, how)

    profile, _ = _settings_for(item, cache)
    fields = {"target_specs": json.dumps(target), "upscale_plan": plan}
    fields["estimated_savings_bytes"] = scanner._estimate_savings(
        size, specs.get("codec", "unknown"), target.get("codec", "unknown"),
        current_specs=specs, profile=profile,
        upscale_plan=plan, stereo_plan=item.get("stereo_plan"),
    )
    if reprobed:
        fields.update(current_specs=json.dumps(specs), file_size_bytes=size,
                      duration_seconds=specs.get("duration", 0))
    db.update_queue_item(item["id"], **fields)
    if reprobed:
        return "updated"
    return "plans_synced" if plan_changed else "targets_synced"


def recheck_quarantined_item(item: Dict) -> str:
    """Re-probe one quarantined row.

    Returns 'pending' (a valid video now — re-queued), 'removed' (file gone —
    row deleted) or 'quarantined' (still broken).
    """
    from app.encoder import preflight_source_check
    from app.scanner import scanner
    problem = preflight_source_check(item["file_path"])
    if problem is None:
        # Re-probe specs too — the row was queued before the file was
        # complete, so codec/resolution/duration are likely stale.
        specs = scanner.analyze_file(item["file_path"]) or {}
        db.update_queue_item(
            item["id"], status="pending", error_message=None, retry_count=0,
            retry_after=None, current_specs=json.dumps(specs),
            duration_seconds=specs.get("duration", 0),
        )
        return "pending"
    if problem == "missing":
        db.delete_queue_item(item["id"])
        return "removed"
    db.update_queue_item(item["id"], error_message=problem)
    return "quarantined"


def revalidate_queue(scanner=None) -> Dict[str, int]:
    """One pass over the waiting queue. Returns what changed."""
    if scanner is None:
        from app.scanner import scanner
    counts = {k: 0 for k in ("removed_missing", "duplicates_removed", "updated",
                             "removed_optimized", "plans_synced", "targets_synced",
                             "unquarantined", "failed")}
    budget = [MAX_REPROBES_PER_PASS]
    cache: Dict = {}
    items = db.get_queue_items()
    # Rows a live job owns anchor duplicate detection: a waiting copy of a
    # file that's encoding right now is the duplicate, never the live one.
    seen = {}
    for item in items:
        if item.get("status") in ("processing", "paused"):
            try:
                seen[_file_identity(item["file_path"], os.stat(item["file_path"]))] = item["id"]
            except OSError:
                pass

    for item in items:  # priority order: the first copy of a file is kept
        status = item.get("status")
        if status not in _WAITING:
            continue
        path = item.get("file_path") or ""
        name = Path(path).name
        try:
            st = os.stat(path)
        except FileNotFoundError:
            # Only when its folder is still there: an unplugged drive or a
            # dropped network share makes EVERY file look deleted.
            if os.path.isdir(os.path.dirname(path)):
                db.delete_queue_item(item["id"])
                counts["removed_missing"] += 1
                optimizarr_logger.app_logger.info(
                    "Queue check: removed %s — file no longer exists", name)
            continue
        except OSError:
            continue  # drive offline / permission blip — leave the row alone

        ident = _file_identity(path, st)
        if ident in seen:
            db.delete_queue_item(item["id"])
            counts["duplicates_removed"] += 1
            optimizarr_logger.app_logger.info(
                "Queue check: removed duplicate of %s (same file as row %s)",
                name, seen[ident])
            continue
        seen[ident] = item["id"]

        try:
            if status == "quarantined":
                if budget[0] > 0:
                    budget[0] -= 1
                    if recheck_quarantined_item(item) == "pending":
                        counts["unquarantined"] += 1
                        optimizarr_logger.app_logger.info(
                            "Queue check: %s is readable now — back to pending", name)
            elif status == "pending":
                outcome = _refresh_pending(item, st.st_size, scanner, cache, budget)
                if outcome in counts:
                    counts[outcome] += 1
        except Exception as e:
            optimizarr_logger.app_logger.warning(
                "Queue check: couldn't refresh %s: %s", name, e)
            counts["failed"] += 1
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
        counts = revalidate_queue()
        changed = {k: v for k, v in counts.items() if v}
        if changed:
            optimizarr_logger.app_logger.info(
                "Queue check: %s", ", ".join(f"{k.replace('_', ' ')} {v}"
                                              for k, v in changed.items()))
        mode = get_auto_sort()
        if mode:
            apply_sort(*mode)
        db.set_setting(LAST_CHECK_SETTING, json.dumps(
            {"at": time.time(), "counts": counts}))
    except Exception as e:
        optimizarr_logger.app_logger.warning("Queue check failed: %s", e)
    finally:
        _lock.release()


def last_check() -> Dict:
    """{"at": epoch seconds, "counts": {...}} of the last pass, or {}."""
    return _as_dict(db.get_setting(LAST_CHECK_SETTING, ""))


def interval_minutes() -> int:
    """Background check interval; 0 = timer off (after-encode still runs)."""
    try:
        return max(0, min(1440, int(db.get_setting(
            INTERVAL_SETTING, str(DEFAULT_INTERVAL_MINUTES)))))
    except (TypeError, ValueError):
        return DEFAULT_INTERVAL_MINUTES


def start_background_checker():
    """Run the upkeep pass every ``interval_minutes()`` (daemon thread).

    Wakes each minute so a changed interval takes effect without a restart.
    """
    def _loop():
        last = time.monotonic()
        while True:
            time.sleep(60)
            minutes = interval_minutes()
            if minutes and time.monotonic() - last >= minutes * 60:
                after_encode()
                last = time.monotonic()

    threading.Thread(target=_loop, daemon=True, name="queue-checker").start()
