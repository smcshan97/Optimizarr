"""
Content classifier: is a video 2D animation, 3D animation or live action?

Runs CLIP (ViT-B/32, quantized ONNX — Xenova/clip-vit-base-patch32) on 8
frames per file in an isolated runtime under %LOCALAPPDATA%/Optimizarr/clip
(onnxruntime CPU + numpy + tokenizers, ~156 MB of models), so the app's own
Python gains no dependencies. app/clip_worker.py does the inference.

Measured on this library (2026-10-13): 91.2% animated-vs-live accuracy on 80
folder-labelled files; deciding only at >= 0.60 confidence covered 85% of
files at 98.5%. Plain frame statistics (noise, saturation, edges) could not
separate the two at all on compressed sources, which is why a model is used.

Uses: the "Auto per file" upscaler (animated -> Real-CUGAN Pro, live -> RTX
VSR — Real-CUGAN Pro won 3D anime at VMAF 96.7 vs 88.1 for VSR), optional
film-grain synthesis for live action, and a tag on each queue row. Files the
model isn't sure about are 'uncertain' and keep the profile's own settings.
The Settings quiz lets the user label frames; those labels override the
model for that file and calibrate the confidence threshold.
"""
import json
import os
import platform
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from app.logger import optimizarr_logger

MODEL_VERSION = "clip-vit-b32-q8-v1"
PACKAGES = ["onnxruntime", "numpy", "tokenizers"]
_HF = "https://huggingface.co/Xenova/clip-vit-base-patch32/resolve/main/"
# local name -> (path in the repo, exact size in bytes)
MODEL_FILES = {
    "vision_model_quantized.onnx": ("onnx/vision_model_quantized.onnx", 89117001),
    "text_model_quantized.onnx": ("onnx/text_model_quantized.onnx", 64504507),
    "tokenizer.json": ("tokenizer.json", 2224119),
}
DEFAULT_THRESHOLD = 0.60
CLASSES = ("2d", "3d", "live")
# Auto upscaler defaults: (key, model). Live falls back to Upscayl Lite when
# RTX VSR isn't installed (no RTX GPU).
DEFAULT_AUTO = {"animated": "realcugan:models-pro", "live": "rtxvsr:ULTRA"}
LIVE_FALLBACK = "upscayl:upscayl-lite-4x"

_CREATION = getattr(subprocess, "CREATE_NO_WINDOW", 0)
# Classification shares the CPU with encodes: run it below normal priority.
_LOW_PRIORITY = getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)


def runtime_dir() -> Path:
    if os.environ.get("OPTIMIZARR_CLIP_DIR"):        # tests point this at a temp dir
        return Path(os.environ["OPTIMIZARR_CLIP_DIR"])
    if platform.system() == "Windows":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        base = Path.home() / ".local" / "share"
    return base / "Optimizarr" / "clip"


def runtime_python() -> Path:
    venv = runtime_dir() / "venv"
    return venv / ("Scripts/python.exe" if platform.system() == "Windows" else "bin/python")


def models_dir() -> Path:
    return runtime_dir() / "models"


def _marker() -> Path:
    return runtime_dir() / "installed.json"


def installed_info() -> Optional[Dict]:
    """Install record (written only after a successful smoke test)."""
    try:
        if runtime_python().exists() and _marker().exists():
            return json.loads(_marker().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    return None


def _setting(key: str, default: str) -> str:
    try:
        from app.database import db
        return db.get_setting(key, default) or default
    except Exception:
        return default


def enabled() -> bool:
    return bool(installed_info()) and _setting("content_detection", "true") == "true"


def threshold() -> float:
    try:
        return max(0.34, min(0.99, float(_setting("content_threshold", str(DEFAULT_THRESHOLD)))))
    except ValueError:
        return DEFAULT_THRESHOLD


def make_test_clip(work: Path) -> Optional[Path]:
    clip = work / "classifier_test.mkv"
    r = subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=24",
         "-t", "2", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(clip)],
        capture_output=True, timeout=120, creationflags=_CREATION)
    return clip if r.returncode == 0 and clip.exists() else None


