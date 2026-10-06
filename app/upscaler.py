"""
AI Upscaler module for Optimizarr.
Detects, downloads, and updates AI upscaler binaries.
Supports: Real-ESRGAN, Real-CUGAN, Waifu2x-NCNN-Vulkan
"""
import subprocess
import shutil
import platform
import threading
import zipfile
import tarfile
import requests
import re
import time
from typing import Dict, Optional, List, Any
from pathlib import Path
from datetime import datetime, timedelta

from app.logger import optimizarr_logger

# ---------------------------------------------------------------------------
# Upscaler definitions
# ---------------------------------------------------------------------------

UPSCALERS = {
    "realesrgan": {
        "name": "Real-ESRGAN",
        "description": "Best for real-world content (photos, live action, films)",
        "icon": "🎬",
        "binary_linux": "realesrgan-ncnn-vulkan",
        "binary_win": "realesrgan-ncnn-vulkan.exe",
        "models": ["realesrgan-x4plus", "realesrgan-x4plus-anime", "realesr-animevideov3"],
        "default_model": "realesrgan-x4plus",
        # Scales each model was trained for. realesrgan-ncnn-vulkan only
        # appends -x<scale> to the file name for animevideov3; the x4plus
        # models are 4x-only and "-s 2" just mis-sizes the output buffer.
        "model_scales": {
            "realesrgan-x4plus": [4],
            "realesrgan-x4plus-anime": [4],
            "realesr-animevideov3": [2, 3, 4],
        },
        "scale_options": [2, 3, 4],
        "default_scale": 2,
        "github_owner": "xinntao",
        "github_repo": "Real-ESRGAN",
        "asset_pattern_win": r"realesrgan-ncnn-vulkan.*(?:windows|win).*\.(zip|7z)",
        "asset_pattern_linux": r"realesrgan-ncnn-vulkan.*(?:linux|ubuntu).*\.(zip|tar\.gz)",
        "best_for": "Movies, TV Shows, Home Videos",
        "url": "https://github.com/xinntao/Real-ESRGAN/releases",
    },
    "realcugan": {
        "name": "Real-CUGAN",
        "description": "Fast GPU upscaling, good balance of speed and quality",
        "icon": "⚡",
        "binary_linux": "realcugan-ncnn-vulkan",
        "binary_win": "realcugan-ncnn-vulkan.exe",
        "models": ["models-se", "models-pro", "models-nose"],
        "default_model": "models-se",
        "model_scales": {
            "models-se": [2, 3, 4],
            "models-pro": [2, 3],
            "models-nose": [2],
        },
        "scale_options": [2, 3, 4],
        "default_scale": 2,
        "github_owner": "nihui",
        "github_repo": "realcugan-ncnn-vulkan",
        "asset_pattern_win": r"realcugan-ncnn-vulkan.*windows.*\.zip",
        "asset_pattern_linux": r"realcugan-ncnn-vulkan.*ubuntu.*\.zip",
        "best_for": "Anime, Cartoons",
        "url": "https://github.com/nihui/realcugan-ncnn-vulkan/releases",
    },
    "waifu2x": {
        "name": "Waifu2x NCNN Vulkan",
        "description": "Classic anime upscaler, GPU-accelerated via Vulkan",
        "icon": "🎌",
        "binary_linux": "waifu2x-ncnn-vulkan",
        "binary_win": "waifu2x-ncnn-vulkan.exe",
        "models": ["models-cunet", "models-upconv_7_anime_style_art_rgb"],
        "default_model": "models-cunet",
        "model_scales": {
            "models-cunet": [1, 2],
            "models-upconv_7_anime_style_art_rgb": [1, 2],
        },
        "scale_options": [1, 2],
        "default_scale": 2,
        "github_owner": "nihui",
        "github_repo": "waifu2x-ncnn-vulkan",
        "asset_pattern_win": r"waifu2x-ncnn-vulkan.*windows.*\.zip",
        "asset_pattern_linux": r"waifu2x-ncnn-vulkan.*ubuntu.*\.zip",
        "best_for": "Anime, Illustrations",
        "url": "https://github.com/nihui/waifu2x-ncnn-vulkan/releases",
    },
}

# Processing engines — installed/updated like upscalers but not selectable as
# a profile's upscaler. Video2X runs the Real-ESRGAN/Real-CUGAN models above
# as a streaming pipeline (see app.video2x).
ENGINES = {
    "video2x": {
        "name": "Video2X (streaming engine)",
        "description": ("Runs Real-ESRGAN / Real-CUGAN on the GPU straight from the "
                        "video — no frame extraction, no hundreds of GB of temp PNGs. "
                        "Used automatically when installed."),
        "icon": "🚀",
        "binary_linux": "Video2X-x86_64.AppImage",
        "binary_win": "video2x.exe",
        "github_owner": "k4yt3x",
        "github_repo": "video2x",
        "asset_pattern_win": r"^video2x-windows-amd64\.zip$",
        "asset_pattern_linux": r"^Video2X-x86_64\.AppImage$",
        "best_for": "Faster upscaling with little temp disk",
        "url": "https://github.com/k4yt3x/video2x/releases",
    },
}


def _spec(key: str) -> Dict:
    return UPSCALERS.get(key) or ENGINES[key]


def is_installable(key: str) -> bool:
    return key in UPSCALERS or key in ENGINES


