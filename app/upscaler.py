"""
AI Upscaler Framework for Optimizarr.
Detects installed AI upscalers and provides configuration.
Supports: Real-ESRGAN, Real-CUGAN, Anime4K, Waifu2x-NCNN-Vulkan
"""
import subprocess
import shutil
import platform
from typing import Dict, Optional, List
from pathlib import Path

from app.logger import optimizarr_logger


# Supported upscaler definitions
UPSCALERS = {
    "realesrgan": {
        "name": "Real-ESRGAN",
        "description": "Best for real-world content (photos, live action, films)",
        "icon": "🎬",
        "binary": "realesrgan-ncnn-vulkan",
        "binary_win": "realesrgan-ncnn-vulkan.exe",
        "models": ["realesrgan-x4plus", "realesrgan-x4plus-anime", "realesr-animevideov3"],
        "default_model": "realesrgan-x4plus",
        "scale_options": [2, 3, 4],
        "default_scale": 2,
        "url": "https://github.com/xinntao/Real-ESRGAN/releases",
        "best_for": "Movies, TV Shows, Home Videos"
    },
    "realcugan": {
        "name": "Real-CUGAN",
        "description": "Fast GPU upscaling, good balance of speed and quality",
        "icon": "⚡",
        "binary": "realcugan-ncnn-vulkan",
        "binary_win": "realcugan-ncnn-vulkan.exe",
        "models": ["models-se", "models-pro", "models-nose"],
        "default_model": "models-se",
        "scale_options": [2, 3, 4],
        "default_scale": 2,
        "url": "https://github.com/nihui/realcugan-ncnn-vulkan/releases",
        "best_for": "Anime, Cartoons"
    },
    "waifu2x": {
        "name": "Waifu2x NCNN Vulkan",
        "description": "Classic anime upscaler, GPU-accelerated via Vulkan",
        "icon": "🎌",
        "binary": "waifu2x-ncnn-vulkan",
        "binary_win": "waifu2x-ncnn-vulkan.exe",
        "models": ["models-cunet", "models-upconv_7_anime_style_art_rgb"],
        "default_model": "models-cunet",
        "scale_options": [1, 2],
        "default_scale": 2,
        "url": "https://github.com/nihui/waifu2x-ncnn-vulkan/releases",
        "best_for": "Anime, Illustrations"
    },
    "anime4k": {
        "name": "Anime4K",
        "description": "Real-time anime upscaling (shader-based, very fast)",
        "icon": "🎮",
        "binary": None,  # Shader-based, used via mpv/ffmpeg
        "binary_win": None,
        "models": [],
        "default_model": None,
        "scale_options": [2],
        "default_scale": 2,
        "url": "https://github.com/bloc97/Anime4K/releases",
        "best_for": "Anime (real-time playback upscaling)"
    }
}


def detect_upscalers() -> Dict:
    """Detect which AI upscalers are installed and available."""
    is_windows = platform.system() == "Windows"
    results = {
        "available": [],
        "not_found": [],
        "details": {}
    }
    
    for key, upscaler in UPSCALERS.items():
        binary = upscaler.get("binary_win" if is_windows else "binary")
        
        if not binary:
            # Shader-based (Anime4K), skip binary check
            results["not_found"].append(key)
            results["details"][key] = {
                "name": upscaler["name"],
                "installed": False,
                "note": "Shader-based, requires manual setup"
            }
            continue
        
        # Check if binary is in PATH
        found_path = shutil.which(binary)
        
        if not found_path:
            # Also check common install locations
            common_paths = _get_common_paths(binary, is_windows)
            for cp in common_paths:
                if Path(cp).exists():
                    found_path = cp
                    break
        
        if found_path:
            version = _get_upscaler_version(found_path)
            results["available"].append(key)
            results["details"][key] = {
                "name": upscaler["name"],
                "installed": True,
                "path": found_path,
                "version": version,
                "models": upscaler["models"],
                "scale_options": upscaler["scale_options"]
            }
            optimizarr_logger.app_logger.info("Found upscaler: %s at %s", upscaler["name"], found_path)
        else:
            results["not_found"].append(key)
            results["details"][key] = {
                "name": upscaler["name"],
                "installed": False,
                "download_url": upscaler["url"]
            }
    
    return results


def _get_common_paths(binary: str, is_windows: bool) -> List[str]:
    """Get common installation paths for upscaler binaries."""
    paths = []
    
    if is_windows:
        home = Path.home()
        paths.extend([
            str(home / "Optimizarr" / "upscalers" / binary),
            str(home / "AppData" / "Local" / "Optimizarr" / "upscalers" / binary),
            f"C:\\Tools\\{binary}",
            f"C:\\Program Files\\{binary.replace('.exe', '')}\\{binary}",
        ])
    else:
        paths.extend([
            f"/usr/local/bin/{binary}",
            f"/opt/optimizarr/upscalers/{binary}",
            str(Path.home() / ".local" / "bin" / binary),
            str(Path.home() / "optimizarr" / "upscalers" / binary),
        ])
    
    return paths


def _get_upscaler_version(binary_path: str) -> Optional[str]:
    """Try to get the version string from an upscaler binary."""
    try:
        result = subprocess.run(
            [binary_path, "--help"],
            capture_output=True, text=True, timeout=5
        )
        # Most ncnn-vulkan tools print version in first few lines
        output = result.stdout + result.stderr
        for line in output.split('\n')[:5]:
            if 'version' in line.lower() or 'v0.' in line or 'v2' in line:
                return line.strip()
        return "installed"
    except Exception:
        return "installed"


def get_upscaler_info() -> Dict:
    """Get full upscaler information for the UI."""
    return {
        "definitions": {k: {
            "name": v["name"],
            "description": v["description"],
            "icon": v["icon"],
            "best_for": v["best_for"],
            "models": v["models"],
            "default_model": v["default_model"],
            "scale_options": v["scale_options"],
            "default_scale": v["default_scale"],
            "download_url": v["url"]
        } for k, v in UPSCALERS.items()},
        "detection": detect_upscalers(),
        "workflow_note": "AI upscaling extracts frames, upscales each with AI, "
                         "then reassembles before HandBrake encoding. "
                         "This is VERY slow (~2-5 min/frame) and GPU-intensive."
    }
