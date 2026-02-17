"""
Pydantic models for API request/response validation.
"""
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime


# ============================================================
# LIBRARY TYPE DEFINITIONS
# ============================================================

LIBRARY_TYPES = {
    "movie": {
        "name": "Movies",
        "description": "Feature films, documentaries",
        "icon": "🎬",
        "recommended": {
            "codec": "av1", "encoder": "svt_av1", "quality": 28,
            "preset": "6", "container": "mkv", "audio_codec": "opus"
        }
    },
    "tv_show": {
        "name": "TV Shows",
        "description": "Series, sitcoms, episodic content",
        "icon": "📺",
        "recommended": {
            "codec": "av1", "encoder": "svt_av1", "quality": 32,
            "preset": "10", "container": "mkv", "audio_codec": "aac"
        }
    },
    "anime": {
        "name": "Anime",
        "description": "Japanese animation",
        "icon": "🎌",
        "recommended": {
            "codec": "av1", "encoder": "svt_av1", "quality": 26,
            "preset": "6", "container": "mkv", "audio_codec": "opus"
        }
    },
    "home_video": {
        "name": "Home Videos",
        "description": "Personal recordings, family videos",
        "icon": "🎥",
        "recommended": {
            "codec": "h265", "encoder": "x265", "quality": 20,
            "preset": "medium", "container": "mp4", "audio_codec": "aac"
        }
    },
    "4k_content": {
        "name": "4K/UHD Content",
        "description": "Ultra HD, 4K movies/shows",
        "icon": "🖥️",
        "recommended": {
            "codec": "av1", "encoder": "svt_av1", "quality": 32,
            "preset": "8", "container": "mkv", "audio_codec": "opus"
        }
    },
    "web_content": {
        "name": "Web/YouTube Downloads",
        "description": "Downloaded web videos, streams",
        "icon": "🌐",
        "recommended": {
            "codec": "av1", "encoder": "svt_av1", "quality": 35,
            "preset": "12", "container": "mp4", "audio_codec": "aac"
        }
    },
    "archive": {
        "name": "Archive / Preservation",
        "description": "Long-term storage, near-lossless",
        "icon": "📦",
        "recommended": {
            "codec": "h265", "encoder": "x265", "quality": 18,
            "preset": "slow", "container": "mkv", "audio_codec": "passthrough"
        }
    },
    "music_video": {
        "name": "Music Videos",
        "description": "Music content, concerts",
        "icon": "🎵",
        "recommended": {
            "codec": "av1", "encoder": "svt_av1", "quality": 26,
            "preset": "6", "container": "mkv", "audio_codec": "opus"
        }
    },
    "custom": {
        "name": "Custom",
        "description": "Manual configuration",
        "icon": "⚙️",
        "recommended": None
    }
}


# ============================================================
# PROFILE MODELS
# ============================================================

class ProfileCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    resolution: Optional[str] = None
    framerate: Optional[int] = None
    codec: str = Field(..., pattern="^(av1|h265|h264|vp9)$")
    encoder: str
    quality: int = Field(..., ge=0, le=51)
    audio_codec: str = Field(..., pattern="^(aac|opus|ac3|flac|passthrough)$")
    container: str = Field(default="mkv", pattern="^(mkv|mp4|webm)$")
    audio_handling: str = Field(default="preserve_all", pattern="^(preserve_all|keep_primary|stereo_mixdown|hd_plus_aac|high_quality)$")
    subtitle_handling: str = Field(default="none", pattern="^(preserve_all|keep_english|burn_in|foreign_scan|none)$")
    enable_filters: bool = False
    chapter_markers: bool = True
    hw_accel_enabled: bool = False
    preset: Optional[str] = None
    two_pass: bool = False
    custom_args: Optional[str] = None
    is_default: bool = False


class ProfileResponse(BaseModel):
    id: int
    name: str
    resolution: Optional[str]
    framerate: Optional[int]
    codec: str
    encoder: str
    quality: int
    audio_codec: str
    container: Optional[str] = "mkv"
    audio_handling: Optional[str] = "preserve_all"
    subtitle_handling: Optional[str] = "none"
    enable_filters: Optional[bool] = False
    chapter_markers: Optional[bool] = True
    hw_accel_enabled: Optional[bool] = False
    preset: Optional[str]
    two_pass: bool
    custom_args: Optional[str]
    is_default: bool
    created_at: Optional[str]


# ============================================================
# SCAN ROOT MODELS
# ============================================================

class ScanRootCreate(BaseModel):
    path: str = Field(..., min_length=1)
    profile_id: int = Field(..., gt=0)
    library_type: str = Field(default="custom")
    enabled: bool = True
    recursive: bool = True


class ScanRootResponse(BaseModel):
    id: int
    path: str
    profile_id: int
    library_type: Optional[str] = "custom"
    enabled: bool
    recursive: bool


