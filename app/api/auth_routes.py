"""
Authentication API routes for Optimizarr.
"""
from fastapi import APIRouter, Depends, HTTPException, Request, status
from typing import Dict

from app.api.models import LoginRequest, LoginResponse, UserResponse, MessageResponse, ChangePasswordRequest
from app.api.dependencies import get_current_user
from app.auth import auth, login_limiter
from app.database import db


router = APIRouter(prefix="/auth", tags=["authentication"])


def _client_key(request: Request) -> str:
    return request.client.host if request.client else "unknown"


@router.post("/login", response_model=LoginResponse)
def login(credentials: LoginRequest, request: Request):
    """
    Authenticate user and return JWT token.

    Failed attempts are throttled per client address (5 per 15 minutes) so
    the password can't be brute-forced. Sync handler: bcrypt is deliberately
    slow and must not block the event loop.
    """
    client = _client_key(request)
    retry = login_limiter.retry_after(client)
    if retry:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Too many failed logins. Try again in {retry // 60 + 1} minute(s).",
            headers={"Retry-After": str(retry)},
        )

    user = auth.authenticate_user(credentials.username, credentials.password)

    if not user:
        login_limiter.record_failure(client)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    login_limiter.reset(client)

    access_token = auth.create_access_token(
        user_id=user['id'],
        username=user['username'],
        is_admin=user['is_admin'],
        token_version=user.get('token_version') or 0,
    )

    return LoginResponse(
        access_token=access_token,
        token_type="bearer",
        user={
            "id": user['id'],
            "username": user['username'],
            "email": user.get('email'),
            "is_admin": user['is_admin'],
            "must_change_password": bool(user.get('must_change_password', False)),
        }
    )


@router.get("/me", response_model=UserResponse)
def get_current_user_info(current_user: Dict = Depends(get_current_user)):
    """Get current authenticated user's information."""
    return UserResponse(
        id=current_user['id'],
        username=current_user['username'],
        email=current_user.get('email'),
        is_admin=current_user['is_admin'],
        created_at=current_user.get('created_at'),
        last_login=current_user.get('last_login')
    )


@router.post("/stream-token")
def stream_token(current_user: Dict = Depends(get_current_user)):
    """Short-lived token for the progress EventSource URL.

    EventSource can't send an Authorization header, so the token travels in
    the query string — where proxies and logs keep it. Handing out a
    one-minute, stream-only token means a leaked URL is worthless.
    """
    return {"token": auth.create_stream_token(current_user)}


@router.post("/logout", response_model=MessageResponse)
def logout(current_user: Dict = Depends(get_current_user)):
    """Logout current user (client should discard token)."""
    # JWT logout is client-side; changing the password revokes all tokens.
    return MessageResponse(
        message="Logged out successfully. Please discard your token."
    )


@router.post("/change-password")
def change_password(
    request: ChangePasswordRequest,
    current_user: Dict = Depends(get_current_user)
):
    """
    Change current user's password.

    Revokes every token issued before the change (other browsers/devices
    are signed out) and returns a fresh token for this session.
    """
    user = db.get_user(current_user['id'])
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )

    if not auth.verify_password(request.current_password, user['password_hash']):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect"
        )

    db.set_user_password(user['id'], auth.hash_password(request.new_password))
    user = db.get_user(user['id'])

    return {
        "message": "Password changed successfully",
        "success": True,
        "access_token": auth.create_access_token(
            user_id=user['id'], username=user['username'],
            is_admin=user['is_admin'], token_version=user.get('token_version') or 0,
        ),
    }
