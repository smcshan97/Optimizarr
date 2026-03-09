"""
Stereo 3D conversion module for Optimizarr.
Converts 2D video to side-by-side 3D using nunif/iw3, and
extracts 2D from 3D using ffmpeg crop.

Mirrors the pattern of app/upscaler.py for pipeline integration.
"""
import subprocess
import shutil
import tempfile
import platform
from typing import Optional, Dict, List, Callable
from pathlib import Path

from app.logger import optimizarr_logger


# ---------------------------------------------------------------------------
# Depth model definitions (iw3 built-in models)
# ---------------------------------------------------------------------------

DEPTH_MODELS: Dict[str, Dict] = {
    "Any_V2_S": {
        "name": "Depth-Anything V2 Small",
        "description": "General purpose, fastest",
        "best_for": "Quick conversions, general content",
    },
    "Any_V2_N_B": {
        "name": "Depth-Anything V2 Base",
        "description": "General purpose, better quality",
        "best_for": "Movies, TV shows",
    },
    "ZoeD_Any_N": {
        "name": "ZoeDepth Any N",
        "description": "Mixed indoor/outdoor scenes",
        "best_for": "Mixed content",
    },
    "ZoeD_N": {
        "name": "ZoeDepth NYUv2",
        "description": "Tuned for indoor scenes",
        "best_for": "Indoor footage, sitcoms",
    },
    "ZoeD_K": {
        "name": "ZoeDepth KITTI",
        "description": "Tuned for outdoor/driving scenes",
        "best_for": "Outdoor footage, dashcam, nature",
    },
}

# Supported stereo output formats and their iw3 CLI flags
STEREO_FORMATS: Dict[str, Dict] = {
    "half_sbs": {
        "name": "Half Side-by-Side",
        "description": "Same resolution as input, compatible with most VR players",
        "iw3_flag": "--half-sbs",
        "crop_filter": "crop=iw/2:ih:0:0",
    },
    "sbs": {
        "name": "Full Side-by-Side",
        "description": "Double width, highest quality SBS",
        "iw3_flag": "",  # default iw3 output
        "crop_filter": "crop=iw/2:ih:0:0",
    },
    "half_tb": {
        "name": "Half Top-Bottom",
        "description": "Same resolution, stacked vertically",
        "iw3_flag": "--half-tb",
        "crop_filter": "crop=iw:ih/2:0:0",
    },
    "tb": {
        "name": "Full Top-Bottom",
        "description": "Double height, stacked vertically",
        "iw3_flag": "--tb",
        "crop_filter": "crop=iw:ih/2:0:0",
    },
    "vr180": {
        "name": "VR180 Equirectangular",
        "description": "For VR headset 180-degree playback",
        "iw3_flag": "--vr180",
        "crop_filter": "crop=iw/2:ih:0:0",
    },
    "anaglyph": {
        "name": "Anaglyph (Red/Cyan)",
        "description": "For red/cyan 3D glasses",
        "iw3_flag": "--anaglyph",
        "crop_filter": None,  # can't extract 2D from anaglyph
    },
}


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

_iw3_available: Optional[bool] = None
_ffmpeg_available: Optional[bool] = None


def detect_iw3() -> Dict:
    """Detect whether iw3 (nunif) is available on the system."""
    global _iw3_available
    info = {
        "available": False,
        "method": None,
        "error": None,
        "depth_models": list(DEPTH_MODELS.keys()),
        "stereo_formats": list(STEREO_FORMATS.keys()),
    }

    # Try python -m iw3 --help
    for python_cmd in ("python", "python3"):
        try:
            result = subprocess.run(
                [python_cmd, "-m", "iw3", "--help"],
                capture_output=True, text=True, timeout=15,
            )
            if result.returncode == 0 or "usage" in (result.stdout + result.stderr).lower():
                info["available"] = True
                info["method"] = f"{python_cmd} -m iw3"
                _iw3_available = True
                optimizarr_logger.app_logger.info("iw3 detected via: %s -m iw3", python_cmd)
                return info
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            continue

    info["error"] = "iw3 (nunif) not found. Install from: https://github.com/nagadomi/nunif"
    _iw3_available = False
    return info