def classify(paths: List[str], timeout: int = 1800) -> Dict[str, Dict]:
    """{path: {"probs": {2d,3d,live}} or {"error": ...}} for each path."""
    if not paths:
        return {}
    if not runtime_python().exists():
        return {p: {"error": "content classifier not installed"} for p in paths}
    fd, list_path = tempfile.mkstemp(prefix="optimizarr_clip_", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(paths, f)
    worker = Path(__file__).with_name("clip_worker.py")
    out: Dict[str, Dict] = {}
    try:
        r = subprocess.run(
            [str(runtime_python()), str(worker), str(models_dir()), list_path],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout, creationflags=_CREATION | _LOW_PRIORITY,
            env={**os.environ, "OPTIMIZARR_CLIP_THREADS": "2"})
        for line in (r.stdout or "").splitlines():
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            out[rec.get("file")] = rec
        if r.returncode != 0 and not out:
            err = (r.stderr or "").strip()[-300:]
            return {p: {"error": f"classifier exited {r.returncode}: {err}"} for p in paths}
    except subprocess.TimeoutExpired:
        pass
    finally:
        try:
            os.unlink(list_path)
        except OSError:
            pass
    return {p: out.get(p, {"error": "no result"}) for p in paths}


def decide(probs: Dict[str, float], floor: Optional[float] = None) -> Tuple[str, float]:
    """('2d'|'3d'|'live'|'uncertain', confidence of the winning side).

    Animated = 2d + 3d. Below the confidence floor the file is 'uncertain'
    and keeps the profile's own settings.
    """
    floor = threshold() if floor is None else floor
    animated = float(probs.get("2d", 0)) + float(probs.get("3d", 0))
    live = float(probs.get("live", 0))
    conf = max(animated, live)
    if conf < floor:
        return "uncertain", conf
    if animated > live:
        return ("2d" if probs.get("2d", 0) >= probs.get("3d", 0) else "3d"), conf
    return "live", conf


def record(probs: Dict[str, float], source: str = "model") -> str:
    """JSON stored in queue.content_probs."""
    return json.dumps({"probs": probs, "version": MODEL_VERSION, "source": source})


def is_animated(content_type: Optional[str]) -> bool:
    return content_type in ("2d", "3d")


def _installed(key: str) -> bool:
    from app.upscaler import _find_binary
    return bool(_find_binary(key))


def auto_choice(content_type: Optional[str]) -> Tuple[str, str]:
    """(upscaler key, model) for the "Auto per file" upscaler."""
    side = "animated" if is_animated(content_type) else "live"
    pick = _setting(f"auto_upscaler_{side}", DEFAULT_AUTO[side])
    key, _, model = pick.partition(":")
    if not _installed(key):
        fallback = DEFAULT_AUTO[side] if pick != DEFAULT_AUTO[side] else LIVE_FALLBACK
        fk, _, fm = fallback.partition(":")
        if _installed(fk):
            key, model = fk, fm
        elif side == "live" and _installed(LIVE_FALLBACK.split(":")[0]):
            key, model = LIVE_FALLBACK.split(":")
    return key, model


def ensure_classified(item: Dict) -> Optional[str]:
    """The row's content type, classifying it now if it hasn't been yet."""
    if item.get("content_type"):
        return item["content_type"]
    if not enabled():
        return None
    res = classify([item["file_path"]], timeout=300).get(item["file_path"], {})
    if "probs" not in res:
        return None
    ctype, _ = decide(res["probs"])
    try:
        from app.database import db
        db.update_queue_item(item["id"], content_type=ctype, content_probs=record(res["probs"]))
    except Exception:
        pass
    item["content_type"] = ctype
    return ctype


def install(state: Dict) -> None:
    """Create the runtime venv, install packages, download and verify the
    models, then smoke-test. ``state`` is the status dict the UI polls."""
    import requests
    rdir = runtime_dir()
    models_dir().mkdir(parents=True, exist_ok=True)

    def run(cmd, msg, pct, timeout):
        state.update(message=msg, progress=pct)
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout, creationflags=_CREATION)
        if r.returncode != 0:
            raise RuntimeError(f"{msg} failed: {(r.stderr or r.stdout).strip()[-600:]}")
        return r.stdout

    if not runtime_python().exists():
        run([sys.executable, "-m", "venv", str(rdir / "venv")], "Creating runtime…", 5, 300)
    py = str(runtime_python())
    run([py, "-m", "pip", "install", "--upgrade", "--disable-pip-version-check", *PACKAGES],
        "Installing ONNX Runtime…", 15, 1800)
    for i, (name, (src, size)) in enumerate(MODEL_FILES.items()):
        dest = models_dir() / name
        if dest.exists() and dest.stat().st_size == size:
            continue
        state.update(message=f"Downloading {name} ({size / 1e6:.0f} MB)…",
                     progress=40 + i * 15)
        tmp = dest.with_suffix(dest.suffix + ".part")
        with requests.get(_HF + src, stream=True, timeout=120) as r:
            r.raise_for_status()
            with open(tmp, "wb") as f:
                for chunk in r.iter_content(chunk_size=1 << 20):
                    f.write(chunk)
        if tmp.stat().st_size != size:
            tmp.unlink(missing_ok=True)
            raise RuntimeError(f"{name} download was incomplete")
        tmp.replace(dest)
    state.update(message="Testing the classifier…", progress=90)
    info = smoke_test()
    if info.get("error"):
        raise RuntimeError(info["error"])
    _marker().write_text(json.dumps({"model": MODEL_VERSION, **info}), encoding="utf-8")
    state.update(version=MODEL_VERSION)
    optimizarr_logger.app_logger.info("Content classifier installed: %s", MODEL_VERSION)


