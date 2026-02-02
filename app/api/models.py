"""
Pydantic models for API request/response validation.
"""
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime


# Profile Models
class ProfileCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    resolution: Optional[str] = None
    framerate: Optional[int] = None
    codec: str = Field(..., pattern="^(av1|h265|h264|vp9)$")
    encoder: str
    quality: int = Field(..., ge=0, le=51)
    audio_codec: str = Field(..., pattern="^(aac|opus|ac3|passthrough)$")
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
    preset: Optional[str]
    two_pass: bool
    custom_args: Optional[str]
    is_default: bool
    created_at: Optional[str]


# Scan Root Models
class ScanRootCreate(BaseModel):
    path: str = Field(..., min_length=1)
    profile_id: int = Field(..., gt=0)
    enabled: bool = True
    recursive: bool = True


class ScanRootResponse(BaseModel):
    id: int
    path: str
    profile_id: int
    enabled: bool
    recursive: bool


# Queue Models
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


# Authentication Models
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


# Statistics Models
class StatsResponse(BaseModel):
    total_files_processed: int
    total_space_saved_bytes: int
    total_space_saved_gb: float
    queue_pending: int
    queue_processing: int
    queue_completed: int
    queue_failed: int


# Generic Response Models
class MessageResponse(BaseModel):
    message: str
    success: bool = True


class ErrorResponse(BaseModel):
    detail: str
    success: bool = False
