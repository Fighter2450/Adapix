"""Authentication routes: signup, login, logout, me."""
from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from ..db import get_session
from ..models import Organization, User
from .auth import (
    ACCESS_TOKEN_EXPIRE_DAYS,
    COOKIE_NAME,
    create_access_token,
    get_current_user,
    hash_password,
    verify_password,
    CurrentUser,
)

router = APIRouter(tags=["auth"])
TEMPLATE_DIR = Path(__file__).parent / "templates"


@router.get("/login", response_class=HTMLResponse)
def login_page():
    return HTMLResponse((TEMPLATE_DIR / "login.html").read_text(encoding="utf-8"))


@router.get("/signup", response_class=HTMLResponse)
def signup_page():
    return HTMLResponse((TEMPLATE_DIR / "signup.html").read_text(encoding="utf-8"))


@router.post("/auth/signup")
async def api_signup(
    email: str = Form(...),
    password: str = Form(...),
    business_name: str = Form(...),
):
    email = email.lower().strip()
    business_name = business_name.strip()

    if len(password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="Enter a valid email address")
    if not business_name:
        raise HTTPException(status_code=400, detail="Business name is required")

    with get_session() as s:
        existing = s.query(User).filter(User.email == email).first()
        if existing:
            raise HTTPException(status_code=400, detail="An account with this email already exists")

        org = Organization(id=str(uuid.uuid4()), name=business_name, plan="trial")
        s.add(org)
        s.flush()

        user = User(
            org_id=org.id,
            email=email,
            password_hash=hash_password(password),
            role="owner",
        )
        s.add(user)
        s.flush()

        token = create_access_token(user.id, org.id, user.email)

    resp = JSONResponse({"ok": True, "redirect": "/app"})
    resp.set_cookie(
        COOKIE_NAME,
        token,
        max_age=ACCESS_TOKEN_EXPIRE_DAYS * 86400,
        httponly=True,
        samesite="lax",
        secure=False,  # set True behind HTTPS in production
    )
    return resp


@router.post("/auth/login")
async def api_login(
    email: str = Form(...),
    password: str = Form(...),
):
    email = email.lower().strip()

    with get_session() as s:
        user = s.query(User).filter(User.email == email).first()
        if not user or not verify_password(password, user.password_hash):
            raise HTTPException(status_code=401, detail="Invalid email or password")
        token = create_access_token(user.id, user.org_id, user.email)

    resp = JSONResponse({"ok": True, "redirect": "/app"})
    resp.set_cookie(
        COOKIE_NAME,
        token,
        max_age=ACCESS_TOKEN_EXPIRE_DAYS * 86400,
        httponly=True,
        samesite="lax",
        secure=False,
    )
    return resp


@router.post("/auth/logout")
async def api_logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(COOKIE_NAME)
    return resp


@router.get("/auth/me")
async def api_me(user: CurrentUser = Depends(get_current_user)):
    return {"id": user.id, "email": user.email, "org_id": user.org_id}
