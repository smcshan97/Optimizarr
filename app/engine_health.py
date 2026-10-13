"""
Engine self-test: push a 2-second clip through every installed engine.

Unit tests mock the GPU and the binaries, so they can't see an engine that
installs fine and then fails on real frames — RTX VSR failed on every
non-multiple-of-16 width for weeks, each job silently falling back to an
un-upscaled encode. This runs the REAL pipeline on a tiny clip after each
install and once a day, records the result (setting ``engine_health``),
shows it on the upscaler cards and the queue page, and fires a notification
when an engine goes from passing to failing.

The clip is 426x240 so a x2 upscale lands on 852x480: 852 isn't a multiple
of 16, the case that broke RTX VSR.
"""
import json
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional

from app.database import db
from app.logger import optimizarr_logger

HEALTH_SETTING = "engine_health"
CLIP_W, CLIP_H, CLIP_FPS, CLIP_SECONDS = 426, 240, 24, 2
MIN_FRAMES = CLIP_FPS * CLIP_SECONDS - 6   # Video2X drops a couple at the end
STARTUP_DELAY_SECONDS = 10 * 60
INTERVAL_SECONDS = 24 * 3600

_lock = threading.Lock()
_CREATION = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def load() -> Dict[str, Dict]:
    """{engine key: {"ok", "message", "seconds", "at"}} from the last runs."""
    try:
        val = json.loads(db.get_setting(HEALTH_SETTING, "") or "{}")
        return val if isinstance(val, dict) else {}
    except ValueError:
        return {}


def _probe(path: str) -> Dict:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "V:0", "-count_packets",
         "-show_entries", "stream=width,height,nb_read_packets", "-of", "json", path],
        capture_output=True, text=True, timeout=60, creationflags=_CREATION)
    try:
        s = json.loads(r.stdout)["streams"][0]
        return {"width": int(s.get("width", 0)), "height": int(s.get("height", 0)),
                "frames": int(s.get("nb_read_packets", 0))}
    except (ValueError, KeyError, IndexError):
        return {}


