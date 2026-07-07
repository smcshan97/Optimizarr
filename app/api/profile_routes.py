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


# NOTE: must be registered BEFORE /profiles/{profile_id} — FastAPI matches in
# declaration order, and the old placement made GET /profiles/export hit the
# int path param ("export" → 422), silently breaking profile export in the UI.
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
    # Imported at call time — app.main imports this module at startup, so a
    # module-level import here would be circular.
    from app.main import DEFAULT_PROFILES

    existing = db.get_profiles()
    existing_names = {p["name"] for p in existing}
    # Re-seeding must not steal the default flag from a profile the user
    # already chose (create_profile clears other defaults when is_default=1).
    has_default = any(p.get("is_default") for p in existing)

    created = []
    skipped = []
    for p in DEFAULT_PROFILES:
        if p["name"] in existing_names:
            skipped.append(p["name"])
        else:
            try:
                fields = dict(p)
                if has_default:
                    fields["is_default"] = False
                db.create_profile(**fields)
                created.append(p["name"])
            except Exception as e:
                skipped.append(f"{p['name']} (error: {e})")

    return {
        "message": f"Added {len(created)} default profile(s), skipped {len(skipped)} already present",
        "created": created,
        "skipped": skipped,
    }


