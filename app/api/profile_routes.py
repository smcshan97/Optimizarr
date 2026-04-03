"""Profile management routes."""
from fastapi import APIRouter, Depends, HTTPException, status
from typing import List

from app.api.models import ProfileCreate, ProfileResponse, MessageResponse
from app.api.dependencies import get_current_user, get_current_admin_user
from app.database import db

router = APIRouter()


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
        is_default=profile.is_default,
        upscale_enabled=profile.upscale_enabled,
        upscale_trigger_below=profile.upscale_trigger_below,
        upscale_target_height=profile.upscale_target_height,
        upscale_model=profile.upscale_model,
        upscale_factor=profile.upscale_factor,
        upscale_key=profile.upscale_key,
        stereo_enabled=profile.stereo_enabled,
        stereo_mode=profile.stereo_mode,
        stereo_format=profile.stereo_format,
        stereo_divergence=profile.stereo_divergence,
        stereo_convergence=profile.stereo_convergence,
        stereo_depth_model=profile.stereo_depth_model,
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


@router.post("/profiles/seed-defaults")
async def seed_default_profiles(
    current_user: dict = Depends(get_current_admin_user)
):
    """
    Add the built-in default profiles if they don't already exist.
    Safe to call multiple times — won't create duplicates.
    """
    from app.main import _seed_default_profiles

    existing_names = {p["name"] for p in db.get_profiles()}
    defaults = [
        {"name": "🎬 Movies — AV1 1080p High Quality",  "resolution": "", "framerate": None,
         "codec": "av1",  "encoder": "svt_av1",   "quality": 28, "audio_codec": "passthrough",
         "container": "mkv",  "audio_handling": "preserve_all", "subtitle_handling": "preserve_all",
         "chapter_markers": True,  "enable_filters": False, "hw_accel_enabled": False,
         "preset": "6",  "two_pass": False, "custom_args": None, "is_default": True},
        {"name": "📺 TV Shows — AV1 1080p Balanced",    "resolution": "", "framerate": None,
         "codec": "av1",  "encoder": "svt_av1",   "quality": 32, "audio_codec": "aac",
         "container": "mkv",  "audio_handling": "keep_primary",  "subtitle_handling": "keep_english",
         "chapter_markers": False, "enable_filters": False, "hw_accel_enabled": False,
         "preset": "8",  "two_pass": False, "custom_args": None, "is_default": False},
        {"name": "⚡ Fast H.265 — GPU Encode (NVENC)",  "resolution": "", "framerate": None,
         "codec": "h265", "encoder": "nvenc_h265", "quality": 28, "audio_codec": "aac",
         "container": "mkv",  "audio_handling": "preserve_all", "subtitle_handling": "none",
         "chapter_markers": True,  "enable_filters": False, "hw_accel_enabled": True,
         "preset": None,  "two_pass": False, "custom_args": None, "is_default": False},
        {"name": "📦 Archive — HEVC 10-bit Near Lossless", "resolution": "", "framerate": None,
         "codec": "h265", "encoder": "x265",       "quality": 18, "audio_codec": "passthrough",
         "container": "mkv",  "audio_handling": "preserve_all", "subtitle_handling": "preserve_all",
         "chapter_markers": True,  "enable_filters": False, "hw_accel_enabled": False,
         "preset": "slower", "two_pass": False,
         "custom_args": "--encoder-profile main10 --encoder-level 5.1", "is_default": False},
        {"name": "📱 Mobile — H.264 720p Compatible",   "resolution": "1280x720", "framerate": 30,
         "codec": "h264", "encoder": "x264",       "quality": 23, "audio_codec": "aac",
         "container": "mp4",  "audio_handling": "stereo_mixdown","subtitle_handling": "none",
         "chapter_markers": False, "enable_filters": True,  "hw_accel_enabled": False,
         "preset": "fast", "two_pass": False, "custom_args": None, "is_default": False},
    ]

    created = []
    skipped = []
    for p in defaults:
        if p["name"] in existing_names:
            skipped.append(p["name"])
        else:
            try:
                db.create_profile(**p)
                created.append(p["name"])
            except Exception as e:
                skipped.append(f"{p['name']} (error: {e})")

    return {
        "message": f"Added {len(created)} default profile(s), skipped {len(skipped)} already present",
        "created": created,
        "skipped": skipped,
    }