# ---------------------------------------------------------------------------
# In-memory download state (key → status dict)
# ---------------------------------------------------------------------------

_download_state: Dict[str, Dict] = {}
_update_cache: Dict[str, Any] = {}   # cached GitHub release info
_CACHE_TTL = 86400                   # 24 h update check interval


def _upscaler_install_dir() -> Path:
    """Return the directory where Optimizarr stores upscaler binaries."""
    is_win = platform.system() == "Windows"
    if is_win:
        base = Path.home() / "AppData" / "Local" / "Optimizarr" / "upscalers"
    else:
        base = Path.home() / ".local" / "share" / "optimizarr" / "upscalers"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _binary_name(key: str) -> str:
    upscaler = _spec(key)
    is_win = platform.system() == "Windows"
    return upscaler["binary_win"] if is_win else upscaler["binary_linux"]


def _find_binary(key: str) -> Optional[str]:
    """Return path to installed binary or None."""
    binary = _binary_name(key)
    # 1. System PATH
    found = shutil.which(binary)
    if found:
        return found
    # 2. Optimizarr install dir (legacy flat layout)
    local = _upscaler_install_dir() / binary
    if local.exists():
        return str(local)
    # 3. Per-upscaler subdirectory — downloads extract the full release tree
    #    here so the binary keeps its required models/ directory beside it
    sub = _upscaler_install_dir() / key
    if sub.exists():
        for p in sub.rglob(binary):
            return str(p)
    return None


def _get_binary_version(binary_path: str) -> str:
    try:
        result = subprocess.run(
            [binary_path, "--help"],
            capture_output=True, text=True, timeout=5,
            encoding='utf-8', errors='replace'
        )
        output = result.stdout + result.stderr
        for line in output.split("\n")[:8]:
            if re.search(r'v?\d+\.\d+', line, re.IGNORECASE):
                return line.strip()
        return "installed"
    except Exception:
        return "installed"


# ---------------------------------------------------------------------------
# GitHub release helpers
# ---------------------------------------------------------------------------

def _fetch_latest_release(owner: str, repo: str) -> Optional[Dict]:
    """
    Fetch latest release with binary assets from GitHub API.
    Falls back to walking the releases list if the 'latest' tag has no assets
    (some repos tag source-only releases as 'latest').
    """
    cache_key = f"{owner}/{repo}"
    cached = _update_cache.get(cache_key)
    if cached and "_error" not in cached:
        age = time.time() - cached.get("_fetched_at", 0)
        if age < _CACHE_TTL:
            return cached

    headers = {"Accept": "application/vnd.github.v3+json"}

    def _get(url):
        try:
            resp = requests.get(url, headers=headers, timeout=15)
            return resp
        except requests.exceptions.Timeout:
            return None

    # 1. Try /releases/latest first
    resp = _get(f"https://api.github.com/repos/{owner}/{repo}/releases/latest")
    if resp is None:
        msg = "GitHub API request timed out. Check your internet connection."
        return {"_error": "timeout", "_message": msg}

    if resp.status_code == 403:
        remaining = resp.headers.get("X-RateLimit-Remaining", "?")
        reset_ts = int(resp.headers.get("X-RateLimit-Reset", 0))
        reset_str = ""
        if reset_ts:
            import datetime
            reset_str = f" Resets at {datetime.datetime.fromtimestamp(reset_ts).strftime('%H:%M:%S')}."
        msg = f"GitHub API rate limit exceeded (remaining: {remaining}).{reset_str} Try again later."
        return {"_error": "rate_limited", "_message": msg}

    if resp.status_code == 200:
        data = resp.json()
        # If this release has assets, use it
        if data.get("assets"):
            data["_fetched_at"] = time.time()
            _update_cache[cache_key] = data
            return data
        # No assets on latest — fall through to list search below

    # 2. Walk /releases list to find most recent release WITH binary assets
    releases_resp = _get(f"https://api.github.com/repos/{owner}/{repo}/releases?per_page=10")
    if releases_resp and releases_resp.status_code == 200:
        for release in releases_resp.json():
            if release.get("assets"):
                release["_fetched_at"] = time.time()
                _update_cache[cache_key] = release
                optimizarr_logger.app_logger.info(f"  ℹ Using release {release.get('tag_name')} (latest tag had no assets)")
                return release

    # 3. Nothing found
    if resp.status_code == 404:
        msg = f"GitHub repo {owner}/{repo} not found or has no releases."
    elif resp.status_code == 200:
        msg = f"No releases with binary assets found for {owner}/{repo}."
    else:
        msg = f"GitHub API returned HTTP {resp.status_code}"
    optimizarr_logger.app_logger.warning(f"⚠ {msg}")
    return {"_error": "not_found", "_message": msg}


def _find_asset(assets: List[Dict], pattern: str) -> Optional[Dict]:
    """Find the best matching release asset for the current OS."""
    try:
        regex = re.compile(pattern, re.IGNORECASE)
    except re.error:
        # Fallback to plain string match if pattern is invalid
        regex = re.compile(re.escape(pattern), re.IGNORECASE)
    for asset in assets:
        if regex.search(asset.get("name", "")):
            return asset
    return None


# ---------------------------------------------------------------------------
# Download functionality
# ---------------------------------------------------------------------------

