"""
Configuration module for Optimizarr.
Loads settings from environment variables with sensible defaults.
"""
import os
from pathlib import Path
from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""
    
    # Security
    secret_key: str = "insecure-default-key-change-in-production"
    admin_username: str = "admin"
    admin_password: str = "admin"
    
    # Application
    host: str = "0.0.0.0"
    port: int = 5000
    log_level: str = "INFO"
    
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


def ensure_data_directories():
    """Ensure required data directories exist."""
    db_path = Path(settings.db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Create config directory
    config_dir = Path("/app/config")
    config_dir.mkdir(parents=True, exist_ok=True)
    
    print(f"✓ Data directory: {db_path.parent}")
    print(f"✓ Config directory: {config_dir}")
