"""
Target-quality encoding: pick the encoder quality per file from a VMAF goal.

The ab-av1 approach: cut a few short samples from the source (stream copy,
so they start on keyframes and serve as an exact VMAF reference), encode
them at a candidate quality with the profile's real settings, score them
with VMAF against the samples, and binary-search the quality value. The
result is the most compression that still meets the target score — so a
clean Blu-ray and a noisy TV rip each get the setting they need instead of
one fixed CRF for everything.

The same samples also predict the output size, so a file that wouldn't
shrink enough is skipped before the full encode — or, for a file that must
be converted anyway (it doesn't meet its profile), the quality is raised
until the output is no bigger than the source ("don't grow"). Measured on
this library: the median queued file is a lean 1.8 Mbps H.264 that grew
53-80% at a fixed CRF 28.

XPSNR is scored in the same pass and logged/stored next to VMAF: VMAF can be
inflated by sharpening, XPSNR (ISO/IEC 23002-7) is a plain perceptually
weighted PSNR, so a big disagreement between them is worth a look.
"""
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from app.logger import optimizarr_logger

SAMPLE_COUNT = 3
SAMPLE_SECONDS = 10
MIN_DURATION = 60           # shorter files just use the profile quality
MAX_ITERATIONS = 6

# HandBrake --quality ranges searched per encoder family (higher = smaller,
# lower quality for all of these). VideoToolbox runs the other way and is
# not supported.
_RANGES = (
    ("svt_av1", (15, 55)),
    ("x265", (14, 34)),
    ("x264", (14, 34)),
    ("nvenc", (15, 40)),
    ("qsv", (15, 40)),
    ("vce", (15, 40)),
)

_CREATION = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def quality_range(hb_encoder: str) -> Optional[Tuple[int, int]]:
    for prefix, rng in _RANGES:
        if hb_encoder.startswith(prefix):
            return rng
    return None


def sample_starts(duration: float, count: int = SAMPLE_COUNT,
                  length: int = SAMPLE_SECONDS) -> List[float]:
    """Evenly spread sample start times, avoiding the very start and end."""
    usable = max(0.0, duration - length)
    return [round(usable * (i + 1) / (count + 1), 2) for i in range(count)]


def cut_samples(source: str, work: Path, duration: float,
                register: Callable) -> List[Path]:
    """Stream-copy video-only samples (keyframe-aligned) from the source."""
    paths = []
    for i, start in enumerate(sample_starts(duration)):
        out = work / f"sample_{i}.mkv"
        cmd = ["ffmpeg", "-v", "error", "-y", "-ss", str(start), "-i", source,
               "-t", str(SAMPLE_SECONDS), "-map", "0:V:0", "-c", "copy", str(out)]
        if _run(cmd, register, timeout=300) == 0 and out.exists() and out.stat().st_size:
            paths.append(out)
    return paths


def vmaf_score(distorted: Path, reference: Path, width: int, height: int,
               register: Callable) -> Optional[float]:
    """Mean VMAF only (see quality_scores)."""
    return quality_scores(distorted, reference, width, height, register)[0]


def quality_scores(distorted: Path, reference: Path, width: int, height: int,
                   register: Callable) -> Tuple[Optional[float], Optional[float]]:
    """(mean VMAF, XPSNR luma dB) of ``distorted`` against ``reference`` in
    one decode (scaled to the reference size; both compared as 10-bit so bit
    depth doesn't skew it).

    Frames are paired by ORDER, not timestamp: libvmaf syncs on pts, and
    HandBrake re-times irregular source timestamps slightly, which paired
    neighbouring frames and dragged scores down (88.6 vs 92.7 measured).
    """
    threads = max(1, (os.cpu_count() or 4) // 2)
    graph = (f"[0:v]scale={width}:{height}:flags=bicubic,format=yuv420p10le,"
             f"setpts=N/(25*TB),split[d][d2];"
             f"[1:v]format=yuv420p10le,setpts=N/(25*TB),split[r][r2];"
             f"[d][r]libvmaf=n_threads={threads};[d2][r2]xpsnr")
    proc = subprocess.Popen(
        ["ffmpeg", "-hide_banner", "-i", str(distorted), "-i", str(reference),
         "-lavfi", graph, "-f", "null", "-"],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
        encoding="utf-8", errors="replace", creationflags=_CREATION)
    register(proc)
    try:
        _, err = proc.communicate(timeout=1800)
    finally:
        register(None)
    m = re.search(r"VMAF score:\s*([\d.]+)", err or "")
    x = re.search(r"XPSNR\s+y:\s*([\d.]+)", err or "")
    return (float(m.group(1)) if m else None, float(x.group(1)) if x else None)


def _run(cmd: list, register: Callable, timeout: int) -> int:
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            stdin=subprocess.DEVNULL, creationflags=_CREATION)
    register(proc)
    try:
        return proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        return -1
    finally:
        register(None)


