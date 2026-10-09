"""
NVIDIA RTX Video Super Resolution backend.

Uses NVIDIA's official ``nvidia-vfx`` Python bindings (VideoSuperRes — the
same model as the driver's RTX VSR, trained on compressed video so it also
removes blocking/ringing while upscaling). Requires an RTX (Tensor Core)
GPU: Turing / RTX 20-series or newer, driver 570.65+ on Windows.

The runtime (~550 MB: nvidia-vfx from NVIDIA's package index, CuPy, NumPy)
lives in its own venv under %LOCALAPPDATA%/Optimizarr/vsr so it never
touches the app's own Python environment. app/vsr_worker.py runs inside it.
"""
import json
import os
import platform
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable, Dict, Optional

from app.logger import optimizarr_logger

# Upscaling quality levels exposed as "models". HIGHBITRATE_* skip artifact
# suppression for clean sources (Blu-ray remux, ProRes).
QUALITY_LEVELS = ["ULTRA", "HIGH", "MEDIUM", "LOW", "HIGHBITRATE_ULTRA", "HIGHBITRATE_HIGH"]
PACKAGES = ["nvidia-vfx", "cupy-cuda12x", "numpy"]
NVIDIA_INDEX = "https://pypi.nvidia.com"
MAX_FACTOR = 4


def runtime_dir() -> Path:
    if platform.system() == "Windows":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        base = Path.home() / ".local" / "share"
    return base / "Optimizarr" / "vsr"


def runtime_python() -> Path:
    venv = runtime_dir() / "venv"
    return venv / ("Scripts/python.exe" if platform.system() == "Windows" else "bin/python")


def _marker() -> Path:
    return runtime_dir() / "installed.json"


def installed_info() -> Optional[Dict]:
    """Install record (written only after a successful GPU smoke test)."""
    try:
        if runtime_python().exists() and _marker().exists():
            return json.loads(_marker().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    return None


_SMOKE = r"""
import json, cupy as cp, numpy as np, nvvfx
from nvvfx import VideoSuperRes
v = VideoSuperRes(quality=VideoSuperRes.QualityLevel.HIGH)
v.output_width, v.output_height = 128, 72
v.load()
out = cp.asnumpy(cp.from_dlpack(v.run(cp.asarray(np.zeros((3, 36, 64), np.float32))).image))
assert out.shape == (3, 72, 128)
import importlib.metadata as m
print(json.dumps({"nvidia-vfx": m.version("nvidia-vfx"), "cupy": cp.__version__,
                  "gpu": cp.cuda.runtime.getDeviceProperties(0)["name"].decode()}))
"""


def install(state: Dict) -> None:
    """Create the runtime venv, install packages, verify on the GPU.

    ``state`` is the shared download-status dict the UI polls.
    """
    rdir = runtime_dir()
    rdir.mkdir(parents=True, exist_ok=True)
    creation = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    def run(cmd, msg, pct, timeout):
        state.update(message=msg, progress=pct)
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout, creationflags=creation)
        if r.returncode != 0:
            raise RuntimeError(f"{msg} failed: {(r.stderr or r.stdout).strip()[-600:]}")
        return r.stdout

    if not runtime_python().exists():
        run([sys.executable, "-m", "venv", str(rdir / "venv")], "Creating runtime…", 5, 300)
    py = str(runtime_python())
    run([py, "-m", "pip", "install", "--upgrade", "pip"], "Updating pip…", 10, 600)
    run([py, "-m", "pip", "install", "--upgrade", "--extra-index-url", NVIDIA_INDEX, *PACKAGES],
        "Downloading NVIDIA VFX runtime (~550 MB)…", 20, 3600)
    out = run([py, "-c", _SMOKE], "Testing on the GPU…", 90, 300)
    info = json.loads(out.strip().splitlines()[-1])
    _marker().write_text(json.dumps(info), encoding="utf-8")
    state.update(version=f"nvidia-vfx {info['nvidia-vfx']} on {info['gpu']}")
    optimizarr_logger.app_logger.info("RTX VSR runtime installed: %s", info)


def run_vsr(input_path: str, output_path: str, *, in_w: int, in_h: int,
            out_w: int, out_h: int, fps: float, quality: str, matrix: str,
            setparams: str, total_frames: int,
            progress: Callable[[float, Optional[float]], None],
            register: Callable) -> bool:
    """Upscale ``input_path`` to ``output_path`` (x264 CRF 12 intermediate,
    audio/subtitles copied). Returns True on success."""
    if not installed_info():
        optimizarr_logger.app_logger.error("RTX VSR runtime is not installed")
        return False
    if quality not in QUALITY_LEVELS:
        quality = "ULTRA"
    fps_str = f"{fps:.6g}"
    decode_cmd = [
        "ffmpeg", "-v", "error", "-i", input_path, "-map", "0:V:0",
        # Constant rate (frames are re-timed at one rate on the way out) via
        # the fps filter — see the note on frame extraction in app.upscaler.
        "-fps_mode", "passthrough",
        "-vf", f"fps=fps={fps_str}:round=near,"
               f"scale=in_color_matrix={matrix}:in_range=auto,format=gbrpf32le",
        "-f", "rawvideo", "-pix_fmt", "gbrpf32le", "-",
    ]
    encode_cmd = [
        "ffmpeg", "-v", "error", "-y",
        "-f", "rawvideo", "-pix_fmt", "gbrpf32le", "-s", f"{out_w}x{out_h}",
        "-framerate", fps_str, "-i", "-",
        "-i", input_path,
        "-map", "0:v:0", "-map", "1:a?", "-map", "1:s?",
        "-vf", f"scale=out_color_matrix={matrix}:out_range=tv,format=yuv420p,{setparams}",
        "-c:v", "libx264", "-crf", "12", "-preset", "veryfast",
        "-c:a", "copy", "-c:s", "copy", "-shortest",
        output_path,
    ]
    job = {"decode_cmd": decode_cmd, "encode_cmd": encode_cmd,
           "in_w": in_w, "in_h": in_h, "out_w": out_w, "out_h": out_h,
           "quality": quality, "total_frames": total_frames}
    fd, job_path = tempfile.mkstemp(prefix="optimizarr_vsr_", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(job, f)

    worker = Path(__file__).with_name("vsr_worker.py")
    creation = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    optimizarr_logger.app_logger.info(
        "RTX VSR: %dx%d -> %dx%d (%s)", in_w, in_h, out_w, out_h, quality)
    import time
    started = time.monotonic()
    try:
        proc = subprocess.Popen(
            [str(runtime_python()), str(worker), job_path],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", creationflags=creation)
    except OSError as e:
        optimizarr_logger.app_logger.error("RTX VSR failed to start: %s", e)
        os.unlink(job_path)
        return False
    register(proc)
    try:
        for line in proc.stdout:
            parts = line.split()
            if len(parts) == 3 and parts[0] == "progress":
                done, total = int(parts[1]), int(parts[2]) or total_frames
                if total:
                    pct = min(100.0, done / total * 100)
                    elapsed = time.monotonic() - started
                    eta = elapsed / done * (total - done) if done else None
                    progress(pct, eta)
        rc = proc.wait()
        err = proc.stderr.read()
    finally:
        register(None)
        try:
            os.unlink(job_path)
        except OSError:
            pass
    if rc != 0:
        optimizarr_logger.app_logger.error("RTX VSR failed (%s): %s", rc, (err or "").strip()[-800:])
        return False
    out = Path(output_path)
    if not out.exists() or out.stat().st_size == 0:
        optimizarr_logger.app_logger.error("RTX VSR produced no output")
        return False
    return True
