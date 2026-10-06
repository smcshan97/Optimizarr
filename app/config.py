"""
Configuration module for Optimizarr.
Loads settings from environment variables with sensible defaults.
"""
import os
import secrets
from pathlib import Path
from pydantic_settings import BaseSettings
from typing import Optional

# Project root. Every relative path (data/, logs/, .env) is resolved against
# this rather than the process CWD — launched from a shortcut, service
# wrapper or another folder, a CWD-relative "data/optimizarr.db" silently
# created an empty database and a new secret key (logging everyone out).
BASE_DIR = Path(__file__).resolve().parent.parent


def project_path(p) -> Path:
    """Absolute path for ``p``; relative paths are anchored at BASE_DIR."""
    p = Path(p)
    return p if p.is_absolute() else BASE_DIR / p


_DEFAULT_SECRET = "insecure-default-key-change-in-production"
_SECRET_FILE = BASE_DIR / "data" / ".secret_key"

# Every placeholder that has ever shipped in .env / .env.example. Matching only
# _DEFAULT_SECRET was not enough: the .env template carried a *different*
# placeholder, so the auto-generation below never fired and JWTs were signed
# with a value published in the repo. Treat all of them as "unset".
_PLACEHOLDER_SECRETS = {
    _DEFAULT_SECRET,
    "dev-secret-key-change-in-production",
    "your-secret-key-here-generate-with-openssl-rand-hex-32",
    "change-me",
    "changeme",
    "secret",
}


def _is_placeholder(value: str) -> bool:
    """True if `value` is empty or a known placeholder, i.e. unusable as a key."""
    return not value or value.strip().lower() in _PLACEHOLDER_SECRETS


def _resolve_secret_key() -> str:
    """Return a stable secret key, auto-generating one on first run.

    Priority:
      1. OPTIMIZARR_SECRET_KEY env var (if set and not a placeholder)
      2. data/.secret_key file (persists across restarts)
      3. Generate a new random key → write to data/.secret_key
    """
    env_val = os.environ.get("OPTIMIZARR_SECRET_KEY", "").strip()
    if not _is_placeholder(env_val):
        return env_val

    # Try reading from persisted file
    if _SECRET_FILE.exists():
        stored = _SECRET_FILE.read_text(encoding='utf-8').strip()
        if not _is_placeholder(stored):
            return stored

    # Generate and persist
    _SECRET_FILE.parent.mkdir(parents=True, exist_ok=True)
    new_key = secrets.token_hex(32)
    _SECRET_FILE.write_text(new_key, encoding='utf-8')
    # ASCII only: this runs at import time, possibly before main.py has
    # reconfigured a cp1252 Windows console — a Unicode arrow here crashed
    # any entry point that imports app.config directly on first run.
    print(f"Generated new SECRET_KEY -> {_SECRET_FILE}")
    return new_key


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""
    
    # Security
    secret_key: str = _DEFAULT_SECRET
    admin_username: str = "admin"
    admin_password: str = "admin"
    
    # Application
    host: str = "0.0.0.0"
    port: int = 5757
    log_level: str = "INFO"
    cors_origins: str = ""  # Comma-separated origins, e.g. "http://localhost:3000,https://myapp.com"
    
    # Database
    db_path: str = "data/optimizarr.db"
    
    # JWT
    jwt_expiration_hours: int = 24
    jwt_algorithm: str = "HS256"
    
    # Unused since the Docker build was dropped — kept only so .env files
    # that still set OPTIMIZARR_PUID/PGID/TZ keep loading (extra keys fail).
    puid: int = 1000
    pgid: int = 1000
    tz: str = "UTC"

    class Config:
        env_prefix = "OPTIMIZARR_"
        env_file = str(BASE_DIR / ".env")
        case_sensitive = False


# Global settings instance
settings = Settings()

# A Linux path left over from the Docker era (/home/..., /app/...) would
# resolve to a nonexistent folder on Windows — fall back to the default,
# loudly. Must happen here, at import: app.database opens the DB on import.
if os.name == 'nt' and settings.db_path.startswith(('/home/', '/app/')):
    print(f"WARNING: OPTIMIZARR_DB_PATH={settings.db_path} is a Linux path; "
          f"using data/optimizarr.db instead. Fix it in .env.")
    settings.db_path = "data/optimizarr.db"
settings.db_path = str(project_path(settings.db_path))

# Override the secret key if it is still any known placeholder. Note this must
# check the *resolved* value, which includes anything pydantic loaded from .env
# -- that is the path the old _DEFAULT_SECRET-only comparison missed.
if _is_placeholder(settings.secret_key):
    settings.secret_key = _resolve_secret_key()


def ensure_data_directories():
    """Ensure required data directories exist."""
    db_path = Path(settings.db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    print(f"✓ Data directory: {db_path.parent}")