def download_upscaler(key: str) -> Dict:
    """
    Start downloading the upscaler binary in a background thread.
    Returns immediately with the initial state dict.
    """
    if not is_installable(key):
        return {"error": f"Unknown upscaler: {key}"}

    # If already downloading, return current state
    state = _download_state.get(key, {})
    if state.get("status") == "downloading":
        return state

    _download_state[key] = {
        "status": "starting",
        "progress": 0,
        "message": "Fetching release info from GitHub…",
        "error": None,
        "version": None,
    }

    t = threading.Thread(target=_download_worker, args=(key,), daemon=True)
    t.start()
    return _download_state[key]


def _download_worker(key: str):
    upscaler = _spec(key)
    state = _download_state[key]
    is_win = platform.system() == "Windows"
    install_dir = _upscaler_install_dir()

    try:
        # 1. Get latest release
        state["status"] = "downloading"
        state["message"] = "Fetching latest release info…"
        release = _fetch_latest_release(upscaler["github_owner"], upscaler["github_repo"])
        if not release:
            raise RuntimeError("Could not fetch release info from GitHub")
        # Surface API errors with friendly messages
        if "_error" in release:
            raise RuntimeError(release.get("_message", "GitHub API error"))

        tag = release.get("tag_name", "unknown")
        assets = release.get("assets", [])
        pattern = upscaler["asset_pattern_win"] if is_win else upscaler["asset_pattern_linux"]
        asset = _find_asset(assets, pattern)

        if not asset:
            # Fallback 1: any zip/tar.gz for this OS hint
            if is_win:
                asset = next((a for a in assets if a["name"].lower().endswith(".zip")), None)
            else:
                asset = next((a for a in assets if a["name"].lower().endswith((".tar.gz", ".zip"))), None)
        if not asset:
            asset_names = [a["name"] for a in assets]
            raise RuntimeError(
                f"No suitable release asset found for {upscaler['name']} on this OS. "
                f"Available assets: {asset_names}"
            )

        download_url = asset["browser_download_url"]
        asset_name = asset["name"]
        total_size = asset.get("size", 0)

        state["message"] = f"Downloading {asset_name} ({_format_size(total_size)})…"

        # 2. Download the asset
        archive_path = install_dir / asset_name
        downloaded = 0
        with requests.get(download_url, stream=True, timeout=120) as r:
            r.raise_for_status()
            with open(archive_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=65536):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total_size:
                            state["progress"] = min(90, int(downloaded / total_size * 90))

        state["progress"] = 90
        state["message"] = "Extracting archive…"

        # 3. Extract the FULL archive tree. The ncnn-vulkan upscalers need
        #    their models/ directory (and bundled DLLs on Windows) next to
        #    the executable — extracting the bare binary produced an install
        #    that immediately failed with "models not found".
        binary_name = _binary_name(key)
        extract_dir = install_dir / key
        if asset_name.endswith(".AppImage"):
            # Single self-contained executable — nothing to extract
            extract_dir.mkdir(parents=True, exist_ok=True)
            import shutil as _sh
            _sh.move(str(archive_path), str(extract_dir / binary_name))
        elif asset_name.endswith(".zip"):
            with zipfile.ZipFile(archive_path, "r") as zf:
                zf.extractall(extract_dir)
        elif asset_name.endswith((".tar.gz", ".tgz")):
            with tarfile.open(archive_path, "r:gz") as tf:
                tf.extractall(extract_dir)
        else:
            raise RuntimeError(f"Unsupported archive format: {asset_name}")

        # Remove a stale flat-layout binary so _find_binary can't return an
        # old version from the install-dir root over the fresh tree.
        legacy = install_dir / binary_name
        if legacy.exists():
            try:
                legacy.unlink()
            except OSError:
                pass

        binary_path = next(extract_dir.rglob(binary_name), None)
        if binary_path is None:
            raise RuntimeError(
                f"Archive extracted but '{binary_name}' was not found inside it"
            )

        # 4. Make binary executable on Linux/macOS
        if not is_win:
            binary_path.chmod(0o755)

        # 5. Clean up archive
        try:
            if archive_path.exists():
                archive_path.unlink()
        except Exception:
            pass

        state["status"] = "installed"
        state["progress"] = 100
        state["version"] = tag
        state["message"] = f"✓ {upscaler['name']} {tag} installed to {install_dir}"
        optimizarr_logger.app_logger.info("Upscaler installed: %s %s", key, tag)

    except Exception as e:
        state["status"] = "error"
        state["error"] = str(e)
        state["message"] = f"Download failed: {e}"
        optimizarr_logger.app_logger.error("Upscaler download failed: %s — %s", key, e)


def _format_size(size_bytes: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} GB"


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

def detect_upscalers() -> Dict:
    """Detect which AI upscalers are installed."""
    results: Dict = {"available": [], "not_found": [], "details": {}}
    for key, upscaler in UPSCALERS.items():
        found_path = _find_binary(key)
        if found_path:
            version = _get_binary_version(found_path)
            results["available"].append(key)
            results["details"][key] = {
                "name": upscaler["name"],
                "installed": True,
                "path": found_path,
                "version": version,
                "models": upscaler["models"],
                "scale_options": upscaler["scale_options"],
                "download_state": _download_state.get(key),
            }
        else:
            results["not_found"].append(key)
            results["details"][key] = {
                "name": upscaler["name"],
                "installed": False,
                "download_url": upscaler["url"],
                "download_state": _download_state.get(key),
            }
    results["engines"] = {}
    for key, engine in ENGINES.items():
        found_path = _find_binary(key)
        results["engines"][key] = {
            "name": engine["name"],
            "installed": bool(found_path),
            "path": found_path,
            "version": _get_binary_version(found_path) if found_path else None,
            "download_url": engine["url"],
            "download_state": _download_state.get(key),
        }
    return results


