"""
Encoder module for Optimizarr.
Handles video transcoding using HandBrakeCLI with progress tracking.
"""
import subprocess
import re
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


# HandBrakeCLI progress line, e.g.:
#   Encoding: task 1 of 1, 23.45 % (113.06 fps, avg 102.33 fps, ETA 00h12m34s)
# The fps/ETA group is absent on the first few lines of an encode.
HB_PROGRESS_RE = re.compile(
    r'Encoding: task \d+ of \d+, ([\d.]+) %'
    r'(?:\s*\(([\d.]+) fps, avg ([\d.]+) fps, ETA (\d+)h(\d+)m(\d+)s\))?'
)


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
        self.progress = 0.0
        self.is_paused = False
        self.paused_reason = None
        self.monitoring_thread = None
        self.stop_monitoring = False
        self._manually_stopped = False
        
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
    
    def build_command(self, input_override: str = None) -> list:
        """Build HandBrakeCLI command from profile settings.
        
        Args:
            input_override: If provided, use this path as the input instead of
                            the queue item's file_path (used for upscaled temp files).
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

        # Base command
        cmd = ['HandBrakeCLI', '-i', input_file, '-o', output_file]

        # Container format
        cmd.extend(['--format', container_info['format']])
        
        # ---- Encoder Selection (with HW accel support) ----
        encoder = self.profile['encoder']
        
        # If hardware acceleration is enabled, try to use a GPU encoder
        if self.profile.get('hw_accel_enabled'):
            hw_encoder = self._get_hw_encoder(encoder)
            if hw_encoder:
                encoder = hw_encoder
        
        # Map encoder names to HandBrake encoder names
        encoder_map = {
            'svt_av1': 'svt_av1',
            'x265': 'x265',
            'x264': 'x264',
            'nvenc_h265': 'nvenc_h265',
            'nvenc_h264': 'nvenc_h264',
            'nvenc_av1': 'nvenc_av1',
            'qsv_h265': 'qsv_h265',
            'qsv_h264': 'qsv_h264',
            'qsv_av1': 'qsv_av1',
            'vce_h264': 'vce_h264',
            'vce_h265': 'vce_h265',
            'vt_h264': 'vt_h264',
            'vt_h265': 'vt_h265',
        }
        
        hb_encoder = encoder_map.get(encoder, encoder)
        cmd.extend(['--encoder', hb_encoder])
        
        # Quality
        cmd.extend(['--quality', str(self.profile['quality'])])
        
        # Preset
        if self.profile.get('preset'):
            cmd.extend(['--encoder-preset', self.profile['preset']])
        
        # Resolution
        if self.profile.get('resolution'):
            width, height = self.profile['resolution'].split('x')
            cmd.extend(['--width', width, '--height', height])
        
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
        
        # Two-pass encoding
        if self.profile.get('two_pass'):
            cmd.append('--multi-pass')
        
        # Custom arguments (always last so they can override)
        if self.profile.get('custom_args'):
            custom_args = self.profile['custom_args'].split()
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
        """Try to map a software encoder to a hardware encoder.
        
        Conservative approach: only map codecs that are reliably supported
        across all GPUs for each vendor. AV1 hardware encoding requires
        specific GPU generations (NVIDIA RTX 40+, Intel Arc) that we
        cannot reliably detect from HandBrake alone, so we skip those
        and let the software encoder handle them.
        """
        # Detect what's available
        hw = detect_hardware_acceleration()
        
        codec = self.profile.get('codec', 'av1')
        
        # NVIDIA: H.264/H.265 are safe on all NVENC GPUs.
        # AV1 requires RTX 40+ (Ada Lovelace) — skip to avoid silent failures.
        if hw.get('nvidia'):
            safe_map = {'h264': 'nvenc_h264', 'h265': 'nvenc_h265'}
            if codec in safe_map:
                return safe_map[codec]
        
        # Intel QSV: H.264/H.265 are broadly supported.
        # AV1 requires Arc GPUs — skip for safety.
        if hw.get('intel'):
            safe_map = {'h264': 'qsv_h264', 'h265': 'qsv_h265'}
            if codec in safe_map:
                return safe_map[codec]
        
        if hw.get('amd'):
            safe_map = {'h264': 'vce_h264', 'h265': 'vce_h265'}
            if codec in safe_map:
                return safe_map[codec]
        
        if hw.get('apple'):
            safe_map = {'h264': 'vt_h264', 'h265': 'vt_h265'}
            if codec in safe_map:
                return safe_map[codec]
        
        # No safe HW encoder for this codec — keep software encoder
        return None
    
    def _monitor_resources(self):
        """Background thread to monitor resources and pause/resume if needed."""
        print("  Starting resource monitoring thread...")
        
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
                print(f"\n⏸ Pausing encoding: {reason}")
                self.pause(reason)
            elif not should_pause and self.is_paused:
                print(f"\n▶ Resuming encoding (resources available)")
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

        db.update_queue_item(
            self.queue_item_id,
            status='processing',
            started_at=datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
        )

        from app.logger import optimizarr_logger
        from app.devlog import devlog
        devlog('enc_start', id=self.queue_item_id,
               f=Path(self.queue_item['file_path']).name,
               c=self.profile.get('codec'))

        # ── Determine input file (may be replaced by stereo/upscaled version) ─
        original_input = self.queue_item['file_path']
        encode_input   = original_input       # may be overridden below
        stereo_temp    = None                 # path to clean up after encode
        upscale_temp   = None                 # path to clean up after encode

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

            def _stereo_progress(pct: float):
                mapped = stereo_start + (pct / 100.0) * s_range
                self.progress = mapped
                db.update_queue_item(self.queue_item_id, progress=mapped)
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

            def _upscale_progress(pct: float):
                mapped = upscale_start + (pct / 100.0) * u_range
                self.progress = mapped
                db.update_queue_item(self.queue_item_id, progress=mapped)
                if progress_callback:
                    progress_callback(mapped)

            upscaled_path = run_upscale_pipeline(
                input_path=encode_input,
                upscaler_key=upscaler_key,
                model=model,
                factor=factor,
                progress_callback=_upscale_progress,
            )

            if upscaled_path:
                encode_input  = upscaled_path
                upscale_temp  = upscaled_path
                optimizarr_logger.app_logger.info(
                    "Upscale complete — encoding from temp: %s", upscaled_path
                )
            else:
                optimizarr_logger.app_logger.warning(
                    "Upscale failed for %s — encoding original file instead",
                    original_input,
                )

        # ── Build HandBrake command (encode_input may be stereo/upscaled) ────
        cmd = self.build_command(input_override=encode_input)
        optimizarr_logger.log_handbrake_start(original_input, cmd)

        try:
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

            for line in self.process.stdout:
                line_stripped = line.rstrip()
                if line_stripped:
                    last_lines.append(line_stripped)
                    if len(last_lines) > 20:
                        last_lines.pop(0)
                    optimizarr_logger.log_handbrake_output(line_stripped)

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
                        fields = {'progress': self.progress}
                        if fps is not None:
                            fields['current_fps'] = fps
                        if hb_eta is not None:
                            fields['eta_seconds'] = hb_eta
                        db.update_queue_item(self.queue_item_id, **fields)

                    if progress_callback:
                        progress_callback(self.progress)
                    print(f"  Progress: {self.progress:.1f}%", end='\r')

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
    
    def _finalize_encoding(self):
        """Replace original file with encoded version and update database."""
        from app.logger import optimizarr_logger

        input_path = Path(self.queue_item['file_path'])

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

        # Calculate encoding duration
        encoding_time = 0
        started_at = self.queue_item.get('started_at')
        if started_at:
            try:
                start_dt = datetime.strptime(started_at, '%Y-%m-%d %H:%M:%S')
                encoding_time = int((datetime.utcnow() - start_dt).total_seconds())
            except (ValueError, TypeError):
                encoding_time = 0

        try:
            # Remove original file
            input_path.unlink()

            # Rename encoded file to original stem with (possibly new) extension
            final_path = input_path.parent / f"{input_path.stem}{output_ext}"
            output_path.rename(final_path)

            # Update queue item
            db.update_queue_item(
                self.queue_item_id,
                status='completed',
                progress=100.0,
                current_fps=0,
                eta_seconds=0,
                completed_at=datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
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
            )

            from app.devlog import devlog
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

                db.update_queue_item(
                    self.queue_item_id,
                    status='paused',
                    paused_reason=reason
                )
                from app.devlog import devlog
                devlog('throttle', a='pause', why=(reason or '')[:80])
                print(f"⏸ Paused: {reason}")
            except Exception as e:
                print(f"✗ Error pausing: {e}")

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

                db.update_queue_item(
                    self.queue_item_id,
                    status='processing',
                    paused_reason=None
                )
                from app.devlog import devlog
                devlog('throttle', a='resume')
                print(f"▶ Resumed encoding")
            except Exception as e:
                print(f"✗ Error resuming: {e}")
    
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
                    print(f"🗑 Removed partial output: {temp_out.name}")
                    return
                except PermissionError:
                    time.sleep(0.5)
                except FileNotFoundError:
                    return
            print(f"⚠ Could not remove partial output (still locked): {temp_out.name}")
        except Exception as e:
            print(f"⚠ Could not remove partial output: {e}")

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
            retry_at = (datetime.utcnow() + timedelta(seconds=delay)).strftime(
                '%Y-%m-%d %H:%M:%S')
            db.update_queue_item(
                self.queue_item_id,
                status='pending',
                retry_count=new_count,
                retry_after=retry_at,
                progress=0.0,
                current_fps=0,
                eta_seconds=0,
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
                print(f"⚠ psutil kill failed, used terminate(): {e}")

        self._cleanup_partial_output()

        if mark_cancelled:
            db.update_queue_item(
                self.queue_item_id,
                status='cancelled',
                current_fps=0,
                eta_seconds=0,
                error_message='Manually stopped'
            )
            from app.devlog import devlog
            devlog('enc_cancel', id=self.queue_item_id)
            print("⏹ Encoding cancelled")
        else:
            print("⏹ Encoding interrupted (will resume on next start)")


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
                print("Encoder already running — ignoring duplicate start")
                return
            self.is_running = True

        print("Starting encoder pool...")
        try:
            max_concurrent = self._load_max_concurrent()
            if max_concurrent <= 1:
                self._run_serial()
            else:
                print(f"Encoder pool: up to {max_concurrent} concurrent jobs (temperature-gated)")
                self._run_parallel(max_concurrent)
        finally:
            # Always release the slot, even on an unexpected error, so the
            # encoder can be started again (and isn't wedged "running").
            self.is_running = False
            print("Encoder pool stopped")
            # Encoder is idle now — sweep any leftover _optimized temp files.
            try:
                from app.scanner import cleanup_orphaned_outputs
                cleanup_orphaned_outputs()
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
                    print("✓ Queue is empty")
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
                print("✓ Queue is empty")
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
            print(f"⚠ Encoding job error: {e}")
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
            with db.get_connection() as conn:
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
            print(f"⚠️ Error loading resource settings: {e}")
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


def detect_hardware_acceleration() -> Dict:
    """Detect available hardware encoders by querying HandBrakeCLI."""
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
        
        output = result.stdout.lower() + result.stderr.lower()
        
        if "nvenc" in output:
            available["nvidia"] = True
            available["encoders"].append("NVENC (NVIDIA)")
            available["details"]["nvidia"] = {
                "h264": "nvenc_h264" in output,
                "h265": "nvenc_h265" in output,
                "av1": "nvenc_av1" in output,
            }
        
        if "vce" in output:
            available["amd"] = True
            available["encoders"].append("VCE (AMD)")
            available["details"]["amd"] = {
                "h264": "vce_h264" in output,
                "h265": "vce_h265" in output,
            }
        
        if "qsv" in output:
            available["intel"] = True
            available["encoders"].append("QuickSync (Intel)")
            available["details"]["intel"] = {
                "h264": "qsv_h264" in output,
                "h265": "qsv_h265" in output,
                "av1": "qsv_av1" in output,
            }
        
        if "videotoolbox" in output or "vt_h" in output:
            available["apple"] = True
            available["encoders"].append("VideoToolbox (Apple)")
            available["details"]["apple"] = {
                "h264": "vt_h264" in output,
                "h265": "vt_h265" in output,
            }
        
    except FileNotFoundError:
        available["error"] = "HandBrakeCLI not found"
    except subprocess.TimeoutExpired:
        available["error"] = "HandBrakeCLI timed out"
    except Exception as e:
        available["error"] = str(e)
    
    return available


# Global encoder pool instance
encoder_pool = EncoderPool(max_concurrent=1)
