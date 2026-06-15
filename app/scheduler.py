"""
Scheduler module for Optimizarr.
Manages scheduled encoding based on time windows, day-of-week settings,
and optionally Windows Active Hours (rest hours).
"""
from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime, time as dt_time
import platform
import threading
import time
from typing import Optional, Dict
from app.database import db


class ScheduleManager:
    """Manages encoding schedule based on time windows and days of week."""
    
    def __init__(self):
        self.scheduler = BackgroundScheduler()
        self.is_enabled = False
        self.schedule_config = None
        self.manual_override = False
        
    def start(self):
        if not self.scheduler.running:
            self.scheduler.start()
            print("✓ Scheduler started")
    
    def stop(self):
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
            print("✓ Scheduler stopped")
    
    def load_schedule(self) -> Optional[Dict]:
        """Load schedule configuration from database."""
        try:
            with db.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("PRAGMA table_info(schedule)")
                cols = {row[1] for row in cursor.fetchall()}
                
                # Ensure new columns exist (migration)
                if 'timezone' not in cols:
                    cursor.execute("ALTER TABLE schedule ADD COLUMN timezone TEXT DEFAULT 'local'")
                if 'use_windows_rest_hours' not in cols:
                    cursor.execute("ALTER TABLE schedule ADD COLUMN use_windows_rest_hours BOOLEAN DEFAULT 0")
                if 'max_concurrent_jobs' not in cols:
                    cursor.execute("ALTER TABLE schedule ADD COLUMN max_concurrent_jobs INTEGER DEFAULT 1")
                if 'finish_before_stop' not in cols:
                    cursor.execute("ALTER TABLE schedule ADD COLUMN finish_before_stop BOOLEAN DEFAULT 0")
                conn.commit()

                cursor.execute("""
                    SELECT enabled, days_of_week, start_time, end_time,
                           timezone, use_windows_rest_hours, max_concurrent_jobs,
                           finish_before_stop
                    FROM schedule LIMIT 1
                """)
                row = cursor.fetchone()

                if row:
                    self.schedule_config = {
                        'enabled': bool(row[0]),
                        'days_of_week': row[1] or '0,1,2,3,4,5,6',
                        'start_time': row[2] or '22:00',
                        'end_time': row[3] or '06:00',
                        'timezone': row[4] or 'local',
                        'use_windows_rest_hours': bool(row[5]),
                        'max_concurrent_jobs': int(row[6] or 1),
                        'finish_before_stop': bool(row[7]),
                    }
                    self.is_enabled = self.schedule_config['enabled']
                    return self.schedule_config
                else:
                    cursor.execute("""
                        INSERT INTO schedule (enabled, days_of_week, start_time, end_time,
                                              timezone, use_windows_rest_hours, max_concurrent_jobs)
                        VALUES (0, '0,1,2,3,4,5,6', '22:00', '06:00', 'local', 0, 1)
                    """)
                    conn.commit()
                    return self.load_schedule()
        except Exception as e:
            print(f"⚠ Error loading schedule: {e}")
            return None
    
    def save_schedule(self, config: Dict) -> bool:
        """Save schedule configuration to database."""
        try:
            with db.get_connection() as conn:
                cursor = conn.cursor()
                # Ensure columns exist before writing
                cursor.execute("PRAGMA table_info(schedule)")
                cols = {row[1] for row in cursor.fetchall()}
                if 'timezone' not in cols:
                    cursor.execute("ALTER TABLE schedule ADD COLUMN timezone TEXT DEFAULT 'local'")
                if 'use_windows_rest_hours' not in cols:
                    cursor.execute("ALTER TABLE schedule ADD COLUMN use_windows_rest_hours BOOLEAN DEFAULT 0")
                if 'max_concurrent_jobs' not in cols:
                    cursor.execute("ALTER TABLE schedule ADD COLUMN max_concurrent_jobs INTEGER DEFAULT 1")
                if 'finish_before_stop' not in cols:
                    cursor.execute("ALTER TABLE schedule ADD COLUMN finish_before_stop BOOLEAN DEFAULT 0")

                cursor.execute("""
                    UPDATE schedule SET
                        enabled = ?,
                        days_of_week = ?,
                        start_time = ?,
                        end_time = ?,
                        timezone = ?,
                        use_windows_rest_hours = ?,
                        max_concurrent_jobs = ?,
                        finish_before_stop = ?
                """, (
                    config.get('enabled', False),
                    config.get('days_of_week', '0,1,2,3,4,5,6'),
                    config.get('start_time', '22:00'),
                    config.get('end_time', '06:00'),
                    config.get('timezone', 'local'),
                    config.get('use_windows_rest_hours', False),
                    config.get('max_concurrent_jobs', 1),
                    config.get('finish_before_stop', False),
                ))
                conn.commit()
            self.load_schedule()
            # ALWAYS reconcile the cron job: setup_schedule_check() adds it when
            # enabled and REMOVES it when disabled. The old code only called it
            # when enabled, so disabling left a stale every-minute checker
            # running (which then stopped manual encodes). Saving the schedule
            # is also a deliberate "let the scheduler manage this" action, so
            # hand control back from any manual override.
            self.manual_override = False
            self.setup_schedule_check()
            return True
        except Exception as e:
            print(f"✗ Error saving schedule: {e}")
            return False

    # ------------------------------------------------------------------
    # Windows Active Hours (rest hours)
    # ------------------------------------------------------------------

    @staticmethod
    def get_windows_active_hours() -> Optional[Dict]:
        """
        Read Windows Active Hours from the registry.
        Active Hours = the times the user is ACTIVE (not rest).
        We invert them to get rest hours for encoding.
        Returns None on non-Windows or if registry key missing.
        """
        if platform.system() != "Windows":
            return None
        try:
            import winreg
            key_path = r"SOFTWARE\Microsoft\WindowsUpdate\UX\Settings"
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path, 0,
                                winreg.KEY_READ) as key:
                active_start, _ = winreg.QueryValueEx(key, "ActiveHoursStart")
                active_end, _ = winreg.QueryValueEx(key, "ActiveHoursEnd")
            # Active hours: user is awake from active_start to active_end
            # Rest hours (good for encoding): from active_end to active_start
            return {
                "active_start": int(active_start),   # e.g. 8  → 08:00
                "active_end": int(active_end),         # e.g. 22 → 22:00
                "rest_start": int(active_end),          # encoding starts at active_end
                "rest_end": int(active_start),           # encoding ends at active_start
                "rest_start_str": f"{int(active_end):02d}:00",
                "rest_end_str": f"{int(active_start):02d}:00",
            }
        except Exception as e:
            print(f"⚠ Could not read Windows Active Hours: {e}")
            return None

    # ------------------------------------------------------------------
    # Schedule window check
    # ------------------------------------------------------------------

    def is_within_schedule(self) -> bool:
        """Check if current time is within the scheduled encoding window."""
        if not self.is_enabled or not self.schedule_config:
            return False
        
        now = datetime.now()
        current_day = now.weekday()  # 0=Monday, 6=Sunday

        # Day-of-week check
        try:
            scheduled_days = [int(d) for d in self.schedule_config['days_of_week'].split(',')]
        except Exception:
            scheduled_days = list(range(7))
        if current_day not in scheduled_days:
            return False

        # Determine effective time window
        if self.schedule_config.get('use_windows_rest_hours'):
            win_hours = self.get_windows_active_hours()
            if win_hours:
                start_str = win_hours['rest_start_str']
                end_str = win_hours['rest_end_str']
            else:
                start_str = self.schedule_config.get('start_time', '22:00')
                end_str = self.schedule_config.get('end_time', '06:00')
        else:
            start_str = self.schedule_config.get('start_time', '22:00')
            end_str = self.schedule_config.get('end_time', '06:00')

        try:
            sh, sm = map(int, start_str.split(':'))
            eh, em = map(int, end_str.split(':'))
        except Exception:
            return False

        start_t = dt_time(sh, sm)
        end_t = dt_time(eh, em)
        current_t = now.time()

        # Handle overnight schedules (22:00 → 06:00)
        if start_t <= end_t:
            return start_t <= current_t <= end_t
        else:
            return current_t >= start_t or current_t <= end_t
    
    def setup_schedule_check(self):
        """Set up periodic schedule checking.

        Removes only its own job — remove_all_jobs() would also kill the
        auto-sync tick, which runs independently of the encode schedule.
        """
        try:
            self.scheduler.remove_job('schedule_check')
        except Exception:
            pass
        if not self.is_enabled:
            return
        self.scheduler.add_job(
            func=self.check_and_trigger,
            trigger='cron',
            minute='*',
            id='schedule_check',
            replace_existing=True
        )
        print("✓ Schedule check configured (runs every minute)")

    def setup_sync_check(self):
        """Recurring auto-sync tick for external connections (Patch 37).

        One job checks ALL connections every 15 minutes and syncs those
        whose interval has elapsed — no per-connection job management, and
        interval changes take effect on the next tick without re-registering.
        """
        self.scheduler.add_job(
            func=self.check_auto_sync,
            trigger='interval',
            minutes=15,
            id='auto_sync_check',
            replace_existing=True,
        )
        print("✓ Auto-sync check configured (every 15 minutes)")

    def check_auto_sync(self):
        """Sync every enabled connection whose sync interval has elapsed."""
        try:
            connections = db.get_external_connections()
        except Exception:
            return
        now = datetime.utcnow()
        for conn in connections:
            if not _sync_due(conn, now):
                continue
            from app.api.connection_routes import _sync_connection_task
            from app.devlog import devlog
            print(f"⏰ Auto-sync: {conn['name']} (every {conn['sync_interval_hours']}h)")
            devlog('auto_sync', name=conn['name'],
                   ivl=conn['sync_interval_hours'])
            t = threading.Thread(
                target=_sync_connection_task, args=(conn['id'],), daemon=True
            )
            t.start()
    
    def check_and_trigger(self):
        """Check schedule and start/stop encoding accordingly.

        A DISABLED schedule makes the scheduler fully inert — it must never
        start OR stop encoding (encoding is then 100% manual). Previously a
        disabled schedule still ran this check, and because is_within_schedule()
        returns False when disabled, it stopped any manual encode every minute.
        That, plus save_schedule() not tearing down this cron job on disable,
        was the bug that killed an entire weekend of encoding.
        """
        from app.encoder import encoder_pool
        if not self.is_enabled or self.manual_override:
            return
        is_scheduled = self.is_within_schedule()
        is_running = encoder_pool.is_running
        if is_scheduled and not is_running:
            print("⏰ Schedule active — Starting encoding")
            t = threading.Thread(target=encoder_pool.process_queue, daemon=True)
            t.start()
        elif not is_scheduled and is_running:
            # Hard stop unless the user opted to let the current encode finish
            graceful = bool((self.schedule_config or {}).get('finish_before_stop'))
            print(f"⏰ Outside schedule window — Stopping encoding"
                  f"{' after current job' if graceful else ''}")
            encoder_pool.stop(graceful=graceful)
    
    def should_encode_now(self) -> bool:
        """Whether encoding is permitted right now (schedule policy only).

        Disabled schedule = no time restriction, encode anytime. Enabled =
        only inside the configured window. Used to wake the encoder on new
        work (webhooks, sync, boot) without fighting the schedule.
        """
        if not self.is_enabled:
            return True
        return self.is_within_schedule()

    def enable_manual_override(self):
        self.manual_override = True
        print("✓ Manual override enabled")
    
    def disable_manual_override(self):
        self.manual_override = False
        print("✓ Manual override disabled")
    
    def get_status(self) -> Dict:
        windows_hours = None
        if self.schedule_config and self.schedule_config.get('use_windows_rest_hours'):
            windows_hours = self.get_windows_active_hours()
        return {
            'enabled': self.is_enabled,
            'manual_override': self.manual_override,
            'within_schedule': self.is_within_schedule(),
            'config': self.schedule_config,
            'scheduler_running': self.scheduler.running,
            'windows_active_hours': windows_hours,
        }


def _sync_due(conn: Dict, now: datetime) -> bool:
    """True when an enabled connection's auto-sync interval has elapsed.

    interval 0/absent = auto-sync off. Never-synced connections (last_synced
    NULL) are due immediately. last_synced is stored as UTC
    '%Y-%m-%d %H:%M:%S'; an unparseable value counts as due.
    """
    interval = conn.get('sync_interval_hours') or 0
    if not conn.get('enabled') or interval <= 0:
        return False
    last = conn.get('last_synced')
    if not last:
        return True
    try:
        last_dt = datetime.strptime(str(last)[:19], '%Y-%m-%d %H:%M:%S')
    except ValueError:
        return True
    return (now - last_dt).total_seconds() >= interval * 3600


# Global scheduler instance
schedule_manager = ScheduleManager()


def initialize_scheduler():
    schedule_manager.load_schedule()
    schedule_manager.start()
    # Auto-sync runs regardless of whether the encode schedule is enabled
    schedule_manager.setup_sync_check()
    if schedule_manager.is_enabled:
        schedule_manager.setup_schedule_check()
        print("✓ Scheduler initialized and enabled")
    else:
        print("✓ Scheduler initialized (disabled)")


def shutdown_scheduler():
    schedule_manager.stop()
