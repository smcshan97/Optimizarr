"""
Server-side filesystem browser for Optimizarr.
The backend reads the filesystem and returns directory listings.
All paths are automatically normalized (quotes stripped, resolved).
"""
import os
import platform
import string
from pathlib import Path
from typing import List, Optional
from fastapi import APIRouter, Depends, Query

from app.api.dependencies import get_current_user

router = APIRouter(prefix="/filesystem", tags=["filesystem"])

VIDEO_EXTENSIONS = {
    ".mp4", ".mkv", ".avi", ".mov", ".m4v", ".wmv", ".flv", ".webm",
    ".ts", ".m2ts", ".mts", ".mpg", ".mpeg",
    ".vob", ".divx", ".3gp", ".3g2", ".ogv", ".ogg",
    ".rm", ".rmvb", ".asf", ".f4v", ".m2v",
    ".amv", ".drc", ".gifv", ".qt", ".yuv", ".svi", ".nsv", ".roq",
}


def _normalize_path(path_str: str) -> str:
    """Strip ALL quote types and whitespace from a path string."""
    if not path_str:
        return ""
    cleaned = path_str.strip()
    cleaned = cleaned.strip('"').strip("'").strip('\u201c').strip('\u201d')
    return cleaned.strip()


def _get_windows_drives() -> List[dict]:
    drives = []
    for letter in string.ascii_uppercase:
        drive = f"{letter}:\\"
        if os.path.exists(drive):
            drives.append({"name": f"{letter}:", "path": drive, "type": "drive"})
    return drives


def _human_size(size_bytes: int) -> str:
    if size_bytes == 0:
        return "0 B"
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if abs(size_bytes) < 1024.0:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.1f} PB"


def _list_directory(dir_path: str) -> dict:
    p = Path(_normalize_path(dir_path))
    if not p.exists():
        return {"error": f"Path does not exist: {dir_path}", "exists": False}
    if not p.is_dir():
        return {"error": f"Not a directory: {dir_path}", "exists": True, "is_dir": False}

    parent = str(p.parent) if p.parent != p else None
    directories, files = [], []
    video_count = total_video_size = 0

    try:
        entries = sorted(p.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower()))
        for entry in entries:
            try:
                if entry.is_dir():
                    if entry.name.startswith(".") or entry.name.startswith("$"):
                        continue
                    directories.append({"name": entry.name, "path": str(entry)})
                elif entry.is_file():
                    ext = entry.suffix.lower()
                    is_video = ext in VIDEO_EXTENSIONS
                    size = 0
                    try:
                        size = entry.stat().st_size
                    except OSError:
                        pass
                    files.append({
                        "name": entry.name, "path": str(entry),
                        "size": size, "size_human": _human_size(size),
                        "extension": ext, "is_video": is_video,
                    })
                    if is_video:
                        video_count += 1
                        total_video_size += size
            except (PermissionError, OSError):
                continue
    except PermissionError:
        return {"error": f"Permission denied: {dir_path}", "exists": True, "is_dir": True, "path": str(p)}

    return {
        "path": str(p), "parent": parent, "exists": True, "is_dir": True,
        "directories": directories, "files": files,
        "video_count": video_count,
        "total_video_size": total_video_size,
        "total_video_size_human": _human_size(total_video_size),
        "total_files": len(files), "total_directories": len(directories),
    }


@router.get("/browse")
async def browse_filesystem(
    path: Optional[str] = Query(default=None),
    current_user: dict = Depends(get_current_user),
):
    """Browse server filesystem. No path = list drives/root."""
    if not path or path.strip() == "":
        if platform.system() == "Windows":
            return {"path": "", "parent": None, "directories": _get_windows_drives(),
                    "files": [], "video_count": 0, "total_files": 0, "total_directories": 0, "is_root": True}
        return _list_directory("/")
    return _list_directory(_normalize_path(path))


@router.get("/validate")
async def validate_path(
    path: str = Query(...),
    current_user: dict = Depends(get_current_user),
):
    """Validate a path and count video files."""
    cleaned = _normalize_path(path)
    p = Path(cleaned)
    result = {"path": str(p), "is_absolute": p.is_absolute(), "exists": p.exists(),
              "is_dir": p.is_dir() if p.exists() else False, "is_valid": False,
              "video_count": 0, "total_files": 0, "message": ""}

    if not p.is_absolute():
        result["message"] = "Path must be absolute (e.g. D:\\Media\\Movies or /mnt/media)"
        return result
    if not p.exists():
        result["message"] = f"Path does not exist: {cleaned}"
        return result
    if not p.is_dir():
        result["message"] = f"Path is a file, not a directory"
        return result

    video_count = total_files = 0
    try:
        for entry in p.rglob("*"):
            if entry.is_file():
                total_files += 1
                if entry.suffix.lower() in VIDEO_EXTENSIONS:
                    video_count += 1
    except PermissionError:
        result["message"] = f"Permission denied"
        return result

    result.update({"is_valid": True, "video_count": video_count, "total_files": total_files,
                   "message": f"Found {video_count} video file(s) in {total_files} total files"})
    return result