def smoke_test() -> Dict:
    """Classify a generated clip: {"seconds": ...} or {"error": ...}."""
    import shutil
    import time
    work = Path(tempfile.mkdtemp(prefix="optimizarr_clip_test_"))
    try:
        clip = make_test_clip(work)
        if clip is None:
            return {"error": "couldn't create the test clip (is ffmpeg installed?)"}
        t0 = time.monotonic()
        res = classify([str(clip)], timeout=300).get(str(clip), {})
        probs = res.get("probs")
        if not probs or abs(sum(probs.values()) - 1.0) > 0.05:
            return {"error": res.get("error") or f"unexpected output: {res}"}
        return {"seconds": round(time.monotonic() - t0, 1)}
    finally:
        shutil.rmtree(work, ignore_errors=True)


# ── Settings quiz ─────────────────────────────────────────────────────────

def quiz_strip(path: str, out: Path, frames: int = 3) -> bool:
    """A 3-frame contact strip (each 426 px wide) for a quiz question."""
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "csv=p=0", path], capture_output=True, text=True,
                       creationflags=_CREATION)
    try:
        dur = float(r.stdout.strip())
    except ValueError:
        return False
    work = out.parent
    parts = []
    for i in range(frames):
        t = dur * (i + 1) / (frames + 1)
        p = work / f"{out.stem}_{i}.jpg"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t:.2f}", "-i", path,
                        "-map", "0:V:0", "-frames:v", "1",
                        "-vf", "scale=426:240:force_original_aspect_ratio=decrease,"
                               "pad=426:240:(ow-iw)/2:(oh-ih)/2", str(p)],
                       capture_output=True, timeout=120, creationflags=_CREATION)
        if p.exists():
            parts.append(p)
    if not parts:
        return False
    cmd = ["ffmpeg", "-v", "error", "-y"]
    for p in parts:
        cmd += ["-i", str(p)]
    cmd += ["-filter_complex", "".join(f"[{i}:v]" for i in range(len(parts)))
            + f"hstack=inputs={len(parts)}" if len(parts) > 1 else "null",
            "-q:v", "4", str(out)]
    subprocess.run(cmd, capture_output=True, timeout=120, creationflags=_CREATION)
    for p in parts:
        p.unlink(missing_ok=True)
    return out.exists()


def calibration(labels: List[Tuple[str, Dict[str, float]]]) -> Dict:
    """Agreement stats from user labels [(label, probs)], per threshold.

    Recommends the lowest threshold whose decided files are >= 97% right,
    once there are enough labels to say so (30+).
    """
    def side(c):
        return "animated" if c in ("2d", "3d") else c

    rows = [(lab, p) for lab, p in labels if lab in CLASSES and p]
    table, recommended = [], None
    for th in (0.5, 0.55, 0.6, 0.65, 0.7, 0.75, 0.8):
        decided = [(lab, decide(p, th)[0]) for lab, p in rows]
        decided = [(lab, pred) for lab, pred in decided if pred != "uncertain"]
        if not decided:
            table.append({"threshold": th, "coverage": 0, "accuracy": None})
            continue
        acc = sum(side(lab) == side(pred) for lab, pred in decided) / len(decided)
        table.append({"threshold": th, "coverage": len(decided) / len(rows), "accuracy": acc})
        if recommended is None and len(rows) >= 30 and acc >= 0.97:
            recommended = th
    confusion: Dict[str, Dict[str, int]] = {}
    for lab, p in rows:
        pred = decide(p, 0.0)[0]
        confusion.setdefault(lab, {}).setdefault(pred, 0)
        confusion[lab][pred] += 1
    return {"labeled": len(rows), "table": table, "recommended": recommended,
            "confusion": confusion}
