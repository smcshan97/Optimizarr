"""
Authentication module for Optimizarr.
Handles password hashing, JWT token generation, and login throttling.
"""
import threading
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict
import bcrypt
import jwt

from app.config import settings
from app.database import db
from app.logger import optimizarr_logger


# Bootstrap passwords that must be changed on first login.
_WEAK_DEFAULT_PASSWORDS = {
    "admin", "password", "changeme", "change-me", "change-this-secure-password",
}

# Stream tokens (for EventSource, which can't send headers) only need to live
# long enough to open the connection — the URL they sit in ends up in logs.
STREAM_TOKEN_SECONDS = 60

# A real bcrypt hash to check against when the username doesn't exist, so a
# login for an unknown user takes as long as one with a wrong password.
_DUMMY_HASH = bcrypt.hashpw(b"optimizarr-dummy", bcrypt.gensalt()).decode()


class LoginRateLimiter:
    """Per-client failed-login throttle (in memory).

    After ``max_failures`` failures inside ``window`` seconds the client is
    refused until the oldest failure ages out. A success clears the record.
    """

    def __init__(self, max_failures: int = 5, window: float = 900):
        self.max_failures = max_failures
        self.window = window
        self._failures: Dict[str, deque] = {}
        self._lock = threading.Lock()

    def _prune(self, key: str, now: float) -> deque:
        q = self._failures.setdefault(key, deque())
        while q and now - q[0] > self.window:
            q.popleft()
        return q

    def retry_after(self, key: str) -> int:
        """Seconds until ``key`` may try again; 0 if not locked out."""
        now = time.monotonic()
        with self._lock:
            q = self._prune(key, now)
            if len(q) < self.max_failures:
                return 0
            return max(1, int(self.window - (now - q[0])) + 1)

    def record_failure(self, key: str):
        now = time.monotonic()
        with self._lock:
            self._prune(key, now).append(now)

    def reset(self, key: str):
        with self._lock:
            self._failures.pop(key, None)


login_limiter = LoginRateLimiter()


class AuthManager:
    """Manages authentication, password hashing, and token generation."""

    # bcrypt only reads the first 72 bytes. bcrypt < 5 truncated silently;
    # bcrypt 5 raises ValueError instead (a 500 on a long password). Truncate
    # explicitly so both behave the same and existing hashes still verify.
    _BCRYPT_MAX = 72

    @staticmethod
    def hash_password(password: str) -> str:
        """Hash a password using bcrypt."""
        password_bytes = password.encode('utf-8')[:AuthManager._BCRYPT_MAX]
        salt = bcrypt.gensalt()
        hashed = bcrypt.hashpw(password_bytes, salt)
        return hashed.decode('utf-8')

    @staticmethod
    def verify_password(plain_password: str, hashed_password: str) -> bool:
        """Verify a password against its hash."""
        password_bytes = plain_password.encode('utf-8')[:AuthManager._BCRYPT_MAX]
        hashed_bytes = hashed_password.encode('utf-8')
        return bcrypt.checkpw(password_bytes, hashed_bytes)

    @staticmethod
    def create_access_token(user_id: int, username: str, is_admin: bool,
                            token_version: int = 0) -> str:
        """Create a JWT access token."""
        now = datetime.now(timezone.utc)
        payload = {
            "sub": str(user_id),
            "username": username,
            "is_admin": is_admin,
            "tv": int(token_version or 0),
            "exp": now + timedelta(hours=settings.jwt_expiration_hours),
            "iat": now,
        }
        return jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)

    @staticmethod
    def create_stream_token(user: Dict) -> str:
        """Short-lived token for EventSource URLs."""
        now = datetime.now(timezone.utc)
        payload = {
            "sub": str(user["id"]),
            "tv": int(user.get("token_version") or 0),
            "scope": "stream",
            "exp": now + timedelta(seconds=STREAM_TOKEN_SECONDS),
            "iat": now,
        }
        return jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)

    @staticmethod
    def decode_token(token: str) -> Optional[Dict]:
        """Decode and validate a JWT token."""
        try:
            payload = jwt.decode(token, settings.secret_key, algorithms=[settings.jwt_algorithm])
            return payload
        except jwt.ExpiredSignatureError:
            return None
        except jwt.InvalidTokenError:
            return None

    @staticmethod
    def user_from_token(token: str, scope: Optional[str] = None) -> Optional[Dict]:
        """Validate ``token`` and return its user, or None.

        Rejects expired/forged tokens, tokens issued before the user's last
        password change (token_version), and tokens of the wrong scope — a
        stream token can't be used as an API token or vice versa.
        """
        payload = AuthManager.decode_token(token) if token else None
        if not payload or payload.get("scope") != scope:
            return None
        try:
            user = db.get_user_by_id(int(payload.get("sub")))
        except (TypeError, ValueError):
            return None
        if not user:
            return None
        if int(payload.get("tv", 0)) != int(user.get("token_version") or 0):
            return None
        return user

    @staticmethod
    def authenticate_user(username: str, password: str) -> Optional[Dict]:
        """Authenticate a user with username and password."""
        user = db.get_user_by_username(username)

        if not user:
            AuthManager.verify_password(password, _DUMMY_HASH)  # equalise timing
            return None

        if not AuthManager.verify_password(password, user['password_hash']):
            return None

        # Update last login
        db.update_user_login(user['id'])

        return user

    @staticmethod
    def create_default_admin():
        """Create the default admin user if it doesn't exist."""
        existing_user = db.get_user_by_username(settings.admin_username)

        if existing_user:
            optimizarr_logger.app_logger.info(f"✓ Admin user '{settings.admin_username}' already exists")
            return

        password_hash = AuthManager.hash_password(settings.admin_password)
        weak = (settings.admin_password.strip().lower() in _WEAK_DEFAULT_PASSWORDS
                or len(settings.admin_password) < 8)
        user_id = db.create_user(
            username=settings.admin_username,
            password_hash=password_hash,
            is_admin=True,
            must_change_password=weak,
        )

        optimizarr_logger.app_logger.info(f"✓ Created admin user '{settings.admin_username}' (ID: {user_id})")
        if weak:
            optimizarr_logger.app_logger.warning("  ⚠ Admin password is a default/weak value — you'll be asked to change it on first login")


# Global auth manager instance
auth = AuthManager()
