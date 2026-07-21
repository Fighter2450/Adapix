"""Authentication routes: signup, login, logout, me."""
from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from ..config import Settings
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

# Session cookie is Secure by default (HTTPS-only) so the 30-day token
# can't leak over a downgraded/http request. Local dev over plain http
# sets ADAPIX_COOKIE_INSECURE=1 to log in.
import os as _os
_COOKIE_SECURE = _os.environ.get("ADAPIX_COOKIE_INSECURE", "") != "1"


def _set_session_cookie(resp, token: str) -> None:
    resp.set_cookie(
        COOKIE_NAME,
        token,
        max_age=ACCESS_TOKEN_EXPIRE_DAYS * 86400,
        httponly=True,
        samesite="lax",
        secure=_COOKIE_SECURE,
    )


# Per-IP throttle on the credential endpoints — bcrypt slows a guesser but
# doesn't stop credential-stuffing or email enumeration. In-memory sliding
# window (resets on restart, fine as a burst brake). 10 attempts / 5 min / IP.
import threading as _threading
import time as _time
from collections import deque as _deque

_auth_attempts: dict[str, _deque] = {}
_auth_lock = _threading.RLock()
_AUTH_MAX = 10
_AUTH_WINDOW = 300


def _auth_rate_ok(request: Request) -> bool:
    ip = (request.headers.get("x-forwarded-for", "").split(",")[0].strip()
          or (request.client.host if request.client else "unknown"))
    now = _time.monotonic()
    with _auth_lock:
        win = _auth_attempts.setdefault(ip, _deque())
        while win and now - win[0] > _AUTH_WINDOW:
            win.popleft()
        if len(win) >= _AUTH_MAX:
            return False
        win.append(now)
        return True

# Marketing-site origins allowed to ask "is this browser signed in?" —
# adapixai.com and app.adapixai.com are the same site, so the session
# cookie (SameSite=Lax) rides along on the fetch; CORS headers below are
# what lets the homepage script actually read the yes/no answer.
_PING_ORIGINS = ("https://adapixai.com", "https://www.adapixai.com")


@router.get("/api/v1/session/ping")
def session_ping(request: Request):
    """Signed-in check for the marketing homepage: returning visitors who
    still have a valid session get bounced straight into the app instead
    of seeing the sales page again. Never errors — an unauthenticated or
    expired browser just gets authed:false."""
    from .auth import _decode_token
    authed = False
    token = request.cookies.get(COOKIE_NAME)
    if token:
        try:
            _decode_token(token)
            authed = True
        except Exception:
            authed = False
    resp = JSONResponse({"authed": authed})
    origin = request.headers.get("origin", "")
    if origin in _PING_ORIGINS:
        resp.headers["Access-Control-Allow-Origin"] = origin
        resp.headers["Access-Control-Allow-Credentials"] = "true"
        resp.headers["Vary"] = "Origin"
    return resp


@router.get("/login", response_class=HTMLResponse)
def login_page():
    return HTMLResponse((TEMPLATE_DIR / "login.html").read_text(encoding="utf-8"))


@router.get("/signup", response_class=HTMLResponse)
def signup_page():
    return HTMLResponse((TEMPLATE_DIR / "signup.html").read_text(encoding="utf-8"))


@router.post("/auth/signup")
async def api_signup(
    request: Request,
    background: BackgroundTasks,
    email: str = Form(...),
    password: str = Form(...),
    business_name: str = Form(...),
    ref: str = Form(""),
):
    if not _auth_rate_ok(request):
        raise HTTPException(status_code=429, detail="Too many attempts — wait a few minutes and try again.")
    email = email.lower().strip()
    business_name = business_name.strip()
    ref = ref.strip().upper()[:16]

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
        # Referral capture: only store codes that actually belong to another
        # org — a typo'd or made-up code must never create a phantom reward.
        if ref:
            referrer = s.query(Organization).filter(Organization.referral_code == ref).first()
            if referrer is not None and referrer.id != org.id:
                org.referred_by_code = ref
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
        new_org_id = org.id

    # NOTE: the dedicated calling number is provisioned when the TRIAL STARTS
    # (checkout completed, card on file) — see the Stripe checkout.session.
    # completed webhook — NOT here at signup. Provisioning at signup would burn
    # a paid carrier number on every tire-kicker who never enters a card.

    resp = JSONResponse({"ok": True, "redirect": "/app/billing"})
    _set_session_cookie(resp, token)
    return resp