def detect_ffmpeg() -> bool:
    """Check if ffmpeg is available."""
    global _ffmpeg_available
    if _ffmpeg_available is not None:
        return _ffmpeg_available
    _ffmpeg_available = shutil.which("ffmpeg") is not None
    return _ffmpeg_available


def _get_python_cmd() -> Optional[str]:
    """Return the python command that has iw3 available."""
    for cmd in ("python", "python3"):
        try:
            result = subprocess.run(
                [cmd, "-m", "iw3", "--help"],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0 or "usage" in (result.stdout + result.stderr).lower():
                return cmd
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            continue
    return None


# ---------------------------------------------------------------------------
# 2D → 3D conversion (iw3)
# ---------------------------------------------------------------------------

def _run_iw3(
    input_path: str,
    output_path: str,
    stereo_format: str = "half_sbs",
    divergence: float = 2.0,
    convergence: float = 0.5,
    depth_model: str = "Any_V2_S",
    progress_callback: Optional[Callable[[float], None]] = None,
) -> bool:
    """
    Run iw3 CLI to convert a 2D video to 3D.

    Returns True on success, False on failure.
    """
    python_cmd = _get_python_cmd()
    if not python_cmd:
        optimizarr_logger.app_logger.error("iw3 not available — cannot convert 2D→3D")
        return False

    fmt_info = STEREO_FORMATS.get(stereo_format, STEREO_FORMATS["half_sbs"])

    cmd = [
        python_cmd, "-m", "iw3",
        "-i", input_path,
        "-o", output_path,
        "--divergence", str(divergence),
        "--convergence", str(convergence),
        "--depth-model", depth_model,
        "--method", "row_flow_v3",
        "--video-format", "mp4",
        "--pix-fmt", "yuv420p",
    ]

    # Add format-specific flag
    if fmt_info["iw3_flag"]:
        cmd.append(fmt_info["iw3_flag"])

    # Use GPU if available
    cmd.extend(["--gpu", "0"])

    optimizarr_logger.app_logger.info("iw3 command: %s", " ".join(cmd))

    try:
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )

        # iw3 prints progress like "Processing: 50/200 frames"
        import re
        frame_pattern = re.compile(r'(\d+)\s*/\s*(\d+)')

        for line in process.stdout:
            line = line.strip()
            if not line:
                continue
            match = frame_pattern.search(line)
            if match and progress_callback:
                current = int(match.group(1))
                total = int(match.group(2))
                if total > 0:
                    progress_callback((current / total) * 100.0)

        return_code = process.wait()

        if return_code == 0:
            optimizarr_logger.app_logger.info("iw3 conversion complete: %s", output_path)
            return True
        else:
            optimizarr_logger.app_logger.error("iw3 exited with code %d", return_code)
            return False

    except Exception as e:
        optimizarr_logger.app_logger.error("iw3 error: %s", str(e))
        return False


# ---------------------------------------------------------------------------
# 3D → 2D conversion (ffmpeg crop)
# ---------------------------------------------------------------------------

def _run_3d_to_2d(
    input_path: str,
    output_path: str,
    stereo_format: str = "half_sbs",
    progress_callback: Optional[Callable[[float], None]] = None,
) -> bool:
    """
    Extract 2D (left eye) from a 3D video using ffmpeg crop.

    Returns True on success, False on failure.
    """
    if not detect_ffmpeg():
        optimizarr_logger.app_logger.error("ffmpeg not available — cannot convert 3D→2D")
        return False

    fmt_info = STEREO_FORMATS.get(stereo_format, STEREO_FORMATS["half_sbs"])
    crop_filter = fmt_info.get("crop_filter")

    if not crop_filter:
        optimizarr_logger.app_logger.error(
            "Cannot extract 2D from format '%s' — no crop filter defined", stereo_format
        )
        return False

    cmd = [
        "ffmpeg", "-y",
        "-i", input_path,
        "-vf", crop_filter,
        "-c:a", "copy",
        "-c:v", "libx264",
        "-crf", "18",
        "-preset", "fast",
        output_path,
    ]

    optimizarr_logger.app_logger.info("3D→2D command: %s", " ".join(cmd))

    try:
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )

        # ffmpeg prints "frame=  500" style progress
        import re
        frame_pattern = re.compile(r'frame=\s*(\d+)')
        duration_pattern = re.compile(r'Duration:\s*(\d+):(\d+):(\d+)')
        total_frames = 0

        for line in process.stdout:
            line = line.strip()
            if not total_frames:
                dur_match = duration_pattern.search(line)
                if dur_match:
                    h, m, s = int(dur_match.group(1)), int(dur_match.group(2)), int(dur_match.group(3))
                    # Rough estimate at 24fps
                    total_frames = (h * 3600 + m * 60 + s) * 24

            frame_match = frame_pattern.search(line)
            if frame_match and total_frames > 0 and progress_callback:
                current = int(frame_match.group(1))
                progress_callback(min((current / total_frames) * 100.0, 99.0))

        return_code = process.wait()

        if return_code == 0:
            optimizarr_logger.app_logger.info("3D→2D extraction complete: %s", output_path)
            if progress_callback:
                progress_callback(100.0)
            return True
        else:
            optimizarr_logger.app_logger.error("ffmpeg exited with code %d", return_code)
            return False

    except Exception as e:
        optimizarr_logger.app_logger.error("ffmpeg error: %s", str(e))
        return False


