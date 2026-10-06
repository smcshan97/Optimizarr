"""
Encoder module for Optimizarr.
Handles video transcoding using HandBrakeCLI with progress tracking.
"""
import subprocess
import re
import shlex
import time
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict, Callable
import os
import shutil
import signal
import platform

from app.database import db
from app.resources import resource_monitor, resource_throttler
from app.timeutil import utc_now
from app.logger import optimizarr_logger


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
    p = Path(file_path)
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


class EncodingJob:
    """Represents a single video encoding job."""
    
    def __init__(self, queue_item_id: int, profile: Dict, resource_limits: Optional[Dict] = None):
        self.queue_item_id = queue_item_id
        self.profile = profile
        self.process: Optional[subprocess.Popen] = None
        # Active pre-phase subprocess (ffmpeg/upscaler) — registered by the
        # upscale pipeline so stop() can kill it; HandBrake uses self.process.
        self.pipeline_process: Optional[subprocess.Popen] = None
        self.progress = 0.0
        self.is_paused = False
        self.paused_reason = None
        self.monitoring_thread = None
        self.stop_monitoring = False
        self._manually_stopped = False
        # Honest encode timing: monotonic clock around the HandBrake phase
        # only, minus time spent thermally paused. The old completed_at −
        # started_at wall clock swallowed upscale phases and pauses, teaching
        # the speed learner ratios like 4800× realtime.
        self._hb_started_monotonic: Optional[float] = None
        self._paused_seconds = 0.0
        self._paused_at_monotonic: Optional[float] = None
        self._preprocessed = False
        # Megapixel-frames of AI work actually performed, recorded to history
        # so the estimator learns seconds-per-megapixel-frame. Stays 0 unless
        # an upscale genuinely ran — a failed upscale that fell back to a
        # plain encode must not teach that the work was free.
        self._upscale_work_mpf = 0.0
        
        # Resource limits — temperature-first defaults
        self.resource_limits = resource_limits or {
            'enable_throttling': True,
            'nice_level': 10,
            # Primary triggers (temperature)
            'pause_on_cpu_temp': True,
            'cpu_temp_threshold': 85.0,
            'pause_on_gpu_temp': True,
            'gpu_temp_threshold': 83.0,
            # Secondary triggers (optional)
            'pause_on_memory': False,
            'memory_threshold': 85.0,
            'pause_on_cpu_usage': False,
            'cpu_usage_threshold': 95.0,
        }
        
        # Get queue item details
        self.queue_item = db.get_queue_item(queue_item_id)
        
        if not self.queue_item:
            raise ValueError(f"Queue item {queue_item_id} not found")
    
    def build_command(self, input_override: str = None, size_override=None) -> list:
        """Build HandBrakeCLI command from profile settings.
        
        Args:
            input_override: If provided, use this path as the input instead of
                            the queue item's file_path (used for upscaled temp files).
            size_override: (width, height) to scale to when the profile sets no
                           resolution — an upscale whose model overshot the
                           requested factor is brought down here.
        """
        input_file = input_override if input_override else self.queue_item['file_path']
        original_file = self.queue_item['file_path']

        # Determine container format and output extension
        container = self.profile.get('container', 'mkv')
        container_map = {
            'mkv': {'ext': '.mkv', 'format': 'av_mkv'},
            'mp4': {'ext': '.mp4', 'format': 'av_mp4'},
            'webm': {'ext': '.webm', 'format': 'av_webm'},
        }
        container_info = container_map.get(container, container_map['mkv'])

        # Output always goes next to the ORIGINAL file, not the temp upscale file
        original_path = Path(original_file)
        output_ext = container_info['ext']
        output_file = str(original_path.parent / f"{original_path.stem}_optimized{output_ext}")

        # Base command. --json emits structured Progress/scan blocks instead
        # of the human text lines — exact progress/fps/ETA, immune to output
        # format drift between HandBrake versions.
        cmd = ['HandBrakeCLI', '--json', '-i', input_file, '-o', output_file]

        # Container format
        cmd.extend(['--format', container_info['format']])
        
        # ---- Encoder Selection (HW accel + 10-bit/HDR) ----
        hb_encoder = self.select_encoder()
        cmd.extend(['--encoder', hb_encoder])

        # Hardware DECODING (independent of the encoder choice): NVDEC/QSV
        # offloads source decode to the GPU, freeing CPU for software encoders
        # like SVT-AV1. HandBrake falls back to software decoding on its own
        # for sources the hardware can't handle.
        if self.profile.get('hw_accel_enabled'):
            hw = detect_hardware_acceleration()
            if hw.get('nvidia'):
                cmd.extend(['--enable-hw-decoding', 'nvdec'])
            elif hw.get('intel'):
                cmd.extend(['--enable-hw-decoding', 'qsv'])
        
        # Quality
        cmd.extend(['--quality', str(self.profile['quality'])])
        
        # Preset
        if self.profile.get('preset'):
            cmd.extend(['--encoder-preset', self.profile['preset']])
        
        # Resolution
        if self.profile.get('resolution'):
            width, height = self.profile['resolution'].split('x')
            cmd.extend(['--width', width, '--height', height])
        elif size_override:
            cmd.extend(['--width', str(size_override[0]),
                        '--height', str(size_override[1])])
        
        # Framerate
        if self.profile.get('framerate'):
            cmd.extend(['--rate', str(self.profile['framerate'])])
        else:
            # Variable framerate (preserve source)
            cmd.append('--vfr')
        
        # ---- Audio Handling ----
        audio_args = self._build_audio_args()
        cmd.extend(audio_args)
        
        # ---- Subtitle Handling ----
        subtitle_args = self._build_subtitle_args()
        cmd.extend(subtitle_args)
        
        # ---- Video Filters ----
        if self.profile.get('enable_filters'):
            filter_args = self._build_filter_args()
            cmd.extend(filter_args)
        
        # ---- Chapter Markers ----
        if self.profile.get('chapter_markers', True):
            cmd.append('--markers')
        
        # Film-grain mode. Grain is noise to an encoder: preserving it eats
        # bits, smoothing it looks plasticky. SVT-AV1 can strip it and
        # re-synthesise it on playback (film-grain=N); x265 has a grain tune.
        grain = int(self.profile.get('film_grain') or 0)
        if grain > 0:
            if hb_encoder.startswith('svt_av1'):
                cmd.extend(['--encopts',
                            f'film-grain={min(grain, 50)}:film-grain-denoise=0'])
            elif hb_encoder.startswith('x265'):
                # x265's grain tune segfaults HandBrake (1.10.2 verified)
                # with the ultrafast/superfast presets — skip it there.
                if (self.profile.get('preset') or '') not in ('ultrafast', 'superfast'):
                    cmd.extend(['--encoder-tune', 'grain'])

        # (two_pass is intentionally not emitted: HandBrake's --multi-pass
        # only applies to average-bitrate encodes and every profile here is
        # constant-quality --quality, so it did nothing but imply otherwise.)
        
        # Custom arguments (always last so they can override).
        # shlex keeps quoted values intact (--encoder-options "a=1:b=2 c") but
        # POSIX-mode would eat backslashes in bare Windows paths — so only use
        # it when the string actually contains quotes.
        if self.profile.get('custom_args'):
            raw_args = self.profile['custom_args']
            if '"' in raw_args or "'" in raw_args:
                try:
                    custom_args = shlex.split(raw_args)
                except ValueError:
                    custom_args = raw_args.split()
            else:
                custom_args = raw_args.split()
            cmd.extend(custom_args)
        
        return cmd
    
    def _build_audio_args(self) -> list:
        """Build audio arguments based on audio handling strategy."""
        strategy = self.profile.get('audio_handling', 'preserve_all')
        audio_codec = self.profile.get('audio_codec', 'aac')
        
        # Map our codec names to HandBrake names
        hb_audio_map = {
            'aac': 'av_aac',
            'opus': 'opus',
            'ac3': 'ac3',
            'flac': 'flac24',
            'passthrough': 'copy',
        }
        hb_codec = hb_audio_map.get(audio_codec, 'av_aac')
        
        if strategy == 'preserve_all':
            # Keep all audio tracks, passthrough originals
            return [
                '--audio', '1,2,3,4,5,6,7,8,9,10',
                '--aencoder', 'copy',
                '--audio-fallback', 'av_aac',
            ]
        
        elif strategy == 'keep_primary':
            # Keep only first track, encode with selected codec
            if audio_codec == 'passthrough':
                return ['--audio', '1', '--aencoder', 'copy']
            return ['--audio', '1', '--aencoder', hb_codec]
        
        elif strategy == 'stereo_mixdown':
            # Downmix to stereo AAC
            return [
                '--audio', '1',
                '--aencoder', 'av_aac',
                '--ab', '192',
                '--mixdown', 'stereo',
            ]
        
        elif strategy == 'hd_plus_aac':
            # Keep original HD audio + add AAC stereo fallback
            return [
                '--audio', '1,1',
                '--aencoder', 'copy,av_aac',
                '--audio-fallback', 'av_aac',
                '--ab', '0,192',
                '--mixdown', ',stereo',
            ]
        
        elif strategy == 'high_quality':
            # Single high-quality AAC at 256kbps
            return [
                '--audio', '1',
                '--aencoder', 'av_aac',
                '--ab', '256',
                '--mixdown', 'stereo',
            ]
        
        else:
            # Fallback: basic single track
            if audio_codec == 'passthrough':
                return ['--audio', '1', '--aencoder', 'copy']
            return ['--audio', '1', '--aencoder', hb_codec]
    
    def _build_subtitle_args(self) -> list:
        """Build subtitle arguments based on subtitle handling strategy."""
        strategy = self.profile.get('subtitle_handling', 'none')
        container = self.profile.get('container', 'mkv')
        
        if strategy == 'preserve_all':
            args = ['--subtitle', '1,2,3,4,5,6,7,8,9,10']
            if container == 'mp4':
                # MP4 has limited subtitle support
                args.append('--subtitle-default=none')
            return args
        
        elif strategy == 'keep_english':
            return [
                '--subtitle-lang-list', 'eng',
                '--all-subtitles',
            ]
        
        elif strategy == 'burn_in':
            return [
                '--subtitle', '1',
                '--subtitle-burned',
            ]
        
        elif strategy == 'foreign_scan':
            return [
                '--subtitle', 'scan',
                '--subtitle-forced',
            ]
        
        elif strategy == 'none':
            # No subtitle flags = no subtitles in output
            return []
        
        return []
    
    def _build_filter_args(self) -> list:
        """Build auto-detect video filter arguments."""
        filters = []
        
        # Comb detection + decomb (for interlaced content)
        filters.extend(['--comb-detect', '--decomb'])
        
        # Light denoise (safe for all content)
        filters.append('--nlmeans=light')
        
        # Auto crop (remove black bars)
        filters.append('--crop-mode=auto')
        
        return filters
    
    def _get_hw_encoder(self, current_encoder: str) -> Optional[str]:
        """Map the profile's codec to a hardware encoder HandBrake offers.

        HandBrake only lists a hardware encoder when the installed GPU can
        actually run it (e.g. nvenc_av1 is absent on pre-RTX-40 cards), so
        its encoder list is the authority — AV1 now goes to NVENC/QSV/VCN
        on GPUs that support it. If the list couldn't be read, fall back to
        the codecs every GPU generation supports (H.264/H.265).
        """
        hw = detect_hardware_acceleration()
        names = set(hw.get('encoder_names') or [])
        codec = self.profile.get('codec', 'av1')

        for vendor, prefix in (('nvidia', 'nvenc'), ('intel', 'qsv'),
                               ('amd', 'vce'), ('apple', 'vt')):
            if not hw.get(vendor):
                continue
            candidate = f"{prefix}_{codec}"
            if names:
                if candidate in names:
                    return candidate
            elif codec in ('h264', 'h265'):
                return candidate

        # No hardware encoder for this codec — keep the software encoder
        return None

    def source_video_info(self) -> Dict:
        """Bit depth / HDR info for the ORIGINAL source (cached per job).

        Scan-queued items carry it in current_specs; webhook/sync items only
        have Sonarr/Radarr's codec+resolution, so probe once here.
        """
        if getattr(self, '_source_info', None) is not None:
            return self._source_info
        specs = self.queue_item.get('current_specs') or {}
        if isinstance(specs, str):
            import json as _json
            try:
                specs = _json.loads(specs)
            except ValueError:
                specs = {}
        if 'bit_depth' in specs:
            info = specs
        else:
            try:
                from app.scanner import scanner
                info = scanner.probe_video_color(self.queue_item['file_path'])
            except Exception:
                info = {}
        self._source_info = info
        return info

    def select_encoder(self) -> str:
        """Choose the HandBrake encoder name for this job.

        1. Hardware encoder if the profile enables it and one exists.
        2. 10-bit: when the source is HDR or >8-bit, use the encoder's
           ``_10bit`` variant — an 8-bit encode of an HDR source drops the
           HDR metadata and comes out washed out. H.264 is left alone (10-bit
           H.264 barely plays anywhere and can't carry HDR anyway).
        3. A ``*_10bit`` encoder HandBrake doesn't offer falls back to its
           8-bit base rather than failing the encode.
        """
        encoder = self.profile['encoder']
        if self.profile.get('hw_accel_enabled'):
            encoder = self._get_hw_encoder(encoder) or encoder

        names = set(detect_hardware_acceleration().get('encoder_names') or [])
        base = encoder[:-6] if encoder.endswith('_10bit') else encoder
        info = self.source_video_info()
        wants_10bit = (encoder.endswith('_10bit') or bool(info.get('hdr'))
                       or (info.get('bit_depth') or 8) > 8)
        is_h264 = base == 'x264' or base.endswith('h264')

        chosen = encoder
        if wants_10bit and not is_h264:
            ten = f"{base}_10bit"
            if not names or ten in names:
                chosen = ten
            else:
                chosen = base
        if names and chosen not in names and chosen.endswith('_10bit'):
            chosen = chosen[:-6]

        if chosen != self.profile['encoder']:
            reason = []
            if info.get('hdr'):
                reason.append('HDR source')
            elif (info.get('bit_depth') or 8) > 8:
                reason.append(f"{info.get('bit_depth')}-bit source")
            if self.profile.get('hw_accel_enabled') and not chosen.startswith(
                    self.profile['encoder'].replace('_10bit', '')):
                reason.append('hardware acceleration')
            try:
                from app.logger import optimizarr_logger
                optimizarr_logger.app_logger.info(
                    "Encoder %s -> %s (%s)", self.profile['encoder'], chosen,
                    ', '.join(reason) or 'availability')
            except Exception:
                pass
        if info.get('hdr') and is_h264:
            try:
                from app.logger import optimizarr_logger
                optimizarr_logger.app_logger.warning(
                    "HDR source encoded with an H.264 profile — HDR will be lost: %s",
                    self.queue_item.get('file_path'))
            except Exception:
                pass
        return chosen
    
    def _monitor_resources(self):
        """Background thread to monitor resources and pause/resume if needed."""
        optimizarr_logger.app_logger.info("  Starting resource monitoring thread...")
        
        while not self.stop_monitoring and self.process and self.process.poll() is None:
            time.sleep(5)  # Check every 5 seconds
            
            if not self.resource_limits.get('enable_throttling'):
                continue
            
            # Build kwargs matching ResourceMonitor.check_thresholds() signature
            throttle_kwargs = {
                'pause_on_cpu_temp':    self.resource_limits.get('pause_on_cpu_temp', True),
                'cpu_temp_threshold':   self.resource_limits.get('cpu_temp_threshold', 85.0),
                'pause_on_gpu_temp':    self.resource_limits.get('pause_on_gpu_temp', True),
                'gpu_temp_threshold':   self.resource_limits.get('gpu_temp_threshold', 83.0),
                'pause_on_memory':      self.resource_limits.get('pause_on_memory', False),
                'memory_threshold':     self.resource_limits.get('memory_threshold', 85.0),
                'pause_on_cpu_usage':   self.resource_limits.get('pause_on_cpu_usage', False),
                'cpu_usage_threshold':  self.resource_limits.get('cpu_usage_threshold', 95.0),
            }
            
            # Check if we should pause
            should_pause, reason = resource_throttler.should_pause_encoding(**throttle_kwargs)
            
            if should_pause and not self.is_paused:
                optimizarr_logger.app_logger.info(f"\n⏸ Pausing encoding: {reason}")
                self.pause(reason)
            elif not should_pause and self.is_paused:
                optimizarr_logger.app_logger.info(f"\n▶ Resuming encoding (resources available)")
                self.resume()
            
            # Update resource usage in database
            if self.process:
                process_resources = resource_monitor.get_process_resources(self.process.pid)
                if process_resources:
                    db.update_queue_item(
                        self.queue_item_id,
                        current_cpu_percent=process_resources['cpu_percent'],
                        current_memory_mb=process_resources['memory_mb']
                    )
    
    def start(self, progress_callback: Optional[Callable[[float], None]] = None):
        """
        Start the encoding job.

        Three-phase pipeline (each phase optional):
          1. Stereo conversion (iw3 2D→3D or ffmpeg 3D→2D)
          2. AI upscale (Real-ESRGAN / etc.)
          3. HandBrake encode

        Progress mapping:
          - All 3 phases: stereo 0-25%, upscale 25-50%, HandBrake 50-100%
          - 2 phases:     pre-process 0-50%, HandBrake 50-100%
          - HandBrake only: 0-100%

        Args:
            progress_callback: Function to call with progress updates (0.0-100.0)
        """
        import json as _json

        from app.logger import optimizarr_logger
        from app.devlog import devlog

        # ── Pre-flight: validate the source before committing any work ──────
        # Files can vanish (deleted/moved after queueing) or be unreadable
        # (incomplete downloads, corrupt sources). HandBrake fails identically
        # for both ("unrecognized file type"), so catch them here instead of
        # burning retries on a file that can never encode.
        source_path = self.queue_item['file_path']
        problem = preflight_source_check(source_path)
        if problem == 'missing':
            optimizarr_logger.app_logger.warning(
                "Source file no longer exists — removing from queue: %s",
                source_path)
            devlog('enc_gone', id=self.queue_item_id, f=Path(source_path).name)
            db.delete_queue_item(self.queue_item_id)
            return False
        if problem is not None:
            # Non-invasive quarantine: the file stays exactly where it is;
            # only the queue row is flagged. Re-check from the UI re-probes it.
            optimizarr_logger.app_logger.warning(
                "Quarantined (not encodable): %s — %s", source_path, problem)
            devlog('enc_quarantine', id=self.queue_item_id,
                   f=Path(source_path).name, why=problem[:120])
            db.update_queue_item(
                self.queue_item_id,
                status='quarantined',
                error_message=problem,
                progress=0.0,
                retry_after=None,
            )
            return False

        # ── Pre-flight: destination name must be free ───────────────────────
        # When the profile changes container (.mp4 source → .mkv output) the
        # encode has to land on a name we don't own. Catching a collision here
        # costs a stat() instead of the hours of encoding it would take to hit
        # the same wall in _finalize_encoding.
        _src = Path(source_path)
        _ext = {'mkv': '.mkv', 'mp4': '.mp4', 'webm': '.webm'}.get(
            self.profile.get('container', 'mkv'), '.mkv')
        _dest = _src.parent / f"{_src.stem}{_ext}"
        if (os.path.normcase(str(_dest)) != os.path.normcase(str(_src))
                and _dest.exists()):
            msg = (f"Destination already exists: {_dest.name}. Another file "
                   f"already occupies the encoded name — remove or rename it "
                   f"(they are usually duplicates — see Duplicates) and "
                   f"re-queue.")
            optimizarr_logger.app_logger.warning(
                "Skipping %s — destination %s already exists",
                _src.name, _dest.name)
            devlog('enc_collision', id=self.queue_item_id, f=_src.name,
                   dest=_dest.name)
            db.update_queue_item(
                self.queue_item_id,
                status='failed',
                progress=0.0,
                phase=None,
                phase_progress=0.0,
                phase_eta_seconds=0,
                error_message=msg,
                retry_after=None,
            )
            return False

        db.update_queue_item(
            self.queue_item_id,
            status='processing',
            started_at=utc_now().strftime('%Y-%m-%d %H:%M:%S')
        )

        self._job_started_monotonic = time.monotonic()
        devlog('enc_start', id=self.queue_item_id,
               f=Path(self.queue_item['file_path']).name,
               c=self.profile.get('codec'))

        # ── Determine input file (may be replaced by stereo/upscaled version) ─
        original_input = self.queue_item['file_path']
        encode_input   = original_input       # may be overridden below
        stereo_temp    = None                 # path to clean up after encode
        upscale_temp   = None                 # path to clean up after encode
        upscale_info: Dict = {}               # filled by the upscale pipeline

        # ── Parse plans ──────────────────────────────────────────────────────
        stereo_plan_raw = self.queue_item.get('stereo_plan')
        if stereo_plan_raw:
            try:
                stereo_plan = _json.loads(stereo_plan_raw) if isinstance(stereo_plan_raw, str) else stereo_plan_raw
            except Exception:
                stereo_plan = {}
        else:
            stereo_plan = {}

        upscale_plan_raw = self.queue_item.get('upscale_plan')
        if upscale_plan_raw:
            try:
                upscale_plan = _json.loads(upscale_plan_raw) if isinstance(upscale_plan_raw, str) else upscale_plan_raw
            except Exception:
                upscale_plan = {}
        else:
            upscale_plan = {}

        has_stereo  = stereo_plan.get('enabled', False)
        has_upscale = upscale_plan.get('enabled', False)
        self._preprocessed = bool(has_stereo or has_upscale)

        # ── Calculate progress ranges per phase ──────────────────────────────
        # Three phases: stereo → upscale → handbrake
        # Each active phase gets an equal share of the pre-handbrake half,
        # and HandBrake always gets the final 50%.
        pre_phases = int(has_stereo) + int(has_upscale)
        if pre_phases == 2:
            stereo_start, stereo_end   = 0.0, 25.0
            upscale_start, upscale_end = 25.0, 50.0
        elif pre_phases == 1:
            stereo_start, stereo_end   = 0.0, 50.0    # only if has_stereo
            upscale_start, upscale_end = 0.0, 50.0    # only if has_upscale
        else:
            stereo_start = stereo_end = 0.0
            upscale_start = upscale_end = 0.0

        hb_start = 50.0 if pre_phases > 0 else 0.0
        hb_range = 50.0 if pre_phases > 0 else 100.0

        # ── Stereo phase ─────────────────────────────────────────────────────
        if has_stereo:
            from app.stereo import run_stereo_pipeline, cleanup_stereo_workdir

            s_mode       = stereo_plan.get('mode', '2d_to_3d')
            s_format     = stereo_plan.get('format', 'half_sbs')
            s_divergence = stereo_plan.get('divergence', 2.0)
            s_convergence = stereo_plan.get('convergence', 0.5)
            s_depth_model = stereo_plan.get('depth_model', 'Any_V2_S')

            optimizarr_logger.app_logger.info(
                "Stereo phase: %s (%s → %s)", original_input, s_mode, s_format
            )

            s_range = stereo_end - stereo_start

            db.update_queue_item(self.queue_item_id, phase='stereo',
                                 phase_progress=0.0, phase_eta_seconds=0)

            def _stereo_progress(pct: float, eta: Optional[float] = None):
                mapped = stereo_start + (pct / 100.0) * s_range
                self.progress = mapped
                db.update_queue_item(
                    self.queue_item_id, progress=mapped, phase='stereo',
                    phase_progress=pct,
                    phase_eta_seconds=int(eta) if eta and eta > 0 else 0,
                )
                if progress_callback:
                    progress_callback(mapped)

            stereo_path = run_stereo_pipeline(
                input_path=original_input,
                mode=s_mode,
                stereo_format=s_format,
                divergence=s_divergence,
                convergence=s_convergence,
                depth_model=s_depth_model,
                progress_callback=_stereo_progress,
            )

            if stereo_path:
                encode_input = stereo_path
                stereo_temp  = stereo_path
                optimizarr_logger.app_logger.info(
                    "Stereo complete — continuing from temp: %s", stereo_path
                )
            else:
                optimizarr_logger.app_logger.warning(
                    "Stereo conversion failed for %s — encoding original file instead",
                    original_input,
                )

        if self._manually_stopped:
            # Stopped during the stereo phase — don't start an upscale/encode
            if stereo_temp:
                from app.stereo import cleanup_stereo_workdir
                cleanup_stereo_workdir(stereo_temp)
            return False

        # ── Upscale phase ────────────────────────────────────────────────────
        if has_upscale:
            from app.upscaler import run_upscale_pipeline, cleanup_upscale_workdir

            upscaler_key = upscale_plan.get('upscaler_key', 'realesrgan')
            model        = upscale_plan.get('model', 'realesrgan-x4plus')
            factor       = upscale_plan.get('factor', 2)

            optimizarr_logger.app_logger.info(
                "Upscale phase: %s (×%d, model=%s)", encode_input, factor, model
            )

            u_range = upscale_end - upscale_start
            db.update_queue_item(self.queue_item_id, phase='upscale',
                                 phase_progress=0.0, phase_eta_seconds=0,
                                 current_fps=0, eta_seconds=0)

            def _upscale_progress(pct: float, eta: Optional[float] = None):
                mapped = upscale_start + (pct / 100.0) * u_range
                self.progress = mapped
                # eta_seconds is the row's overall "time left" figure. During
                # the upscale that IS the upscale's ETA plus the encode still
                # to come; the encode part is unknowable until HandBrake
                # starts, so report the AI time and let the phase bar label it.
                db.update_queue_item(
                    self.queue_item_id, progress=mapped, phase='upscale',
                    phase_progress=pct,
                    phase_eta_seconds=int(eta) if eta and eta > 0 else 0,
                    eta_seconds=int(eta) if eta and eta > 0 else 0,
                )
                if progress_callback:
                    progress_callback(mapped)

            def _register_pipeline_proc(proc):
                self.pipeline_process = proc

            upscaled_path = run_upscale_pipeline(
                input_path=encode_input,
                upscaler_key=upscaler_key,
                model=model,
                factor=factor,
                progress_callback=_upscale_progress,
                register_process=_register_pipeline_proc,
                result_info=upscale_info,
            )

            if self._manually_stopped:
                # Stopped mid-upscale — don't fall through and encode anyway
                if stereo_temp:
                    from app.stereo import cleanup_stereo_workdir
                    cleanup_stereo_workdir(stereo_temp)
                return False

            if upscaled_path:
                encode_input  = upscaled_path
                upscale_temp  = upscaled_path
                try:
                    from app.scanner import upscale_work_mpf
                    self._upscale_work_mpf = upscale_work_mpf(
                        self.queue_item.get('current_specs'), upscale_plan)
                except Exception:
                    self._upscale_work_mpf = 0.0
                optimizarr_logger.app_logger.info(
                    "Upscale complete — encoding from temp: %s", upscaled_path
                )
            else:
                optimizarr_logger.app_logger.warning(
                    "Upscale failed for %s — encoding original file instead",
                    original_input,
                )

        # ── Build HandBrake command (encode_input may be stereo/upscaled) ────
        cmd = self.build_command(input_override=encode_input,
                                 size_override=upscale_info.get('target_size'))
        optimizarr_logger.log_handbrake_start(original_input, cmd)

        try:
            self._hb_started_monotonic = time.monotonic()
            db.update_queue_item(self.queue_item_id, phase='encode',
                                 phase_progress=0.0, phase_eta_seconds=0,
                                 eta_seconds=0)
            self.process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                encoding='utf-8',
                errors='replace',
            )

            if self.resource_limits.get('nice_level'):
                resource_throttler.set_process_priority(
                    self.process.pid,
                    self.resource_limits['nice_level']
                )

            if self.resource_limits.get('enable_throttling'):
                self.monitoring_thread = threading.Thread(
                    target=self._monitor_resources, daemon=True
                )
                self.monitoring_thread.start()

            last_lines = []  # Keep last 20 lines for diagnostics
            last_db_write = 0.0
            json_stream = HBJsonStream()

            for line in self.process.stdout:
                line_stripped = line.rstrip()
                if line_stripped:
                    last_lines.append(line_stripped)
                    if len(last_lines) > 20:
                        last_lines.pop(0)
                    optimizarr_logger.log_handbrake_output(line_stripped)

                # Primary: structured --json Progress blocks. Fallback: the
                # legacy "Encoding: task…" regex (older HandBrake or a custom
                # build without JSON output).
                parsed = None
                block = json_stream.feed(line_stripped)
                if block:
                    parsed = parse_hb_json_progress(block[0], block[1])
                if parsed is None:
                    parsed = parse_hb_progress(line)
                if parsed:
                    hb_pct, fps, hb_eta = parsed
                    # Remap HandBrake 0-100 → hb_start to hb_start+hb_range
                    self.progress = hb_start + (hb_pct / 100.0) * hb_range

                    # Throttle DB writes to ~every 2s: HandBrake emits many
                    # progress lines per second and each update_queue_item
                    # takes the global write lock, starving UI reads.
                    now = time.monotonic()
                    if now - last_db_write >= 2.0:
                        last_db_write = now
                        fields = {'progress': self.progress,
                                  'phase': 'encode',
                                  'phase_progress': hb_pct}
                        if fps is not None:
                            fields['current_fps'] = fps
                        if hb_eta is not None:
                            fields['eta_seconds'] = hb_eta
                            fields['phase_eta_seconds'] = hb_eta
                        db.update_queue_item(self.queue_item_id, **fields)

                    if progress_callback:
                        progress_callback(self.progress)

            return_code = self.process.wait()

            if return_code == 0:
                self._finalize_encoding()
                return True
            else:
                # stop() already set status='cancelled' — don't overwrite it
                if self._manually_stopped:
                    return False

                # Include last HandBrake output in error for diagnostics
                tail = '\n'.join(last_lines[-5:]) if last_lines else '(no output captured)'
                optimizarr_logger.log_handbrake_error(
                    original_input,
                    f"HandBrakeCLI exited with code {return_code}\n{tail}"
                )
                final_status = self._handle_failure(
                    f"HandBrakeCLI exited with code {return_code}: {tail[:200]}"
                )
                # Only notify on permanent failure, not on auto-retry
                if final_status == 'failed':
                    try:
                        from app.notifications import notify_encode_failed
                        notify_encode_failed(original_input, f"Exit code {return_code}", self.profile.get('name', ''))
                    except Exception:
                        pass
                return False

        except Exception as e:
            if self._manually_stopped:
                return False
            optimizarr_logger.log_handbrake_error(original_input, str(e))
            final_status = self._handle_failure(str(e))
            if final_status == 'failed':
                try:
                    from app.notifications import notify_encode_failed
                    notify_encode_failed(original_input, str(e), self.profile.get('name', ''))
                except Exception:
                    pass
            return False

        finally:
            # Clean up stereo temp file/directory
            if stereo_temp:
                from app.stereo import cleanup_stereo_workdir
                cleanup_stereo_workdir(stereo_temp)
            # Clean up upscale temp file/directory
            if upscale_temp:
                from app.upscaler import cleanup_upscale_workdir
                cleanup_upscale_workdir(upscale_temp)
    
    def _diagnose_short_output(self, input_path, out_duration: float,
                               source_duration: float, tolerance: float):
        """Work out WHY the encode came out shorter than the source's duration.

        A container's duration is the length of its longest track, not the
        length of its picture. WEB-DL mkvs routinely carry a subtitle track
        that runs a minute past the last video frame, so a perfectly complete
        encode reads as "88s short" against the container. Failing those threw
        away good encodes and left whole seasons stuck.

        So instead of trusting the declared duration, ask the source directly
        whether it holds any picture past the point the encode ends:

          'ok'          — it does not. The encode is complete; the container
                          duration was simply counting a longer sidecar track.
          'bad_source'  — it does not, but the source's own VIDEO stream
                          claimed it should. The source file is damaged or
                          incomplete (truncated download): no encode of it can
                          ever be whole.
          'truncated'   — it does. HandBrake stopped early (disk full mid-mux,
                          bad source tail), or we could not verify either way.
                          Keep the original.

        Returns (verdict, message).
        """
        from app.scanner import probe_last_video_pts, probe_declared_video_duration

        short_by = source_duration - out_duration
        # Start a little before the output ends so a stream whose final packet
        # lands just shy of the reported duration still registers.
        real_end = probe_last_video_pts(
            str(input_path), start_seconds=out_duration - 10)

        if real_end is None:
            return 'truncated', (
                f"Output duration {out_duration:.0f}s is shorter than source "
                f"{source_duration:.0f}s and the source could not be re-probed "
                f"to confirm — output kept out of the library and original "
                f"file kept")

        if real_end > out_duration + tolerance:
            return 'truncated', (
                f"Output duration {out_duration:.0f}s is shorter than source "
                f"{source_duration:.0f}s — the source really does hold video "
                f"through {real_end:.0f}s, so the encode stopped "
                f"{real_end - out_duration:.0f}s early; original file kept")

        declared = probe_declared_video_duration(str(input_path))
        if declared and real_end < declared - tolerance:
            return 'bad_source', (
                f"Source file is damaged: its video stream declares "
                f"{declared:.0f}s but the file only contains picture through "
                f"{real_end:.0f}s. The encode is not at fault — re-download "
                f"this file. Original left untouched.")

        return 'ok', (
            f"source container claims {source_duration:.0f}s but its video "
            f"ends at {real_end:.0f}s (a longer subtitle/metadata track "
            f"inflates the container duration); encode is complete at "
            f"{out_duration:.0f}s, {short_by:.0f}s of the claim was never "
            f"picture")

    def _finalize_encoding(self):
        """Replace original file with encoded version and update database."""
        from app.logger import optimizarr_logger
        from app.devlog import devlog

        input_path = Path(self.queue_item['file_path'])

        # Finalizing is its own phase, and not a quick one: it re-probes the
        # output with ffprobe and moves a multi-GB file. Without a marker the
        # row just sat at 100% looking hung.
        db.update_queue_item(self.queue_item_id, phase='finalize',
                             phase_progress=0.0, phase_eta_seconds=0,
                             current_fps=0, eta_seconds=0)

        # Determine output extension based on container format
        container = self.profile.get('container', 'mkv')
        ext_map = {'mkv': '.mkv', 'mp4': '.mp4', 'webm': '.webm'}
        output_ext = ext_map.get(container, '.mkv')
        output_path = input_path.parent / f"{input_path.stem}_optimized{output_ext}"

        if not output_path.exists():
            # Diagnostic: list files in the directory that match the stem
            try:
                nearby = [f.name for f in input_path.parent.iterdir()
                          if input_path.stem in f.name and f.name != input_path.name]
            except Exception:
                nearby = []
            diag = f"Expected: {output_path}"
            if nearby:
                diag += f" | Found nearby: {', '.join(nearby[:5])}"
            else:
                diag += " | No matching files found in directory"
            optimizarr_logger.app_logger.error("Output file not found: %s", diag)

            db.update_queue_item(
                self.queue_item_id,
                status='failed',
                error_message=f"Output file not found after encoding. {diag}"
            )
            optimizarr_logger.log_handbrake_error(
                str(input_path), 'Output file not found after encoding'
            )
            return

        # Get file sizes
        original_size = input_path.stat().st_size
        new_size = output_path.stat().st_size
        savings = original_size - new_size

        # ── Sanity-check the output BEFORE deleting the original ──────────
        # HandBrake can exit 0 with a truncated file (disk full mid-mux, bad
        # source tail). Deleting the original is irreversible, so compare
        # durations first. A shorter output is only a SUSPICION — container
        # durations lie — so _diagnose_short_output re-probes the source
        # before anything is thrown away.
        source_duration = 0.0
        specs_for_check = self.queue_item.get('current_specs')
        if isinstance(specs_for_check, str):
            try:
                import json as _cjson
                specs_for_check = _cjson.loads(specs_for_check)
            except (ValueError, TypeError):
                specs_for_check = {}
        if isinstance(specs_for_check, dict):
            source_duration = float(specs_for_check.get('duration') or 0)
        if not source_duration:
            source_duration = float(self.queue_item.get('duration_seconds') or 0)

        if source_duration > 0:
            try:
                from app.scanner import scanner as _probe_scanner
                out_specs = _probe_scanner.analyze_file(str(output_path)) or {}
                out_duration = float(out_specs.get('duration') or 0)
            except Exception:
                out_duration = 0.0
            # Tolerance: 5s or 2% of runtime, whichever is larger. Only fail
            # when we could actually measure the output (ffprobe available).
            tolerance = max(5.0, source_duration * 0.02)
            if out_duration > 0 and out_duration < source_duration - tolerance:
                verdict, msg = self._diagnose_short_output(
                    input_path, out_duration, source_duration, tolerance)

                if verdict == 'ok':
                    # Container duration was overstated; the encode holds
                    # everything the source actually had. Carry on.
                    optimizarr_logger.app_logger.info(
                        "Short output accepted for %s: %s", input_path.name, msg)
                    devlog('enc_shortok', id=self.queue_item_id,
                           f=input_path.name, out=round(out_duration),
                           claimed=round(source_duration))
                else:
                    optimizarr_logger.app_logger.error(
                        "%s for %s: %s",
                        'Damaged source' if verdict == 'bad_source'
                        else 'Truncated output',
                        input_path.name, msg)
                    try:
                        output_path.unlink()
                    except OSError:
                        pass
                    db.update_queue_item(
                        self.queue_item_id,
                        # A damaged source can never encode fully, so retrying
                        # it is wasted GPU time — quarantine keeps the file
                        # untouched and takes it out of rotation instead.
                        status=('quarantined' if verdict == 'bad_source'
                                else 'failed'),
                        phase=None,
                        phase_progress=0.0,
                        phase_eta_seconds=0,
                        retry_after=None,
                        error_message=msg,
                    )
                    optimizarr_logger.log_handbrake_error(str(input_path), msg)
                    return

        # Calculate encoding duration for the speed learner, minus thermal
        # pauses. Plain encodes: HandBrake phase only. Preprocessed jobs
        # (upscale/stereo): the whole pipeline — their true cost IS the
        # multi-hour AI phase, and they're learned as a separate group so
        # they can't pollute the plain-encode ratios.
        encoding_time = 0
        phase_start = (getattr(self, '_job_started_monotonic', None)
                       if self._preprocessed else self._hb_started_monotonic)
        if phase_start is not None:
            paused = self._paused_seconds
            if self._paused_at_monotonic is not None:
                paused += time.monotonic() - self._paused_at_monotonic
            encoding_time = max(0, int(time.monotonic() - phase_start - paused))
        else:
            # Fallback (shouldn't happen): legacy wall-clock from started_at
            started_at = self.queue_item.get('started_at')
            if started_at:
                try:
                    start_dt = datetime.strptime(started_at, '%Y-%m-%d %H:%M:%S')
                    encoding_time = int((utc_now() - start_dt).total_seconds())
                except (ValueError, TypeError):
                    encoding_time = 0

        final_path = input_path.parent / f"{input_path.stem}{output_ext}"
        # Same name as the source when the container didn't change.
        # normcase because Windows paths are case-insensitive.
        in_place = (os.path.normcase(str(final_path))
                    == os.path.normcase(str(input_path)))

        # A container change lands the encode on a name we don't own. If a
        # DIFFERENT file already holds it — typically the same title already
        # present as .mkv next to the .mp4 — claiming it would destroy a file
        # nobody asked us to touch. Refuse, and keep the source: the old order
        # (unlink source, THEN rename) had already deleted the original by
        # this point, so the failed rename left only the _optimized temp,
        # which the orphan sweeper then deleted as well. That path lost files.
        if not in_place and final_path.exists():
            msg = (f"Destination already exists: {final_path.name}. Another "
                   f"file already occupies the encoded name, so the original "
                   f"was kept and nothing was overwritten. Remove or rename "
                   f"the existing file (they are usually duplicates — see "
                   f"Duplicates) and re-queue.")
            optimizarr_logger.app_logger.error(
                "Destination collision for %s: %s already exists — original kept",
                input_path.name, final_path.name)
            # Our own output is redundant now; the source is still intact.
            self._cleanup_partial_output()
            db.update_queue_item(
                self.queue_item_id,
                status='failed',
                phase=None,
                phase_progress=0.0,
                phase_eta_seconds=0,
                error_message=msg,
            )
            optimizarr_logger.log_handbrake_error(str(input_path), msg)
            return

        try:
            if in_place:
                # Atomic overwrite of the source — no window in which neither
                # the original nor the encode exists.
                os.replace(str(output_path), str(final_path))
            else:
                # Move the encode into place FIRST, drop the source only once
                # it is safely there. rename() (not replace()) so a file that
                # appeared since the check above still can't be clobbered.
                output_path.rename(final_path)
                try:
                    input_path.unlink()
                except OSError as unlink_err:
                    # Encode is in place and correct; only the old container
                    # lingers. Worth a warning, not a failure.
                    optimizarr_logger.app_logger.warning(
                        "Encoded %s but could not remove the original %s: %s",
                        final_path.name, input_path.name, unlink_err)

            # Update queue item
            db.update_queue_item(
                self.queue_item_id,
                status='completed',
                progress=100.0,
                current_fps=0,
                eta_seconds=0,
                phase=None,
                phase_progress=0.0,
                phase_eta_seconds=0,
                completed_at=utc_now().strftime('%Y-%m-%d %H:%M:%S')
            )

            # Add to history — use db.add_history so all fields are populated
            # Extract video duration from current_specs for encode speed tracking
            specs = self.queue_item.get('current_specs', {})
            if isinstance(specs, str):
                try:
                    import json as _json
                    specs = _json.loads(specs)
                except (ValueError, TypeError):
                    specs = {}
            video_duration = specs.get('duration', 0)

            db.add_history(
                file_path=str(final_path),
                profile_name=self.profile['name'],
                original_size_bytes=original_size,
                new_size_bytes=new_size,
                savings_bytes=savings,
                encoding_time_seconds=encoding_time,
                codec=self.profile.get('codec'),
                container=container,
                duration_seconds=video_duration,
                preprocessed=self._preprocessed,
                pre_work_mpf=self._upscale_work_mpf,
            )

            devlog('enc_done', id=self.queue_item_id, f=final_path.name,
                   s=encoding_time, sv=savings)

            # Log completion
            savings_pct = (savings / original_size * 100) if original_size > 0 else 0
            optimizarr_logger.log_handbrake_complete(str(input_path), {
                "original_size_mb": original_size / (1024 ** 2),
                "new_size_mb": new_size / (1024 ** 2),
                "savings_percent": savings_pct,
                "duration_seconds": encoding_time,
            })

            # Fire notification
            try:
                from app.notifications import notify_encode_complete
                notify_encode_complete(
                    str(input_path), original_size, new_size,
                    savings, encoding_time, self.profile['name']
                )
            except Exception:
                pass

        except Exception as e:
            optimizarr_logger.log_handbrake_error(str(input_path), str(e))
            db.update_queue_item(
                self.queue_item_id,
                status='failed',
                error_message=f"Finalization error: {e}"
            )
    
    def pause(self, reason: str = None):
        """Pause the encoding job.
        
        On Linux/macOS: uses SIGSTOP/SIGCONT for true process suspension.
        On Windows: SIGSTOP is not available; we use psutil to suspend the
        process tree instead (suspend all child processes too).
        """
        if self.process and not self.is_paused:
            try:
                if platform.system() == "Windows":
                    # Windows: suspend via psutil process tree
                    import psutil
                    try:
                        parent = psutil.Process(self.process.pid)
                        for child in parent.children(recursive=True):
                            child.suspend()
                        parent.suspend()
                    except psutil.NoSuchProcess:
                        pass
                else:
                    # Linux/macOS: SIGSTOP
                    self.process.send_signal(signal.SIGSTOP)

                self.is_paused = True
                self.paused_reason = reason
                self._paused_at_monotonic = time.monotonic()

                db.update_queue_item(
                    self.queue_item_id,
                    status='paused',
                    paused_reason=reason
                )
                from app.devlog import devlog
                devlog('throttle', a='pause', why=(reason or '')[:80])
                optimizarr_logger.app_logger.info(f"⏸ Paused: {reason}")
            except Exception as e:
                optimizarr_logger.app_logger.error(f"✗ Error pausing: {e}")

    def resume(self):
        """Resume a paused encoding job."""
        if self.process and self.is_paused:
            try:
                if platform.system() == "Windows":
                    import psutil
                    try:
                        parent = psutil.Process(self.process.pid)
                        for child in parent.children(recursive=True):
                            child.resume()
                        parent.resume()
                    except psutil.NoSuchProcess:
                        pass
                else:
                    self.process.send_signal(signal.SIGCONT)

                self.is_paused = False
                self.paused_reason = None
                if self._paused_at_monotonic is not None:
                    self._paused_seconds += time.monotonic() - self._paused_at_monotonic
                    self._paused_at_monotonic = None

                db.update_queue_item(
                    self.queue_item_id,
                    status='processing',
                    paused_reason=None
                )
                from app.devlog import devlog
                devlog('throttle', a='resume')
                optimizarr_logger.app_logger.info(f"▶ Resumed encoding")
            except Exception as e:
                optimizarr_logger.app_logger.error(f"✗ Error resuming: {e}")
    
    def _cleanup_partial_output(self):
        """Remove the partial _optimized.* output file if it exists.

        Used on manual stop and on failure — a half-written HandBrake output
        is useless and would otherwise linger next to the source file.
        """
        try:
            original_path = Path(self.queue_item['file_path'])
            container = self.profile.get('container', 'mkv')
            ext_map = {'mkv': '.mkv', 'mp4': '.mp4', 'webm': '.webm'}
            output_ext = ext_map.get(container, '.mkv')
            temp_out = original_path.parent / f"{original_path.stem}_optimized{output_ext}"
            if not temp_out.exists():
                return
            # Windows doesn't release the file handle the instant the process
            # is killed (WinError 32: file in use). Retry a few times before
            # giving up so the partial output doesn't linger.
            for attempt in range(5):
                try:
                    temp_out.unlink()
                    optimizarr_logger.app_logger.info(f"🗑 Removed partial output: {temp_out.name}")
                    return
                except PermissionError:
                    time.sleep(0.5)
                except FileNotFoundError:
                    return
            optimizarr_logger.app_logger.warning(f"⚠ Could not remove partial output (still locked): {temp_out.name}")
        except Exception as e:
            optimizarr_logger.app_logger.warning(f"⚠ Could not remove partial output: {e}")

    def _handle_failure(self, error_message: str) -> str:
        """Apply retry logic on encode failure.

        Reads ``max_retries`` (default 3). If the item is under the limit it is
        re-queued as 'pending' with an incremented retry_count; otherwise it is
        marked 'failed' permanently. Also cleans up the partial output file.
        Returns the resulting status ('pending' or 'failed').
        """
        from app.logger import optimizarr_logger
        from app.devlog import devlog

        self._cleanup_partial_output()

        try:
            max_retries = int(db.get_setting('max_retries', '3'))
        except (ValueError, TypeError):
            max_retries = 3

        # Re-read retry_count from DB — self.queue_item may be stale
        current = db.get_queue_item(self.queue_item_id) or {}
        retry_count = current.get('retry_count', 0) or 0

        if retry_count < max_retries:
            new_count = retry_count + 1
            # Exponential backoff so a transient failure doesn't burn every
            # retry in seconds: 60s, 120s, 240s … capped at 30 min.
            delay = min(60 * (2 ** (new_count - 1)), 1800)
            retry_at = (utc_now() + timedelta(seconds=delay)).strftime(
                '%Y-%m-%d %H:%M:%S')
            db.update_queue_item(
                self.queue_item_id,
                status='pending',
                retry_count=new_count,
                retry_after=retry_at,
                progress=0.0,
                current_fps=0,
                eta_seconds=0,
                phase=None,
                phase_progress=0.0,
                phase_eta_seconds=0,
                error_message=f"Retry {new_count}/{max_retries} in {delay}s: {error_message[:180]}",
            )
            optimizarr_logger.app_logger.warning(
                "Encode failed (item %s) — retry %d/%d in %ds",
                self.queue_item_id, new_count, max_retries, delay
            )
            devlog('enc_retry', id=self.queue_item_id, n=new_count,
                   delay=delay, err=error_message[:150])
            return 'pending'

        db.update_queue_item(
            self.queue_item_id,
            status='failed',
            current_fps=0,
            eta_seconds=0,
            phase=None,
            phase_progress=0.0,
            phase_eta_seconds=0,
            error_message=error_message[:500],
        )
        optimizarr_logger.app_logger.error(
            "Encode failed permanently (item %s) after %d retries",
            self.queue_item_id, retry_count
        )
        devlog('enc_fail', id=self.queue_item_id, n=retry_count,
               err=error_message[:150])
        return 'failed'

    def stop(self, mark_cancelled: bool = True):
        """Stop the encoding job.

        Kills the full HandBrakeCLI process tree via psutil (terminate() only
        kills the parent; child processes survive on Windows) and cleans up the
        partial output file.

        Args:
            mark_cancelled: When True (manual stop) the item is set to
                'cancelled'. When False (server shutdown) the status is left
                untouched so startup recovery can re-queue it as 'pending'.
        """
        self._manually_stopped = True
        self.stop_monitoring = True
        if self.monitoring_thread and self.monitoring_thread.is_alive():
            self.monitoring_thread.join(timeout=2)

        # Kill any pre-phase subprocess (ffmpeg extraction/reassembly or the
        # AI upscaler). Without this, stopping during the upscale phase left
        # the upscaler running as a GPU-burning orphan — there is no HandBrake
        # process yet at that point, so the block below had nothing to kill.
        pipeline_proc = self.pipeline_process
        if pipeline_proc is not None and pipeline_proc.poll() is None:
            try:
                import psutil as _psutil
                try:
                    parent = _psutil.Process(pipeline_proc.pid)
                    for child in parent.children(recursive=True):
                        child.kill()
                    parent.kill()
                except _psutil.NoSuchProcess:
                    pass
            except Exception:
                try:
                    pipeline_proc.kill()
                except Exception:
                    pass

        if self.process:
            try:
                import psutil as _psutil
                try:
                    parent = _psutil.Process(self.process.pid)
                    for child in parent.children(recursive=True):
                        child.kill()
                    parent.kill()
                except _psutil.NoSuchProcess:
                    pass
                try:
                    self.process.wait(timeout=10)
                except Exception:
                    pass
            except Exception as e:
                # psutil unavailable or process already gone — fall back
                try:
                    self.process.terminate()
                    self.process.wait(timeout=5)
                except Exception:
                    pass
                optimizarr_logger.app_logger.warning(f"⚠ psutil kill failed, used terminate(): {e}")

        self._cleanup_partial_output()

        if mark_cancelled:
            db.update_queue_item(
                self.queue_item_id,
                status='cancelled',
                current_fps=0,
                eta_seconds=0,
                phase=None,
                phase_progress=0.0,
                phase_eta_seconds=0,
                error_message='Manually stopped'
            )
            from app.devlog import devlog
            devlog('enc_cancel', id=self.queue_item_id)
            optimizarr_logger.app_logger.info("⏹ Encoding cancelled")
        else:
            optimizarr_logger.app_logger.info("⏹ Encoding interrupted (will resume on next start)")


