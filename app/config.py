"""
Configuration module for Optimizarr.
Loads settings from environment variables with sensible defaults.
"""
import os
import secrets
from pathlib import Path
from pydantic_settings import BaseSettings
from typing import Optional

_DEFAULT_SECRET = "insecure-default-key-change-in-production"
_SECRET_FILE = Path("data") / ".secret_key"


def _resolve_secret_key() -> str:
    """Return a stable secret key, auto-generating one on first run.

    Priority:
      1. OPTIMIZARR_SECRET_KEY env var (if set and not the default)
      2. data/.secret_key file (persists across restarts)
      3. Generate a new random key → write to data/.secret_key
    """
    env_val = os.environ.get("OPTIMIZARR_SECRET_KEY", "").strip()
    if env_val and env_val != _DEFAULT_SECRET:
        return env_val

    # Try reading from persisted file
    if _SECRET_FILE.exists():
        stored = _SECRET_FILE.read_text().strip()
        if stored and stored != _DEFAULT_SECRET:
            return stored

    # Generate and persist
    _SECRET_FILE.parent.mkdir(parents=True, exist_ok=True)
    new_key = secrets.token_hex(32)
    _SECRET_FILE.write_text(new_key)
    print(f"✓ Generated new SECRET_KEY → {_SECRET_FILE}")
    return new_key


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""
    
    # Security
    secret_key: str = _DEFAULT_SECRET
    admin_username: str = "admin"
    admin_password: str = "admin"
    
    # Application
    host: str = "0.0.0.0"
    port: int = 5000
    log_level: str = "INFO"
    cors_origins: str = ""  # Comma-separated origins, e.g. "http://localhost:3000,https://myapp.com"
    
    # Database
    db_path: str = "data/optimizarr.db"
    
    # JWT
    jwt_expiration_hours: int = 24
    jwt_algorithm: str = "HS256"
    
    # Docker
    puid: int = 1000
    pgid: int = 1000
    tz: str = "UTC"
    
    class Config:
        env_prefix = "OPTIMIZARR_"
        env_file = ".env"
        case_sensitive = False


# Global settings instance
settings = Settings()

# Override secret key if still the insecure default
if settings.secret_key == _DEFAULT_SECRET:
    settings.secret_key = _resolve_secret_key()


def ensure_data_directories():
    """Ensure required data directories exist."""
    # Fix path to be relative if it's absolute
    db_path_str = settings.db_path
    if db_path_str.startswith('/home/') or db_path_str.startswith('/app/'):
        # Linux path in .env, convert to relative
        db_path_str = 'data/optimizarr.db'
        settings.db_path = db_path_str
    
    db_path = Path(db_path_str)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Create config directory if we're in docker
    config_dir = Path("config")
    config_dir.mkdir(parents=True, exist_ok=True)
    
    print(f"✓ Data directory: {db_path.parent.absolute()}")
    print(f"✓ Config directory: {config_dir.absolute()}")
