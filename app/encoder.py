"""
Encoder module for Optimizarr.
Handles video transcoding using HandBrakeCLI with progress tracking.
"""
import subprocess
import re
import time
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Callable
import os
import shutil
import signal
import platform

from app.database import db
from app.resources import resource_monitor, resource_throttler


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

            progress_pattern = re.compile(r'Encoding: task \d+ of \d+, ([\d.]+) %')
            last_lines = []  # Keep last 20 lines for diagnostics

            for line in self.process.stdout:
                line_stripped = line.rstrip()
                if line_stripped:
                    last_lines.append(line_stripped)
                    if len(last_lines) > 20:
                        last_lines.pop(0)
                    optimizarr_logger.log_handbrake_output(line_stripped)

                match = progress_pattern.search(line)
                if match:
                    hb_pct = float(match.group(1))
                    # Remap HandBrake 0-100 → hb_start to hb_start+hb_range
                    self.progress = hb_start + (hb_pct / 100.0) * hb_range
                    db.update_queue_item(self.queue_item_id, progress=self.progress)
                    if progress_callback:
                        progress_callback(self.progress)
                    print(f"  Progress: {self.progress:.1f}%", end='\r')

            return_code = self.process.wait()

            if return_code == 0:
                self._finalize_encoding()
                return True
            else:
                # Include last HandBrake output in error for diagnostics
                tail = '\n'.join(last_lines[-5:]) if last_lines else '(no output captured)'
                optimizarr_logger.log_handbrake_error(
                    original_input,
                    f"HandBrakeCLI exited with code {return_code}\n{tail}"
                )
                db.update_queue_item(
                    self.queue_item_id,
                    status='failed',
                    error_message=f"HandBrakeCLI exited with code {return_code}: {tail[:200]}"
                )
                return False

        except Exception as e:
            optimizarr_logger.log_handbrake_error(original_input, str(e))
            db.update_queue_item(
                self.queue_item_id,
                status='failed',
                error_message=str(e)
            )
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
                completed_at=datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
            )

            # Add to history — use db.add_history so all fields are populated
            db.add_history(
                file_path=str(final_path),
                profile_name=self.profile['name'],
                original_size_bytes=original_size,
                new_size_bytes=new_size,
                savings_bytes=savings,
                encoding_time_seconds=encoding_time,
                codec=self.profile.get('codec'),
                container=container,
            )

            # Log completion
            savings_pct = (savings / original_size * 100) if original_size > 0 else 0
            optimizarr_logger.log_handbrake_complete(str(input_path), {
                "original_size_mb": original_size / (1024 ** 2),
                "new_size_mb": new_size / (1024 ** 2),
                "savings_percent": savings_pct,
                "duration_seconds": encoding_time,
            })

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
                print(f"▶ Resumed encoding")
            except Exception as e:
                print(f"✗ Error resuming: {e}")
    
    def stop(self):
        """Stop the encoding job."""
        # Stop monitoring thread
        self.stop_monitoring = True
        if self.monitoring_thread and self.monitoring_thread.is_alive():
            self.monitoring_thread.join(timeout=2)
        
        if self.process:
            try:
                self.process.terminate()
                self.process.wait(timeout=10)
                
                db.update_queue_item(
                    self.queue_item_id,
                    status='failed',
                    error_message='Manually stopped'
                )
                
                print(f"⏹ Stopped encoding")
            except Exception as e:
                print(f"✗ Error stopping: {e}")


class EncoderPool:
    """Manages a pool of encoding jobs."""
    
    def __init__(self, max_concurrent: int = 1):
        self.max_concurrent = max_concurrent
        self.active_jobs: list[EncodingJob] = []
        self.is_running = False
    
    def process_queue(self):
        """Process the encoding queue."""
        self.is_running = True
        
        print("Starting encoder pool...")
        
        while self.is_running:
            # Check if we can start a new job
            if len(self.active_jobs) < self.max_concurrent:
                # Get next pending item
                pending_items = db.get_queue_items(status='pending')
                
                if pending_items:
                    item = pending_items[0]  # Highest priority
                    
                    # Get profile
                    profile = db.get_profile(item['profile_id'])
                    
                    if profile:
                        # Load resource settings
                        resource_limits = self._load_resource_settings()
                        
                        # Create and start job
                        job = EncodingJob(item['id'], profile, resource_limits)
                        self.active_jobs.append(job)
                        
                        # Start encoding (blocking for now - will be async later)
                        success = job.start()
                        
                        # Remove from active jobs
                        self.active_jobs.remove(job)
                else:
                    # No more pending items
                    print("✓ Queue is empty")
                    break
            
            time.sleep(1)
        
        print("Encoder pool stopped")
    
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
    
    def stop(self):
        """Stop the encoder pool."""
        self.is_running = False
        
        # Stop all active jobs
        for job in self.active_jobs:
            job.stop()


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
            timeout=10
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