def check_for_updates() -> Dict:
    """
    Check GitHub for newer versions of all detected upscalers.
    Returns a dict of key → {current, latest, update_available}.
    """
    results = {}
    for key, upscaler in {**UPSCALERS, **ENGINES}.items():
        found = _find_binary(key)
        if not found:
            continue
        release = _fetch_latest_release(upscaler["github_owner"], upscaler["github_repo"])
        if not release:
            continue
        latest_tag = release.get("tag_name", "")
        current_ver = _get_binary_version(found)
        results[key] = {
            "name": upscaler["name"],
            "current_version": current_ver,
            "latest_version": latest_tag,
            "update_available": latest_tag and latest_tag not in current_ver,
            "release_url": release.get("html_url", upscaler["url"]),
        }
    return results


def get_download_status(key: str) -> Dict:
    """Return current download state for a specific upscaler."""
    return _download_state.get(key, {"status": "idle", "progress": 0})


def get_upscaler_info() -> Dict:
    """Get full upscaler information for the UI."""
    detection = detect_upscalers()
    try:
        from app.database import db as _db
        engine_pref = _db.get_setting("upscale_engine", "auto")
    except Exception:
        engine_pref = "auto"
    return {
        "engine_pref": engine_pref,
        "engines": {k: {
            "name": v["name"],
            "description": v["description"],
            "icon": v["icon"],
            "best_for": v["best_for"],
            "download_url": v["url"],
            **detection["engines"].get(k, {}),
        } for k, v in ENGINES.items()},
        "definitions": {k: {
            "name": v["name"],
            "description": v["description"],
            "icon": v["icon"],
            "best_for": v["best_for"],
            "models": v["models"],
            "default_model": v["default_model"],
            "scale_options": v["scale_options"],
            "default_scale": v["default_scale"],
            "download_url": v["url"],
        } for k, v in UPSCALERS.items()},
        "detection": detection,
        "workflow_note": (
            "AI upscaling runs before HandBrake encoding and is slow and GPU-intensive. "
            "With Video2X installed, Real-ESRGAN/Real-CUGAN stream straight from the "
            "video; otherwise every frame is extracted to temp PNGs first."
        ),
    }


# ---------------------------------------------------------------------------
# Background update scheduler (called from main.py startup)
# ---------------------------------------------------------------------------

def start_update_checker():
    """Start a background thread that checks for upscaler updates every 24 h."""
    def _loop():
        while True:
            time.sleep(_CACHE_TTL)  # sleep first, then check
            try:
                updates = check_for_updates()
                for key, info in updates.items():
                    if info.get("update_available"):
                        optimizarr_logger.app_logger.info(
                            "Upscaler update available: %s → %s",
                            key, info["latest_version"]
                        )
            except Exception:
                pass

    t = threading.Thread(target=_loop, daemon=True, name="upscaler-update-checker")
    t.start()


# ---------------------------------------------------------------------------
# Upscale pipeline — called by encoder.py before HandBrake
# ---------------------------------------------------------------------------

def _check_disk_space(path: str, required_bytes: int) -> bool:
    """Return True if the filesystem holding *path* has at least required_bytes free."""
    import shutil as _shutil
    try:
        free = _shutil.disk_usage(path).free
        return free >= required_bytes
    except Exception:
        return True  # if we can't check, proceed and let ffmpeg fail naturally


def model_scale_for(upscaler_key: str, model: str, factor: int) -> int:
    """Scale to run ``model`` at so the result can reach ``factor``.

    The requested factor when the model supports it; otherwise the smallest
    supported scale above it (the pipeline then downscales to the target),
    or the largest one available.
    """
    scales = (UPSCALERS.get(upscaler_key, {}).get("model_scales", {})
              .get(model))
    if not scales or factor in scales:
        return factor
    above = [s for s in scales if s > factor]
    return min(above) if above else max(scales)


# ffprobe color_space -> swscale colour-matrix name
_SWS_MATRIX = {
    "bt709": "bt709", "smpte170m": "smpte170m", "bt470bg": "bt470",
    "bt601": "bt601", "smpte240m": "smpte240m", "fcc": "fcc",
    "bt2020nc": "bt2020", "bt2020c": "bt2020",
}


def _color_matrix(info: Dict) -> str:
    """swscale matrix for the source: its tag, else the HD/SD convention.

    Untagged sources made ffmpeg assume BT.601 for the YUV<->RGB round trip
    through PNG, shifting colours on HD content.
    """
    return _SWS_MATRIX.get(info.get("color_space") or "",
                           "bt709" if (info.get("height") or 0) >= 720 else "smpte170m")


def _fps_value(raw: str, default: float = 0.0) -> float:
    try:
        num, den = str(raw).split("/")
        return float(num) / float(den) if float(den) else default
    except Exception:
        try:
            return float(raw)
        except Exception:
            return default