def search(evaluate: Callable[[int], Optional[Tuple]],
           lo: int, hi: int, start: int, target: float,
           max_iter: int = MAX_ITERATIONS,
           max_size_ratio: Optional[float] = None) -> Dict:
    """Largest quality value whose VMAF >= ``target``.

    ``evaluate(q)`` returns (vmaf, size_ratio[, xpsnr]) or None on failure.
    VMAF falls as q rises, so this is a binary search on a monotonic
    function, started at the profile's own value and bounded to
    ``max_iter`` evaluations.

    ``max_size_ratio`` ("don't grow"): if the chosen value's output would be
    bigger than that fraction of the source, keep raising q (smaller, lower
    quality) to the first value that fits — size falls as q rises too.
    """
    seen: Dict[int, Tuple[float, float]] = {}
    start = max(lo, min(hi, start))
    best_ok: Optional[int] = None      # largest q meeting the target
    worst_fail: Optional[int] = None   # smallest q missing it
    q = start
    for _ in range(max_iter):
        if q in seen:
            break
        res = evaluate(q)
        if res is None:
            break
        seen[q] = res
        if res[0] >= target:
            best_ok = q if best_ok is None else max(best_ok, q)
        else:
            worst_fail = q if worst_fail is None else min(worst_fail, q)
        low = best_ok if best_ok is not None else lo - 1
        high = worst_fail if worst_fail is not None else hi + 1
        if high - low <= 1:
            break
        if best_ok is None and q == lo:
            break                       # even the best quality misses: give up
        if best_ok is None and res[1] >= 1.0:
            # Still short of the target while already no smaller than the
            # source: better quality only grows it further, so the file will
            # be skipped anyway — stop spending encodes on it.
            break
        if worst_fail is None and q == hi:
            break                       # even the smallest passes
        if best_ok is None:
            q = max(lo, (lo + q) // 2 if q > lo else lo)
            if worst_fail is not None and q >= worst_fail:
                q = worst_fail - 1
        elif worst_fail is None:
            q = min(hi, (q + hi + 1) // 2)
        else:
            q = (best_ok + worst_fail) // 2
        q = max(lo, min(hi, q))
    if best_ok is None:
        chosen = min(seen) if seen else start          # closest we could get
    else:
        chosen = best_ok
    size_limited = False
    if max_size_ratio is not None and chosen in seen and seen[chosen][1] > max_size_ratio:
        chosen = _fit_size(evaluate, seen, chosen, hi, max_size_ratio, max_iter)
        size_limited = True
    res = seen.get(chosen, (None, None))
    return {"quality": chosen, "vmaf": res[0], "size_ratio": res[1],
            "xpsnr": res[2] if len(res) > 2 else None,
            "met_target": best_ok is not None and not size_limited,
            "size_limited": size_limited, "evaluations": seen}


def _fit_size(evaluate, seen: Dict, too_big: int, hi: int, limit: float,
              max_iter: int) -> int:
    """Smallest q above ``too_big`` whose size ratio <= ``limit`` (binary
    search, reusing evaluations already made). If nothing fits within the
    budget, the q with the smallest output found."""
    fits = [q for q, r in seen.items() if q > too_big and r[1] <= limit]
    low, high = too_big, (min(fits) if fits else None)
    for _ in range(max_iter):
        if high is not None and high - low <= 1:
            break
        if high is not None:
            q = (low + high) // 2
        else:
            q = min(hi, low + max(4, (hi - low) // 2))
        if q in seen:
            res = seen[q]
        else:
            res = evaluate(q)
            if res is None:
                break
            seen[q] = res
        if res[1] <= limit:
            high = q
        else:
            low = q
            if q == hi:
                break
    if high is not None:
        return high
    return min(seen, key=lambda k: (seen[k][1], -k)) if seen else too_big


def find_quality(*, source: str, duration: float, width: int, height: int,
                 hb_encoder: str, start_quality: int, target: float,
                 encode_sample: Callable[[Path, int, Path], bool],
                 register: Callable,
                 progress: Callable[[float], None],
                 max_size_ratio: Optional[float] = None) -> Optional[Dict]:
    """Run the whole search for one file. None = not applicable / failed
    (the caller then encodes with the profile's own quality)."""
    rng = quality_range(hb_encoder)
    if rng is None or duration < MIN_DURATION or width <= 0 or height <= 0:
        return None
    work = Path(tempfile.mkdtemp(prefix="optimizarr_tq_"))
    try:
        samples = cut_samples(source, work, duration, register)
        if len(samples) < 2:
            optimizarr_logger.app_logger.warning("Target quality: couldn't cut samples")
            return None
        ref_bytes = sum(p.stat().st_size for p in samples)
        step = [0]

        def evaluate(q: int):
            scores, xps, out_bytes = [], [], 0
            for i, ref in enumerate(samples):
                out = work / f"enc_{q}_{i}.mkv"
                if not encode_sample(ref, q, out) or not out.exists():
                    return None
                out_bytes += out.stat().st_size
                score, xp = quality_scores(out, ref, width, height, register)
                if score is None:
                    return None
                scores.append(score)
                if xp is not None:
                    xps.append(xp)
            step[0] += 1
            progress(min(95.0, step[0] / (MAX_ITERATIONS * 2) * 100))
            vmaf = sum(scores) / len(scores)
            xpsnr = sum(xps) / len(xps) if xps else None
            optimizarr_logger.app_logger.info(
                "Target quality: q=%d -> VMAF %.2f (samples %s), XPSNR %s, size %.0f%% of source video",
                q, vmaf, ", ".join(f"{s:.1f}" for s in scores),
                f"{xpsnr:.2f} dB" if xpsnr is not None else "n/a", out_bytes / ref_bytes * 100)
            return vmaf, out_bytes / ref_bytes, xpsnr

        result = search(evaluate, rng[0], rng[1], start_quality, target,
                        max_size_ratio=max_size_ratio)
        if not result["evaluations"]:
            return None
        progress(100.0)
        return result
    finally:
        shutil.rmtree(work, ignore_errors=True)
