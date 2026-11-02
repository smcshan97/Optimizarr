"""
HandBrakeCLI helpers: progress parsing (--json and legacy text), source
preflight, and encoder / hardware detection from ``HandBrakeCLI --help``.
"""
import re
import subprocess
from pathlib import Path
from typing import Optional, Dict


# HandBrakeCLI progress line, e.g.:
#   Encoding: task 1 of 1, 23.45 % (113.06 fps, avg 102.33 fps, ETA 00h12m34s)
# The fps/ETA group is absent on the first few lines of an encode.
# Kept as a fallback — the primary path is now the --json stream below.
HB_PROGRESS_RE = re.compile(
    r'Encoding: task \d+ of \d+, ([\d.]+) %'
    r'(?:\s*\(([\d.]+) fps, avg ([\d.]+) fps, ETA (\d+)h(\d+)m(\d+)s\))?'
)


class HBJsonStream:
    """Incremental parser for HandBrakeCLI --json output.

    With --json, HandBrake emits labelled multi-line JSON blocks like:

        Progress: {
            "State": "WORKING",
            "Working": { "Progress": 0.1234, "Rate": 45.6, "ETASeconds": 321, ... }
        }

    feed() consumes one line at a time and returns (label, dict) when a block
    completes, else None. Brace depth is tracked with JSON string/escape
    awareness so braces inside string values can't derail it.
    """

    _START_RE = re.compile(r'^(\w[\w ]*):\s*(\{.*)$')

    def __init__(self):
        self._label = None
        self._buf: list = []
        self._depth = 0
        self._in_str = False
        self._esc = False

    def feed(self, line: str):
        if self._label is None:
            m = self._START_RE.match(line)
            if not m:
                return None
            self._label = m.group(1)
            self._buf = []
            self._depth = 0
            self._in_str = False
            self._esc = False
            return self._consume(m.group(2))
        return self._consume(line)

    def _consume(self, chunk: str):
        self._buf.append(chunk)
        for ch in chunk:
            if self._esc:
                self._esc = False
                continue
            if ch == '\\' and self._in_str:
                self._esc = True
            elif ch == '"':
                self._in_str = not self._in_str
            elif not self._in_str:
                if ch == '{':
                    self._depth += 1
                elif ch == '}':
                    self._depth -= 1
                    if self._depth == 0:
                        import json as _json
                        label, text = self._label, ''.join(self._buf)
                        self._label = None
                        try:
                            return label, _json.loads(text)
                        except ValueError:
                            return None
        return None


def parse_hb_json_progress(label: str, data: dict):
    """Map a --json Progress block to (percent, fps, eta_seconds) or None."""
    if label != 'Progress' or not isinstance(data, dict):
        return None
    state = data.get('State')
    if state == 'WORKING':
        w = data.get('Working') or {}
        try:
            pct = float(w.get('Progress') or 0) * 100.0
        except (TypeError, ValueError):
            return None
        fps = w.get('Rate') or None
        eta = w.get('ETASeconds') or None
        return (max(0.0, min(100.0, pct)),
                float(fps) if fps else None,
                int(eta) if eta else None)
    if state == 'MUXING':
        return (100.0, None, None)
    return None


def preflight_source_check(file_path: str) -> Optional[str]:
    """Validate an encode source before committing any work to it.

    Returns:
      None       — file looks encodable
      'missing'  — file no longer exists on disk (caller should drop it)
      other str  — human-readable reason the file is unreadable/incomplete
                   (caller should quarantine it, without touching the file)
    """
    from app.winpath import fs_path
    # Long-path form: past 260 characters a present file reads as missing,
    # and 'missing' drops the queue row.
    p = Path(fs_path(file_path))
    if not p.exists():
        return 'missing'
    try:
        if p.stat().st_size == 0:
            return 'File is empty (0 bytes)'
    except OSError as e:
        return f'File is unreadable: {e}'

    from app.scanner import scanner as _scanner
    if not (_scanner.ffprobe_available or _scanner.handbrake_available):
        return None  # no probe tool available — can't validate, let HandBrake try

    try:
        specs = _scanner.analyze_file(file_path) or {}
    except Exception:
        specs = {}
    if (specs.get('codec', 'unknown') == 'unknown'
            and specs.get('resolution', 'unknown') == 'unknown'):
        return ('No decodable video stream found — file is corrupt, incomplete, '
                'or still downloading')
    return None


