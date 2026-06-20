"""
Database module for Optimizarr.
Handles SQLite database initialization, schema creation, and CRUD operations.
"""
import sqlite3
import json
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any
from contextlib import contextmanager

from app.config import settings


def _safe_json(raw, default=None):
    """Parse a JSON column value, returning ``default`` on bad/empty data.

    A single corrupt current_specs/target_specs cell must not crash a whole
    queue read — that would freeze every polled UI that lists the queue.
    """
    if not raw:
        return default
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return default


class Database:
    """SQLite database manager for Optimizarr."""
    
    def __init__(self, db_path: str = None):
        self.db_path = db_path or settings.db_path
        self._write_lock = __import__('threading').Lock()
        
        # CRITICAL: Ensure directory exists before trying to connect
        db_file = Path(self.db_path)
        db_file.parent.mkdir(parents=True, exist_ok=True)
        
        # Enable WAL mode once at startup for concurrent read/write safety
        _conn = sqlite3.connect(self.db_path)
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA busy_timeout = 5000")
        _conn.close()
        
        self.initialize_database()
    
    @contextmanager
    def get_connection(self, write: bool = True):
        """Context manager for a database connection.

        WAL mode (set at init) allows unlimited concurrent readers alongside
        the single writer, so READS take no lock — serializing them behind
        the global write-lock was what let a busy encoder freeze the whole
        UI (an async handler blocking the event loop on lock.acquire()).

        ``write`` defaults to True so any caller that doesn't opt out keeps
        the original serialized-write behavior — a missed call site is merely
        unoptimized, never unsafe. Read-only callers pass ``write=False``
        (or use :meth:`get_read_connection`).
        """
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row  # Enable dict-like access
        conn.execute("PRAGMA busy_timeout = 5000")
        if write:
            self._write_lock.acquire()
        try:
            yield conn
            if write:
                conn.commit()
        except Exception as e:
            if write:
                conn.rollback()
            raise e
        finally:
            if write:
                self._write_lock.release()
            conn.close()

    def get_read_connection(self):
        """Lock-free read-only connection (see :meth:`get_connection`)."""
        return self.get_connection(write=False)
    
    def initialize_database(self):
        """Create database schema if it doesn't exist."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            # Profiles table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS profiles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE NOT NULL,
                    resolution TEXT,
                    framerate INTEGER,
                    codec TEXT NOT NULL,
                    encoder TEXT NOT NULL,
                    quality INTEGER NOT NULL,
                    audio_codec TEXT NOT NULL,
                    container TEXT DEFAULT 'mkv',
                    audio_handling TEXT DEFAULT 'preserve_all',
                    subtitle_handling TEXT DEFAULT 'none',
                    enable_filters BOOLEAN DEFAULT 0,
                    chapter_markers BOOLEAN DEFAULT 1,
                    hw_accel_enabled BOOLEAN DEFAULT 0,
                    preset TEXT,
                    two_pass BOOLEAN DEFAULT 0,
                    custom_args TEXT,
                    is_default BOOLEAN DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Scan roots table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS scan_roots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    path TEXT UNIQUE NOT NULL,
                    profile_id INTEGER,
                    library_type TEXT DEFAULT 'custom',
                    enabled BOOLEAN DEFAULT 1,
                    recursive BOOLEAN DEFAULT 1,
                    FOREIGN KEY (profile_id) REFERENCES profiles(id)
                )
            """)
            
            # Queue table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS queue (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    file_path TEXT NOT NULL,
                    root_id INTEGER,
                    profile_id INTEGER,
                    status TEXT DEFAULT 'pending',
                    priority INTEGER DEFAULT 0,
                    current_specs TEXT,
                    target_specs TEXT,
                    file_size_bytes INTEGER,
                    estimated_savings_bytes INTEGER,
                    progress REAL DEFAULT 0.0,
                    current_cpu_percent REAL DEFAULT 0.0,
                    current_memory_mb REAL DEFAULT 0.0,
                    permission_status TEXT,
                    permission_message TEXT,
                    paused_reason TEXT,
                    error_message TEXT,
                    started_at TIMESTAMP,
                    completed_at TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (root_id) REFERENCES scan_roots(id),
                    FOREIGN KEY (profile_id) REFERENCES profiles(id)
                )
            """)
            
            # Users table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    email TEXT,
                    is_admin BOOLEAN DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_login TIMESTAMP
                )
            """)
            
            # Sessions table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    token TEXT UNIQUE NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    expires_at TIMESTAMP NOT NULL,
                    last_used TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                )
            """)
            
            # API keys table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS api_keys (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    key TEXT UNIQUE NOT NULL,
                    name TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_used TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                )
            """)
            
            # Schedule table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS schedule (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    enabled BOOLEAN DEFAULT 0,
                    days_of_week TEXT DEFAULT '0,1,2,3,4,5,6',
                    start_time TEXT DEFAULT '22:00',
                    end_time TEXT DEFAULT '06:00',
                    max_concurrent_jobs INTEGER DEFAULT 1
                )
            """)
            
            # Settings table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS settings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    key TEXT UNIQUE NOT NULL,
                    value TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # History table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    file_path TEXT NOT NULL,
                    profile_name TEXT,
                    original_size_bytes INTEGER,
                    new_size_bytes INTEGER,
                    savings_bytes INTEGER,
                    encoding_time_seconds INTEGER,
                    codec TEXT,
                    container TEXT,
                    completed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Folder watches table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS folder_watches (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    path TEXT UNIQUE NOT NULL,
                    profile_id INTEGER NOT NULL,
                    enabled BOOLEAN DEFAULT 1,
                    recursive BOOLEAN DEFAULT 1,
                    auto_queue BOOLEAN DEFAULT 1,
                    extensions TEXT DEFAULT '.mkv,.mp4,.avi,.mov,.wmv,.flv,.webm,.m4v,.ts,.mpg,.mpeg',
                    last_check TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (profile_id) REFERENCES profiles(id)
                )
            """)
            
            # Incoming webhook dead-letter table — every received webhook is
            # persisted BEFORE processing so events survive restarts/crashes.
            # processed_at NULL = not yet (or unsuccessfully) processed.
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS webhook_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    app_type TEXT NOT NULL,
                    payload_json TEXT,
                    received_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    processed_at TIMESTAMP,
                    error TEXT
                )
            """)

            # Notification webhooks table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS notifications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    webhook_url TEXT NOT NULL,
                    webhook_type TEXT DEFAULT 'discord',
                    events TEXT DEFAULT '["encode_complete","encode_failed","queue_empty"]',
                    enabled BOOLEAN DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Insert default schedule if none exists
            cursor.execute("SELECT COUNT(*) FROM schedule")
            if cursor.fetchone()[0] == 0:
                cursor.execute("""
                    INSERT INTO schedule (enabled, days_of_week, start_time, end_time)
                    VALUES (0, '0,1,2,3,4,5,6', '22:00', '06:00')
                """)
            
            # ---- MIGRATIONS for existing databases ----
            # Add 'container' column to profiles if missing
            cursor.execute("PRAGMA table_info(profiles)")
            profile_columns = [col[1] for col in cursor.fetchall()]
            if 'container' not in profile_columns:
                cursor.execute("ALTER TABLE profiles ADD COLUMN container TEXT DEFAULT 'mkv'")
                print("  ↳ Migrated: added 'container' column to profiles")
            
            # Phase 2 migrations
            if 'audio_handling' not in profile_columns:
                cursor.execute("ALTER TABLE profiles ADD COLUMN audio_handling TEXT DEFAULT 'preserve_all'")
                print("  ↳ Migrated: added 'audio_handling' column to profiles")
            if 'subtitle_handling' not in profile_columns:
                cursor.execute("ALTER TABLE profiles ADD COLUMN subtitle_handling TEXT DEFAULT 'none'")
                print("  ↳ Migrated: added 'subtitle_handling' column to profiles")
            if 'enable_filters' not in profile_columns:
                cursor.execute("ALTER TABLE profiles ADD COLUMN enable_filters BOOLEAN DEFAULT 0")
                print("  ↳ Migrated: added 'enable_filters' column to profiles")
            if 'chapter_markers' not in profile_columns:
                cursor.execute("ALTER TABLE profiles ADD COLUMN chapter_markers BOOLEAN DEFAULT 1")
                print("  ↳ Migrated: added 'chapter_markers' column to profiles")
            if 'hw_accel_enabled' not in profile_columns:
                cursor.execute("ALTER TABLE profiles ADD COLUMN hw_accel_enabled BOOLEAN DEFAULT 0")
                print("  ↳ Migrated: added 'hw_accel_enabled' column to profiles")
            
            # Add columns to scan_roots if missing
            cursor.execute("PRAGMA table_info(scan_roots)")
            root_columns = [col[1] for col in cursor.fetchall()]
            if 'library_type' not in root_columns:
                cursor.execute("ALTER TABLE scan_roots ADD COLUMN library_type TEXT DEFAULT 'custom'")
                print("  ↳ Migrated: added 'library_type' column to scan_roots")
            if 'show_in_stats' not in root_columns:
                cursor.execute("ALTER TABLE scan_roots ADD COLUMN show_in_stats BOOLEAN DEFAULT 1")
                print("  ↳ Migrated: added 'show_in_stats' column to scan_roots")
            
            # History table migrations
            cursor.execute("PRAGMA table_info(history)")
            history_columns = [col[1] for col in cursor.fetchall()]
            if 'codec' not in history_columns:
                cursor.execute("ALTER TABLE history ADD COLUMN codec TEXT")
                print("  ↳ Migrated: added 'codec' column to history")
            if 'container' not in history_columns:
                cursor.execute("ALTER TABLE history ADD COLUMN container TEXT")
                print("  ↳ Migrated: added 'container' column to history")
            
            # External connections table (Sonarr / Radarr / Stash)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS external_connections (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    name            TEXT NOT NULL,
                    app_type        TEXT NOT NULL,
                    base_url        TEXT NOT NULL,
                    api_key_encrypted TEXT NOT NULL,
                    enabled         BOOLEAN DEFAULT 1,
                    show_in_stats   BOOLEAN DEFAULT 1,
                    last_tested     TIMESTAMP,
                    last_synced     TIMESTAMP,
                    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # scan_roots: link to an external connection (optional)
            cursor.execute("PRAGMA table_info(scan_roots)")
            root_columns_check = [col[1] for col in cursor.fetchall()]
            if 'external_connection_id' not in root_columns_check:
                cursor.execute(
                    "ALTER TABLE scan_roots ADD COLUMN external_connection_id INTEGER"
                )
                print("  ↳ Migrated: added 'external_connection_id' to scan_roots")

            # scan_roots: AI upscale settings per library
            cursor.execute("PRAGMA table_info(scan_roots)")
            root_cols = [col[1] for col in cursor.fetchall()]
            upscale_migrations = [
                ("upscale_enabled",       "BOOLEAN DEFAULT 0"),
                ("upscale_trigger_below", "INTEGER DEFAULT 720"),
                ("upscale_target_height", "INTEGER DEFAULT 1080"),
                ("upscale_model",         "TEXT DEFAULT 'realesrgan-x4plus'"),
                ("upscale_factor",        "INTEGER DEFAULT 2"),
                ("upscale_key",           "TEXT DEFAULT 'realesrgan'"),
            ]
            for col_name, col_def in upscale_migrations:
                if col_name not in root_cols:
                    cursor.execute(
                        f"ALTER TABLE scan_roots ADD COLUMN {col_name} {col_def}"
                    )
                    print(f"  ↳ Migrated: added '{col_name}' to scan_roots")

            # queue: upscale_plan stores the per-item upscale JSON
            cursor.execute("PRAGMA table_info(queue)")
            queue_cols = [col[1] for col in cursor.fetchall()]
            if 'upscale_plan' not in queue_cols:
                cursor.execute("ALTER TABLE queue ADD COLUMN upscale_plan TEXT")
                print("  ↳ Migrated: added 'upscale_plan' to queue")

            # profiles: AI upscale settings per profile
            cursor.execute("PRAGMA table_info(profiles)")
            profile_cols = [col[1] for col in cursor.fetchall()]
            profile_upscale_migrations = [
                ("upscale_enabled",       "BOOLEAN DEFAULT 0"),
                ("upscale_trigger_below", "INTEGER DEFAULT 720"),
                ("upscale_target_height", "INTEGER DEFAULT 1080"),
                ("upscale_model",         "TEXT DEFAULT 'realesrgan-x4plus'"),
                ("upscale_factor",        "INTEGER DEFAULT 2"),
                ("upscale_key",           "TEXT DEFAULT 'realesrgan'"),
            ]
            for col_name, col_def in profile_upscale_migrations:
                if col_name not in profile_cols:
                    cursor.execute(f"ALTER TABLE profiles ADD COLUMN {col_name} {col_def}")
                    print(f"  ↳ Migrated: added '{col_name}' to profiles")

            # profiles: 3D / stereo conversion settings per profile
            cursor.execute("PRAGMA table_info(profiles)")
            profile_cols_stereo = [col[1] for col in cursor.fetchall()]
            profile_stereo_migrations = [
                ("stereo_enabled",     "BOOLEAN DEFAULT 0"),
                ("stereo_mode",        "TEXT DEFAULT '2d_to_3d'"),
                ("stereo_format",      "TEXT DEFAULT 'half_sbs'"),
                ("stereo_divergence",  "REAL DEFAULT 2.0"),
                ("stereo_convergence", "REAL DEFAULT 0.5"),
                ("stereo_depth_model", "TEXT DEFAULT 'Any_V2_S'"),
            ]
            for col_name, col_def in profile_stereo_migrations:
                if col_name not in profile_cols_stereo:
                    cursor.execute(f"ALTER TABLE profiles ADD COLUMN {col_name} {col_def}")
                    print(f"  ↳ Migrated: added '{col_name}' to profiles")

            # scan_roots: 3D / stereo conversion settings per library
            cursor.execute("PRAGMA table_info(scan_roots)")
            root_cols_stereo = [col[1] for col in cursor.fetchall()]
            scanroot_stereo_migrations = [
                ("stereo_enabled",     "BOOLEAN DEFAULT 0"),
                ("stereo_mode",        "TEXT DEFAULT '2d_to_3d'"),
                ("stereo_format",      "TEXT DEFAULT 'half_sbs'"),
                ("stereo_divergence",  "REAL DEFAULT 2.0"),
                ("stereo_convergence", "REAL DEFAULT 0.5"),
                ("stereo_depth_model", "TEXT DEFAULT 'Any_V2_S'"),
            ]
            for col_name, col_def in scanroot_stereo_migrations:
                if col_name not in root_cols_stereo:
                    cursor.execute(f"ALTER TABLE scan_roots ADD COLUMN {col_name} {col_def}")
                    print(f"  ↳ Migrated: added '{col_name}' to scan_roots")

            # queue: stereo_plan stores the per-item stereo conversion JSON
            cursor.execute("PRAGMA table_info(queue)")
            queue_cols_stereo = [col[1] for col in cursor.fetchall()]
            if 'stereo_plan' not in queue_cols_stereo:
                cursor.execute("ALTER TABLE queue ADD COLUMN stereo_plan TEXT")
                print("  ↳ Migrated: added 'stereo_plan' to queue")

            # duration_seconds — real video duration for accurate ETA + savings
            cursor.execute("PRAGMA table_info(queue)")
            queue_cols_dur = [col[1] for col in cursor.fetchall()]
            if 'duration_seconds' not in queue_cols_dur:
                cursor.execute("ALTER TABLE queue ADD COLUMN duration_seconds REAL DEFAULT 0")
                print("  ↳ Migrated: added 'duration_seconds' to queue")

            cursor.execute("PRAGMA table_info(history)")
            history_cols = [col[1] for col in cursor.fetchall()]
            if 'duration_seconds' not in history_cols:
                cursor.execute("ALTER TABLE history ADD COLUMN duration_seconds REAL DEFAULT 0")
                print("  ↳ Migrated: added 'duration_seconds' to history")

            # retry_count — automatic retry attempts for failed encodes
            cursor.execute("PRAGMA table_info(queue)")
            queue_cols_retry = [col[1] for col in cursor.fetchall()]
            if 'retry_count' not in queue_cols_retry:
                cursor.execute("ALTER TABLE queue ADD COLUMN retry_count INTEGER DEFAULT 0")
                print("  ↳ Migrated: added 'retry_count' to queue")

            # Live encode telemetry parsed from HandBrakeCLI output (Patch 33)
            cursor.execute("PRAGMA table_info(queue)")
            queue_cols_live = [col[1] for col in cursor.fetchall()]
            if 'current_fps' not in queue_cols_live:
                cursor.execute("ALTER TABLE queue ADD COLUMN current_fps REAL DEFAULT 0")
                print("  ↳ Migrated: added 'current_fps' to queue")
            if 'eta_seconds' not in queue_cols_live:
                cursor.execute("ALTER TABLE queue ADD COLUMN eta_seconds INTEGER DEFAULT 0")
                print("  ↳ Migrated: added 'eta_seconds' to queue")

            # retry_after — backoff gate; a re-queued failure isn't eligible to
            # run again until this UTC timestamp (NULL = eligible now) (Patch 39)
            cursor.execute("PRAGMA table_info(queue)")
            queue_cols_ra = [col[1] for col in cursor.fetchall()]
            if 'retry_after' not in queue_cols_ra:
                cursor.execute("ALTER TABLE queue ADD COLUMN retry_after TIMESTAMP")
                print("  ↳ Migrated: added 'retry_after' to queue")

            # Auto-sync interval for external connections (Patch 37); 0 = off
            cursor.execute("PRAGMA table_info(external_connections)")
            conn_cols = [col[1] for col in cursor.fetchall()]
            if 'sync_interval_hours' not in conn_cols:
                cursor.execute(
                    "ALTER TABLE external_connections "
                    "ADD COLUMN sync_interval_hours INTEGER DEFAULT 0"
                )
                print("  ↳ Migrated: added 'sync_interval_hours' to external_connections")

            # folder_watches.scan_root_id — links a watch to its library (scan root).
            # Folder watching is now toggled per-library via the eye toggle.
            cursor.execute("PRAGMA table_info(folder_watches)")
            fw_cols = [col[1] for col in cursor.fetchall()]
            if 'scan_root_id' not in fw_cols:
                cursor.execute("ALTER TABLE folder_watches ADD COLUMN scan_root_id INTEGER")
                print("  ↳ Migrated: added 'scan_root_id' to folder_watches")
                # Best-effort: link existing watches to a scan root with the same path
                cursor.execute("""
                    UPDATE folder_watches
                    SET scan_root_id = (
                        SELECT sr.id FROM scan_roots sr WHERE sr.path = folder_watches.path
                    )
                    WHERE scan_root_id IS NULL
                """)

            # Priority scheme: rank-based (1 = first to encode, ascending).
            # One-time renumber of the old "higher number = sooner" scheme,
            # preserving each item's existing position in the queue.
            cursor.execute("SELECT value FROM settings WHERE key = 'priority_scheme'")
            scheme_row = cursor.fetchone()
            if not scheme_row or scheme_row[0] != 'rank':
                cursor.execute("""
                    UPDATE queue SET priority = (
                        SELECT rn FROM (
                            SELECT id, ROW_NUMBER() OVER (
                                ORDER BY priority DESC, created_at
                            ) AS rn FROM queue
                        ) ranked WHERE ranked.id = queue.id
                    )
                """)
                cursor.execute("""
                    INSERT INTO settings (key, value, updated_at)
                    VALUES ('priority_scheme', 'rank', CURRENT_TIMESTAMP)
                    ON CONFLICT(key) DO UPDATE SET
                        value = 'rank', updated_at = CURRENT_TIMESTAMP
                """)
                print("  ↳ Migrated: queue priorities renumbered to rank scheme (1 = first)")

            # Security: must_change_password flag for default admin
            try:
                cursor.execute("SELECT must_change_password FROM users LIMIT 1")
            except Exception:
                cursor.execute("ALTER TABLE users ADD COLUMN must_change_password BOOLEAN DEFAULT 0")
                # Flag the default admin to force a password change
                cursor.execute("UPDATE users SET must_change_password = 1 WHERE username = 'admin'")
                print("  ↳ Migrated: added 'must_change_password' to users")

            # Enforce single default profile — keep only the lowest-id one
            cursor.execute("SELECT id FROM profiles WHERE is_default = 1 ORDER BY id")
            default_rows = cursor.fetchall()
            if len(default_rows) > 1:
                keep_id = default_rows[0][0]
                cursor.execute("UPDATE profiles SET is_default = 0 WHERE is_default = 1 AND id != ?", (keep_id,))
                print(f"  ↳ Migrated: enforced single default profile (kept id={keep_id})")

            print("✓ Database schema initialized")
    
    # Profile CRUD
    def create_profile(self, **kwargs) -> int:
        """Create a new encoding profile. Enforces only one default at a time."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            # If this new profile is being set as default, clear any existing default first
            if kwargs.get('is_default', False):
                cursor.execute("UPDATE profiles SET is_default = 0 WHERE is_default = 1")
            cursor.execute("""
                INSERT INTO profiles 
                (name, resolution, framerate, codec, encoder, quality, audio_codec, 
                 container, audio_handling, subtitle_handling, enable_filters,
                 chapter_markers, hw_accel_enabled, preset, two_pass, custom_args, is_default,
                 upscale_enabled, upscale_trigger_below, upscale_target_height,
                 upscale_model, upscale_factor, upscale_key,
                 stereo_enabled, stereo_mode, stereo_format,
                 stereo_divergence, stereo_convergence, stereo_depth_model)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                kwargs.get('name'),
                kwargs.get('resolution'),
                kwargs.get('framerate'),
                kwargs.get('codec'),
                kwargs.get('encoder'),
                kwargs.get('quality'),
                kwargs.get('audio_codec'),
                kwargs.get('container', 'mkv'),
                kwargs.get('audio_handling', 'preserve_all'),
                kwargs.get('subtitle_handling', 'none'),
                kwargs.get('enable_filters', False),
                kwargs.get('chapter_markers', True),
                kwargs.get('hw_accel_enabled', False),
                kwargs.get('preset'),
                kwargs.get('two_pass', False),
                kwargs.get('custom_args'),
                kwargs.get('is_default', False),
                kwargs.get('upscale_enabled', False),
                kwargs.get('upscale_trigger_below', 720),
                kwargs.get('upscale_target_height', 1080),
                kwargs.get('upscale_model', 'realesrgan-x4plus'),
                kwargs.get('upscale_factor', 2),
                kwargs.get('upscale_key', 'realesrgan'),
                kwargs.get('stereo_enabled', False),
                kwargs.get('stereo_mode', '2d_to_3d'),
                kwargs.get('stereo_format', 'half_sbs'),
                kwargs.get('stereo_divergence', 2.0),
                kwargs.get('stereo_convergence', 0.5),
                kwargs.get('stereo_depth_model', 'Any_V2_S'),
            ))
            return cursor.lastrowid
    
    def update_profile(self, profile_id: int, **kwargs) -> bool:
        """Update an existing encoding profile. Enforces only one default at a time."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            # If setting this profile as default, clear all others first
            if kwargs.get('is_default', False):
                cursor.execute("UPDATE profiles SET is_default = 0 WHERE is_default = 1 AND id != ?", (profile_id,))
            
            updates = []
            values = []
            
            allowed_fields = [
                'name', 'resolution', 'framerate', 'codec', 'encoder',
                'quality', 'audio_codec', 'container', 'audio_handling',
                'subtitle_handling', 'enable_filters', 'chapter_markers',
                'hw_accel_enabled', 'preset', 'two_pass', 'custom_args', 'is_default',
                'upscale_enabled', 'upscale_trigger_below', 'upscale_target_height',
                'upscale_model', 'upscale_factor', 'upscale_key',
                'stereo_enabled', 'stereo_mode', 'stereo_format',
                'stereo_divergence', 'stereo_convergence', 'stereo_depth_model',
            ]
            
            for field in allowed_fields:
                if field in kwargs:
                    updates.append(f"{field} = ?")
                    values.append(kwargs[field])
            
            if not updates:
                return False
            
            values.append(profile_id)
            query = f"UPDATE profiles SET {', '.join(updates)} WHERE id = ?"
            cursor.execute(query, values)
            return cursor.rowcount > 0
    
    def get_profiles(self) -> List[Dict]:
        """Get all encoding profiles."""
        with self.get_read_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM profiles ORDER BY name")
            return [dict(row) for row in cursor.fetchall()]

    def get_profile(self, profile_id: int) -> Optional[Dict]:
        """Get a specific profile by ID."""
        with self.get_read_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM profiles WHERE id = ?", (profile_id,))
            row = cursor.fetchone()
            return dict(row) if row else None
    
    def delete_profile(self, profile_id: int) -> bool:
        """Delete a profile by ID."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM profiles WHERE id = ?", (profile_id,))
            return cursor.rowcount > 0
    
    # Scan Root CRUD
    def create_scan_root(self, path: str, profile_id: int, library_type: str = "custom",
                        enabled: bool = True, recursive: bool = True, show_in_stats: bool = True,
                        upscale_enabled: bool = False, upscale_trigger_below: int = 720,
                        upscale_target_height: int = 1080, upscale_model: str = "realesrgan-x4plus",
                        upscale_factor: int = 2, upscale_key: str = "realesrgan",
                        stereo_enabled: bool = False, stereo_mode: str = "2d_to_3d",
                        stereo_format: str = "half_sbs", stereo_divergence: float = 2.0,
                        stereo_convergence: float = 0.5, stereo_depth_model: str = "Any_V2_S") -> int:
        """Create a new scan root."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO scan_roots
                    (path, profile_id, library_type, enabled, recursive, show_in_stats,
                     upscale_enabled, upscale_trigger_below, upscale_target_height,
                     upscale_model, upscale_factor, upscale_key,
                     stereo_enabled, stereo_mode, stereo_format,
                     stereo_divergence, stereo_convergence, stereo_depth_model)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (path, profile_id, library_type, enabled, recursive, show_in_stats,
                  upscale_enabled, upscale_trigger_below, upscale_target_height,
                  upscale_model, upscale_factor, upscale_key,
                  stereo_enabled, stereo_mode, stereo_format,
                  stereo_divergence, stereo_convergence, stereo_depth_model))
            return cursor.lastrowid
    
    # Joins the linked folder watch so callers see watch_enabled / watch_id.
    _SCAN_ROOT_SELECT = """
        SELECT sr.*,
               fw.id AS watch_id,
               CASE WHEN fw.id IS NOT NULL AND fw.enabled = 1 THEN 1 ELSE 0 END AS watch_enabled
        FROM scan_roots sr
        LEFT JOIN folder_watches fw ON fw.scan_root_id = sr.id
    """

    def get_scan_roots(self, enabled_only: bool = False) -> List[Dict]:
        """Get all scan roots (including linked folder-watch state)."""
        with self.get_read_connection() as conn:
            cursor = conn.cursor()
            query = self._SCAN_ROOT_SELECT
            if enabled_only:
                query += " WHERE sr.enabled = 1"
            cursor.execute(query)
            return [dict(row) for row in cursor.fetchall()]

    def get_scan_root(self, root_id: int) -> Optional[Dict]:
        """Get a specific scan root by ID (including linked folder-watch state)."""
        with self.get_read_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(self._SCAN_ROOT_SELECT + " WHERE sr.id = ?", (root_id,))
            row = cursor.fetchone()
            return dict(row) if row else None
    
    def update_scan_root(self, root_id: int, path: str = None, profile_id: int = None,
                        library_type: str = None, enabled: bool = None, recursive: bool = None,
                        show_in_stats: bool = None, upscale_enabled: bool = None,
                        upscale_trigger_below: int = None, upscale_target_height: int = None,
                        upscale_model: str = None, upscale_factor: int = None,
                        upscale_key: str = None, stereo_enabled: bool = None,
                        stereo_mode: str = None, stereo_format: str = None,
                        stereo_divergence: float = None, stereo_convergence: float = None,
                        stereo_depth_model: str = None) -> bool:
        """Update a scan root."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            updates = []
            values = []

            field_map = {
                "path":                   path,
                "profile_id":             profile_id,
                "library_type":           library_type,
                "enabled":                enabled,
                "recursive":              recursive,
                "show_in_stats":          show_in_stats,
                "upscale_enabled":        upscale_enabled,
                "upscale_trigger_below":  upscale_trigger_below,
                "upscale_target_height":  upscale_target_height,
                "upscale_model":          upscale_model,
                "upscale_factor":         upscale_factor,
                "upscale_key":            upscale_key,
                "stereo_enabled":         stereo_enabled,
                "stereo_mode":            stereo_mode,
                "stereo_format":          stereo_format,
                "stereo_divergence":      stereo_divergence,
                "stereo_convergence":     stereo_convergence,
                "stereo_depth_model":     stereo_depth_model,
            }
            for col, val in field_map.items():
                if val is not None:
                    updates.append(f"{col} = ?")
                    values.append(val)

            if not updates:
                return False

            values.append(root_id)
            cursor.execute(
                f"UPDATE scan_roots SET {', '.join(updates)} WHERE id = ?", values
            )
            return cursor.rowcount > 0
    
    def delete_scan_root(self, root_id: int) -> bool:
        """Delete a scan root by ID. Also removes any linked folder watch."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM folder_watches WHERE scan_root_id = ?", (root_id,))
            cursor.execute("DELETE FROM scan_roots WHERE id = ?", (root_id,))
            return cursor.rowcount > 0
    
    # Queue CRUD
    def add_to_queue(self, **kwargs) -> int:
        """Add a file to the encoding queue.

        Priority is a rank: 1 = first to encode. When not given, the item is
        appended at the end of the queue (MAX(priority) + 1).
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()
            priority = kwargs.get('priority')
            if priority is None:
                cursor.execute("SELECT COALESCE(MAX(priority), 0) + 1 FROM queue")
                priority = cursor.fetchone()[0]
            cursor.execute("""
                INSERT INTO queue
                (file_path, root_id, profile_id, status, priority, current_specs,
                 target_specs, file_size_bytes, estimated_savings_bytes, upscale_plan,
                 stereo_plan, duration_seconds)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                kwargs.get('file_path'),
                kwargs.get('root_id'),
                kwargs.get('profile_id'),
                kwargs.get('status', 'pending'),
                priority,
                json.dumps(kwargs.get('current_specs', {})),
                json.dumps(kwargs.get('target_specs', {})),
                kwargs.get('file_size_bytes', 0),
                kwargs.get('estimated_savings_bytes', 0),
                kwargs.get('upscale_plan'),
                kwargs.get('stereo_plan'),
                kwargs.get('duration_seconds', 0),
            ))
            return cursor.lastrowid

    def get_next_pending_item(self, exclude_ids=None) -> Optional[Dict]:
        """The next pending item eligible to encode (rank 1 first).

        Skips items still in retry backoff (retry_after in the future) and
        fetches only ONE row — the encoder no longer loads the whole pending
        backlog just to pick the head. ``exclude_ids`` omits items already
        in flight (so a parallel controller doesn't dispatch the same row
        twice before its job has flipped it to 'processing').
        """
        exclude_ids = [int(i) for i in (exclude_ids or [])]
        with self.get_read_connection() as conn:
            cursor = conn.cursor()
            sql = ("SELECT * FROM queue WHERE status = 'pending' "
                   "AND (retry_after IS NULL OR retry_after <= CURRENT_TIMESTAMP)")
            params = []
            if exclude_ids:
                placeholders = ",".join("?" * len(exclude_ids))
                sql += f" AND id NOT IN ({placeholders})"
                params.extend(exclude_ids)
            sql += " ORDER BY priority ASC, created_at LIMIT 1"
            cursor.execute(sql, params)
            row = cursor.fetchone()
            if not row:
                return None
            item = dict(row)
            item['current_specs'] = _safe_json(item.get('current_specs'))
            item['target_specs'] = _safe_json(item.get('target_specs'))
            return item

    def count_pending(self) -> int:
        """Total pending items (any backoff state). Lets the encoder tell
        'queue empty' apart from 'everything is waiting on backoff'."""
        with self.get_read_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM queue WHERE status = 'pending'")
            return cursor.fetchone()[0]

    def get_queue_items(self, status: Optional[str] = None) -> List[Dict]:
        """Get queue items, optionally filtered by status.

        Ordered by priority rank ascending (1 = first to encode).
        """
        with self.get_read_connection() as conn:
            cursor = conn.cursor()
            if status:
                cursor.execute("SELECT * FROM queue WHERE status = ? ORDER BY priority ASC, created_at", (status,))
            else:
                cursor.execute("SELECT * FROM queue ORDER BY priority ASC, created_at")
            
            items = []
            for row in cursor.fetchall():
                item = dict(row)
                # Parse JSON fields (corruption-tolerant — see _safe_json)
                item['current_specs'] = _safe_json(item.get('current_specs'))
                item['target_specs'] = _safe_json(item.get('target_specs'))
                items.append(item)
            return items

    def get_queue_items_paginated(
        self,
        status: Optional[str] = None,
        search: Optional[str] = None,
        sort_field: str = 'priority',
        sort_dir: str = 'asc',
        page: int = 1,
        page_size: int = 50,
    ) -> Dict:
        """Get queue items with server-side filtering, sorting, and pagination.

        Returns dict with keys: items, total, page, page_size, total_pages, counts.
        ``counts`` gives per-status totals across the ENTIRE queue (unfiltered)
        so the summary bar can display accurate totals regardless of page/filter.
        """
        # Whitelist sortable columns to prevent SQL injection.
        # codec/resolution live inside the current_specs JSON blob; resolution
        # sorts by height so 480p < 720p < 1080p < 2160p.
        allowed_sorts = {
            'file': 'file_path',
            'file_path': 'file_path',
            'status': 'status',
            'priority': 'priority',
            'progress': 'progress',
            'size': 'file_size_bytes',
            'file_size_bytes': 'file_size_bytes',
            'savings': 'estimated_savings_bytes',
            'estimated_savings_bytes': 'estimated_savings_bytes',
            'created_at': 'created_at',
            'codec': "json_extract(current_specs, '$.codec')",
            'resolution': "CAST(json_extract(current_specs, '$.height') AS INTEGER)",
            'duration': 'duration_seconds',
            'duration_seconds': 'duration_seconds',
        }
        db_sort_col = allowed_sorts.get(sort_field, 'priority')
        db_sort_dir = 'ASC' if sort_dir.lower() == 'asc' else 'DESC'
        # Secondary sort for deterministic ordering
        secondary = ', created_at ASC' if db_sort_col != 'created_at' else ''

        with self.get_read_connection() as conn:
            cursor = conn.cursor()

            # ── Total counts per status (unfiltered) ──
            cursor.execute(
                "SELECT status, COUNT(*) as cnt FROM queue GROUP BY status"
            )
            counts = {row['status']: row['cnt'] for row in cursor.fetchall()}

            # ── Build WHERE clause ──
            conditions = []
            params = []
            if status:
                conditions.append("status = ?")
                params.append(status)
            if search:
                conditions.append("file_path LIKE ?")
                params.append(f"%{search}%")

            where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

            # ── Count matching rows ──
            cursor.execute(f"SELECT COUNT(*) FROM queue {where}", params)
            total = cursor.fetchone()[0]

            # ── Fetch page ──
            offset = (max(1, page) - 1) * page_size
            cursor.execute(
                f"SELECT * FROM queue {where} "
                f"ORDER BY {db_sort_col} {db_sort_dir}{secondary} "
                f"LIMIT ? OFFSET ?",
                params + [page_size, offset]
            )

            items = []
            for row in cursor.fetchall():
                item = dict(row)
                item['current_specs'] = _safe_json(item.get('current_specs'))
                item['target_specs'] = _safe_json(item.get('target_specs'))
                items.append(item)

            total_pages = max(1, -(-total // page_size))  # ceil division

            return {
                'items': items,
                'total': total,
                'page': page,
                'page_size': page_size,
                'total_pages': total_pages,
                'counts': counts,
            }

    def get_queue_item(self, item_id: int) -> Optional[Dict]:
        """Get a single queue item by ID."""
        with self.get_read_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM queue WHERE id = ?", (item_id,))
            row = cursor.fetchone()
            if not row:
                return None
            item = dict(row)
            item['current_specs'] = _safe_json(item.get('current_specs'))
            item['target_specs'] = _safe_json(item.get('target_specs'))
            return item
    
    def update_queue_item(self, item_id: int, **kwargs):
        """Update a queue item."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            # Build update query dynamically
            fields = []
            values = []
            for key, value in kwargs.items():
                if key in ['current_specs', 'target_specs'] and isinstance(value, dict):
                    value = json.dumps(value)
                fields.append(f"{key} = ?")
                values.append(value)
            
            if fields:
                values.append(item_id)
                query = f"UPDATE queue SET {', '.join(fields)} WHERE id = ?"
                cursor.execute(query, values)
    
    def delete_queue_item(self, item_id: int) -> bool:
        """Delete a queue item by ID."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM queue WHERE id = ?", (item_id,))
            return cursor.rowcount > 0
    
    def clear_queue(self, status: Optional[str] = None):
        """Clear queue items, optionally filtered by status."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            if status:
                cursor.execute("DELETE FROM queue WHERE status = ?", (status,))
            else:
                cursor.execute("DELETE FROM queue")

    def reset_stale_processing(self) -> int:
        """Reset leftover 'processing'/'paused' items to 'pending' on startup.

        After an unclean shutdown (crash, kill, Ctrl+C, power loss) the encode
        process is gone but the DB row may still read 'processing' or 'paused'.
        No encode can actually be running at startup, so re-queue these so they
        are picked up again. Does NOT touch retry_count (this isn't a failure).
        Returns the number of rows reset.
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE queue SET status = 'pending', progress = 0.0, "
                "paused_reason = NULL WHERE status IN ('processing', 'paused')"
            )
            return cursor.rowcount

    # Generic settings (key-value) helpers
    def get_setting(self, key: str, default: Optional[str] = None) -> Optional[str]:
        """Read a single setting value by key, or ``default`` if not set."""
        with self.get_read_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM settings WHERE key = ?", (key,))
            row = cursor.fetchone()
            return row[0] if row else default

    def get_all_settings(self) -> Dict:
        """Return every setting as a {key: value} dict (for config export)."""
        with self.get_read_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT key, value FROM settings")
            return {row[0]: row[1] for row in cursor.fetchall()}

    def set_setting(self, key: str, value: str):
        """Insert or update a single setting value."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO settings (key, value, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = CURRENT_TIMESTAMP
            """, (key, str(value)))

    # User CRUD
    def create_user(self, username: str, password_hash: str, email: Optional[str] = None, is_admin: bool = False) -> int:
        """Create a new user."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO users (username, password_hash, email, is_admin)
                VALUES (?, ?, ?, ?)
            """, (username, password_hash, email, is_admin))
            return cursor.lastrowid
    
    def get_user_by_username(self, username: str) -> Optional[Dict]:
        """Get a user by username."""
        with self.get_read_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM users WHERE username = ?", (username,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_user_by_id(self, user_id: int) -> Optional[Dict]:
        """Get a user by ID."""
        with self.get_read_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))
            row = cursor.fetchone()
            return dict(row) if row else None
    
    def get_user(self, user_id: int) -> Optional[Dict]:
        """Get a user by ID (alias for get_user_by_id)."""
        return self.get_user_by_id(user_id)
    
    def update_user_login(self, user_id: int):
        """Update user's last login timestamp."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE users SET last_login = CURRENT_TIMESTAMP WHERE id = ?
            """, (user_id,))
    
    # Folder Watch CRUD
    def create_folder_watch(self, path: str, profile_id: int, **kwargs) -> int:
        """Create a new folder watch."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO folder_watches (path, profile_id, enabled, recursive, auto_queue, extensions, scan_root_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                path, profile_id,
                kwargs.get('enabled', True),
                kwargs.get('recursive', True),
                kwargs.get('auto_queue', True),
                kwargs.get('extensions', '.mkv,.mp4,.avi,.mov,.wmv,.flv,.webm,.m4v,.ts,.mpg,.mpeg'),
                kwargs.get('scan_root_id'),
            ))
            return cursor.lastrowid

    def get_folder_watch_by_scan_root(self, scan_root_id: int) -> Optional[Dict]:
        """Get the folder watch linked to a scan root, if any."""
        with self.get_read_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM folder_watches WHERE scan_root_id = ? LIMIT 1",
                (scan_root_id,)
            )
            row = cursor.fetchone()
            return dict(row) if row else None
    
    def get_folder_watches(self, enabled_only: bool = False) -> List[Dict]:
        """Get all folder watches."""
        with self.get_read_connection() as conn:
            cursor = conn.cursor()
            if enabled_only:
                cursor.execute("SELECT fw.*, p.name as profile_name FROM folder_watches fw LEFT JOIN profiles p ON fw.profile_id = p.id WHERE fw.enabled = 1")
            else:
                cursor.execute("SELECT fw.*, p.name as profile_name FROM folder_watches fw LEFT JOIN profiles p ON fw.profile_id = p.id")
            return [dict(row) for row in cursor.fetchall()]
    
    def get_folder_watch(self, watch_id: int) -> Optional[Dict]:
        """Get a folder watch by ID."""
        with self.get_read_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT fw.*, p.name as profile_name FROM folder_watches fw LEFT JOIN profiles p ON fw.profile_id = p.id WHERE fw.id = ?", (watch_id,))
            row = cursor.fetchone()
            return dict(row) if row else None
    
    def update_folder_watch(self, watch_id: int, **kwargs) -> bool:
        """Update a folder watch."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            updates = []
            values = []
            for field in ['path', 'profile_id', 'enabled', 'recursive', 'auto_queue', 'extensions', 'last_check']:
                if field in kwargs:
                    updates.append(f"{field} = ?")
                    values.append(kwargs[field])
            if not updates:
                return False
            values.append(watch_id)
            cursor.execute(f"UPDATE folder_watches SET {', '.join(updates)} WHERE id = ?", values)
            return cursor.rowcount > 0
    
    def delete_folder_watch(self, watch_id: int) -> bool:
        """Delete a folder watch."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM folder_watches WHERE id = ?", (watch_id,))
            return cursor.rowcount > 0
    
    # Incoming webhook dead-letter queue
    def add_webhook_event(self, app_type: str, payload_json: str) -> int:
        """Persist an incoming webhook BEFORE processing it."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO webhook_events (app_type, payload_json) VALUES (?, ?)",
                (app_type, payload_json)
            )
            return cursor.lastrowid

    def mark_webhook_processed(self, event_id: int, error: Optional[str] = None):
        """Mark an event as done. ``error`` records a deterministic non-ok
        outcome (bad payload, no profile) — still processed, won't replay."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE webhook_events SET processed_at = CURRENT_TIMESTAMP, "
                "error = ? WHERE id = ?",
                (error, event_id)
            )

    def set_webhook_error(self, event_id: int, error: str):
        """Record a processing exception WITHOUT marking processed — the
        event stays eligible for replay at next startup."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE webhook_events SET error = ? WHERE id = ?",
                (error, event_id)
            )

    def get_unprocessed_webhooks(self, hours: int = 24) -> List[Dict]:
        """Events never successfully processed, received in the last N hours."""
        with self.get_read_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM webhook_events "
                "WHERE processed_at IS NULL "
                "AND received_at >= datetime('now', ?) "
                "ORDER BY received_at",
                (f'-{int(hours)} hours',)
            )
            return [dict(row) for row in cursor.fetchall()]

    def count_unprocessed_webhooks(self) -> int:
        """Unprocessed event count (any age) — surfaced on /health."""
        with self.get_read_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT COUNT(*) FROM webhook_events WHERE processed_at IS NULL"
            )
            return cursor.fetchone()[0]

    def prune_webhook_events(self, days: int = 7) -> int:
        """Delete processed events older than N days. Unprocessed rows are
        kept regardless of age so failures stay visible on /health."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM webhook_events "
                "WHERE processed_at IS NOT NULL "
                "AND received_at < datetime('now', ?)",
                (f'-{int(days)} days',)
            )
            return cursor.rowcount

    # History and Statistics
    def add_history(self, **kwargs) -> int:
        """Add a history record."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO history (file_path, profile_name, original_size_bytes, new_size_bytes, 
                                     savings_bytes, encoding_time_seconds, codec, container,
                                     duration_seconds)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                kwargs.get('file_path'),
                kwargs.get('profile_name'),
                kwargs.get('original_size_bytes', 0),
                kwargs.get('new_size_bytes', 0),
                kwargs.get('savings_bytes', 0),
                kwargs.get('encoding_time_seconds', 0),
                kwargs.get('codec'),
                kwargs.get('container'),
                kwargs.get('duration_seconds', 0),
            ))
            return cursor.lastrowid
    
    def get_encode_speed_stats(self) -> Dict:
        """Average encode speed ratios from history.

        Ratio = encoding wall-clock seconds per second of video (2.0 means a
        1-hour video takes ~2 hours to encode). Grouped by target codec since
        speed differs wildly between e.g. NVENC H.265 and software AV1.
        Only rows with a known duration count (pre-duration-tracking history
        has duration_seconds = 0 and is excluded).

        Returns {'overall': float|None, 'by_codec': {codec: float}, 'samples': int}.
        """
        with self.get_read_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT codec,
                       AVG(CAST(encoding_time_seconds AS REAL) / duration_seconds) AS ratio,
                       COUNT(*) AS n
                FROM history
                WHERE encoding_time_seconds > 0 AND duration_seconds > 0
                GROUP BY codec
            """)
            by_codec = {}
            weighted = 0.0
            samples = 0
            for row in cursor.fetchall():
                by_codec[row['codec'] or ''] = row['ratio']
                weighted += row['ratio'] * row['n']
                samples += row['n']
            overall = (weighted / samples) if samples else None
            return {'overall': overall, 'by_codec': by_codec, 'samples': samples}

    def get_pending_duration_by_codec(self) -> List[Dict]:
        """Sum of video durations for pending queue items, per target codec.

        Feeds the total-queue-ETA estimate: each codec group's duration is
        multiplied by that codec's encode speed ratio. ``unknown_count`` is
        the number of items whose duration is unrecorded (no ETA possible).
        """
        with self.get_read_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT COALESCE(json_extract(target_specs, '$.codec'), '') AS codec,
                       COALESCE(SUM(CASE WHEN duration_seconds > 0
                           THEN duration_seconds ELSE 0 END), 0) AS total_duration,
                       SUM(CASE WHEN duration_seconds IS NULL OR duration_seconds <= 0
                           THEN 1 ELSE 0 END) AS unknown_count
                FROM queue
                WHERE status = 'pending'
                GROUP BY 1
            """)
            return [dict(row) for row in cursor.fetchall()]

    def get_stats_dashboard(self, days: int = 30) -> Dict:
        """Get comprehensive statistics for the dashboard."""
        with self.get_read_connection() as conn:
            cursor = conn.cursor()
            
            # Overall totals
            cursor.execute("""
                SELECT COUNT(*) as total,
                       COALESCE(SUM(original_size_bytes), 0) as total_original,
                       COALESCE(SUM(new_size_bytes), 0) as total_new,
                       COALESCE(SUM(savings_bytes), 0) as total_saved,
                       COALESCE(SUM(encoding_time_seconds), 0) as total_time,
                       COALESCE(AVG(CASE WHEN original_size_bytes > 0
                           THEN (savings_bytes * 100.0 / original_size_bytes) ELSE 0 END), 0) as avg_savings_pct,
                       COALESCE(AVG(encoding_time_seconds), 0) as avg_encode_seconds,
                       COALESCE(AVG(CASE WHEN duration_seconds > 0 AND encoding_time_seconds > 0
                           THEN duration_seconds * 1.0 / encoding_time_seconds END), 0) as avg_speed_x
                FROM history
                WHERE completed_at >= datetime('now', ?)
            """, (f'-{days} days',))
            totals = dict(cursor.fetchone())
            
            # Daily breakdown (last N days)
            cursor.execute("""
                SELECT DATE(completed_at) as date,
                       COUNT(*) as files,
                       COALESCE(SUM(savings_bytes), 0) as saved,
                       COALESCE(SUM(encoding_time_seconds), 0) as time_seconds
                FROM history 
                WHERE completed_at >= datetime('now', ?)
                GROUP BY DATE(completed_at)
                ORDER BY date
            """, (f'-{days} days',))
            daily = [dict(row) for row in cursor.fetchall()]
            
            # Codec breakdown (within selected period)
            cursor.execute("""
                SELECT COALESCE(codec, 'unknown') as codec,
                       COUNT(*) as count,
                       COALESCE(SUM(savings_bytes), 0) as saved
                FROM history
                WHERE completed_at >= datetime('now', ?)
                GROUP BY codec ORDER BY count DESC
            """, (f'-{days} days',))
            codecs = [dict(row) for row in cursor.fetchall()]
            
            # Recent history (last 20)
            cursor.execute("""
                SELECT * FROM history ORDER BY completed_at DESC LIMIT 20
            """)
            recent = [dict(row) for row in cursor.fetchall()]
            
            result = {
                'totals': totals,
                'daily': daily,
                'codecs': codecs,
                'recent': recent,
            }
        # Attached OUTSIDE the with block: get_encode_speed_stats opens its
        # own connection, and get_connection's lock is NOT reentrant —
        # calling it nested deadlocks the whole Database instance.
        # All-time speed ratios (same numbers the ETA estimator uses).
        result['encode_speed'] = self.get_encode_speed_stats()
        return result
    
    def clear_history_file_names(self) -> int:
        """Anonymize file paths in history while preserving all statistical data."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE history SET file_path = 'cleared' WHERE file_path != 'cleared'"
            )
            return cursor.rowcount

    def purge_history(self) -> int:
        """Delete all history records. Stats will reset to zero."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM history")
            count = cursor.fetchone()[0]
            cursor.execute("DELETE FROM history")
            return count

    # Preset export/import
    def export_profiles(self) -> List[Dict]:
        """Export all profiles as a list of dicts for JSON export."""
        profiles = self.get_profiles()
        # Strip internal fields
        for p in profiles:
            p.pop('id', None)
            p.pop('created_at', None)
        return profiles
    
    def import_profiles(self, profiles: List[Dict]) -> Dict:
        """Import profiles from a list of dicts. Returns stats."""
        imported = 0
        skipped = 0
        errors = []
        for p in profiles:
            try:
                # Skip if name already exists
                existing = self.get_profiles()
                existing_names = {ep['name'] for ep in existing}
                if p.get('name') in existing_names:
                    skipped += 1
                    continue
                self.create_profile(**p)
                imported += 1
            except Exception as e:
                errors.append(f"{p.get('name', '?')}: {str(e)}")
        return {'imported': imported, 'skipped': skipped, 'errors': errors}


    # External Connection CRUD

    def create_external_connection(self, **kwargs) -> int:
        """Create a new external connection. API key must already be encrypted."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO external_connections
                    (name, app_type, base_url, api_key_encrypted, enabled,
                     show_in_stats, sync_interval_hours)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                kwargs["name"],
                kwargs["app_type"],
                kwargs["base_url"].rstrip("/"),
                kwargs["api_key_encrypted"],
                kwargs.get("enabled", True),
                kwargs.get("show_in_stats", True),
                kwargs.get("sync_interval_hours", 0),
            ))
            return cursor.lastrowid

    def get_external_connections(self) -> List[Dict]:
        """Get all external connections."""
        with self.get_read_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM external_connections ORDER BY name")
            return [dict(row) for row in cursor.fetchall()]

    def get_external_connection(self, conn_id: int) -> Optional[Dict]:
        """Get a single external connection by ID."""
        with self.get_read_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM external_connections WHERE id = ?", (conn_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def update_external_connection(self, conn_id: int, **kwargs) -> bool:
        """Update an external connection."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            allowed = [
                "name", "app_type", "base_url", "api_key_encrypted",
                "enabled", "show_in_stats", "last_tested", "last_synced",
                "sync_interval_hours",
            ]
            updates, values = [], []
            for field in allowed:
                if field in kwargs:
                    val = kwargs[field]
                    if field == "base_url" and val:
                        val = val.rstrip("/")
                    updates.append(f"{field} = ?")
                    values.append(val)
            if not updates:
                return False
            values.append(conn_id)
            cursor.execute(
                f"UPDATE external_connections SET {', '.join(updates)} WHERE id = ?",
                values,
            )
            return cursor.rowcount > 0

    def delete_external_connection(self, conn_id: int) -> bool:
        """Delete an external connection and unlink any scan roots pointing to it."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            # Unlink scan roots first
            cursor.execute(
                "UPDATE scan_roots SET external_connection_id = NULL "
                "WHERE external_connection_id = ?",
                (conn_id,),
            )
            cursor.execute("DELETE FROM external_connections WHERE id = ?", (conn_id,))
            return cursor.rowcount > 0


# Global database instance
db = Database()
