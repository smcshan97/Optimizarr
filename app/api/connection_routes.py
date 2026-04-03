"""External connections (Sonarr, Radarr, Stash) and webhook routes."""
from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks
from typing import List

from app.api.models import MessageResponse
from app.api.dependencies import get_current_user, get_current_admin_user
from app.database import db
from app.logger import optimizarr_logger

router = APIRouter()


@router.get("/connections")
async def list_connections(current_user: dict = Depends(get_current_user)):
    """List all external connections (API keys are masked)."""
    from app.external_connections import public_connection
    conns = db.get_external_connections()
    return [public_connection(c) for c in conns]


@router.post("/connections", status_code=status.HTTP_201_CREATED)
async def create_connection(
    data: dict,
    current_user: dict = Depends(get_current_admin_user),
):
    """
    Add a new external connection.
    Tests connectivity before saving — returns 400 if test fails.
    Body: { name, app_type, base_url, api_key, show_in_stats? }
    """
    from app.external_connections import (
        encrypt_api_key, public_connection, connection_manager
    )

    app_type = data.get("app_type", "").lower()
    if app_type not in ("sonarr", "radarr", "stash"):
        raise HTTPException(
            status_code=400,
            detail="Unsupported app_type. Supported values: 'sonarr', 'radarr', 'stash'.",
        )

    raw_key = data.get("api_key", "").strip()
    if not raw_key:
        raise HTTPException(status_code=400, detail="api_key is required")

    base_url = data.get("base_url", "").strip()
    if not base_url:
        raise HTTPException(status_code=400, detail="base_url is required")

    name = data.get("name", "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")

    # Test before saving
    test_conn = {
        "app_type": app_type,
        "base_url": base_url,
        "api_key_encrypted": encrypt_api_key(raw_key),
    }
    result = connection_manager.test_connection(test_conn)
    if not result["ok"]:
        raise HTTPException(
            status_code=400,
            detail=f"Connection test failed: {result['error']}",
        )

    conn_id = db.create_external_connection(
        name=name,
        app_type=app_type,
        base_url=base_url,
        api_key_encrypted=encrypt_api_key(raw_key),
        enabled=data.get("enabled", True),
        show_in_stats=data.get("show_in_stats", True),
    )

    # Stamp last_tested
    db.update_external_connection(
        conn_id,
        last_tested=datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
    )

    conn = db.get_external_connection(conn_id)
    optimizarr_logger.app_logger.info("External connection created: %s (%s)", name, app_type)
    return {
        **public_connection(conn),
        "test_result": result,
        "message": f"Connected to {result.get('app_name', app_type)} {result.get('version', '')}",
    }


