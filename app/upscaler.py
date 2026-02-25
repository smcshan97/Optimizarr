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
    upscaler = UPSCALERS[key]
    is_win = platform.system() == "Windows"
    return upscaler["binary_win"] if is_win else upscaler["binary_linux"]


def _find_binary(key: str) -> Optional[str]:
    """Return path to installed binary or None."""
    binary = _binary_name(key)
    # 1. System PATH
    found = shutil.which(binary)
    if found:
        return found
    # 2. Optimizarr install dir
    local = _upscaler_install_dir() / binary
    if local.exists():
        return str(local)
    return None


def _get_binary_version(binary_path: str) -> str:
    try:
        result = subprocess.run(
            [binary_path, "--help"],
            capture_output=True, text=True, timeout=5
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
                print(f"  ℹ Using release {release.get('tag_name')} (latest tag had no assets)")
                return release

    # 3. Nothing found
    if resp.status_code == 404:
        msg = f"GitHub repo {owner}/{repo} not found or has no releases."
    elif resp.status_code == 200:
        msg = f"No releases with binary assets found for {owner}/{repo}."
    else:
        msg = f"GitHub API returned HTTP {resp.status_code}"
    print(f"⚠ {msg}")
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
    if key not in UPSCALERS:
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
    upscaler = UPSCALERS[key]
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

        # 3. Extract
        binary_name = _binary_name(key)
        if asset_name.endswith(".zip"):
            with zipfile.ZipFile(archive_path, "r") as zf:
                # Find the binary inside the zip
                names = zf.namelist()
                target = next((n for n in names if Path(n).name == binary_name), None)
                if target is None:
                    # Just extract everything
                    zf.extractall(install_dir / key)
                    # Try to find binary in extracted tree
                    for p in (install_dir / key).rglob(binary_name):
                        p.rename(install_dir / binary_name)
                        break
                else:
                    data = zf.read(target)
                    dest = install_dir / binary_name
                    with open(dest, "wb") as bf:
                        bf.write(data)
        elif asset_name.endswith((".tar.gz", ".tgz")):
            with tarfile.open(archive_path, "r:gz") as tf:
                for member in tf.getmembers():
                    if Path(member.name).name == binary_name:
                        f_obj = tf.extractfile(member)
                        if f_obj:
                            dest = install_dir / binary_name
                            with open(dest, "wb") as bf:
                                bf.write(f_obj.read())
                        break

        # 4. Make binary executable on Linux/macOS
        binary_path = install_dir / binary_name
        if binary_path.exists() and not is_win:
            binary_path.chmod(0o755)

        # 5. Clean up archive
        try:
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
    return results


def check_for_updates() -> Dict:
    """
    Check GitHub for newer versions of all detected upscalers.
    Returns a dict of key → {current, latest, update_available}.
    """
    results = {}
    for key, upscaler in UPSCALERS.items():
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
            "download_url": v["url"],
        } for k, v in UPSCALERS.items()},
        "detection": detect_upscalers(),
        "workflow_note": (
            "AI upscaling extracts frames, upscales each with AI, then reassembles "
            "before HandBrake encoding. This is VERY slow (~2-5 min/frame) and GPU-intensive."
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


def _get_video_info(input_path: str) -> Dict:
    """Use ffprobe to get width, height, fps, duration of a video."""
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "quiet", "-print_format", "json",
                "-show_streams", "-select_streams", "v:0", input_path,
            ],
            capture_output=True, text=True, timeout=30,
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
        duration = float(stream.get("duration", 0))
        return {"width": width, "height": height, "fps": fps, "duration": duration}
    except Exception as e:
        optimizarr_logger.app_logger.warning("ffprobe failed: %s", e)
        return {"width": 0, "height": 0, "fps": 24.0, "duration": 0}


