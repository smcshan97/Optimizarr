"""
Encoder module for Optimizarr.
Handles video transcoding using HandBrakeCLI with progress tracking.
"""
import subprocess
import re
import time
import threading
from pathlib import Path
from typing import Optional, Dict, Callable
import os
import shutil
import signal

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
        
        # Resource limits
        self.resource_limits = resource_limits or {
            'cpu_threshold': 90.0,
            'memory_threshold': 85.0,
            'gpu_threshold': 90.0,
            'nice_level': 10,
            'enable_throttling': True
        }
        
        # Get queue item details
        queue_items = db.get_queue_items()
        self.queue_item = next((item for item in queue_items if item['id'] == queue_item_id), None)
        
        if not self.queue_item:
            raise ValueError(f"Queue item {queue_item_id} not found")
    
    def build_command(self) -> list:
        """Build HandBrakeCLI command from profile settings."""
        input_file = self.queue_item['file_path']
        
        # Determine container format and output extension
        container = self.profile.get('container', 'mkv')
        container_map = {
            'mkv': {'ext': '.mkv', 'format': 'av_mkv'},
            'mp4': {'ext': '.mp4', 'format': 'av_mp4'},
            'webm': {'ext': '.webm', 'format': 'av_webm'},
        }
        container_info = container_map.get(container, container_map['mkv'])
        
        # Create output path with correct extension
        input_path = Path(input_file)
        output_ext = container_info['ext']
        output_file = str(input_path.parent / f"{input_path.stem}_optimized{output_ext}")
        
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
            cmd.append('--two-pass')
        
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
        """Try to map a software encoder to a hardware encoder."""
        # Detect what's available
        hw = detect_hardware_acceleration()
        
        # Map codec families to HW encoders
        codec = self.profile.get('codec', 'av1')
        
        if hw.get('nvidia'):
            hw_map = {'h264': 'nvenc_h264', 'h265': 'nvenc_h265', 'av1': 'nvenc_av1'}
            if codec in hw_map:
                return hw_map[codec]
        
        if hw.get('intel'):
            hw_map = {'h264': 'qsv_h264', 'h265': 'qsv_h265', 'av1': 'qsv_av1'}
            if codec in hw_map:
                return hw_map[codec]
        
        if hw.get('amd'):
            hw_map = {'h264': 'vce_h264', 'h265': 'vce_h265'}
            if codec in hw_map:
                return hw_map[codec]
        
        if hw.get('apple'):
            hw_map = {'h264': 'vt_h264', 'h265': 'vt_h265'}
            if codec in hw_map:
                return hw_map[codec]
        
        # No HW encoder available, return None to keep software encoder
        return None
    
    def _monitor_resources(self):
        """Background thread to monitor resources and pause/resume if needed."""
        print("  Starting resource monitoring thread...")
        
        while not self.stop_monitoring and self.process and self.process.poll() is None:
            time.sleep(5)  # Check every 5 seconds
            
            if not self.resource_limits.get('enable_throttling'):
                continue
            
            # Check if we should pause
            should_pause, reason = resource_throttler.should_pause_encoding(
                cpu_threshold=self.resource_limits['cpu_threshold'],
                memory_threshold=self.resource_limits['memory_threshold'],
                gpu_threshold=self.resource_limits['gpu_threshold']
            )
            
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
        
        Args:
            progress_callback: Function to call with progress updates (0.0-100.0)
        """
        # Update queue item status
        db.update_queue_item(
            self.queue_item_id,
            status='processing',
            started_at=time.strftime('%Y-%m-%d %H:%M:%S')
        )
        
        # Build command
        cmd = self.build_command()
        
        # Log the encoding start
        from app.logger import optimizarr_logger
        optimizarr_logger.log_handbrake_start(self.queue_item['file_path'], cmd)
        
        try:
            # Start HandBrakeCLI process
            self.process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1
            )
            
            # Set process priority (nice level)
            if self.resource_limits.get('nice_level'):
                resource_throttler.set_process_priority(
                    self.process.pid,
                    self.resource_limits['nice_level']
                )
            
            # Start resource monitoring thread
            if self.resource_limits.get('enable_throttling'):
                self.monitoring_thread = threading.Thread(
                    target=self._monitor_resources,
                    daemon=True
                )
                self.monitoring_thread.start()
            
            # Monitor progress
            progress_pattern = re.compile(r'Encoding: task \d+ of \d+, ([\d.]+) %')
            
            for line in self.process.stdout:
                # Parse progress
                match = progress_pattern.search(line)
                if match:
                    self.progress = float(match.group(1))
                    
                    # Update database
                    db.update_queue_item(
                        self.queue_item_id,
                        progress=self.progress
                    )
                    
                    # Call progress callback
                    if progress_callback:
                        progress_callback(self.progress)
                    
                    print(f"  Progress: {self.progress:.1f}%", end='\r')
            
            # Wait for process to complete
            return_code = self.process.wait()
            
            if return_code == 0:
                self._finalize_encoding()
                return True
            else:
                from app.logger import optimizarr_logger
                optimizarr_logger.log_handbrake_error(
                    self.queue_item['file_path'],
                    f"HandBrakeCLI exited with code {return_code}"
                )
                db.update_queue_item(
                    self.queue_item_id,
                    status='failed',
                    error_message=f"HandBrakeCLI exited with code {return_code}"
                )
                return False
                
        except Exception as e:
            from app.logger import optimizarr_logger
            optimizarr_logger.log_handbrake_error(self.queue_item['file_path'], str(e))
            db.update_queue_item(
                self.queue_item_id,
                status='failed',
                error_message=str(e)
            )
            return False
    
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
            db.update_queue_item(
                self.queue_item_id,
                status='failed',
                error_message='Output file not found'
            )
            optimizarr_logger.log_handbrake_error(
                str(input_path), 'Output file not found after encoding'
            )
            return
        
        # Get file sizes
        original_size = input_path.stat().st_size
        new_size = output_path.stat().st_size
        savings = original_size - new_size
        start_time = self.queue_item.get('started_at', '')
        
        try:
            # Remove original file
            input_path.unlink()
            
            # If container changed, the new file has a different extension
            # Rename to same stem but with new extension
            final_path = input_path.parent / f"{input_path.stem}{output_ext}"
            output_path.rename(final_path)
            
            # Update queue item
            db.update_queue_item(
                self.queue_item_id,
                status='completed',
                progress=100.0,
                completed_at=time.strftime('%Y-%m-%d %H:%M:%S')
            )
            
            # Add to history
            with db.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO history 
                    (file_path, profile_name, original_size_bytes, new_size_bytes, savings_bytes)
                    VALUES (?, ?, ?, ?, ?)
                """, (
                    str(final_path),
                    self.profile['name'],
                    original_size,
                    new_size,
                    savings
                ))
            
            # Log completion
            savings_pct = (savings / original_size * 100) if original_size > 0 else 0
            optimizarr_logger.log_handbrake_complete(str(input_path), {
                "original_size_mb": original_size / (1024**2),
                "new_size_mb": new_size / (1024**2),
                "savings_percent": savings_pct,
                "duration_seconds": 0  # TODO: calculate from started_at
            })
            
        except Exception as e:
            optimizarr_logger.log_handbrake_error(str(input_path), str(e))
            db.update_queue_item(
                self.queue_item_id,
                status='failed',
                error_message=f"Finalization error: {e}"
            )
    
    def pause(self, reason: str = None):
        """Pause the encoding job."""
        if self.process and not self.is_paused:
            try:
                self.process.send_signal(subprocess.signal.SIGSTOP)
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
                self.process.send_signal(subprocess.signal.SIGCONT)
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
            
            # Parse settings with defaults
            return {
                'cpu_threshold': float(settings.get('resource_cpu_threshold', '90.0')),
                'memory_threshold': float(settings.get('resource_memory_threshold', '85.0')),
                'gpu_threshold': float(settings.get('resource_gpu_threshold', '90.0')),
                'nice_level': int(settings.get('resource_nice_level', '10')),
                'enable_throttling': settings.get('resource_enable_throttling', 'true').lower() == 'true'
            }
        except Exception as e:
            print(f"⚠️ Error loading resource settings: {e}")
            # Return defaults
            return {
                'cpu_threshold': 90.0,
                'memory_threshold': 85.0,
                'gpu_threshold': 90.0,
                'nice_level': 10,
                'enable_throttling': True
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
