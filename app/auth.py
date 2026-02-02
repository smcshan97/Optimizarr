"""
Authentication module for Optimizarr.
Handles password hashing, JWT token generation, and API key management.
"""
import secrets
from datetime import datetime, timedelta
from typing import Optional, Dict
import bcrypt
import jwt

from app.config import settings
from app.database import db


class AuthManager:
    """Manages authentication, password hashing, and token generation."""
    
    @staticmethod
    def hash_password(password: str) -> str:
        """Hash a password using bcrypt."""
        # Convert to bytes and hash
        password_bytes = password.encode('utf-8')
        salt = bcrypt.gensalt()
        hashed = bcrypt.hashpw(password_bytes, salt)
        return hashed.decode('utf-8')
    
    @staticmethod
    def verify_password(plain_password: str, hashed_password: str) -> bool:
        """Verify a password against its hash."""
        password_bytes = plain_password.encode('utf-8')
        hashed_bytes = hashed_password.encode('utf-8')
        return bcrypt.checkpw(password_bytes, hashed_bytes)
    
    @staticmethod
    def create_access_token(user_id: int, username: str, is_admin: bool) -> str:
        """Create a JWT access token."""
        expires = datetime.utcnow() + timedelta(hours=settings.jwt_expiration_hours)
        
        payload = {
            "sub": str(user_id),
            "username": username,
            "is_admin": is_admin,
            "exp": expires,
            "iat": datetime.utcnow()
        }
        
        token = jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)
        return token
    
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
    def authenticate_user(username: str, password: str) -> Optional[Dict]:
        """Authenticate a user with username and password."""
        user = db.get_user_by_username(username)
        
        if not user:
            return None
        
        if not AuthManager.verify_password(password, user['password_hash']):
            return None
        
        # Update last login
        db.update_user_login(user['id'])
        
        return user
    
    @staticmethod
    def generate_api_key() -> str:
        """Generate a new API key."""
        return f"opt_{secrets.token_urlsafe(32)}"
    
    @staticmethod
    def create_default_admin():
        """Create the default admin user if it doesn't exist."""
        existing_user = db.get_user_by_username(settings.admin_username)
        
        if existing_user:
            print(f"✓ Admin user '{settings.admin_username}' already exists")
            return
        
        password_hash = AuthManager.hash_password(settings.admin_password)
        user_id = db.create_user(
            username=settings.admin_username,
            password_hash=password_hash,
            is_admin=True
        )
        
        print(f"✓ Created admin user '{settings.admin_username}' (ID: {user_id})")


# Global auth manager instance
auth = AuthManager()