class EncoderPool:
    """Manages a pool of encoding jobs."""
    
    def __init__(self, max_concurrent: int = 1):
        self.max_concurrent = max_concurrent
        self.active_jobs: list[EncodingJob] = []
        self.is_running = False
        # Guards the single running loop. Multiple near-simultaneous triggers
        # (boot auto-start, BURSTY webhooks — Radarr fires Download+Test in the
        # same second — the scheduler, manual Start) must not each spawn a
        # process_queue loop: that ran many concurrent HandBrakeCLI processes
        # that stop() couldn't fully track ("wouldn't close, stop didn't work").
        self._lifecycle_lock = threading.Lock()
        self._last_temp_check = 0.0
        self._effective_cache = None

    def process_queue(self):
        """Process the encoding queue. Exactly one controller loop runs.

        max_concurrent_jobs (schedule setting) governs parallelism. ==1 (the
        default) takes the simple blocking single-job path — byte-for-byte the
        proven behavior. >1 enables temperature-gated parallel encoding.
        """
        # Atomically claim the single controller slot (prevents the runaway
        # multi-loop bug regardless of how many things try to start us).
        with self._lifecycle_lock:
            if self.is_running:
                optimizarr_logger.app_logger.info("Encoder already running — ignoring duplicate start")
                return
            self.is_running = True

        optimizarr_logger.app_logger.info("Starting encoder pool...")
        try:
            max_concurrent = self._load_max_concurrent()
            if max_concurrent <= 1:
                self._run_serial()
            else:
                optimizarr_logger.app_logger.info(f"Encoder pool: up to {max_concurrent} concurrent jobs (temperature-gated)")
                self._run_parallel(max_concurrent)
        finally:
            # Always release the slot, even on an unexpected error, so the
            # encoder can be started again (and isn't wedged "running").
            self.is_running = False
            optimizarr_logger.app_logger.info("Encoder pool stopped")
            # Encoder is idle now — sweep any leftover _optimized temp files
            # and stale upscale work directories from crashed/killed jobs.
            try:
                from app.scanner import cleanup_orphaned_outputs
                cleanup_orphaned_outputs()
            except Exception:
                pass
            try:
                from app.upscaler import cleanup_stale_upscale_workdirs
                cleanup_stale_upscale_workdirs()
            except Exception:
                pass

    def _run_serial(self):
        """Single job at a time (default). Unchanged proven behavior."""
        while self.is_running:
            if len(self.active_jobs) < 1:
                item = db.get_next_pending_item()
                if item:
                    profile = db.get_profile(item['profile_id'])
                    if profile:
                        resource_limits = self._load_resource_settings()
                        job = EncodingJob(item['id'], profile, resource_limits)
                        self.active_jobs.append(job)
                        try:
                            job.start()  # blocking
                        finally:
                            if job in self.active_jobs:
                                self.active_jobs.remove(job)
                    else:
                        # Profile gone — fail the item so we don't spin on it
                        db.update_queue_item(item['id'], status='failed',
                                             error_message='Profile not found')
                elif db.count_pending() == 0:
                    optimizarr_logger.app_logger.info("✓ Queue is empty")
                    try:
                        from app.notifications import notify_queue_empty
                        notify_queue_empty()
                    except Exception:
                        pass
                    break
                # else: pending items all waiting on retry backoff — keep looping
            time.sleep(1)

    def _run_parallel(self, max_concurrent: int):
        """Run up to N jobs at once, gated by temperature headroom.

        Each job runs in its own thread; the controller reaps finished ones,
        dispatches new ones up to the (temp-limited) effective concurrency,
        and never dispatches an item already in flight (exclude_ids).
        """
        while self.is_running:
            # Reap finished job threads
            for job in list(self.active_jobs):
                th = getattr(job, '_thread', None)
                if th is not None and not th.is_alive():
                    self.active_jobs.remove(job)

            effective = self._effective_concurrency(max_concurrent)

            # Dispatch up to capacity
            while self.is_running and len(self.active_jobs) < effective:
                exclude = [j.queue_item_id for j in self.active_jobs]
                item = db.get_next_pending_item(exclude_ids=exclude)
                if not item:
                    break
                profile = db.get_profile(item['profile_id'])
                if not profile:
                    db.update_queue_item(item['id'], status='failed',
                                         error_message='Profile not found')
                    continue
                resource_limits = self._load_resource_settings()
                job = EncodingJob(item['id'], profile, resource_limits)
                th = threading.Thread(target=self._run_job, args=(job,), daemon=True)
                job._thread = th
                self.active_jobs.append(job)
                th.start()

            # Termination: nothing running and nothing left to run
            if not self.active_jobs and db.count_pending() == 0:
                optimizarr_logger.app_logger.info("✓ Queue is empty")
                try:
                    from app.notifications import notify_queue_empty
                    notify_queue_empty()
                except Exception:
                    pass
                break

            time.sleep(1)

        # Wait for any in-flight jobs to finish/stop before releasing the slot
        for job in list(self.active_jobs):
            th = getattr(job, '_thread', None)
            if th is not None and th.is_alive():
                th.join(timeout=30)

    def _run_job(self, job: 'EncodingJob'):
        """Worker-thread wrapper around a single blocking job.start()."""
        try:
            job.start()
        except Exception as e:
            optimizarr_logger.app_logger.error(f"⚠ Encoding job error: {e}")
        finally:
            if job in self.active_jobs:
                try:
                    self.active_jobs.remove(job)
                except ValueError:
                    pass

    def _load_max_concurrent(self) -> int:
        """Read the configured max concurrent jobs (schedule table), 1..8."""
        try:
            with db.get_read_connection() as conn:
                cur = conn.cursor()
                cur.execute("SELECT max_concurrent_jobs FROM schedule LIMIT 1")
                row = cur.fetchone()
                if row and row[0]:
                    return max(1, min(8, int(row[0])))
        except Exception:
            pass
        return 1

    def _effective_concurrency(self, max_concurrent: int) -> int:
        """How many jobs may run right now given thermal headroom.

        Returns max_concurrent when the GPU/CPU are comfortably below their
        pause thresholds (a 10°C buffer), else 1 — so we don't pile on a
        second encode that would immediately trip the per-job pause logic.
        Temps are read at most every 15s (pynvml is not free). If temps
        can't be read, stay conservative (1).
        """
        if max_concurrent <= 1:
            return 1
        now = time.monotonic()
        if self._effective_cache is not None and now - self._last_temp_check < 15:
            return self._effective_cache
        self._last_temp_check = now
        allowed = max_concurrent
        try:
            limits = self._load_resource_settings()
            margin = 10.0
            res = resource_monitor.get_all_resources()
            gpu_temp = (res.get('gpu') or {}).get('temperature_c')
            cpu_temp = (res.get('cpu') or {}).get('temperature_c')
            if gpu_temp is not None and gpu_temp >= limits['gpu_temp_threshold'] - margin:
                allowed = 1
            if cpu_temp is not None and cpu_temp >= limits['cpu_temp_threshold'] - margin:
                allowed = 1
        except Exception:
            allowed = 1
        self._effective_cache = allowed
        return allowed
    
    def _load_resource_settings(self) -> Dict:
        """Load resource management settings from database."""
        try:
            with db.get_read_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT key, value FROM settings
                    WHERE key LIKE 'resource_%'
                """)
                settings = {row[0]: row[1] for row in cursor.fetchall()}
            
            # Parse settings with defaults — temperature-first model
            return {
                'enable_throttling': settings.get('resource_enable_throttling', 'true').lower() == 'true',
                'nice_level': int(settings.get('resource_nice_level', '10')),
                # Primary triggers (temperature)
                'pause_on_cpu_temp': settings.get('resource_pause_on_cpu_temp', 'true').lower() == 'true',
                'cpu_temp_threshold': float(settings.get('resource_cpu_temp_threshold', '85.0')),
                'pause_on_gpu_temp': settings.get('resource_pause_on_gpu_temp', 'true').lower() == 'true',
                'gpu_temp_threshold': float(settings.get('resource_gpu_temp_threshold', '83.0')),
                # Secondary triggers (optional)
                'pause_on_memory': settings.get('resource_pause_on_memory', 'false').lower() == 'true',
                'memory_threshold': float(settings.get('resource_memory_threshold', '85.0')),
                'pause_on_cpu_usage': settings.get('resource_pause_on_cpu_usage', 'false').lower() == 'true',
                'cpu_usage_threshold': float(settings.get('resource_cpu_usage_threshold', '95.0')),
            }
        except Exception as e:
            optimizarr_logger.app_logger.error(f"⚠️ Error loading resource settings: {e}")
            # Return defaults
            return {
                'enable_throttling': True,
                'nice_level': 10,
                'pause_on_cpu_temp': True,
                'cpu_temp_threshold': 85.0,
                'pause_on_gpu_temp': True,
                'gpu_temp_threshold': 83.0,
                'pause_on_memory': False,
                'memory_threshold': 85.0,
                'pause_on_cpu_usage': False,
                'cpu_usage_threshold': 95.0,
            }
    
    def stop(self, mark_cancelled: bool = True, graceful: bool = False):
        """Stop the encoder pool.

        Args:
            mark_cancelled: Forwarded to each job's stop(). True for a manual
                stop (items → 'cancelled'); False for server shutdown (items
                left for startup recovery to re-queue).
            graceful: When True, stop accepting NEW jobs but let the active
                encode finish on its own — process_queue() exits after the
                current job because is_running is now False. Used at a
                schedule-window boundary when "finish current encode" is on.
        """
        self.is_running = False

        if graceful:
            return  # leave the running job alone; loop ends when it completes

        # Iterate a copy — process_queue() mutates active_jobs concurrently
        for job in list(self.active_jobs):
            job.stop(mark_cancelled=mark_cancelled)


# Cached once per process: build_command calls this for every encode, and the
# installed HandBrakeCLI's encoder list can't change while we're running.
_hw_accel_cache: Optional[Dict] = None


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
    global _hw_accel_cache
    if _hw_accel_cache is not None:
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
            ["HandBrakeCLI", "--help"],
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
    return available


# Global encoder pool instance
encoder_pool = EncoderPool(max_concurrent=1)
