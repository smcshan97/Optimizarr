"""
Scheduler module for Optimizarr.
Manages scheduled encoding based on time windows and day-of-week settings.
"""
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from datetime import datetime, time as dt_time
import time
from typing import Optional, Dict, List
from app.database import db


class ScheduleManager:
    """Manages encoding schedule based on time windows and days of week."""
    
    def __init__(self):
        self.scheduler = BackgroundScheduler()
        self.is_enabled = False
        self.schedule_config = None
        self.manual_override = False  # Manual start/stop overrides schedule
        
    def start(self):
        """Start the scheduler."""
        if not self.scheduler.running:
            self.scheduler.start()
            print("✓ Scheduler started")
    
    def stop(self):
        """Stop the scheduler."""
        if self.scheduler.running:
            self.scheduler.shutdown()
            print("✓ Scheduler stopped")
    
    def load_schedule(self) -> Optional[Dict]:
        """Load schedule configuration from database."""
        try:
            with db.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT enabled, days_of_week, start_time, end_time, timezone
                    FROM schedule
                    LIMIT 1
                """)
                row = cursor.fetchone()
                
                if row:
                    self.schedule_config = {
                        'enabled': bool(row[0]),
                        'days_of_week': row[1],  # Comma-separated: "0,1,2,3,4,5,6"
                        'start_time': row[2],    # HH:MM format
                        'end_time': row[3],      # HH:MM format
                        'timezone': row[4] or 'UTC'
                    }
                    self.is_enabled = self.schedule_config['enabled']
                    return self.schedule_config
                else:
                    # Create default schedule (disabled)
                    cursor.execute("""
                        INSERT INTO schedule (enabled, days_of_week, start_time, end_time, timezone)
                        VALUES (0, '0,1,2,3,4,5,6', '22:00', '06:00', 'UTC')
                    """)
                    conn.commit()
                    return self.load_schedule()
                    
        except Exception as e:
            print(f"⚠️ Error loading schedule: {e}")
            return None
    
    def save_schedule(self, config: Dict) -> bool:
        """Save schedule configuration to database."""
        try:
            with db.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    UPDATE schedule SET
                        enabled = ?,
                        days_of_week = ?,
                        start_time = ?,
                        end_time = ?,
                        timezone = ?,
                        updated_at = CURRENT_TIMESTAMP
                """, (
                    config.get('enabled', False),
                    config.get('days_of_week', '0,1,2,3,4,5,6'),
                    config.get('start_time', '22:00'),
                    config.get('end_time', '06:00'),
                    config.get('timezone', 'UTC')
                ))
                conn.commit()
            
            # Reload configuration
            self.load_schedule()
            
            # Restart scheduler if enabled
            if self.is_enabled:
                self.setup_schedule_check()
            
            return True
            
        except Exception as e:
            print(f"✗ Error saving schedule: {e}")
            return False
    
    def is_within_schedule(self) -> bool:
        """Check if current time is within the scheduled encoding window."""
        if not self.is_enabled or not self.schedule_config:
            return False
        
        now = datetime.now()
        current_day = now.weekday()  # 0=Monday, 6=Sunday
        current_time = now.time()
        
        # Check if today is a scheduled day
        scheduled_days = [int(d) for d in self.schedule_config['days_of_week'].split(',')]
        if current_day not in scheduled_days:
            return False
        
        # Parse start and end times
        start_time_str = self.schedule_config['start_time']
        end_time_str = self.schedule_config['end_time']
        
        start_hour, start_min = map(int, start_time_str.split(':'))
        end_hour, end_min = map(int, end_time_str.split(':'))
        
        start_time = dt_time(start_hour, start_min)
        end_time = dt_time(end_hour, end_min)
        
        # Handle overnight schedules (e.g., 22:00 to 06:00)
        if start_time <= end_time:
            # Same day schedule (e.g., 09:00 to 17:00)
            return start_time <= current_time <= end_time
        else:
            # Overnight schedule (e.g., 22:00 to 06:00)
            return current_time >= start_time or current_time <= end_time
    
    def setup_schedule_check(self):
        """Set up periodic schedule checking."""
        # Remove existing jobs
        self.scheduler.remove_all_jobs()
        
        if not self.is_enabled:
            return
        
        # Add a job that runs every minute to check schedule
        self.scheduler.add_job(
            func=self.check_and_trigger,
            trigger='cron',
            minute='*',  # Every minute
            id='schedule_check',
            replace_existing=True
        )
        
        print(f"✓ Schedule check configured (runs every minute)")
    
    def check_and_trigger(self):
        """Check schedule and trigger encoding if within window."""
        from app.encoder import encoder_pool
        
        # Skip if manual override is active
        if self.manual_override:
            return
        
        is_scheduled = self.is_within_schedule()
        is_running = encoder_pool.is_running
        
        if is_scheduled and not is_running:
            print(f"⏰ Schedule active - Starting encoding")
            # Start encoding in a separate thread to avoid blocking scheduler
            import threading
            thread = threading.Thread(target=encoder_pool.process_queue)
            thread.daemon = True
            thread.start()
            
        elif not is_scheduled and is_running and not self.manual_override:
            print(f"⏰ Outside schedule window - Stopping encoding")
            encoder_pool.stop()
    
    def enable_manual_override(self):
        """Enable manual override (user started encoding manually)."""
        self.manual_override = True
        print("✓ Manual override enabled - schedule will not stop encoding")
    
    def disable_manual_override(self):
        """Disable manual override (return to scheduled mode)."""
        self.manual_override = False
        print("✓ Manual override disabled - schedule active")
    
    def get_status(self) -> Dict:
        """Get current schedule status."""
        return {
            'enabled': self.is_enabled,
            'manual_override': self.manual_override,
            'within_schedule': self.is_within_schedule(),
            'config': self.schedule_config,
            'scheduler_running': self.scheduler.running
        }


# Global scheduler instance
schedule_manager = ScheduleManager()


def initialize_scheduler():
    """Initialize the scheduler on application startup."""
    schedule_manager.load_schedule()
    schedule_manager.start()
    
    if schedule_manager.is_enabled:
        schedule_manager.setup_schedule_check()
        print(f"✓ Scheduler initialized and enabled")
    else:
        print(f"✓ Scheduler initialized (disabled)")


def shutdown_scheduler():
    """Shutdown the scheduler on application exit."""
    schedule_manager.stop()
