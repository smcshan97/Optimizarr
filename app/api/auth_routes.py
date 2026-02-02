"""
Authentication API routes for Optimizarr.
"""
from fastapi import APIRouter, Depends, HTTPException, status
from typing import Dict

from app.api.models import LoginRequest, LoginResponse, UserResponse, MessageResponse
from app.api.dependencies import get_current_user, get_current_admin_user
from app.auth import auth
from app.database import db


router = APIRouter(prefix="/auth", tags=["authentication"])


@router.post("/login", response_model=LoginResponse)
async def login(credentials: LoginRequest):
    """
    Authenticate user and return JWT token.
    
    Args:
        credentials: Username and password
        
    Returns:
        Access token and user info
        
    Raises:
        HTTPException: If authentication fails
    """
    # Authenticate user
    user = auth.authenticate_user(credentials.username, credentials.password)
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # Generate access token
    access_token = auth.create_access_token(
        user_id=user['id'],
        username=user['username'],
        is_admin=user['is_admin']
    )
    
    # Return token and user info
    return LoginResponse(
        access_token=access_token,
        token_type="bearer",
        user={
            "id": user['id'],
            "username": user['username'],
            "email": user.get('email'),
            "is_admin": user['is_admin']
        }
    )


@router.get("/me", response_model=UserResponse)
async def get_current_user_info(current_user: Dict = Depends(get_current_user)):
    """
    Get current authenticated user's information.
    
    Args:
        current_user: Injected current user from token
        
    Returns:
        User information
    """
    return UserResponse(
        id=current_user['id'],
        username=current_user['username'],
        email=current_user.get('email'),
        is_admin=current_user['is_admin'],
        created_at=current_user.get('created_at'),
        last_login=current_user.get('last_login')
    )


@router.post("/logout", response_model=MessageResponse)
async def logout(current_user: Dict = Depends(get_current_user)):
    """
    Logout current user (client should discard token).
    
    Args:
        current_user: Injected current user from token
        
    Returns:
        Success message
    """
    # In a JWT-based system, logout is primarily client-side
    # The client should discard the token
    # We could optionally add the token to a blacklist here
    
    return MessageResponse(
        message="Logged out successfully. Please discard your token."
    )


@router.post("/change-password", response_model=MessageResponse)
async def change_password(
    current_password: str,
    new_password: str,
    current_user: Dict = Depends(get_current_user)
):
    """
    Change current user's password.
    
    Args:
        current_password: User's current password
        new_password: New password to set
        current_user: Injected current user from token
        
    Returns:
        Success message
        
    Raises:
        HTTPException: If current password is incorrect
    """
    # Verify current password
    if not auth.verify_password(current_password, current_user['password_hash']):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect"
        )
    
    # Hash new password
    new_password_hash = auth.hash_password(new_password)
    
    # Update password in database
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (new_password_hash, current_user['id'])
        )
    
    return MessageResponse(message="Password changed successfully")
