"""
Video2X streaming upscale backend.

Video2X 6.x decodes, upscales on the GPU (ncnn/Vulkan) and re-encodes in one
process — no per-frame PNGs on disk. The PNG pipeline in app.upscaler needs
hundreds of GB of temp space for a feature film and spends most of its time
in PNG encode/decode; this backend needs only the (compressed) output file.

Used automatically for Real-ESRGAN / Real-CUGAN jobs when Video2X is
installed; waifu2x (which Video2X doesn't ship) and any Video2X failure fall
back to the PNG pipeline.
"""
import re
import subprocess
import time
from pathlib import Path
from typing import Callable, Dict, Optional, Tuple

from app.logger import optimizarr_logger

# Our model names -> Video2X's (it renamed the x4plus models).
_MODEL_MAP = {
    "realesrgan": {
        "realesrgan-x4plus": "realesrgan-plus",
        "realesrgan-x4plus-anime": "realesrgan-plus-anime",
        "realesr-animevideov3": "realesr-animevideov3",
    },
    "realcugan": {
        "models-se": "models-se",
        "models-pro": "models-pro",
        "models-nose": "models-nose",
    },
}

# frame=1234/5678 (21.73%); fps=12.3; elapsed=...; remaining=00:12:34
_PROGRESS_RE = re.compile(
    r"frame=(\d+)/(\d+)\s*\(([\d.]+)%\)(?:;\s*fps=([\d.]+))?"
    r"(?:;\s*elapsed=[\d:]+)?(?:;\s*remaining=(\d+):(\d+):(\d+))?"
)

# Intermediate codec. The output is re-encoded by HandBrake, so it only has
# to be visually lossless — x264 CRF 12 is ~10x smaller than FFV1/lossless.
INTERMEDIATE_ARGS = ["-c", "libx264", "--pix-fmt", "yuv420p",
                     "-e", "crf=12", "-e", "preset=veryfast"]


def video2x_model(upscaler_key: str, model: str) -> Optional[Tuple[str, str, str]]:
    """(processor, model option, Video2X model name), or None if unsupported."""
    name = _MODEL_MAP.get(upscaler_key, {}).get(model)
    if not name:
        return None
    return upscaler_key, f"--{upscaler_key}-model", name


def parse_progress(text: str) -> Optional[Dict]:
    """Last progress record in a chunk of Video2X stdout, or None."""
    last = None
    for m in _PROGRESS_RE.finditer(text):
        last = m
    if not last:
        return None
    done, total, pct, fps, h, mi, s = last.groups()
    remaining = (int(h) * 3600 + int(mi) * 60 + int(s)) if h is not None else None
    return {"frame": int(done), "total": int(total), "percent": float(pct),
            "fps": float(fps) if fps else 0.0, "remaining": remaining}


def build_command(binary: str, input_path: str, output_path: str,
                  upscaler_key: str, model: str, model_scale: int) -> Optional[list]:
    spec = video2x_model(upscaler_key, model)
    if not spec:
        return None
    processor, model_opt, v2x_model = spec
    return [
        binary,
        "-i", input_path,
        "-o", output_path,
        "-p", processor,
        "-s", str(model_scale),
        model_opt, v2x_model,
        *INTERMEDIATE_ARGS,
        "--log-level", "warn",
    ]


def run_video2x(binary: str, input_path: str, output_path: str,
                upscaler_key: str, model: str, model_scale: int,
                progress: Callable[[float, Optional[float]], None],
                register: Callable) -> bool:
    """Run one Video2X upscale. ``progress(pct_0_100, eta_seconds)``.

    Video2X reads the keyboard (space = pause, q = abort), so stdin is
    detached; stdout carries the \\r-separated progress line.
    """
    cmd = build_command(binary, input_path, output_path, upscaler_key, model, model_scale)
    if not cmd:
        return False
    optimizarr_logger.app_logger.info("Video2X: %s", " ".join(cmd))
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        proc = subprocess.Popen(
            cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, creationflags=creationflags,
            cwd=str(Path(binary).parent),  # models/ is resolved beside the exe
        )
    except OSError as e:
        optimizarr_logger.app_logger.error("Video2X failed to start: %s", e)
        return False
    register(proc)
    tail = ""
    try:
        while True:
            chunk = proc.stdout.read1(4096) if hasattr(proc.stdout, "read1") \
                else proc.stdout.read(4096)
            if not chunk:
                break
            text = chunk.decode("utf-8", "replace")
            tail = (tail + text)[-4000:]
            rec = parse_progress(text)
            if rec:
                progress(rec["percent"], rec["remaining"])
        rc = proc.wait()
    finally:
        register(None)

    if rc != 0:
        cleaned = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", tail).replace("\r", "\n")
        optimizarr_logger.app_logger.error(
            "Video2X exited %s: %s", rc, cleaned.strip()[-800:])
        return False
    out = Path(output_path)
    if not out.exists() or out.stat().st_size == 0:
        optimizarr_logger.app_logger.error("Video2X produced no output")
        return False
    return True
