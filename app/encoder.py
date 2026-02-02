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
        
        # Create output path (temporary location)
        input_path = Path(input_file)
        output_file = str(input_path.parent / f"{input_path.stem}_optimized{input_path.suffix}")
        
        # Base command
        cmd = ['HandBrakeCLI', '-i', input_file, '-o', output_file]
        
        # Encoder selection
        encoder = self.profile['encoder']
        
        # Map encoder names to HandBrake encoder names
        encoder_map = {
            'svt_av1': 'svt_av1',
            'x265': 'x265',
            'x264': 'x264',
            'nvenc_h265': 'nvenc_h265',
            'nvenc_h264': 'nvenc_h264',
            'nvenc_av1': 'nvenc_av1',
            'qsv_h265': 'qsv_h265',
            'qsv_h264': 'qsv_h264'
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
        
        # Audio
        audio_codec = self.profile['audio_codec']
        if audio_codec == 'passthrough':
            cmd.extend(['--audio', '1', '--aencoder', 'copy'])
        else:
            cmd.extend(['--audio', '1', '--aencoder', audio_codec])
        
        # Two-pass encoding
        if self.profile.get('two_pass'):
            cmd.append('--two-pass')
        
        # Custom arguments
        if self.profile.get('custom_args'):
            custom_args = self.profile['custom_args'].split()
            cmd.extend(custom_args)
        
        return cmd
    
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
        
        print(f"Starting encoding: {Path(self.queue_item['file_path']).name}")
        print(f"Command: {' '.join(cmd)}")
        
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
                print(f"\n✓ Encoding completed: {Path(self.queue_item['file_path']).name}")
                self._finalize_encoding()
                return True
            else:
                print(f"\n✗ Encoding failed with code {return_code}")
                db.update_queue_item(
                    self.queue_item_id,
                    status='failed',
                    error_message=f"HandBrakeCLI exited with code {return_code}"
                )
                return False
                
        except Exception as e:
            print(f"\n✗ Encoding error: {e}")
            db.update_queue_item(
                self.queue_item_id,
                status='failed',
                error_message=str(e)
            )
            return False
    
    def _finalize_encoding(self):
        """Replace original file with encoded version and update database."""
        input_path = Path(self.queue_item['file_path'])
        output_path = input_path.parent / f"{input_path.stem}_optimized{input_path.suffix}"
        
        if not output_path.exists():
            db.update_queue_item(
                self.queue_item_id,
                status='failed',
                error_message='Output file not found'
            )
            return
        
        # Get file sizes
        original_size = input_path.stat().st_size
        new_size = output_path.stat().st_size
        savings = original_size - new_size
        
        # Backup original (optional - for now just replace)
        try:
            # Replace original with new file
            input_path.unlink()
            output_path.rename(input_path)
            
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
                    str(input_path),
                    self.profile['name'],
                    original_size,
                    new_size,
                    savings
                ))
            
            print(f"  Saved {savings / (1024**2):.1f} MB ({(savings/original_size)*100:.1f}%)")
            
        except Exception as e:
            print(f"✗ Error finalizing: {e}")
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


# Global encoder pool instance
encoder_pool = EncoderPool(max_concurrent=1)
