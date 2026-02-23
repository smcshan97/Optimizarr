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
                conn.commit()

                cursor.execute("""
                    SELECT enabled, days_of_week, start_time, end_time,
                           timezone, use_windows_rest_hours, max_concurrent_jobs
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

                cursor.execute("""
                    UPDATE schedule SET
                        enabled = ?,
                        days_of_week = ?,
                        start_time = ?,
                        end_time = ?,
                        timezone = ?,
                        use_windows_rest_hours = ?,
                        max_concurrent_jobs = ?
                """, (
                    config.get('enabled', False),
                    config.get('days_of_week', '0,1,2,3,4,5,6'),
                    config.get('start_time', '22:00'),
                    config.get('end_time', '06:00'),
                    config.get('timezone', 'local'),
                    config.get('use_windows_rest_hours', False),
                    config.get('max_concurrent_jobs', 1),
                ))
                conn.commit()
            self.load_schedule()
            if self.is_enabled:
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
        """Set up periodic schedule checking."""
        self.scheduler.remove_all_jobs()
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
    
    def check_and_trigger(self):
        """Check schedule and trigger/stop encoding accordingly."""
        from app.encoder import encoder_pool
        if self.manual_override:
            return
        is_scheduled = self.is_within_schedule()
        is_running = encoder_pool.is_running
        if is_scheduled and not is_running:
            print("⏰ Schedule active — Starting encoding")
            t = threading.Thread(target=encoder_pool.process_queue, daemon=True)
            t.start()
        elif not is_scheduled and is_running and not self.manual_override:
            print("⏰ Outside schedule window — Stopping encoding")
            encoder_pool.stop()
    
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


# Global scheduler instance
schedule_manager = ScheduleManager()


def initialize_scheduler():
    schedule_manager.load_schedule()
    schedule_manager.start()
    if schedule_manager.is_enabled:
        schedule_manager.setup_schedule_check()
        print("✓ Scheduler initialized and enabled")
    else:
        print("✓ Scheduler initialized (disabled)")


def shutdown_scheduler():
    schedule_manager.stop()