# ---------------------------------------------------------------------------
# Public pipeline entry point
# ---------------------------------------------------------------------------

def run_stereo_pipeline(
    input_path: str,
    mode: str = "2d_to_3d",
    stereo_format: str = "half_sbs",
    divergence: float = 2.0,
    convergence: float = 0.5,
    depth_model: str = "Any_V2_S",
    progress_callback: Optional[Callable[[float], None]] = None,
) -> Optional[str]:
    """
    Run the stereo conversion pipeline on *input_path*.

    For 2D→3D: runs iw3 to generate SBS/TB/VR180 output.
    For 3D→2D: runs ffmpeg crop to extract left eye.

    Returns the path to the converted temp file on success, or None on failure.
    The caller is responsible for cleanup via cleanup_stereo_workdir().

    Progress callback receives values 0–100.
    """
    input_file = Path(input_path)
    if not input_file.exists():
        optimizarr_logger.app_logger.error("Stereo input not found: %s", input_path)
        return None

    # Create temp working directory
    work_dir = Path(tempfile.mkdtemp(prefix="optimizarr_stereo_"))
    suffix = "_3D" if mode == "2d_to_3d" else "_2D"
    output_path = str(work_dir / f"{input_file.stem}{suffix}.mp4")

    optimizarr_logger.app_logger.info(
        "Stereo pipeline: mode=%s format=%s input=%s",
        mode, stereo_format, input_path,
    )

    if mode == "2d_to_3d":
        success = _run_iw3(
            input_path=input_path,
            output_path=output_path,
            stereo_format=stereo_format,
            divergence=divergence,
            convergence=convergence,
            depth_model=depth_model,
            progress_callback=progress_callback,
        )
    elif mode == "3d_to_2d":
        success = _run_3d_to_2d(
            input_path=input_path,
            output_path=output_path,
            stereo_format=stereo_format,
            progress_callback=progress_callback,
        )
    else:
        optimizarr_logger.app_logger.error("Unknown stereo mode: %s", mode)
        return None

    if success and Path(output_path).exists():
        return output_path

    # Cleanup on failure
    cleanup_stereo_workdir(output_path)
    return None


def cleanup_stereo_workdir(stereo_path: str):
    """Remove the stereo work directory after encoding is complete."""
    try:
        work_dir = Path(stereo_path).parent
        if work_dir.name.startswith("optimizarr_stereo_"):
            shutil.rmtree(work_dir, ignore_errors=True)
            optimizarr_logger.app_logger.debug("Cleaned up stereo workdir: %s", work_dir)
    except Exception:
        pass


def get_stereo_info() -> Dict:
    """Return full stereo capability info for the settings UI."""
    iw3_info = detect_iw3()
    return {
        "iw3": iw3_info,
        "ffmpeg": detect_ffmpeg(),
        "depth_models": DEPTH_MODELS,
        "stereo_formats": {
            k: {"name": v["name"], "description": v["description"]}
            for k, v in STEREO_FORMATS.items()
        },
        "supported_modes": {
            "2d_to_3d": iw3_info["available"],
            "3d_to_2d": detect_ffmpeg(),
        },
    }