# ============================================================
# QUEUE MODELS
# ============================================================

class QueueItemResponse(BaseModel):
    id: int
    file_path: str
    root_id: Optional[int]
    profile_id: Optional[int]
    status: str
    priority: int
    current_specs: Optional[Dict[str, Any]]
    target_specs: Optional[Dict[str, Any]]
    file_size_bytes: int
    estimated_savings_bytes: int
    progress: float
    current_cpu_percent: float
    current_memory_mb: float
    permission_status: Optional[str]
    permission_message: Optional[str]
    paused_reason: Optional[str]
    error_message: Optional[str]
    started_at: Optional[str]
    completed_at: Optional[str]
    created_at: Optional[str]


class QueueUpdateRequest(BaseModel):
    priority: Optional[int] = Field(None, ge=0, le=100)
    status: Optional[str] = None


# ============================================================
# AUTHENTICATION MODELS
# ============================================================

class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: Dict[str, Any]


class UserResponse(BaseModel):
    id: int
    username: str
    email: Optional[str]
    is_admin: bool
    created_at: Optional[str]
    last_login: Optional[str]


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(..., min_length=1)
    new_password: str = Field(..., min_length=6)


# ============================================================
# STATISTICS MODELS
# ============================================================

class StatsResponse(BaseModel):
    total_files_processed: int
    total_space_saved_bytes: int
    total_space_saved_gb: float
    queue_pending: int
    queue_processing: int
    queue_completed: int
    queue_failed: int


# ============================================================
# GENERIC RESPONSE MODELS
# ============================================================

class MessageResponse(BaseModel):
    message: str
    success: bool = True


class ErrorResponse(BaseModel):
    detail: str
    success: bool = False


# ============================================================
# AUDIO HANDLING STRATEGIES
# ============================================================

AUDIO_STRATEGIES = {
    "preserve_all": {
        "name": "Preserve All Tracks",
        "description": "Keep every audio track from the source",
        "icon": "🔊",
        "best_for": "Movies, Archive"
    },
    "keep_primary": {
        "name": "Keep Primary Only",
        "description": "Keep only the first/default audio track",
        "icon": "🔈",
        "best_for": "TV Shows, Web Content"
    },
    "stereo_mixdown": {
        "name": "Convert to Stereo",
        "description": "Downmix all audio to stereo AAC",
        "icon": "🎧",
        "best_for": "Mobile, Web Content"
    },
    "hd_plus_aac": {
        "name": "HD Audio + AAC Fallback",
        "description": "Keep original HD audio + add AAC stereo track",
        "icon": "🎭",
        "best_for": "Home Theater, Movies"
    },
    "high_quality": {
        "name": "High Quality Audio",
        "description": "Single high-bitrate AAC track at 256kbps",
        "icon": "🎵",
        "best_for": "Music Videos"
    }
}


# ============================================================
# SUBTITLE HANDLING STRATEGIES
# ============================================================

SUBTITLE_STRATEGIES = {
    "preserve_all": {
        "name": "Preserve All Subtitles",
        "description": "Keep every subtitle track from the source",
        "icon": "💬",
        "best_for": "Movies, Archive"
    },
    "keep_english": {
        "name": "English Only",
        "description": "Keep only English subtitle tracks",
        "icon": "🇺🇸",
        "best_for": "Personal Libraries"
    },
    "burn_in": {
        "name": "Burn-in First Track",
        "description": "Burn first subtitle permanently into video",
        "icon": "🔥",
        "best_for": "Anime (hardcoded subs)"
    },
    "foreign_scan": {
        "name": "Foreign Audio Scan",
        "description": "Auto-detect and subtitle only foreign language parts",
        "icon": "🌍",
        "best_for": "Movies with mixed languages"
    },
    "none": {
        "name": "Remove All",
        "description": "Strip all subtitle tracks",
        "icon": "❌",
        "best_for": "Space savings, Web Content"
    }
}


# ============================================================
# VIDEO FILTER DEFINITIONS
# ============================================================

VIDEO_FILTERS = {
    "deinterlace": {
        "name": "Deinterlace",
        "description": "Remove interlacing from old video (DVD, TV recordings)",
        "icon": "📡",
        "auto_detect": True
    },
    "denoise": {
        "name": "Denoise",
        "description": "Reduce noise from low-light or compressed sources",
        "icon": "✨",
        "auto_detect": True
    },
    "deblock": {
        "name": "Deblock",
        "description": "Remove compression artifacts from MPEG2/MPEG4",
        "icon": "🧱",
        "auto_detect": True
    },
    "auto_crop": {
        "name": "Auto Crop",
        "description": "Remove black bars automatically",
        "icon": "✂️",
        "auto_detect": True
    }
}
