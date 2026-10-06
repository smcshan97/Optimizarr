"""Encoder pool: the single queue loop, serial or parallel jobs."""
import threading
import time
from typing import Dict, TYPE_CHECKING

from app.database import db
from app.logger import optimizarr_logger
from app.resources import resource_monitor

if TYPE_CHECKING:
    from app.encoder import EncodingJob


class EncoderPool:
    """Manages a pool of encoding jobs."""
    
    def __init__(self, max_concurrent: int = 1):
        self.max_concurrent = max_concurrent
        self.active_jobs: 'list[EncodingJob]' = []
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
                        job = _encoding_job_cls()(item['id'], profile, resource_limits)
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
                job = _encoding_job_cls()(item['id'], profile, resource_limits)
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


def _encoding_job_cls():
    from app.encoder import EncodingJob
    return EncodingJob