@router.post("/auth/login")
async def api_login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
):
    if not _auth_rate_ok(request):
        raise HTTPException(status_code=429, detail="Too many attempts — wait a few minutes and try again.")
    email = email.lower().strip()

    with get_session() as s:
        user = s.query(User).filter(User.email == email).first()
        if not user or not verify_password(password, user.password_hash):
            raise HTTPException(status_code=401, detail="Invalid email or password")
        token = create_access_token(user.id, user.org_id, user.email)

    resp = JSONResponse({"ok": True, "redirect": "/app"})
    _set_session_cookie(resp, token)
    return resp


@router.post("/auth/logout")
async def api_logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(COOKIE_NAME)
    return resp


# ---------------------------------------------------------------------------
# Password reset — request a link, then set a new password.
# ---------------------------------------------------------------------------
@router.get("/forgot-password", response_class=HTMLResponse)
def forgot_password_page():
    return HTMLResponse((TEMPLATE_DIR / "forgot-password.html").read_text(encoding="utf-8"))


@router.get("/reset-password", response_class=HTMLResponse)
def reset_password_page():
    return HTMLResponse((TEMPLATE_DIR / "reset-password.html").read_text(encoding="utf-8"))


@router.post("/auth/forgot")
async def api_forgot(request: Request, email: str = Form(...)):
    """Email a password-reset link. ALWAYS returns ok — never reveal whether
    an email is registered (no enumeration oracle). Rate-limited per IP."""
    if not _auth_rate_ok(request):
        raise HTTPException(status_code=429, detail="Too many attempts — wait a few minutes and try again.")
    email = email.lower().strip()
    from .auth import create_reset_token
    from ..config import Settings
    try:
        with get_session() as s:
            user = s.query(User).filter(User.email == email).first()
            if user is not None:
                token = create_reset_token(user.id, user.password_hash)
                uid_email = user.email
        if user is not None:
            base = (Settings().public_base_url or f"{request.url.scheme}://{request.url.netloc}").rstrip("/")
            link = f"{base}/reset-password?token={token}"
            try:
                from ..channels import EmailChannel
                EmailChannel(Settings()).send(
                    uid_email,
                    "Reset your Adapix password",
                    (
                        "Someone (hopefully you) asked to reset the password on your "
                        "Adapix account.\n\nUse this link within 30 minutes to set a new "
                        f"password:\n{link}\n\nIf you didn't ask for this, you can ignore "
                        "this email — your password won't change."
                    ),
                    from_name="Adapix",
                )
            except Exception as e:
                print(f"[adapix] reset email send failed: {e}")
    except Exception as e:
        print(f"[adapix] forgot-password error: {e}")
    return JSONResponse({"ok": True})


@router.post("/auth/reset")
async def api_reset(request: Request, token: str = Form(...), password: str = Form(...)):
    """Set a new password from a valid reset token. The token is signed over
    the OLD password hash, so it's single-use — once the hash changes it stops
    verifying."""
    if not _auth_rate_ok(request):
        raise HTTPException(status_code=429, detail="Too many attempts — wait a few minutes and try again.")
    if len(password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")
    from .auth import _decode_token, verify_reset_token
    with get_session() as s:
        # The token's signature is already valid (we signed it); decode it to
        # get the user id, load that user, then verify_reset_token confirms it
        # still matches their CURRENT password hash (single-use enforcement).
        try:
            uid = int(_decode_token(token).get("sub", 0))
        except Exception:
            raise HTTPException(status_code=400, detail="This reset link is invalid or has expired.")
        user = s.query(User).filter(User.id == uid).first()
        if user is None or verify_reset_token(token, user.password_hash) != user.id:
            raise HTTPException(status_code=400, detail="This reset link is invalid or has expired.")
        user.password_hash = hash_password(password)
        s.commit()
    return JSONResponse({"ok": True})


@router.get("/auth/me")
async def api_me(user: CurrentUser = Depends(get_current_user)):
    return {"id": user.id, "email": user.email, "org_id": user.org_id}
