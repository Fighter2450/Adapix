"""JWT cookie-based auth for the Adapix SaaS platform."""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import bcrypt
from fastapi import Depends, HTTPException, Request, status
from jose import JWTError, jwt

def _jwt_secret() -> str:
    """Env var wins. Without one, use a per-install random secret persisted
    on the data volume — NEVER a hardcoded default anyone can read on GitHub
    and use to forge sessions."""
    env = os.environ.get("JWT_SECRET_KEY", "").strip()
    if env:
        return env
    from pathlib import Path
    p = Path(os.environ.get("ADAPIX_VAR", ".")) / "jwt_secret"
    if p.exists():
        return p.read_text().strip()
    import secrets
    val = secrets.token_hex(32)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(val)
    return val


SECRET_KEY = _jwt_secret()
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


RESET_TOKEN_EXPIRE_MINUTES = 30


def create_reset_token(user_id: int, password_hash: str) -> str:
    """A short-lived password-reset token. It's signed over a fingerprint of
    the CURRENT password hash, so the moment the password changes (or the
    token is used to change it) the token stops verifying — single-use for
    free, no server-side token store needed."""
    import hashlib
    fp = hashlib.sha256(password_hash.encode()).hexdigest()[:16]
    expire = datetime.now(timezone.utc) + timedelta(minutes=RESET_TOKEN_EXPIRE_MINUTES)
    payload = {"sub": str(user_id), "pfp": fp, "typ": "reset", "exp": expire}
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def verify_reset_token(token: str, password_hash: str) -> int | None:
    """Return the user id if the reset token is valid for THIS password hash,
    else None (expired, wrong type, or already used because the hash moved)."""
    import hashlib
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        if payload.get("typ") != "reset":
            return None
        fp = hashlib.sha256(password_hash.encode()).hexdigest()[:16]
        if payload.get("pfp") != fp:
            return None
        return int(payload["sub"])
    except (JWTError, KeyError, ValueError):
        return None


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