def _get_video_info(input_path: str) -> Dict:
    """Use ffprobe to get width, height, fps, duration, colour info of a video.

    ``fps`` is the rate frames are extracted/reassembled at: the nominal
    r_frame_rate for constant-frame-rate sources, the average rate for
    variable-frame-rate ones (``vfr``).
    """
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "quiet", "-print_format", "json",
                "-show_streams", "-show_format", "-select_streams", "v:0", input_path,
            ],
            capture_output=True, text=True, timeout=30,
            encoding='utf-8', errors='replace',
        )
        import json as _json
        data = _json.loads(result.stdout)
        stream = data.get("streams", [{}])[0]
        width  = int(stream.get("width",  0))
        height = int(stream.get("height", 0))
        # fps: "24000/1001" or "24" or "30/1"
        fps_raw = stream.get("r_frame_rate", "24/1")
        try:
            num, den = fps_raw.split("/")
            fps = float(num) / float(den)
        except Exception:
            fps = 24.0
        duration = float(stream.get("duration", 0) or 0)
        if not duration:
            duration = float((data.get("format") or {}).get("duration", 0) or 0)
        avg = _fps_value(stream.get("avg_frame_rate", ""), 0.0)
        vfr = bool(avg) and abs(avg - fps) / max(fps, 1e-6) > 0.01
        if vfr:
            fps = avg
        from app.scanner import video_color_info
        info = {"width": width, "height": height, "fps": fps, "duration": duration,
                "vfr": vfr}
        info.update(video_color_info(stream))
        return info
    except Exception as e:
        optimizarr_logger.app_logger.warning("ffprobe failed: %s", e)
        return {"width": 0, "height": 0, "fps": 24.0, "duration": 0, "vfr": False}


def _color_setparams(info: Dict, matrix: str) -> str:
    """``setparams`` filter that tags the reassembled video with the source's
    colour description, so HandBrake doesn't have to guess (and mis-guess).

    Done in the filter graph because output flags (-color_trc etc.) are
    overridden by frame properties: left untagged, the PNG frames' sRGB
    transfer (iec61966-2-1) leaked into the video.
    """
    def known(v):
        return v and v not in ("unknown", "reserved", "unspecified")

    # Defaults for untagged sources, matching the matrix chosen for them.
    tag_space, default_prim, default_trc = {
        "bt709": ("bt709", "bt709", "bt709"),
        "bt470": ("bt470bg", "bt470bg", "smpte170m"),
        "bt2020": ("bt2020nc", "bt2020", "bt2020-10"),
    }.get(matrix, (matrix, "smpte170m", "smpte170m"))
    space = info["color_space"] if known(info.get("color_space")) else tag_space
    prim = info["color_primaries"] if known(info.get("color_primaries")) else default_prim
    trc = info["color_transfer"] if known(info.get("color_transfer")) else default_trc
    return f"setparams=colorspace={space}:color_primaries={prim}:color_trc={trc}:range=tv"


