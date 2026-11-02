"""AI upscaler and stereo 3D detection/download routes."""
from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks

from app.api.models import MessageResponse
from app.api.dependencies import get_current_user, get_current_admin_user

router = APIRouter()


@router.get("/upscalers")
def get_upscalers(current_user: dict = Depends(get_current_user)):
    """Get AI upscaler info and detection results."""
    from app.upscaler import get_upscaler_info
    return get_upscaler_info()


@router.get("/upscalers/detect")
def detect_upscalers_endpoint(current_user: dict = Depends(get_current_user)):
    """Re-detect available AI upscalers."""
    from app.upscaler import detect_upscalers
    return detect_upscalers()


# Stereo 3D Endpoints
@router.get("/stereo/detect")
def detect_stereo(current_user: dict = Depends(get_current_user)):
    """Detect iw3 and ffmpeg availability for stereo 3D conversion."""
    from app.stereo import get_stereo_info
    return get_stereo_info()


@router.get("/stereo/depth-models")
def get_depth_models(current_user: dict = Depends(get_current_user)):
    """Get available depth estimation models for 2D→3D conversion."""
    from app.stereo import DEPTH_MODELS
    return DEPTH_MODELS


@router.get("/stereo/formats")
def get_stereo_formats(current_user: dict = Depends(get_current_user)):
    """Get available stereo output formats."""
    from app.stereo import STEREO_FORMATS
    return {k: {"name": v["name"], "description": v["description"]} for k, v in STEREO_FORMATS.items()}


@router.post("/upscalers/{key}/download")
def start_upscaler_download(
    key: str,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(get_current_admin_user)
):
    """Start downloading an AI upscaler binary (admin only)."""
    from app.upscaler import download_upscaler, is_installable, _spec
    if not is_installable(key):
        raise HTTPException(status_code=404, detail=f"Unknown upscaler: {key}")
    state = download_upscaler(key)
    return {"message": f"Download started for {_spec(key)['name']}", "state": state}


@router.post("/upscalers/engine", response_model=MessageResponse)
def set_upscale_engine(data: dict, current_user: dict = Depends(get_current_admin_user)):
    """Choose the upscale engine: 'auto' (Video2X when installed) or 'frames'
    (always the PNG frame pipeline)."""
    from app.database import db
    engine = (data or {}).get("engine")
    if engine not in ("auto", "frames"):
        raise HTTPException(status_code=400, detail="engine must be 'auto' or 'frames'")
    db.set_setting("upscale_engine", engine)
    return MessageResponse(message=f"Upscale engine set to {engine}")


@router.get("/upscalers/{key}/download/status")
def get_upscaler_download_status(
    key: str,
    current_user: dict = Depends(get_current_user)
):
    """Get download progress for an upscaler."""
    from app.upscaler import get_download_status, is_installable
    if not is_installable(key):
        raise HTTPException(status_code=404, detail=f"Unknown upscaler: {key}")
    return get_download_status(key)


@router.post("/upscalers/check-updates")
def check_upscaler_updates(current_user: dict = Depends(get_current_user)):
    """Check GitHub for newer versions of installed upscalers."""
    from app.upscaler import check_for_updates
    return check_for_updates()


