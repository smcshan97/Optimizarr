"""Key/value settings and users."""
from typing import Optional, Dict


class AccountsMixin:
    """Database methods: key/value settings and users."""

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
    def create_user(self, username: str, password_hash: str, email: Optional[str] = None,
                    is_admin: bool = False, must_change_password: bool = False) -> int:
        """Create a new user."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO users (username, password_hash, email, is_admin, must_change_password)
                VALUES (?, ?, ?, ?, ?)
            """, (username, password_hash, email, is_admin, must_change_password))
            return cursor.lastrowid

    def set_user_password(self, user_id: int, password_hash: str):
        """Store a new password hash, clear the forced-change flag and bump
        token_version so every previously issued token is revoked."""
        with self.get_connection() as conn:
            conn.execute("""
                UPDATE users
                SET password_hash = ?, must_change_password = 0,
                    token_version = COALESCE(token_version, 0) + 1
                WHERE id = ?
            """, (password_hash, user_id))
    
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