def parse_hb_progress(line: str):
    """Parse a HandBrakeCLI progress line.

    Returns (percent, current_fps, eta_seconds) — fps/eta are None when
    HandBrake hasn't printed them yet — or None for non-progress lines.
    """
    m = HB_PROGRESS_RE.search(line)
    if not m:
        return None
    pct = float(m.group(1))
    fps = float(m.group(2)) if m.group(2) else None
    eta = None
    if m.group(4) is not None:
        eta = int(m.group(4)) * 3600 + int(m.group(5)) * 60 + int(m.group(6))
    return pct, fps, eta


_version_cache: Dict[str, tuple] = {}


def handbrake_version() -> tuple:
    """(major, minor, patch) of the HandBrakeCLI in use; (0,) if unknown."""
    binary = handbrake_cli()
    if binary not in _version_cache:
        version = (0,)
        try:
            r = subprocess.run([binary, "--version"], capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=15)
            m = re.search(r"HandBrake (\d+)\.(\d+)\.(\d+)", (r.stdout or "") + (r.stderr or ""))
            if m:
                version = tuple(int(x) for x in m.groups())
        except Exception:
            pass
        _version_cache[binary] = version
    return _version_cache[binary]


def handbrake_cli() -> str:
    """Path of the HandBrakeCLI to run.

    An Optimizarr-managed copy (Settings → AI Upscalers → HandBrakeCLI,
    newest version folder) wins over the one on PATH, so HandBrake can be
    updated without admin rights or a package manager. Falls back to the
    bare name, resolved through PATH by the OS.
    """
    try:
        from app.upscaler import _find_binary
        return _find_binary("handbrake") or "HandBrakeCLI"
    except Exception:
        return "HandBrakeCLI"


# Cached per HandBrakeCLI binary: build_command calls this for every encode,
# and a given binary's encoder list can't change. Keyed by path so installing
# a newer managed copy is picked up without a restart.
_hw_accel_cache: Optional[Dict] = None
_hw_accel_cache_path: Optional[str] = None


def parse_hb_encoder_list(help_text: str) -> list:
    """Video encoder names from ``HandBrakeCLI --help``.

    The list sits under the ``-e, --encoder <string>`` option, one name per
    line, and ends at the next option line. Returns [] if not found.
    """
    names, in_list = [], False
    for line in help_text.splitlines():
        stripped = line.strip()
        if not in_list:
            if '--encoder <string>' in line:
                in_list = True
            continue
        if not stripped or stripped.startswith('-'):
            break
        if re.fullmatch(r'[A-Za-z0-9_]+', stripped):
            names.append(stripped)
        else:
            break
    return names


def detect_hardware_acceleration() -> Dict:
    """Detect available hardware encoders by querying HandBrakeCLI (cached)."""
    global _hw_accel_cache, _hw_accel_cache_path
    binary = handbrake_cli()
    if _hw_accel_cache is not None and _hw_accel_cache_path in (None, binary):
        return _hw_accel_cache
    available = {
        "nvidia": False,
        "amd": False,
        "intel": False,
        "apple": False,
        "encoders": [],
        "details": {}
    }
    
    try:
        result = subprocess.run(
            [binary, "--help"],
            capture_output=True,
            text=True,
            timeout=10,
            encoding='utf-8',
            errors='replace'
        )
        
        raw_output = (result.stdout or '') + (result.stderr or '')
        names = parse_hb_encoder_list(raw_output)
        available["encoder_names"] = names
        # Prefer the exact encoder list: a bare substring search matched
        # option text (e.g. "--enable-qsv-decoding") and reported Intel
        # QuickSync on machines without it. Substring search stays only as
        # a fallback for help output we can't parse.
        tokens = set(names) if names else None
        output = raw_output.lower()

        def has(name):
            return name in tokens if tokens is not None else name in output

        for vendor, prefix, label in (
            ("nvidia", "nvenc", "NVENC (NVIDIA)"),
            ("amd", "vce", "VCE (AMD)"),
            ("intel", "qsv", "QuickSync (Intel)"),
            ("apple", "vt", "VideoToolbox (Apple)"),
        ):
            detail = {c: has(f"{prefix}_{c}") for c in ("h264", "h265", "av1")}
            if any(detail.values()):
                available[vendor] = True
                available["encoders"].append(label)
                available["details"][vendor] = detail

    except FileNotFoundError:
        available["error"] = "HandBrakeCLI not found"
    except subprocess.TimeoutExpired:
        available["error"] = "HandBrakeCLI timed out"
    except Exception as e:
        available["error"] = str(e)

    # Don't cache a probe failure — HandBrake may be mid-install/on a flaky
    # network mount; retry next call.
    if "error" not in available:
        _hw_accel_cache = available
        _hw_accel_cache_path = binary
    return available
