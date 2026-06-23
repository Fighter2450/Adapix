"""HTTP Basic auth for the admin UI.

Single shared admin user/password from .env (ADMIN_USERNAME / ADMIN_PASSWORD).
If both are blank, auth is DISABLED (dev convenience). For production, set
both in .env.

Multi-tenant per-practice auth is a follow-up. v0 ships single-tenant
deployment per pilot, so one admin credential pair is enough.
"""
from __future__ import annotations

import secrets

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from ..config import Settings

security = HTTPBasic(auto_error=False)


def verify_admin(
    creds: HTTPBasicCredentials | None = Depends(security),
) -> str:
    """FastAPI dependency that protects admin routes.

    Returns the authenticated username, or raises 401.

    If ADMIN_USERNAME / ADMIN_PASSWORD are unset in .env, auth is bypassed
    and "anonymous" is returned. This makes local dev frictionless. Always
    set both in production.
    """
    settings = Settings()
    expected_user = settings.admin_username
    expected_pass = settings.admin_password

    if not expected_user or not expected_pass:
        # Auth disabled (dev mode)
        return "anonymous"

    if creds is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": 'Basic realm="Adapix Admin"'},
        )

    user_ok = secrets.compare_digest(
        creds.username.encode("utf-8"), expected_user.encode("utf-8")
    )
    pass_ok = secrets.compare_digest(
        creds.password.encode("utf-8"), expected_pass.encode("utf-8")
    )
    if not (user_ok and pass_ok):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": 'Basic realm="Adapix Admin"'},
        )
    return creds.username