@router.put("/connections/{conn_id}")
async def update_connection(
    conn_id: int,
    data: dict,
    current_user: dict = Depends(get_current_admin_user),
):
    """
    Update an existing connection.
    If a new api_key is provided it will be encrypted and stored.
    If api_key is omitted or empty the existing key is kept.
    """
    from app.external_connections import encrypt_api_key, public_connection

    existing = db.get_external_connection(conn_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Connection not found")

    update_kwargs = {}
    for field in ("name", "base_url", "enabled", "show_in_stats", "app_type"):
        if field in data:
            update_kwargs[field] = data[field]

    # Only re-encrypt if a new key was actually supplied
    new_key = data.get("api_key", "").strip()
    if new_key:
        update_kwargs["api_key_encrypted"] = encrypt_api_key(new_key)

    db.update_external_connection(conn_id, **update_kwargs)
    conn = db.get_external_connection(conn_id)
    return {"message": "Connection updated", **public_connection(conn)}


@router.delete("/connections/{conn_id}")
async def delete_connection(
    conn_id: int,
    current_user: dict = Depends(get_current_admin_user),
):
    """Delete an external connection."""
    existing = db.get_external_connection(conn_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Connection not found")
    db.delete_external_connection(conn_id)
    optimizarr_logger.app_logger.info("External connection deleted: id=%s", conn_id)
    return {"message": "Connection deleted"}


@router.post("/connections/{conn_id}/test")
async def test_connection(
    conn_id: int,
    current_user: dict = Depends(get_current_user),
):
    """Test connectivity to the external app and return version info."""
    from app.external_connections import connection_manager

    conn = db.get_external_connection(conn_id)
    if not conn:
        raise HTTPException(status_code=404, detail="Connection not found")

    result = connection_manager.test_connection(conn)

    # Update last_tested regardless of result
    db.update_external_connection(
        conn_id,
        last_tested=datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
    )
    return result


@router.post("/connections/{conn_id}/sync")
async def sync_connection(
    conn_id: int,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(get_current_admin_user),
):
    """
    Pull the full library from Sonarr or Radarr and add new files to the queue.
    Runs in the background — returns immediately with a task confirmation.
    """
    conn = db.get_external_connection(conn_id)
    if not conn:
        raise HTTPException(status_code=404, detail="Connection not found")
    if not conn.get("enabled"):
        raise HTTPException(status_code=400, detail="Connection is disabled")

    background_tasks.add_task(_sync_connection_task, conn_id)
    return {"message": f"Sync started for '{conn['name']}' — files will appear in the queue shortly."}


def _sync_connection_task(conn_id: int):
    """Background task: fetch library from Sonarr/Radarr and queue new files."""
    import json as _json
    from app.external_connections import connection_manager
    from app.scanner import scanner

    conn = db.get_external_connection(conn_id)
    if not conn:
        return

    app_type = conn["app_type"]
    optimizarr_logger.app_logger.info("Sync started: %s (%s)", conn["name"], app_type)

    try:
        if app_type == "radarr":
            items = connection_manager.fetch_radarr_library(conn)
        elif app_type == "sonarr":
            items = connection_manager.fetch_sonarr_library(conn)
        elif app_type == "stash":
            items = connection_manager.fetch_stash_library(conn)
        else:
            return
    except Exception as e:
        optimizarr_logger.app_logger.error("Sync failed for %s: %s", conn["name"], e)
        return

    # Find a scan root linked to this connection for profile assignment
    linked_roots = [
        r for r in db.get_scan_roots()
        if r.get("external_connection_id") == conn_id
    ]
    profile_id = None
    root_id = None
    if linked_roots:
        profile_id = linked_roots[0]["profile_id"]
        root_id = linked_roots[0]["id"]
    else:
        # Fall back to the default profile
        defaults = [p for p in db.get_profiles() if p.get("is_default")]
        if defaults:
            profile_id = defaults[0]["id"]

    if not profile_id:
        optimizarr_logger.app_logger.warning(
            "Sync: no profile found for connection %s — skipping", conn["name"]
        )
        return

    profile = db.get_profile(profile_id)
    if not profile:
        return

    # Build set of already-queued paths
    existing_paths = {item["file_path"] for item in db.get_queue_items()}

    added = 0
    skipped = 0
    for item in items:
        path = item["file_path"]
        if path in existing_paths:
            skipped += 1
            continue

        current_specs = item.get("current_specs", {})
        target_specs = {
            "codec": profile["codec"],
            "resolution": profile.get("resolution", ""),
            "audio_codec": profile["audio_codec"],
        }

        # Skip if already at target codec
        if not scanner._needs_encoding(current_specs, target_specs):
            skipped += 1
            continue

        perm_status, _ = scanner.check_file_permissions(path)
        file_size = item.get("file_size_bytes", 0)

        # Evaluate upscale eligibility: linked scan root > profile fallback
        import json as _json
        upscale_plan   = None
        linked_root    = db.get_scan_root(root_id) if root_id else None
        upscale_source = None
        if linked_root and linked_root.get("upscale_enabled"):
            upscale_source = linked_root
        elif profile.get("upscale_enabled"):
            upscale_source = profile
        if upscale_source:
            src_h   = current_specs.get("height", 0) or 0
            trigger = upscale_source.get("upscale_trigger_below", 720)
            t_h     = upscale_source.get("upscale_target_height", 1080)
            if src_h > 0 and src_h < trigger and src_h < (t_h * 0.85):
                upscale_plan = _json.dumps({
                    "enabled":       True,
                    "upscaler_key":  upscale_source.get("upscale_key", "realesrgan"),
                    "model":         upscale_source.get("upscale_model", "realesrgan-x4plus"),
                    "factor":        upscale_source.get("upscale_factor", 2),
                    "source_height": src_h,
                    "target_height": t_h,
                })

        # Evaluate stereo eligibility: linked scan root > profile fallback
        stereo_plan    = None
        stereo_source  = None
        if linked_root and linked_root.get("stereo_enabled"):
            stereo_source = linked_root
        elif profile.get("stereo_enabled"):
            stereo_source = profile
        if stereo_source:
            stereo_plan = _json.dumps({
                "enabled":     True,
                "mode":        stereo_source.get("stereo_mode", "2d_to_3d"),
                "format":      stereo_source.get("stereo_format", "half_sbs"),
                "divergence":  stereo_source.get("stereo_divergence", 2.0),
                "convergence": stereo_source.get("stereo_convergence", 0.5),
                "depth_model": stereo_source.get("stereo_depth_model", "Any_V2_S"),
            })

        db.add_to_queue(
            file_path=path,
            root_id=root_id,
            profile_id=profile_id,
            status="pending" if perm_status == "ok" else "permission_error",
            current_specs=current_specs,
            target_specs=target_specs,
            file_size_bytes=file_size,
            estimated_savings_bytes=scanner._estimate_savings(
                file_size,
                current_specs.get("codec", "unknown"),
                profile["codec"],
                current_specs=current_specs,
                profile=profile,
            ),
            upscale_plan=upscale_plan,
            stereo_plan=stereo_plan,
        )
        added += 1

    db.update_external_connection(
        conn_id,
        last_synced=datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
    )
    optimizarr_logger.app_logger.info(
        "Sync complete: %s — %d added, %d skipped", conn["name"], added, skipped
    )


@router.post("/connections/{conn_id}/register-webhook")
async def register_webhook(
    conn_id: int,
    data: dict,
    current_user: dict = Depends(get_current_admin_user),
):
    """
    Register Optimizarr as a webhook in Sonarr/Radarr.
    Body: { optimizarr_url: "http://your-optimizarr:5000" }
    """
    from app.external_connections import connection_manager

    conn = db.get_external_connection(conn_id)
    if not conn:
        raise HTTPException(status_code=404, detail="Connection not found")

    optimizarr_url = data.get("optimizarr_url", "").strip()
    if not optimizarr_url:
        raise HTTPException(status_code=400, detail="optimizarr_url is required")

    result = connection_manager.register_webhook(conn, optimizarr_url)
    if not result["ok"]:
        raise HTTPException(status_code=400, detail=f"Webhook registration failed: {result['error']}")
    return result


# ============================================================
# WEBHOOKS — Receive download events from Sonarr / Radarr
# ============================================================

@router.post("/webhooks/{app_type}")
async def receive_webhook(app_type: str, payload: dict):
    """
    Receive download completion events from Sonarr or Radarr.
    No authentication — the caller is on the local network.
    Files are queued as pending (respects scheduler rest hours).
    """
    import json as _json
    from app.external_connections import CODEC_MAP
    from app.scanner import scanner

    app_type = app_type.lower()
    event_type = payload.get("eventType", "")

    optimizarr_logger.app_logger.info(
        "Webhook received: %s event=%s", app_type, event_type
    )

    # We only act on Download and Upgrade events
    if event_type not in ("Download", "Upgrade"):
        return {"ok": True, "message": f"Event '{event_type}' ignored"}

    # Extract file path and media info from the payload
    file_path = None
    file_size = 0
    raw_codec = "unknown"
    resolution = "unknown"

    if app_type == "radarr":
        mf = payload.get("movieFile", {}) or {}
        file_path = mf.get("path")
        file_size = mf.get("size", 0)
        mi = mf.get("mediaInfo", {}) or {}
        raw_codec = mi.get("videoCodec", "unknown")
        resolution = mi.get("videoResolution", "unknown")

    elif app_type == "sonarr":
        ef = payload.get("episodeFile", {}) or {}
        file_path = ef.get("path")
        file_size = ef.get("size", 0)
        mi = ef.get("mediaInfo", {}) or {}
        raw_codec = mi.get("videoCodec", "unknown")
        resolution = mi.get("resolution", "unknown")

    if not file_path:
        return {"ok": False, "message": "No file path in webhook payload"}

    codec = CODEC_MAP.get(raw_codec.lower(), raw_codec.lower() or "unknown")

    # Find a connection of this app_type and a linked scan root
    connections = [c for c in db.get_external_connections()
                   if c["app_type"] == app_type and c.get("enabled")]
    profile_id = None
    root_id = None

    for c in connections:
        linked = [r for r in db.get_scan_roots()
                  if r.get("external_connection_id") == c["id"]]
        if linked:
            profile_id = linked[0]["profile_id"]
            root_id = linked[0]["id"]
            break

    if not profile_id:
        defaults = [p for p in db.get_profiles() if p.get("is_default")]
        if defaults:
            profile_id = defaults[0]["id"]

    if not profile_id:
        return {"ok": False, "message": "No profile found to assign this file"}

    profile = db.get_profile(profile_id)
    if not profile:
        return {"ok": False, "message": "Profile not found"}

    # Skip if already queued
    existing = {item["file_path"] for item in db.get_queue_items()}
    if file_path in existing:
        return {"ok": True, "message": "File already in queue"}

    current_specs = {"codec": codec, "resolution": resolution, "bit_rate": 0}
    target_specs = {
        "codec": profile["codec"],
        "resolution": profile.get("resolution", ""),
        "audio_codec": profile["audio_codec"],
    }

    perm_status, _ = scanner.check_file_permissions(file_path)

    # Evaluate upscale eligibility: linked scan root > profile fallback
    import json as _json
    upscale_plan   = None
    linked_root    = db.get_scan_root(root_id) if root_id else None
    upscale_source = None
    if linked_root and linked_root.get("upscale_enabled"):
        upscale_source = linked_root
    elif profile.get("upscale_enabled"):
        upscale_source = profile
    if upscale_source:
        src_h   = current_specs.get("height", 0) or 0
        trigger = upscale_source.get("upscale_trigger_below", 720)
        t_h     = upscale_source.get("upscale_target_height", 1080)
        if src_h > 0 and src_h < trigger and src_h < (t_h * 0.85):
            upscale_plan = _json.dumps({
                "enabled":       True,
                "upscaler_key":  upscale_source.get("upscale_key", "realesrgan"),
                "model":         upscale_source.get("upscale_model", "realesrgan-x4plus"),
                "factor":        upscale_source.get("upscale_factor", 2),
                "source_height": src_h,
                "target_height": t_h,
            })

    # Evaluate stereo eligibility: linked scan root > profile fallback
    stereo_plan    = None
    stereo_source  = None
    if linked_root and linked_root.get("stereo_enabled"):
        stereo_source = linked_root
    elif profile.get("stereo_enabled"):
        stereo_source = profile
    if stereo_source:
        stereo_plan = _json.dumps({
            "enabled":     True,
            "mode":        stereo_source.get("stereo_mode", "2d_to_3d"),
            "format":      stereo_source.get("stereo_format", "half_sbs"),
            "divergence":  stereo_source.get("stereo_divergence", 2.0),
            "convergence": stereo_source.get("stereo_convergence", 0.5),
            "depth_model": stereo_source.get("stereo_depth_model", "Any_V2_S"),
        })

    db.add_to_queue(
        file_path=file_path,
        root_id=root_id,
        profile_id=profile_id,
        status="pending" if perm_status == "ok" else "permission_error",
        current_specs=current_specs,
        target_specs=target_specs,
        file_size_bytes=file_size,
        estimated_savings_bytes=scanner._estimate_savings(
            file_size, codec, profile["codec"], current_specs=current_specs,
            profile=profile,
        ),
        upscale_plan=upscale_plan,
        stereo_plan=stereo_plan,
    )

    # Wake the encoder if it's not already running and schedule allows
    try:
        from app.encoder import encoder_pool
        from app.scheduler import encoding_scheduler
        import threading
        if not encoder_pool.is_running and encoding_scheduler.should_encode_now():
            t = threading.Thread(target=encoder_pool.process_queue, daemon=True)
            t.start()
    except Exception:
        pass  # Non-fatal — item is queued, encoder will pick it up on next scheduler tick

    optimizarr_logger.app_logger.info(
        "Webhook: queued %s from %s", file_path, app_type
    )
    return {"ok": True, "message": f"Queued: {file_path}"}

