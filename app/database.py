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
                    completed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
            
            # Add 'library_type' column to scan_roots if missing
            cursor.execute("PRAGMA table_info(scan_roots)")
            root_columns = [col[1] for col in cursor.fetchall()]
            if 'library_type' not in root_columns:
                cursor.execute("ALTER TABLE scan_roots ADD COLUMN library_type TEXT DEFAULT 'custom'")
                print("  ↳ Migrated: added 'library_type' column to scan_roots")
            
            print("✓ Database schema initialized")
    
    # Profile CRUD
    def create_profile(self, **kwargs) -> int:
        """Create a new encoding profile."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO profiles 
                (name, resolution, framerate, codec, encoder, quality, audio_codec, 
                 container, preset, two_pass, custom_args, is_default)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                kwargs.get('name'),
                kwargs.get('resolution'),
                kwargs.get('framerate'),
                kwargs.get('codec'),
                kwargs.get('encoder'),
                kwargs.get('quality'),
                kwargs.get('audio_codec'),
                kwargs.get('container', 'mkv'),
                kwargs.get('preset'),
                kwargs.get('two_pass', False),
                kwargs.get('custom_args'),
                kwargs.get('is_default', False)
            ))
            return cursor.lastrowid
    
    def update_profile(self, profile_id: int, **kwargs) -> bool:
        """Update an existing encoding profile."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            updates = []
            values = []
            
            allowed_fields = [
                'name', 'resolution', 'framerate', 'codec', 'encoder',
                'quality', 'audio_codec', 'container', 'preset', 'two_pass',
                'custom_args', 'is_default'
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
                        enabled: bool = True, recursive: bool = True) -> int:
        """Create a new scan root."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO scan_roots (path, profile_id, library_type, enabled, recursive)
                VALUES (?, ?, ?, ?, ?)
            """, (path, profile_id, library_type, enabled, recursive))
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
                        library_type: str = None, enabled: bool = None, recursive: bool = None) -> bool:
        """Update a scan root."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            # Build update query dynamically
            updates = []
            values = []
            
            if path is not None:
                updates.append("path = ?")
                values.append(path)
            if profile_id is not None:
                updates.append("profile_id = ?")
                values.append(profile_id)
            if library_type is not None:
                updates.append("library_type = ?")
                values.append(library_type)
            if enabled is not None:
                updates.append("enabled = ?")
                values.append(enabled)
            if recursive is not None:
                updates.append("recursive = ?")
                values.append(recursive)
            
            if not updates:
                return False
            
            values.append(root_id)
            query = f"UPDATE scan_roots SET {', '.join(updates)} WHERE id = ?"
            cursor.execute(query, values)
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
                 target_specs, file_size_bytes, estimated_savings_bytes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                kwargs.get('file_path'),
                kwargs.get('root_id'),
                kwargs.get('profile_id'),
                kwargs.get('status', 'pending'),
                kwargs.get('priority', 50),
                json.dumps(kwargs.get('current_specs', {})),
                json.dumps(kwargs.get('target_specs', {})),
                kwargs.get('file_size_bytes', 0),
                kwargs.get('estimated_savings_bytes', 0)
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


# Global database instance
db = Database()