def run_upscale_pipeline(
    input_path: str,
    upscaler_key: str,
    model: str,
    factor: int,
    progress_callback=None,
    register_process=None,
    result_info: Optional[Dict] = None,
) -> Optional[str]:
    """
    Run the full AI upscale pipeline on *input_path*.

    Stages:
      1. Probe source video (ffprobe)
      2. Disk-space guard
      3. Extract frames (ffmpeg → PNG)
      4. Upscale frames (upscaler binary, batch mode)
      5. Reassemble: upscaled frames + original audio → temp MKV
      6. Return path to the temp MKV (caller must delete it)

    ``register_process`` (optional) is called with each subprocess Popen as it
    starts and with None when it ends, so the caller can kill the currently
    active process on stop/shutdown — without it, an in-flight upscaler
    outlives the server as a GPU-burning orphan.

    Returns the path to the upscaled video file on success, or None on failure.

    Progress callback receives ``(pct, eta_seconds)`` where pct is 0–100
    across the whole pipeline and eta_seconds is the estimated remaining
    upscale time (None while it can't yet be measured):
      0-10%  : extraction
      10-90% : upscaling (frames finished / total, with a measured ETA)
      90-100%: reassembly
    """
    import tempfile
    import shutil as _shutil
    import json as _json

    if upscaler_key not in UPSCALERS:
        optimizarr_logger.app_logger.error("Unknown upscaler key: %s", upscaler_key)
        return None

    binary_path = _find_binary(upscaler_key)
    if not binary_path:
        optimizarr_logger.app_logger.error(
            "Upscaler binary not found for '%s'. "
            "Go to Settings → AI Upscalers and click Download.", upscaler_key
        )
        return None

    def _progress(pct: float, eta_seconds: Optional[float] = None):
        if progress_callback:
            try:
                progress_callback(min(100.0, max(0.0, pct)), eta_seconds)
            except Exception:
                pass

    def _register(proc):
        if register_process:
            try:
                register_process(proc)
            except Exception:
                pass

    def _run_ffmpeg(cmd, timeout):
        """Run an ffmpeg step as a registered (killable) subprocess.

        Returns (returncode, stderr_text)."""
        proc = subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            text=True, encoding='utf-8', errors='replace',
        )
        _register(proc)
        try:
            _, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            _, err = proc.communicate()
        finally:
            _register(None)
        return proc.returncode, err or ''

    input_p = Path(input_path)
    if not input_p.exists():
        optimizarr_logger.app_logger.error("Upscale input not found: %s", input_path)
        return None

    # ── Stage 1: probe ─────────────────────────────────────────────────────
    info = _get_video_info(input_path)
    src_w, src_h, fps, duration = info["width"], info["height"], info["fps"], info["duration"]
    fps_str = f"{fps:.6g}"
    if src_w == 0 or src_h == 0:
        optimizarr_logger.app_logger.error("Could not determine video dimensions: %s", input_path)
        return None

    # Frames pass through 8-bit RGB PNGs, which would flatten HDR to a
    # washed-out SDR picture — refuse; the caller encodes the original.
    if info.get("hdr"):
        optimizarr_logger.app_logger.warning(
            "Skipping upscale of HDR source %s — the frame pipeline is 8-bit SDR",
            input_p.name)
        return None

    out_w = src_w * factor
    out_h = src_h * factor
    model_scale = model_scale_for(upscaler_key, model, factor)
    if model_scale != factor:
        optimizarr_logger.app_logger.info(
            "Model %s runs at x%d; downscaling its output to x%d",
            model, model_scale, factor)
    if info.get("vfr"):
        optimizarr_logger.app_logger.info(
            "Variable frame rate source — extracting at a constant %.3f fps "
            "so audio stays in sync", fps)
    matrix = _color_matrix(info)

    # Estimate frame count and disk requirement
    # PNG at the model's output size: rough estimate ~3 bytes/pixel (pre-compression, but
    # PNG tends to compress well; we use a conservative 1.5 bytes/pixel for estimate)
    frame_count_approx = int(fps * duration) if duration > 0 else 2000
    bytes_per_frame_approx = int(src_w * model_scale * src_h * model_scale * 1.5)
    # Source + output frames + reassembly buffer
    required_bytes = (frame_count_approx * bytes_per_frame_approx * 2) + (500 * 1024 * 1024)

    _progress(2)
    optimizarr_logger.app_logger.info(
        "Upscale pipeline: %s — %dx%d → %dx%d (×%d, ~%d frames, ~%s needed)",
        input_p.name, src_w, src_h, out_w, out_h, factor,
        frame_count_approx, _format_size(required_bytes),
    )

    # Streaming engine? Needs only its compressed output on disk (~0.08
    # bytes/pixel at CRF 12), not two full sets of PNG frames.
    v2x_binary = None
    from app.video2x import video2x_model
    if video2x_model(upscaler_key, model):
        try:
            from app.database import db as _db
            engine_pref = _db.get_setting("upscale_engine", "auto")
        except Exception:
            engine_pref = "auto"
        if engine_pref != "frames":
            v2x_binary = _find_binary("video2x")
    if v2x_binary:
        required_bytes = int(frame_count_approx * src_w * model_scale
                             * src_h * model_scale * 0.08) + (500 * 1024 * 1024)

    # ── Stage 2: disk space guard ───────────────────────────────────────────
    tmp_base = Path(tempfile.gettempdir())
    if not _check_disk_space(str(tmp_base), required_bytes):
        free = _shutil.disk_usage(str(tmp_base)).free
        optimizarr_logger.app_logger.error(
            "Upscale aborted: insufficient disk space. "
            "Need %s, have %s free in %s",
            _format_size(required_bytes), _format_size(free), tmp_base,
        )
        return None

    # Work directory — DETERMINISTIC per (input, upscaler, model, factor) so
    # an interrupted job RESUMES instead of redoing hours/days of GPU work.
    # (The old time.time() suffix meant every restart started from zero —
    # one server restart threw away 25 hours of upscaling.)
    import hashlib
    job_key = hashlib.md5(
        f"{input_path}|{upscaler_key}|{model}|{factor}|x{model_scale}|cfr".encode('utf-8', 'replace')
    ).hexdigest()[:12]
    safe_stem = re.sub(r'[^\w.-]+', '_', input_p.stem)[:40]
    work_dir = tmp_base / f"optimizarr_upscale_{safe_stem}_{job_key}"
    frames_in  = work_dir / "frames_in"
    frames_out = work_dir / "frames_out"
    extract_marker = work_dir / "extract_done"
    frames_in.mkdir(parents=True, exist_ok=True)
    frames_out.mkdir(parents=True, exist_ok=True)
    output_path = work_dir / f"{input_p.stem}_upscaled.mkv"
    pipeline_succeeded = False

    if v2x_binary:
        from app.video2x import run_video2x
        optimizarr_logger.app_logger.info(
            "Upscaling %s with Video2X (%s x%d, streaming)…",
            input_p.name, model, model_scale)
        v2x_out = work_dir / f"{input_p.stem}_upscaled_v2x.mkv"
        ok = run_video2x(
            v2x_binary, input_path, str(v2x_out), upscaler_key, model, model_scale,
            progress=lambda pct, eta: _progress(2 + pct * 0.97, eta),
            register=_register,
        )
        if ok:
            # Video2X can't resize after the model; when the model's native
            # scale overshoots the requested factor, HandBrake scales down.
            if result_info is not None and model_scale != factor:
                result_info["target_size"] = (out_w, out_h)
            _progress(100)
            return str(v2x_out)
        try:
            v2x_out.unlink()
        except OSError:
            pass
        optimizarr_logger.app_logger.warning(
            "Video2X failed — falling back to the frame-extraction pipeline")

    try:
        # ── Stage 3: extract frames (skipped when a completed extraction
        #    from an interrupted run is already on disk) ────────────────────
        total_frames = 0
        if extract_marker.exists():
            try:
                total_frames = int(extract_marker.read_text().strip() or 0)
            except (ValueError, OSError):
                total_frames = 0

        if total_frames > 0:
            optimizarr_logger.app_logger.info(
                "Resuming upscale of %s — %d frames already extracted",
                input_p.name, total_frames)
        else:
            # No completion marker: any frames present are from a partial
            # extraction and can't be trusted — start clean.
            _shutil.rmtree(frames_in, ignore_errors=True)
            frames_in.mkdir(parents=True, exist_ok=True)
            _progress(3)
            optimizarr_logger.app_logger.info("Extracting frames from %s…", input_p.name)
            extract_cmd = [
                "ffmpeg", "-i", input_path,
                "-map", "0:v:0",
                # Constant frame rate: the frames are reassembled at a single
                # rate, so a VFR source extracted frame-for-frame (the old
                # -vsync 0) drifted out of sync with its audio. For CFR
                # sources this is a no-op.
                "-fps_mode", "cfr", "-r", fps_str,
                "-vf", f"scale=in_color_matrix={matrix}:in_range=auto,format=rgb24",
                str(frames_in / "%08d.png"),
                "-y",
            ]
            rc, err = _run_ffmpeg(extract_cmd, timeout=3600)
            if rc != 0:
                optimizarr_logger.app_logger.error(
                    "ffmpeg frame extraction failed: %s", err[-500:]
                )
                return None

            actual_frames = sorted(frames_in.glob("*.png"))
            if not actual_frames:
                optimizarr_logger.app_logger.error("No frames extracted from %s", input_path)
                return None

            total_frames = len(actual_frames)
            extract_marker.write_text(str(total_frames))
            optimizarr_logger.app_logger.info("Extracted %d frames", total_frames)
        _progress(10)

        # ── Stage 4: upscale frames ─────────────────────────────────────────
        # Resume support: frames already upscaled by an interrupted run are
        # removed from the input set so the binary only processes what's left.
        existing_out = list(frames_out.glob("*.png"))
        if existing_out:
            # The newest output may be a partial write from the kill — redo it
            newest = max(existing_out, key=lambda p: p.stat().st_mtime)
            try:
                newest.unlink()
                existing_out.remove(newest)
            except OSError:
                pass
            for out_frame in existing_out:
                stale_in = frames_in / out_frame.name
                if stale_in.exists():
                    try:
                        stale_in.unlink()
                    except OSError:
                        pass
            if existing_out:
                optimizarr_logger.app_logger.info(
                    "Resuming upscale: %d/%d frames already done",
                    len(existing_out), total_frames)

        upscale_cmd = [
            binary_path,
            "-i", str(frames_in),
            "-o", str(frames_out),
            "-n", model,
            "-s", str(model_scale),
            "-f", "png",
            # load:proc:save threads. Default 1:2:2 starves the GPU on small
            # video frames — single-threaded PNG decode can't feed it. 4:4:4
            # is the upstream recommendation for many small images.
            "-j", "4:4:4",
        ]
        optimizarr_logger.app_logger.info(
            "Upscaling %d frames with %s (model=%s ×%d)…",
            total_frames - len(existing_out), upscaler_key, model, factor,
        )

        frames_remaining = sum(1 for _ in frames_in.glob("*.png"))
        if frames_remaining > 0:
            upscale_proc = subprocess.Popen(
                upscale_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                encoding='utf-8',
                errors='replace',
            )
            _register(upscale_proc)

            # Progress comes from counting finished frames in frames_out, not
            # from parsing the binary's output: the ncnn-vulkan tools print
            # per-image tile percentages ("25.00%"), which say nothing about
            # batch progress — the old "N/total" regex never matched, so the
            # UI froze for the whole (possibly day-long) upscale.
            stop_poll = threading.Event()

            # Baseline for the ETA: frames already on disk from a resumed run
            # cost this run nothing, so rate must be measured from the frames
            # produced SINCE the poller started, not from zero.
            poll_t0 = time.monotonic()
            poll_done0 = len(existing_out)

            def _poll_frames():
                while not stop_poll.wait(5):
                    try:
                        done = sum(1 for _ in frames_out.glob("*.png"))
                    except OSError:
                        continue
                    if total_frames <= 0:
                        continue
                    elapsed = time.monotonic() - poll_t0
                    produced = done - poll_done0
                    eta = None
                    if produced > 0 and elapsed > 0:
                        rate = produced / elapsed          # frames per second
                        remaining = max(0, total_frames - done)
                        eta = remaining / rate
                    _progress(10 + (done / total_frames) * 80, eta)

            poller = threading.Thread(target=_poll_frames, daemon=True,
                                      name="upscale-progress")
            poller.start()

            # Drain stdout and keep a tail for diagnostics — a crash with no
            # captured output ("exited with code 3221226505") is undebuggable.
            last_lines: List[str] = []
            try:
                for line in upscale_proc.stdout:
                    line = line.strip()
                    if line:
                        last_lines.append(line)
                        if len(last_lines) > 20:
                            last_lines.pop(0)
            finally:
                stop_poll.set()

            upscale_proc.wait()
            _register(None)
            if upscale_proc.returncode != 0:
                tail = ' | '.join(last_lines[-8:]) if last_lines else '(no output captured)'
                hint = ''
                # 0xC0000409 = Windows fail-fast abort: broken install (missing
                # models/ beside the exe) or a GPU/Vulkan driver problem.
                if upscale_proc.returncode in (3221226505, -1073740791):
                    hint = (" — the upscaler crashed at startup (0xC0000409); this "
                            "usually means a broken install (missing models/) or a "
                            "GPU/Vulkan driver issue. Try re-downloading it in "
                            "Settings → AI Upscalers.")
                optimizarr_logger.app_logger.error(
                    "Upscaler exited with code %d%s Output tail: %s",
                    upscale_proc.returncode, hint, tail
                )
                return None
        else:
            optimizarr_logger.app_logger.info(
                "All %d frames already upscaled — skipping to reassembly",
                total_frames)

        # Strict completeness check: ffmpeg's %08d input stops at the first
        # gap in the sequence, so a missing frame would silently truncate the
        # reassembled video rather than fail.
        upscaled_count = sum(1 for _ in frames_out.glob("*.png"))
        if upscaled_count < total_frames:
            optimizarr_logger.app_logger.error(
                "Upscaler produced %d of %d frames — refusing to reassemble a "
                "truncated video", upscaled_count, total_frames)
            return None

        _progress(90)

        # ── Stage 5: reassemble video with original audio ───────────────────
        optimizarr_logger.app_logger.info("Reassembling video with original audio…")
        reassemble_cmd = [
            "ffmpeg",
            "-framerate", fps_str,
            "-i", str(frames_out / "%08d.png"),
            "-i", input_path,            # original (for audio streams)
            "-map", "0:v:0",             # upscaled video
            "-map", "1:a?",              # all audio from original (? = optional)
            "-map", "1:s?",              # subtitle streams (? = optional)
            # Back to YUV with the SAME matrix used on the way in (and down to
            # the target size when the model ran at a larger native scale)
            "-vf", ",".join(
                ([f"scale={out_w}:{out_h}:flags=lanczos"] if model_scale != factor else [])
                + [f"scale=out_color_matrix={matrix}:out_range=tv", "format=yuv420p",
                   _color_setparams(info, matrix)]),
            "-c:v", "ffv1",              # lossless intermediate — HandBrake re-encodes
            "-c:a", "copy",
            "-c:s", "copy",
            "-shortest",
            str(output_path),
            "-y",
        ]
        rc, err = _run_ffmpeg(reassemble_cmd, timeout=3600)
        if rc != 0:
            optimizarr_logger.app_logger.error(
                "ffmpeg reassembly failed: %s", err[-500:]
            )
            return None

        if not output_path.exists() or output_path.stat().st_size == 0:
            optimizarr_logger.app_logger.error("Reassembled file is missing or empty")
            return None

        _progress(100)
        optimizarr_logger.app_logger.info(
            "Upscale pipeline complete → %s (%s)",
            output_path.name, _format_size(output_path.stat().st_size)
        )
        pipeline_succeeded = True
        return str(output_path)

    except Exception as e:
        optimizarr_logger.app_logger.error("Upscale pipeline error: %s", e)
        return None

    finally:
        # On SUCCESS, free the (potentially tens of GB of) frame PNGs right
        # away — only the reassembled MKV is needed for the encode. On
        # failure or kill the frames MUST survive: they're the resume
        # checkpoint the next attempt picks up from.
        if pipeline_succeeded:
            try:
                _shutil.rmtree(frames_in, ignore_errors=True)
                _shutil.rmtree(frames_out, ignore_errors=True)
            except Exception:
                pass