def run_upscale_pipeline(
    input_path: str,
    upscaler_key: str,
    model: str,
    factor: int,
    progress_callback=None,
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

    Returns the path to the upscaled video file on success, or None on failure.

    Progress callback receives values 0–100 across the whole pipeline:
      0-10%  : extraction
      10-90% : upscaling (per-frame progress from upscaler stderr)
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

    def _progress(pct: float):
        if progress_callback:
            try:
                progress_callback(min(100.0, max(0.0, pct)))
            except Exception:
                pass

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

    out_w = src_w * factor
    out_h = src_h * factor

    # Estimate frame count and disk requirement
    # PNG at out_h resolution: rough estimate ~3 bytes/pixel (pre-compression, but
    # PNG tends to compress well; we use a conservative 1.5 bytes/pixel for estimate)
    frame_count_approx = int(fps * duration) if duration > 0 else 2000
    bytes_per_frame_approx = int(out_w * out_h * 1.5)
    # Source + output frames + reassembly buffer
    required_bytes = (frame_count_approx * bytes_per_frame_approx * 2) + (500 * 1024 * 1024)

    _progress(2)
    optimizarr_logger.app_logger.info(
        "Upscale pipeline: %s — %dx%d → %dx%d (×%d, ~%d frames, ~%s needed)",
        input_p.name, src_w, src_h, out_w, out_h, factor,
        frame_count_approx, _format_size(required_bytes),
    )

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

    # Work directory — unique per job
    work_dir = tmp_base / f"optimizarr_upscale_{input_p.stem}_{int(time.time())}"
    frames_in  = work_dir / "frames_in"
    frames_out = work_dir / "frames_out"
    frames_in.mkdir(parents=True)
    frames_out.mkdir(parents=True)
    output_path = work_dir / f"{input_p.stem}_upscaled.mkv"

    try:
        # ── Stage 3: extract frames ─────────────────────────────────────────
        _progress(3)
        optimizarr_logger.app_logger.info("Extracting frames from %s…", input_p.name)
        extract_cmd = [
            "ffmpeg", "-i", input_path,
            "-vf", "fps=fps=source_fps",   # extract all frames at native fps
            "-q:v", "1",                   # maximum PNG quality
            str(frames_in / "%08d.png"),
            "-y",
        ]
        # Use actual fps value rather than the literal placeholder
        extract_cmd = [
            "ffmpeg", "-i", input_path,
            "-vsync", "0",
            str(frames_in / "%08d.png"),
            "-y",
        ]
        result = subprocess.run(
            extract_cmd, capture_output=True, text=True, timeout=3600
        )
        if result.returncode != 0:
            optimizarr_logger.app_logger.error(
                "ffmpeg frame extraction failed: %s", result.stderr[-500:]
            )
            return None

        actual_frames = sorted(frames_in.glob("*.png"))
        if not actual_frames:
            optimizarr_logger.app_logger.error("No frames extracted from %s", input_path)
            return None

        total_frames = len(actual_frames)
        optimizarr_logger.app_logger.info("Extracted %d frames", total_frames)
        _progress(10)

        # ── Stage 4: upscale frames ─────────────────────────────────────────
        upscale_cmd = [
            binary_path,
            "-i", str(frames_in),
            "-o", str(frames_out),
            "-n", model,
            "-s", str(factor),
            "-f", "png",
        ]
        optimizarr_logger.app_logger.info(
            "Upscaling %d frames with %s (model=%s ×%d)…",
            total_frames, upscaler_key, model, factor,
        )

        upscale_proc = subprocess.Popen(
            upscale_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )

        # Parse per-frame progress from upscaler output
        # Real-ESRGAN / CUGAN print lines like:  "1/43200"
        frame_re = re.compile(r'(\d+)/(\d+)')
        for line in upscale_proc.stdout:
            m = frame_re.search(line)
            if m:
                done  = int(m.group(1))
                total = int(m.group(2))
                if total > 0:
                    # Map frame progress to 10-90% range
                    _progress(10 + (done / total) * 80)

        upscale_proc.wait()
        if upscale_proc.returncode != 0:
            optimizarr_logger.app_logger.error(
                "Upscaler exited with code %d", upscale_proc.returncode
            )
            return None

        upscaled_frames = sorted(frames_out.glob("*.png"))
        if not upscaled_frames:
            optimizarr_logger.app_logger.error("No upscaled frames produced")
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
            "-c:v", "ffv1",              # lossless intermediate — HandBrake re-encodes
            "-c:a", "copy",
            "-c:s", "copy",
            "-shortest",
            str(output_path),
            "-y",
        ]
        result = subprocess.run(
            reassemble_cmd, capture_output=True, text=True, timeout=3600
        )
        if result.returncode != 0:
            optimizarr_logger.app_logger.error(
                "ffmpeg reassembly failed: %s", result.stderr[-500:]
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
        return str(output_path)

    except Exception as e:
        optimizarr_logger.app_logger.error("Upscale pipeline error: %s", e)
        return None

    finally:
        # Always clean up source frames immediately (output frames cleaned by caller)
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
