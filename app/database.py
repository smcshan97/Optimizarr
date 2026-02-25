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


class Database:
    """SQLite database manager for Optimizarr."""
    
    def __init__(self, db_path: str = None):
        self.db_path = db_path or settings.db_path
        
        # CRITICAL: Ensure directory exists before trying to connect
        db_file = Path(self.db_path)
        db_file.parent.mkdir(parents=True, exist_ok=True)
        
        self.initialize_database()
    
    @contextmanager
    def get_connection(self):
        """Context manager for database connections."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row  # Enable dict-like access
        try:
            yield conn
            conn.commit()
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            conn.close()
    
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
                    priority INTEGER DEFAULT 50,
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
                 upscale_model, upscale_factor, upscale_key)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM profiles ORDER BY name")
            return [dict(row) for row in cursor.fetchall()]
    
    def get_profile(self, profile_id: int) -> Optional[Dict]:
        """Get a specific profile by ID."""
        with self.get_connection() as conn:
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
                        upscale_factor: int = 2, upscale_key: str = "realesrgan") -> int:
        """Create a new scan root."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO scan_roots
                    (path, profile_id, library_type, enabled, recursive, show_in_stats,
                     upscale_enabled, upscale_trigger_below, upscale_target_height,
                     upscale_model, upscale_factor, upscale_key)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (path, profile_id, library_type, enabled, recursive, show_in_stats,
                  upscale_enabled, upscale_trigger_below, upscale_target_height,
                  upscale_model, upscale_factor, upscale_key))
            return cursor.lastrowid
    
    def get_scan_roots(self, enabled_only: bool = False) -> List[Dict]:
        """Get all scan roots."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            query = "SELECT * FROM scan_roots"
            if enabled_only:
                query += " WHERE enabled = 1"
            cursor.execute(query)
            return [dict(row) for row in cursor.fetchall()]
    
    def get_scan_root(self, root_id: int) -> Optional[Dict]:
        """Get a specific scan root by ID."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM scan_roots WHERE id = ?", (root_id,))
            row = cursor.fetchone()
            return dict(row) if row else None
    
    def update_scan_root(self, root_id: int, path: str = None, profile_id: int = None,
                        library_type: str = None, enabled: bool = None, recursive: bool = None,
                        show_in_stats: bool = None, upscale_enabled: bool = None,
                        upscale_trigger_below: int = None, upscale_target_height: int = None,
                        upscale_model: str = None, upscale_factor: int = None,
                        upscale_key: str = None) -> bool:
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
        """Delete a scan root by ID."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM scan_roots WHERE id = ?", (root_id,))
            return cursor.rowcount > 0
    
    # Queue CRUD
    def add_to_queue(self, **kwargs) -> int:
        """Add a file to the encoding queue."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO queue 
                (file_path, root_id, profile_id, status, priority, current_specs, 
                 target_specs, file_size_bytes, estimated_savings_bytes, upscale_plan)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                kwargs.get('file_path'),
                kwargs.get('root_id'),
                kwargs.get('profile_id'),
                kwargs.get('status', 'pending'),
                kwargs.get('priority', 50),
                json.dumps(kwargs.get('current_specs', {})),
                json.dumps(kwargs.get('target_specs', {})),
                kwargs.get('file_size_bytes', 0),
                kwargs.get('estimated_savings_bytes', 0),
                kwargs.get('upscale_plan'),
            ))
            return cursor.lastrowid
    
    def get_queue_items(self, status: Optional[str] = None) -> List[Dict]:
        """Get queue items, optionally filtered by status."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            if status:
                cursor.execute("SELECT * FROM queue WHERE status = ? ORDER BY priority DESC, created_at", (status,))
            else:
                cursor.execute("SELECT * FROM queue ORDER BY priority DESC, created_at")
            
            items = []
            for row in cursor.fetchall():
                item = dict(row)
                # Parse JSON fields
                if item.get('current_specs'):
                    item['current_specs'] = json.loads(item['current_specs'])
                if item.get('target_specs'):
                    item['target_specs'] = json.loads(item['target_specs'])
                items.append(item)
            return items
    
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
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM users WHERE username = ?", (username,))
            row = cursor.fetchone()
            return dict(row) if row else None
    
    def get_user_by_id(self, user_id: int) -> Optional[Dict]:
        """Get a user by ID."""
        with self.get_connection() as conn:
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
                INSERT INTO folder_watches (path, profile_id, enabled, recursive, auto_queue, extensions)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                path, profile_id,
                kwargs.get('enabled', True),
                kwargs.get('recursive', True),
                kwargs.get('auto_queue', True),
                kwargs.get('extensions', '.mkv,.mp4,.avi,.mov,.wmv,.flv,.webm,.m4v,.ts,.mpg,.mpeg')
            ))
            return cursor.lastrowid
    
    def get_folder_watches(self, enabled_only: bool = False) -> List[Dict]:
        """Get all folder watches."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            if enabled_only:
                cursor.execute("SELECT fw.*, p.name as profile_name FROM folder_watches fw LEFT JOIN profiles p ON fw.profile_id = p.id WHERE fw.enabled = 1")
            else:
                cursor.execute("SELECT fw.*, p.name as profile_name FROM folder_watches fw LEFT JOIN profiles p ON fw.profile_id = p.id")
            return [dict(row) for row in cursor.fetchall()]
    
    def get_folder_watch(self, watch_id: int) -> Optional[Dict]:
        """Get a folder watch by ID."""
        with self.get_connection() as conn:
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
    
    # History and Statistics
    def add_history(self, **kwargs) -> int:
        """Add a history record."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO history (file_path, profile_name, original_size_bytes, new_size_bytes, 
                                     savings_bytes, encoding_time_seconds, codec, container)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                kwargs.get('file_path'),
                kwargs.get('profile_name'),
                kwargs.get('original_size_bytes', 0),
                kwargs.get('new_size_bytes', 0),
                kwargs.get('savings_bytes', 0),
                kwargs.get('encoding_time_seconds', 0),
                kwargs.get('codec'),
                kwargs.get('container')
            ))
            return cursor.lastrowid
    
    def get_stats_dashboard(self, days: int = 30) -> Dict:
        """Get comprehensive statistics for the dashboard."""
        with self.get_connection() as conn:
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
                       COALESCE(AVG(encoding_time_seconds), 0) as avg_encode_seconds
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
            
            return {
                'totals': totals,
                'daily': daily,
                'codecs': codecs,
                'recent': recent
            }
    
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
                    (name, app_type, base_url, api_key_encrypted, enabled, show_in_stats)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                kwargs["name"],
                kwargs["app_type"],
                kwargs["base_url"].rstrip("/"),
                kwargs["api_key_encrypted"],
                kwargs.get("enabled", True),
                kwargs.get("show_in_stats", True),
            ))
            return cursor.lastrowid

    def get_external_connections(self) -> List[Dict]:
        """Get all external connections."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM external_connections ORDER BY name")
            return [dict(row) for row in cursor.fetchall()]

    def get_external_connection(self, conn_id: int) -> Optional[Dict]:
        """Get a single external connection by ID."""
        with self.get_connection() as conn:
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