def cleanup_upscale_workdir(upscaled_path: str):
    """Remove the upscale work directory after encoding is complete."""
    import shutil as _shutil
    try:
        work_dir = Path(upscaled_path).parent
        if work_dir.name.startswith("optimizarr_upscale_"):
            _shutil.rmtree(work_dir, ignore_errors=True)
    except Exception:
        pass


def cleanup_stale_upscale_workdirs(min_age_seconds: float = 48 * 3600.0) -> int:
    """Remove leftover optimizarr_upscale_* work directories from temp.

    A crashed or killed encode never reaches cleanup_upscale_workdir, so its
    workdir (potentially tens of GB of PNG frames) lingers in the temp dir
    forever. Called when the encoder pool goes idle.

    A dir is skipped when the dir itself or anything directly inside it was
    modified within ``min_age_seconds``. The 48h default is deliberate:
    workdirs double as RESUME CHECKPOINTS for interrupted upscales, so they
    must survive a restart-and-retry cycle — only dirs untouched for two
    days are treated as dead.

    Returns the number of directories removed.
    """
    import tempfile
    import shutil as _shutil

    base = Path(tempfile.gettempdir())
    now = time.time()
    removed = 0
    for d in base.glob("optimizarr_upscale_*"):
        try:
            if not d.is_dir():
                continue
            newest = d.stat().st_mtime
            for child in d.iterdir():
                newest = max(newest, child.stat().st_mtime)
            if now - newest < min_age_seconds:
                continue
            _shutil.rmtree(d, ignore_errors=True)
            removed += 1
        except OSError:
            continue
    if removed:
        optimizarr_logger.app_logger.info(
            "Cleaned %d stale upscale work directorie(s)", removed)
    return removed
