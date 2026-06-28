"""JWT cookie-based auth for the Adapix SaaS platform."""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import bcrypt
from fastapi import Depends, HTTPException, Request, status
from jose import JWTError, jwt

SECRET_KEY = os.environ.get("JWT_SECRET_KEY", "dev-secret-change-in-production-please")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_DAYS = 30
COOKIE_NAME = "adapix_session"


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))


def create_access_token(user_id: int, org_id: str, email: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(days=ACCESS_TOKEN_EXPIRE_DAYS)
    payload = {"sub": str(user_id), "org": org_id, "email": email, "exp": expire}
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def _decode_token(token: str) -> dict:
    return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])


class CurrentUser:
    """Lightweight session context extracted from the JWT cookie."""

    def __init__(self, user_id: int, org_id: str, email: str):
        self.id = user_id
        self.org_id = org_id
        self.email = email


async def get_current_user(request: Request) -> CurrentUser:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"X-Redirect": "/login"},
        )
    try:
        payload = _decode_token(token)
        return CurrentUser(
            user_id=int(payload["sub"]),
            org_id=payload["org"],
            email=payload["email"],
        )
    except (JWTError, KeyError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired session",
            headers={"X-Redirect": "/login"},
        )


async def verify_admin(user: CurrentUser = Depends(get_current_user)) -> str:
    """Backwards-compat dependency. Returns org_id (used as practice_id throughout)."""
    return user.org_id