def make_clip(work: Path) -> Optional[Path]:
    clip = work / "selftest.mkv"
    r = subprocess.run(
        ["ffmpeg", "-v", "error", "-y",
         "-f", "lavfi", "-i", f"testsrc2=size={CLIP_W}x{CLIP_H}:rate={CLIP_FPS}",
         "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
         "-t", str(CLIP_SECONDS), "-c:v", "libx264", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-shortest", str(clip)],
        capture_output=True, text=True, timeout=120, creationflags=_CREATION)
    return clip if r.returncode == 0 and clip.exists() else None


def _check_output(path: Optional[str], min_w: int, min_h: int) -> Optional[str]:
    """None when ``path`` is a plausible result, else what's wrong."""
    if not path or not Path(path).exists():
        return "produced no output"
    info = _probe(path)
    if info.get("frames", 0) < MIN_FRAMES:
        return f"output has {info.get('frames', 0)} frames (expected ≥{MIN_FRAMES})"
    if info.get("width", 0) < min_w or info.get("height", 0) < min_h:
        return f"output is {info.get('width')}x{info.get('height')} (expected ≥{min_w}x{min_h})"
    return None


def _test_handbrake(clip: Path, work: Path) -> Optional[str]:
    from app.handbrake import handbrake_cli
    out = work / "hb.mkv"
    r = subprocess.run(
        [handbrake_cli(), "-i", str(clip), "-o", str(out), "-e", "svt_av1_10bit",
         "--encoder-preset", "12", "-q", "40", "--crop-mode", "none"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=300, creationflags=_CREATION)
    if r.returncode != 0:
        return f"HandBrakeCLI exited {r.returncode}: {(r.stderr or r.stdout).strip()[-300:]}"
    return _check_output(str(out), CLIP_W - 16, CLIP_H - 16)   # auto-crop off; allow rounding


def _test_classifier() -> Optional[str]:
    from app import content_classifier as cc
    res = cc.smoke_test()
    return res.get("error")


def _test_upscaler(key: str, clip: Path) -> Optional[str]:
    from app import upscaler as up
    model = up.UPSCALERS[key]["default_model"]
    out = up.run_upscale_pipeline(str(clip), key, model, 2)
    try:
        return _check_output(out, CLIP_W * 2, CLIP_H * 2)
    finally:
        if out:
            up.cleanup_upscale_workdir(out)


def engines_to_test(only: Optional[List[str]] = None) -> List[str]:
    from app import upscaler as up
    from app.handbrake import handbrake_cli
    keys = []
    if shutil.which(handbrake_cli()) or Path(handbrake_cli()).exists():
        keys.append("handbrake")
    keys += [k for k in up.UPSCALERS if up._find_binary(k)]
    from app import content_classifier as cc
    if cc.installed_info():
        keys.append("classifier")
    return [k for k in keys if not only or k in only]


def _notify_failure(key: str, message: str):
    try:
        from app.notifications import notify_engine_failed
        notify_engine_failed(key, message)
    except Exception:
        pass


def run_self_test(only: Optional[List[str]] = None) -> Dict[str, Dict]:
    """Test every installed engine (or just ``only``); returns all results."""
    if not _lock.acquire(blocking=False):
        return load()
    work = Path(tempfile.mkdtemp(prefix="optimizarr_selftest_"))
    try:
        results = load()
        clip = make_clip(work)
        for key in engines_to_test(only):
            t0 = time.monotonic()
            if clip is None:
                problem = "couldn't create the test clip (is ffmpeg installed?)"
            else:
                try:
                    if key == "handbrake":
                        problem = _test_handbrake(clip, work)
                    elif key == "classifier":
                        problem = _test_classifier()
                    else:
                        problem = _test_upscaler(key, clip)
                except Exception as e:
                    problem = f"crashed: {e}"
            was_ok = results.get(key, {}).get("ok", True)
            results[key] = {"ok": problem is None, "message": problem or "passed",
                            "seconds": round(time.monotonic() - t0, 1), "at": time.time()}
            if problem:
                optimizarr_logger.app_logger.warning("Engine self-test: %s FAILED — %s", key, problem)
                if was_ok:
                    _notify_failure(key, problem)
            else:
                optimizarr_logger.app_logger.info(
                    "Engine self-test: %s passed (%.1fs)", key, results[key]["seconds"])
        db.set_setting(HEALTH_SETTING, json.dumps(results))
        return results
    finally:
        shutil.rmtree(work, ignore_errors=True)
        _lock.release()


def test_in_background(only: Optional[List[str]] = None):
    threading.Thread(target=run_self_test, args=(only,), daemon=True,
                     name="engine-selftest").start()


def engines_in_use() -> List[str]:
    """Engines the current settings depend on: HandBrake, plus the
    upscaler of every enabled profile / scan root with upscaling on."""
    keys = {"handbrake"}
    for p in db.get_profiles():
        if p.get("upscale_enabled") and p.get("upscale_key"):
            keys.add(p["upscale_key"])
    for r in db.get_scan_roots():
        if r.get("upscale_enabled") and r.get("upscale_key"):
            keys.add(r["upscale_key"])
    if "auto" in keys:
        # "Auto per file" relies on the classifier and both engines it picks
        from app import content_classifier as cc
        keys.discard("auto")
        keys.add("classifier")
        keys.update(cc.auto_choice(c)[0] for c in ("2d", "live"))
    from app import content_classifier as cc
    if cc.enabled():
        keys.add("classifier")
    return sorted(keys)


def problems_in_use() -> List[Dict]:
    """Failing engines that current settings rely on (for the queue page)."""
    health = load()
    return [{"engine": k, "message": health[k]["message"]}
            for k in engines_in_use() if k in health and not health[k].get("ok")]


def start_health_checker():
    """Self-test shortly after startup, then daily (daemon thread)."""
    def _loop():
        time.sleep(STARTUP_DELAY_SECONDS)
        while True:
            try:
                run_self_test()
            except Exception as e:
                optimizarr_logger.app_logger.warning("Engine self-test failed to run: %s", e)
            time.sleep(INTERVAL_SECONDS)

    threading.Thread(target=_loop, daemon=True, name="engine-health").start()
