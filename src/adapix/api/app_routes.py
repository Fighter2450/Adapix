"""Adapix mobile PWA — surgeon-facing companion app.

This is the v0 of the "Adapix app" promised on the landing page. It's a PWA:
served at /app, installs to the home screen on iOS + Android, runs against
the same backend as the admin dashboard.

Routes:
  GET  /app                         - single-page mobile UI (no auth on shell)
  GET  /app/manifest.json           - PWA manifest
  GET  /app/sw.js                   - service worker
  GET  /app/icon-{192,512}.png      - app icons (served from branding/)
  GET  /api/v1/feed                 - JSON: escalations + pending approvals + digest
  POST /api/v1/approvals/{id}/approve
  POST /api/v1/approvals/{id}/reject
  POST /api/v1/escalations/{id}/resolve

The HTML shell loads without auth (no PHI in it). The JSON API calls trigger
HTTP Basic auth via the existing verify_admin dependency. Browsers cache the
credentials so the surgeon enters them once at install time.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Request, UploadFile
from typing import List
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
)
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from ..approval import ApprovalManager
from ..db import get_session
from ..models import Campaign, EscalationEvent, Message, Patient
from .auth import verify_admin

router = APIRouter(tags=["app"])

TEMPLATE_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))

# Branding folder (icons live here so we don't duplicate)
BRANDING_DIR = Path(__file__).resolve().parents[3] / "branding"


# ---------------------------------------------------------------------------
# PWA shell (HTML + manifest + service worker + icons)
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Billing — one plan, Stripe-hosted checkout. Shown right after signup;
# skippable so the "no card to start" promise holds.
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Home dashboard chart: daily activity counts for the last N days
# ---------------------------------------------------------------------------
@router.get("/api/v1/stats/daily")
def api_stats_daily(days: int = 30, _user: str = Depends(verify_admin)):
    from collections import defaultdict
    from datetime import datetime, timedelta
    days = max(7, min(90, days))
    cutoff = datetime.utcnow() - timedelta(days=days - 1)
    cutoff = datetime(cutoff.year, cutoff.month, cutoff.day)
    with get_session() as s:
        rows = (
            s.query(Message.created_at, Message.direction, Message.status)
            .join(Campaign, Message.campaign_id == Campaign.id)
            .filter(Campaign.practice_id == _user, Message.created_at >= cutoff)
            .all()
        )
    sent: dict[str, int] = defaultdict(int)
    replies: dict[str, int] = defaultdict(int)
    for created, direction, status in rows:
        day = created.date().isoformat()
        if direction == "outbound" and status in ("sent", "delivered", "replied"):
            sent[day] += 1
        elif direction == "inbound":
            replies[day] += 1
    today = datetime.utcnow().date()
    out = []
    for i in range(days - 1, -1, -1):
        day = (today - timedelta(days=i)).isoformat()
        out.append({"date": day, "sent": sent.get(day, 0), "replies": replies.get(day, 0)})
    return {"days": out}


@router.get("/app/billing", response_class=HTMLResponse)
def billing_page(request: Request):
    from fastapi.responses import RedirectResponse
    from .auth import COOKIE_NAME
    if not request.cookies.get(COOKIE_NAME):
        return RedirectResponse(url="/login", status_code=302)
    return HTMLResponse((TEMPLATE_DIR / "billing.html").read_text(encoding="utf-8"))


@router.get("/api/v1/billing/status")
def api_billing_status(_user: str = Depends(verify_admin)):
    from ..billing import configured, refresh_status
    if not configured():
        return {"configured": False, "status": "none"}
    return {"configured": True, "status": refresh_status(_user)}


@router.post("/api/v1/billing/checkout")
def api_billing_checkout(request: Request, _user: str = Depends(verify_admin)):
    from ..billing import configured, create_checkout_session
    from ..config import Settings
    from ..models import User
    if not configured():
        raise HTTPException(status_code=503, detail="Billing isn't set up yet — you can skip this step.")
    from ..models import Organization
    with get_session() as s:
        owner = s.query(User).filter(User.org_id == _user, User.role == "owner").first()
        email = owner.email if owner else ""
        org = s.get(Organization, _user)
        used = (datetime.utcnow() - org.created_at).days if org and org.created_at else 14
        trial_days = max(0, 14 - used)
    base = (Settings().public_base_url or f"{request.url.scheme}://{request.url.netloc}").rstrip("/")
    try:
        url = create_checkout_session(_user, email, base, trial_days=trial_days)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Stripe error: {e}")
    return {"url": url}


@router.post("/api/v1/billing/cancel")
def api_billing_cancel(_user: str = Depends(verify_admin)):
    from ..billing import cancel_subscription, configured
    if not configured():
        raise HTTPException(status_code=503, detail="Billing isn't set up")
    try:
        status = cancel_subscription(_user)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Stripe error: {e}")
    return {"ok": True, "status": status, "cancel_at_period_end": True}


@router.get("/app/billing/success", response_class=HTMLResponse)
def billing_success(request: Request, session_id: str = ""):
    from fastapi.responses import RedirectResponse
    from .auth import COOKIE_NAME, _decode_token
    token = request.cookies.get(COOKIE_NAME)
    if not token or not session_id:
        return RedirectResponse(url="/app", status_code=302)
    try:
        org_id = _decode_token(token).get("org")
        from ..billing import confirm_checkout
        confirm_checkout(org_id, session_id)
    except Exception:
        pass  # page still loads; status endpoint will re-poll
    return RedirectResponse(url="/app/billing", status_code=302)


@router.get("/app", response_class=HTMLResponse)
def app_shell(request: Request):
    from fastapi.responses import RedirectResponse
    from .auth import COOKIE_NAME, _decode_token
    from jose import JWTError
    from ..db import get_engine
    from ..models import OrgProfile
    from sqlalchemy.orm import Session
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return RedirectResponse(url="/login", status_code=302)
    try:
        payload = _decode_token(token)
        org_id = payload.get("org")
    except (JWTError, Exception):
        return RedirectResponse(url="/login", status_code=302)
    # Check per-org profile in DB first; fall back to legacy flat file
    configured = False
    if org_id:
        try:
            with Session(get_engine()) as s:
                configured = s.get(OrgProfile, org_id) is not None
        except Exception:
            pass
    if not configured and not org_id:
        # Legacy single-tenant fallback only — a real org is judged solely
        # by its own profile, otherwise the first business to finish setup
        # would silence the welcome wizard for every signup after it.
        configured = CONFIGURED_FLAG.exists()
    if not configured:
        return RedirectResponse(url="/welcome", status_code=302)
    return HTMLResponse((TEMPLATE_DIR / "app.html").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# First-time setup wizard
# ---------------------------------------------------------------------------
# Persistence: a flag file marks the device as configured; the practice
# profile + voice + workflow choices land in a small JSON sidecar.
# Both default to ./ in dev mode so you can iterate without root perms.
SETUP_DIR = Path(os.environ.get("ADAPIX_VAR", "."))
CONFIGURED_FLAG = SETUP_DIR / "configured.flag"
PRACTICE_JSON = SETUP_DIR / "practice_profile.json"


@router.get("/welcome", response_class=HTMLResponse)
def welcome_page():
    """6-step first-time setup wizard. Runs once per device."""
    return HTMLResponse((TEMPLATE_DIR / "welcome.html").read_text(encoding="utf-8"))


@router.get("/calculator", response_class=HTMLResponse)
def calculator_page():
    """Revenue gap calculator — public-facing sales tool. No auth required."""
    return HTMLResponse((TEMPLATE_DIR / "calculator.html").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# In-dashboard chatbot — ongoing conversation with the practice
# ---------------------------------------------------------------------------
@router.get("/chat", response_class=HTMLResponse)
def chat_page():
    # Legacy standalone teaching chat — superseded by the Workshop tab
    # inside the dashboard. Old links land in the app instead of a stale page.
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/app", status_code=302)


@router.get("/api/v1/chat/history")
def api_chat_history(org_id: str = Depends(verify_admin)):
    from ..chat import load_history, missing_topics, suggestions_for
    from ..practice import load_profile
    profile = load_profile(org_id)
    msgs = load_history(org_id)
    return {
        "messages": msgs,
        "suggestions": suggestions_for(missing_topics(profile, msgs)),
    }


class OpenerBody(BaseModel):
    onboarding: bool = False


@router.post("/api/v1/chat/opener")
def api_chat_opener(body: OpenerBody | None = None, _user: str = Depends(verify_admin)):
    """Generate the bot's first message — used when there's no history yet.
    Pass {"onboarding": true} the first time after the welcome wizard so
    Adapix knows she's interviewing the practice (rather than the regular
    'hi, how can I help' opener)."""
    from ..chat import generate_opener
    try:
        return generate_opener(onboarding=bool(body and body.onboarding), org_id=_user)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"opener failed: {e}")


async def _parse_attachments(files: List[UploadFile]) -> list:
    """Read uploaded files into attachment dicts for the AI layer."""
    import base64
    result = []
    for f in files:
        if not f.filename:
            continue
        content = await f.read()
        mime = f.content_type or ""
        if mime.startswith("image/"):
            result.append({
                "type": "image",
                "name": f.filename,
                "media_type": mime,
                "data": base64.b64encode(content).decode(),
            })
        elif f.filename.lower().endswith(".pdf"):
            try:
                from pypdf import PdfReader
                import io
                reader = PdfReader(io.BytesIO(content))
                text = "\n".join(p.extract_text() or "" for p in reader.pages)
                result.append({"type": "text", "name": f.filename, "content": text})
            except Exception:
                result.append({"type": "text", "name": f.filename,
                                "content": content.decode("utf-8", errors="replace")})
        elif f.filename.lower().endswith(".docx"):
            try:
                from docx import Document
                import io
                doc = Document(io.BytesIO(content))
                text = "\n".join(p.text for p in doc.paragraphs)
                result.append({"type": "text", "name": f.filename, "content": text})
            except Exception:
                result.append({"type": "text", "name": f.filename,
                                "content": content.decode("utf-8", errors="replace")})
        else:
            result.append({"type": "text", "name": f.filename,
                            "content": content.decode("utf-8", errors="replace")})
    return result


class ChatSendBody(BaseModel):
    message: str


@router.post("/api/v1/chat/send")
async def api_chat_send(
    message: str = Form(""),
    files: List[UploadFile] = File(default=[]),
    _user: str = Depends(verify_admin),
):
    """User sent a message → return Adapix's reply + any new suggestions."""
    from ..chat import reply_to
    text = (message or "").strip()
    attachments = await _parse_attachments(files)
    if not text and not attachments:
        raise HTTPException(status_code=400, detail="empty message")
    try:
        return reply_to(text, attachments=attachments or None, org_id=_user)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"chat failed: {e}")


# ---------------------------------------------------------------------------
# Unified connectors status — single endpoint the dashboard hits to render
# the Connections tab. Returns one entry per integration with three flags:
#
#   configured  — credentials present in .env (so we can even attempt it)
#   connected   — OAuth complete / API keys validated
#   email/name  — for OAuth providers, who the account is bound to
#
# UI flow per card:
#   not configured  → show "Add credentials" with help link
#   configured but not connected → show "Connect"
#   connected → show "Test" + "Disconnect"
# ---------------------------------------------------------------------------
@router.get("/api/v1/connectors")
def api_connectors_status(org_id: str = Depends(verify_admin)):
    from ..config import Settings
    from ..oauth import status as oauth_status
    s = Settings()
    oauth = oauth_status(org_id)
    return {
        "connectors": [
            {
                "id": "google",
                "name": "Gmail (Google Workspace)",
                "kind": "email",
                "configured": bool(s.google_client_id and s.google_client_secret),
                "connected": oauth.get("google", {}).get("connected", False),
                "account": oauth.get("google", {}).get("email", ""),
                "help_url": "https://console.cloud.google.com/apis/credentials",
                "env_keys": ["GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET"],
                "connect_url": "/api/v1/oauth/google/start",
                "test_url": "/api/v1/email/test",
                "disconnect_url": "/api/v1/oauth/disconnect",
            },
            {
                "id": "microsoft",
                "name": "Outlook (Microsoft 365)",
                "kind": "email",
                "configured": bool(s.microsoft_client_id and s.microsoft_client_secret),
                "connected": oauth.get("microsoft", {}).get("connected", False),
                "account": oauth.get("microsoft", {}).get("email", ""),
                "help_url": "https://portal.azure.com/#blade/Microsoft_AAD_RegisteredApps/ApplicationsListBlade",
                "env_keys": ["MICROSOFT_CLIENT_ID", "MICROSOFT_CLIENT_SECRET"],
                "connect_url": "/api/v1/oauth/microsoft/start",
                "test_url": "/api/v1/email/test",
                "disconnect_url": "/api/v1/oauth/disconnect",
            },
            {
                "id": "twilio",
                "name": "Twilio SMS",
                "kind": "sms",
                "configured": bool(
                    s.twilio_account_sid and s.twilio_auth_token and s.twilio_from_number
                ),
                "connected": bool(
                    s.twilio_account_sid and s.twilio_auth_token and s.twilio_from_number
                ),
                "account": s.twilio_from_number,
                "help_url": "https://console.twilio.com/",
                "env_keys": [
                    "TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM_NUMBER",
                ],
                "connect_url": None,         # global config, no per-account OAuth
                "test_url": "/api/v1/sms/test",
                "disconnect_url": None,
            },
            {
                "id": "resend",
                "name": "Resend (transactional email)",
                "kind": "email",
                "configured": bool(s.resend_api_key and s.resend_from_email),
                "connected": bool(s.resend_api_key and s.resend_from_email),
                "account": s.resend_from_email,
                "help_url": "https://resend.com/api-keys",
                "env_keys": ["RESEND_API_KEY", "RESEND_FROM_EMAIL"],
                "connect_url": None,
                "test_url": "/api/v1/email/test",
                "disconnect_url": None,
            },
            {
                "id": "webpush",
                "name": "Web push notifications",
                "kind": "push",
                "configured": True,   # VAPID keys auto-generate on first run
                "connected": True,
                "account": "(this device)",
                "help_url": "",
                "env_keys": [],
                "connect_url": None,
                "test_url": "/api/v1/notify/test",
                "disconnect_url": None,
            },
        ],
    }


# ---------------------------------------------------------------------------
# Skills catalog — Anthropic-style skill bundles Adapix can run during
# the in-product chat. Each skill is a folder with SKILL.md inside.
# The list endpoint surfaces what's available for the current mode so
# the dashboard can render skill chips; the detail endpoint returns
# the full markdown body of a single skill.
# ---------------------------------------------------------------------------
@router.get("/api/v1/skills")
def api_skills_list(mode: str | None = None):
    """List skills available for a given mode (\"new\", \"existing\", or
    omit for everything). If `mode` is omitted, falls back to the mode
    saved by the welcome wizard so the dashboard can call this with no
    query params and get the right list."""
    from ..skills import list_skills
    from ..practice import load_profile
    effective = mode or load_profile().mode or "any"
    skills = list_skills(mode=effective)
    return {
        "mode": effective,
        "skills": [
            {
                "slug": s.slug,
                "name": s.name,
                "description": s.description,
                "mode": s.mode,
                "triggers": s.triggers,
            }
            for s in skills
        ],
    }


@router.get("/api/v1/skills/{slug}")
def api_skill_detail(slug: str):
    """Return the full SKILL.md body for one skill. The dashboard uses
    this to render a 'what this skill does' preview before the owner
    actually runs it."""
    from ..skills import get_skill
    s = get_skill(slug)
    if not s:
        raise HTTPException(status_code=404, detail=f"skill {slug!r} not found")
    return {
        "slug": s.slug,
        "name": s.name,
        "description": s.description,
        "mode": s.mode,
        "triggers": s.triggers,
        "body": s.body,
    }


# ---------------------------------------------------------------------------
# Structured long-term memory (facts Adapix has learned from /chat)
# ---------------------------------------------------------------------------
@router.get("/api/v1/memory")
def api_memory_list(_user: str = Depends(verify_admin)):
    from ..memory import all_facts
    return {"facts": all_facts(_user)}


@router.delete("/api/v1/memory/{fact_id}")
def api_memory_delete(fact_id: str, _user: str = Depends(verify_admin)):
    from ..memory import remove_fact
    ok = remove_fact(fact_id, _user)
    if not ok:
        raise HTTPException(status_code=404, detail="fact not found")
    return {"ok": True, "id": fact_id}


# ---------------------------------------------------------------------------
# Web Push notifications — the PWA can send lock-screen pings to the
# practice owner's phone any time something interesting happens.
# ---------------------------------------------------------------------------
@router.get("/api/v1/notify/vapid-public-key")
def api_notify_vapid_key():
    """Return the server's VAPID public key. The PWA's service worker
    needs this to subscribe to push."""
    from ..notifications import get_vapid_keys
    keys = get_vapid_keys()
    if not keys.get("public_key"):
        raise HTTPException(
            status_code=503,
            detail="push notifications are not configured on this server",
        )
    return {"public_key": keys["public_key"]}


class SubscribeBody(BaseModel):
    endpoint: str
    keys: dict = {}
    user_agent: str | None = None


@router.post("/api/v1/notify/subscribe")
def api_notify_subscribe(body: SubscribeBody, _user: str = Depends(verify_admin)):
    """Persist the browser's push subscription so we can target this
    device for notifications later."""
    from ..notifications import add_subscription
    try:
        rec = add_subscription({
            "endpoint": body.endpoint,
            "keys": body.keys,
            "user_agent": body.user_agent or "",
        }, org_id=_user)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "endpoint": rec["endpoint"]}


class UnsubscribeBody(BaseModel):
    endpoint: str


@router.post("/api/v1/notify/unsubscribe")
def api_notify_unsubscribe(body: UnsubscribeBody, _user: str = Depends(verify_admin)):
    from ..notifications import remove_subscription
    ok = remove_subscription(body.endpoint)
    return {"ok": ok}


@router.get("/api/v1/notify/status")
def api_notify_status(_user: str = Depends(verify_admin)):
    """How many devices are currently subscribed to push, so the UI
    can show 'Notifications on (1 device)' vs the 'Enable' button."""
    from ..notifications import list_subscriptions
    subs = list_subscriptions(_user)
    return {"count": len(subs), "endpoints": [s.get("endpoint") for s in subs]}


class TestPushBody(BaseModel):
    title: str | None = None
    body: str | None = None
    url: str | None = None


@router.post("/api/v1/notify/test")
def api_notify_test(body: TestPushBody, _user: str = Depends(verify_admin)):
    """Fire a test notification to every subscribed device — used by
    the 'Send test notification' button in Settings."""
    from ..notifications import push_notification
    res = push_notification(
        title=body.title or "Adapix",
        body=body.body or "This is a test notification — looking good.",
        url=body.url or "/app",
        tag="adapix-test",
        org_id=_user,
    )
    if not res.get("ok"):
        raise HTTPException(
            status_code=400,
            detail=res.get("error") or "no devices delivered",
        )
    return res


# ---------------------------------------------------------------------------
# Expense tracker — founder bookkeeping (personal for now, will be exposed
# to practices later as a billable feature on Adapix proper).
# ---------------------------------------------------------------------------
@router.get("/expenses", response_class=HTMLResponse)
def expenses_page():
    return HTMLResponse((TEMPLATE_DIR / "expenses.html").read_text(encoding="utf-8"))


@router.get("/api/v1/expenses")
def api_expenses_list(_user: str = Depends(verify_admin)):
    from ..expenses import (
        list_expenses, totals_by_category, totals_by_month,
        total_all_time, total_this_month, count_all, all_categories,
        process_due_subscriptions, list_subscriptions, monthly_burn, yearly_burn,
    )
    # Catch up any subscriptions that are due before we read totals
    process_due_subscriptions()
    return {
        "items":              list_expenses(),
        "totals_by_category": totals_by_category(),
        "totals_by_month":    totals_by_month(),
        "total_all_time":     total_all_time(),
        "total_this_month":   total_this_month(),
        "count":              count_all(),
        "categories":         all_categories(),
        "subscriptions":      list_subscriptions(),
        "monthly_burn":       monthly_burn(),
        "yearly_burn":        yearly_burn(),
    }


class AddSubscriptionBody(BaseModel):
    amount: float
    cycle: str = "monthly"           # "monthly" or "yearly"
    vendor: str = ""
    description: str = ""
    category: str = "Software / services"
    start_date: str | None = None


@router.post("/api/v1/subscriptions")
def api_subscriptions_add(body: AddSubscriptionBody, _user: str = Depends(verify_admin)):
    from ..expenses import add_subscription
    try:
        sub = add_subscription(
            amount=body.amount,
            cycle=body.cycle,
            vendor=body.vendor,
            description=body.description,
            category=body.category,
            start_date=body.start_date,
        )
        return {"ok": True, "subscription": sub}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/api/v1/subscriptions/{sub_id}/cancel")
def api_subscriptions_cancel(sub_id: str, _user: str = Depends(verify_admin)):
    """Stop future auto-charges. Past charges stay in history."""
    from ..expenses import cancel_subscription
    if not cancel_subscription(sub_id):
        raise HTTPException(status_code=404, detail="subscription not found or already cancelled")
    return {"ok": True, "id": sub_id}


@router.delete("/api/v1/subscriptions/{sub_id}")
def api_subscriptions_delete(sub_id: str, _user: str = Depends(verify_admin)):
    """Remove a subscription entirely from the list. Past expense entries
    from it remain in history."""
    from ..expenses import delete_subscription
    if not delete_subscription(sub_id):
        raise HTTPException(status_code=404, detail="subscription not found")
    return {"ok": True, "id": sub_id}


class AddExpenseBody(BaseModel):
    amount: float
    category: str = "Other"
    vendor: str = ""
    description: str = ""
    date: str | None = None


@router.post("/api/v1/expenses")
def api_expenses_add(body: AddExpenseBody, _user: str = Depends(verify_admin)):
    from ..expenses import add_expense
    try:
        rec = add_expense(
            amount=body.amount,
            category=body.category,
            vendor=body.vendor,
            description=body.description,
            date=body.date,
            source="manual",
        )
        return {"ok": True, "expense": rec}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/api/v1/expenses/{expense_id}")
def api_expenses_delete(expense_id: str, _user: str = Depends(verify_admin)):
    from ..expenses import remove_expense
    if not remove_expense(expense_id):
        raise HTTPException(status_code=404, detail="expense not found")
    return {"ok": True, "id": expense_id}


@router.get("/api/v1/expenses/export.csv", response_class=PlainTextResponse)
def api_expenses_csv(_user: str = Depends(verify_admin)):
    from ..expenses import to_csv
    return PlainTextResponse(content=to_csv(), media_type="text/csv")


class BulkExpenseBody(BaseModel):
    text: str


@router.post("/api/v1/expenses/bulk")
def api_expenses_bulk(body: BulkExpenseBody, _user: str = Depends(verify_admin)):
    """Bulk paste — one expense per non-blank line. Each line goes through
    the Claude expense extractor and gets added if it parses as an expense.
    Returns a summary of how many were added vs skipped, plus per-line
    results so the UI can show the user exactly what landed."""
    from ..expenses import extract_expense_from_message, add_expense
    lines = [ln.strip() for ln in (body.text or "").split("\n") if ln.strip()]
    results: list[dict] = []
    added = 0
    skipped = 0
    for ln in lines:
        try:
            parsed = extract_expense_from_message(ln)
        except Exception as e:
            results.append({"line": ln, "added": False, "error": str(e)})
            skipped += 1
            continue
        if parsed and parsed.get("amount", 0) > 0:
            rec = add_expense(
                amount=parsed["amount"],
                category=parsed["category"],
                vendor=parsed["vendor"],
                description=parsed["description"],
                source="bulk",
            )
            results.append({"line": ln, "added": True, "expense": rec})
            added += 1
        else:
            results.append({"line": ln, "added": False})
            skipped += 1
    return {"ok": True, "added": added, "skipped": skipped, "total_lines": len(lines), "results": results}


class SetupBody(BaseModel):
    practice: dict = {}
    tone: str = "warm_professional"
    workflows: list[str] = []
    escalations: list[str] = []
    # Optional free-form custom items from the "Other" cards in the wizard
    workflow_custom: str = ""
    escalation_custom: str = ""
    # Free-form description of the problems the practice is facing — the
    # heart of "adapt to my practice". Pasted into the AI's system prompt.
    practice_problems: str = ""
    # What kind of practice this is. The wizard now uses a searchable picker
    # backed by ~200 specific business types — practice_type is the slug id
    # (e.g. "oral_surgeon", "coffee_shop") and practice_type_label is the
    # human-readable name we show in the UI and feed to Adapix's prompt.
    # practice_type_custom is filled only when the user picks "Not listed".
    practice_type: str = ""
    practice_type_label: str = ""
    practice_type_custom: str = ""
    # Branch chosen at the top of the welcome wizard. "existing" = user already
    # runs a business and Adapix is automating follow-up for it. "new" = user
    # is launching a business and Adapix should help them set it up (naming,
    # services, pricing, first customers, etc.). The downstream system prompt
    # and dashboard widget defaults differ between the two.
    mode: str = "existing"


@router.post("/api/v1/setup/save")
def api_setup_save(body: SetupBody, org_id: str = Depends(verify_admin)):
    """Save the wizard's collected config per org into the DB."""
    from ..db import get_engine
    from ..models import OrgProfile
    from sqlalchemy.orm import Session
    payload = {
        "practice": body.practice,
        "tone": body.tone,
        "workflows": body.workflows,
        "escalations": body.escalations,
        "workflow_custom": body.workflow_custom,
        "escalation_custom": body.escalation_custom,
        "practice_problems": body.practice_problems,
        "practice_type": body.practice_type,
        "practice_type_label": body.practice_type_label,
        "practice_type_custom": body.practice_type_custom,
        "mode": body.mode if body.mode in ("new", "existing") else "existing",
        "configured_at": datetime.utcnow().isoformat() + "Z",
    }
    try:
        with Session(get_engine()) as s:
            row = s.get(OrgProfile, org_id)
            if row:
                # MERGE, never replace. The profile blob also holds everything
                # taught after the wizard (knowledge_base, services, rules,
                # description, paused…) — a wizard re-run must not wipe it.
                # Empty wizard values never overwrite existing non-empty ones.
                merged = dict(row.data or {})
                for k, v in payload.items():
                    if k == "practice" and isinstance(v, dict):
                        practice = dict(merged.get("practice") or {})
                        for pk, pv in v.items():
                            if pv not in ("", None, []):
                                practice[pk] = pv
                        merged["practice"] = practice
                    elif v not in ("", None, []):
                        merged[k] = v
                merged["configured_at"] = payload["configured_at"]
                row.data = merged
                row.configured_at = datetime.utcnow()
            else:
                s.add(OrgProfile(org_id=org_id, data=payload))
            s.commit()
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"could not save: {e}")


@router.get("/api/v1/setup/status")
def api_setup_status(org_id: str = Depends(verify_admin)):
    """Read current setup state for the calling org."""
    from ..db import get_engine
    from ..models import Organization, OrgProfile
    from sqlalchemy.orm import Session
    try:
        with Session(get_engine()) as s:
            row = s.get(OrgProfile, org_id)
            if row:
                return {"configured": True, "profile": row.data}
            # Not configured yet — surface the business name given at signup
            # so the wizard can prefill instead of asking for it twice.
            org = s.get(Organization, org_id)
            if org and org.name:
                return {"configured": False, "profile": None, "org_name": org.name}
    except Exception:
        pass
    # Legacy fallback for dev environments
    if CONFIGURED_FLAG.exists():
        try:
            profile = json.loads(PRACTICE_JSON.read_text()) if PRACTICE_JSON.exists() else None
        except Exception:
            profile = None
        return {"configured": True, "profile": profile}
    return {"configured": False, "profile": None}


# ---------------------------------------------------------------------------
# Business Knowledge — Q&A facts the owner teaches Adapix about THEIR
# business (services, pricing, hours, policies...). Stored inside the same
# org_profiles.data JSON blob as the wizard's setup payload (key
# "knowledge_base"), so no schema migration is needed. Read by
# PracticeProfile.knowledge_fragment() (feeds message composition) and
# .classifier_context_fragment() (tells the inbound classifier which
# questions Adapix can now answer itself instead of escalating).
# ---------------------------------------------------------------------------
class KnowledgeEntryBody(BaseModel):
    q: str
    a: str


def _load_org_profile_data(s, org_id: str) -> dict:
    """Read-modify-write guard: every save handler on the Business Knowledge
    / Business Profile / rules / notify-prefs pages follows the same
    load -> mutate one key -> save-the-whole-blob pattern, sharing one JSON
    column per org. Without a lock, two overlapping saves (e.g. adding a
    service right as another card's Save button fires) race: the second
    request reads a copy that predates the first request's write, then
    writes its own copy back — silently erasing the first save. FOR UPDATE
    holds a row lock for the rest of this transaction, so a second request
    against the same org blocks here until the first one commits, then
    reads the already-updated row instead of a stale one."""
    from ..models import OrgProfile
    row = s.query(OrgProfile).filter(OrgProfile.org_id == org_id).with_for_update().first()
    return dict(row.data) if row and row.data else {}


def _save_org_profile_data(s, org_id: str, data: dict) -> None:
    from ..models import OrgProfile
    row = s.get(OrgProfile, org_id)
    if row:
        row.data = data
    else:
        s.add(OrgProfile(org_id=org_id, data=data))


# ---------------------------------------------------------------------------
# Website import — paste your site URL, Adapix reads it and stages the
# business facts for review. A Home reminder stays up until it's applied.
# ---------------------------------------------------------------------------
class WebsiteImportBody(BaseModel):
    url: str


@router.post("/api/v1/website-import")
def api_website_import_start(body: WebsiteImportBody, background: BackgroundTasks, org_id: str = Depends(verify_admin)):
    from ..db import get_engine
    from ..website_import import run_import
    from sqlalchemy.orm import Session
    url = body.url.strip()
    if not url or " " in url or "." not in url:
        raise HTTPException(status_code=400, detail="Enter your website address, like yourbusiness.com")
    with Session(get_engine()) as s:
        data = _load_org_profile_data(s, org_id)
        data["website_import"] = {"url": url, "status": "running", "error": "",
                                  "at": datetime.utcnow().isoformat() + "Z"}
        practice = dict(data.get("practice") or {})
        practice["website"] = url
        data["practice"] = practice
        _save_org_profile_data(s, org_id, data)
        s.commit()
    background.add_task(run_import, org_id, url)
    return {"ok": True, "status": "running"}


@router.get("/api/v1/website-import")
def api_website_import_status(org_id: str = Depends(verify_admin)):
    from ..db import get_engine
    from sqlalchemy.orm import Session
    with Session(get_engine()) as s:
        imp = (_load_org_profile_data(s, org_id).get("website_import")) or {}
    out = {"status": imp.get("status") or "none", "url": imp.get("url") or "", "error": imp.get("error") or ""}
    if imp.get("status") == "ready":
        info = imp.get("data") or {}
        out["preview"] = {
            "business_name": info.get("business_name") or "",
            "phone": info.get("phone") or "",
            "hours": info.get("hours") or "",
            "description": (info.get("description") or "")[:200],
            "services": len(info.get("services") or []),
            "knowledge": len(info.get("knowledge") or []),
        }
    return out


@router.post("/api/v1/website-import/apply")
def api_website_import_apply(org_id: str = Depends(verify_admin)):
    from ..website_import import apply_import
    try:
        result = apply_import(org_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, **result}


@router.get("/api/v1/knowledge")
def api_knowledge_list(org_id: str = Depends(verify_admin)):
    from ..db import get_engine
    from sqlalchemy.orm import Session
    with Session(get_engine()) as s:
        data = _load_org_profile_data(s, org_id)
    return {"entries": data.get("knowledge_base") or []}


@router.post("/api/v1/knowledge")
def api_knowledge_add(body: KnowledgeEntryBody, org_id: str = Depends(verify_admin)):
    import secrets
    q = body.q.strip()
    a = body.a.strip()
    if not q or not a:
        raise HTTPException(status_code=400, detail="Enter both a question and an answer")
    from ..db import get_engine
    from sqlalchemy.orm import Session
    entry = {"id": secrets.token_hex(4), "q": q, "a": a}
    with Session(get_engine()) as s:
        data = _load_org_profile_data(s, org_id)
        entries = list(data.get("knowledge_base") or [])
        entries.append(entry)
        data["knowledge_base"] = entries
        _save_org_profile_data(s, org_id, data)
        s.commit()
    return {"ok": True, "entry": entry}


@router.delete("/api/v1/knowledge/{entry_id}")
def api_knowledge_delete(entry_id: str, org_id: str = Depends(verify_admin)):
    from ..db import get_engine
    from sqlalchemy.orm import Session
    with Session(get_engine()) as s:
        data = _load_org_profile_data(s, org_id)
        entries = [e for e in (data.get("knowledge_base") or []) if e.get("id") != entry_id]
        data["knowledge_base"] = entries
        _save_org_profile_data(s, org_id, data)
        s.commit()
    return {"ok": True}


# ---------------------------------------------------------------------------
# Database tab — one screen showing EVERYTHING Adapix knows about this
# business, editable in place. Aggregates the org_profiles JSON blob (wizard
# fields + knowledge base), the org's calling number, and its email
# connection, rather than introducing a new storage model of its own.
# ---------------------------------------------------------------------------
class DatabaseUpdateBody(BaseModel):
    business_name: str | None = None
    owner_name: str | None = None
    phone: str | None = None
    hours: str | None = None
    tone: str | None = None
    description: str | None = None
    website: str | None = None
    address: str | None = None
    hours_weekday: str | None = None
    hours_saturday: str | None = None
    hours_sunday: str | None = None
    business_type_id: str | None = None
    business_type_label: str | None = None
    pronunciation: str | None = None


@router.get("/api/v1/database")
def api_database(org_id: str = Depends(verify_admin)):
    from ..config import Settings
    from ..db import get_engine
    from ..models import Organization
    from ..practice import ESCALATION_LABELS, WORKFLOW_LABELS
    from ..oauth import status as oauth_status
    from sqlalchemy.orm import Session

    with Session(get_engine()) as s:
        data = _load_org_profile_data(s, org_id)
        org = s.get(Organization, org_id)
        practice = data.get("practice") or {}

        workflows = [
            {"key": w, "label": WORKFLOW_LABELS.get(w, w.replace("_", " "))}
            for w in (data.get("workflows") or []) if w != "other"
        ]
        if data.get("workflow_custom"):
            workflows.append({"key": "other", "label": data["workflow_custom"]})

        escalations = [
            {"key": e, "label": (ESCALATION_LABELS.get(e, "") or e.replace("_", " ")).split(".")[0]}
            for e in (data.get("escalations") or []) if e != "other"
        ]
        if data.get("escalation_custom"):
            escalations.append({"key": "other", "label": data["escalation_custom"]})

        email = oauth_status(org_id)
        connected_email_provider = next((p for p in ("google", "microsoft", "smtp") if email.get(p, {}).get("connected")), None)

        return {
            "business_name": practice.get("name") or "",
            "owner_name": practice.get("owner") or practice.get("doctor") or "",
            "phone": practice.get("phone") or "",
            "hours": practice.get("hours") or "",
            "website": practice.get("website") or "",
            "address": practice.get("address") or "",
            "hours_weekday": practice.get("hours_weekday") or "",
            "hours_saturday": practice.get("hours_saturday") or "",
            "hours_sunday": practice.get("hours_sunday") or "",
            "business_type": data.get("practice_type_label") or (data.get("practice_type") or "").replace("_", " "),
            "business_type_id": data.get("practice_type") or "",
            "tone": data.get("tone") or "warm_professional",
            "description": data.get("description") or "",
            "pronunciation": data.get("pronunciation") or "",
            "services": data.get("services") or [],
            "workflows": workflows,
            "escalations": escalations,
            "knowledge_base": data.get("knowledge_base") or [],
            "configured_at": data.get("configured_at") or "",
            "calling_number": org.phone_number if org else None,
            "calling_status": org.phone_status if org else "none",
            "imessage_number": org.imessage_number if org else None,
            # Blue texts are on when either the platform Claw line (shared
            # across every org — one Claw account = one number today) or a
            # per-org Blooio line (this org's own dedicated number) is set.
            "imessage_connected": bool(
                Settings().claw_api_key or (org and org.blooio_channel_id)),
            "imessage_dedicated": bool(org and org.blooio_channel_id),
            "paused": bool(data.get("paused")),
            "email_connected": connected_email_provider is not None,
            "email_provider": connected_email_provider,
            "email_address": email.get(connected_email_provider, {}).get("email") if connected_email_provider else None,
        }


@router.post("/api/v1/database")
def api_database_update(body: DatabaseUpdateBody, org_id: str = Depends(verify_admin)):
    from ..db import get_engine
    from sqlalchemy.orm import Session
    with Session(get_engine()) as s:
        data = _load_org_profile_data(s, org_id)
        practice = dict(data.get("practice") or {})
        if body.business_name is not None:
            practice["name"] = body.business_name.strip()
        if body.owner_name is not None:
            practice["owner"] = body.owner_name.strip()
        if body.phone is not None:
            practice["phone"] = body.phone.strip()
        if body.hours is not None:
            practice["hours"] = body.hours.strip()
        if body.website is not None:
            practice["website"] = body.website.strip()
        if body.address is not None:
            practice["address"] = body.address.strip()
        if body.hours_weekday is not None:
            practice["hours_weekday"] = body.hours_weekday.strip()
        if body.hours_saturday is not None:
            practice["hours_saturday"] = body.hours_saturday.strip()
        if body.hours_sunday is not None:
            practice["hours_sunday"] = body.hours_sunday.strip()
        data["practice"] = practice
        if body.tone is not None and body.tone in ("warm_professional", "casual_friendly", "clinical_formal"):
            data["tone"] = body.tone
        if body.description is not None:
            data["description"] = body.description.strip()
        if body.pronunciation is not None:
            data["pronunciation"] = body.pronunciation.strip()
        if body.business_type_id is not None:
            data["practice_type"] = body.business_type_id.strip()
            data["practice_type_label"] = (body.business_type_label or "").strip()
        _save_org_profile_data(s, org_id, data)
        s.commit()
    return {"ok": True}


# ---------------------------------------------------------------------------
# Services & pricing catalog — same storage pattern as knowledge_base (a
# list living under org_profiles.data). This is what lets Adapix quote a
# real price instead of deflecting every cost question to a human.
# ---------------------------------------------------------------------------
class ServiceEntryBody(BaseModel):
    name: str
    price: str = ""
    details: str = ""
    # "one_time" (a single charge) or "subscription" (recurring). billing_period
    # is only meaningful for subscriptions ("month" or "year"). term_length is
    # how many of those periods the customer commits to when they buy (e.g.
    # billing_period="month" + term_length="12" = a 12-month plan) — blank
    # means no minimum commitment, just billed on that cadence indefinitely.
    pricing_type: str = "one_time"
    billing_period: str = "month"
    term_length: str = ""
    # Subscriptions only: length of a free trial before the first charge, in
    # days. Blank means no trial — never assume one.
    trial_days: str = ""


@router.post("/api/v1/services")
def api_services_add(body: ServiceEntryBody, org_id: str = Depends(verify_admin)):
    import secrets
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Enter a service name")
    pricing_type = body.pricing_type if body.pricing_type in ("one_time", "subscription") else "one_time"
    billing_period = body.billing_period if body.billing_period in ("month", "year") else "month"
    from ..db import get_engine
    from sqlalchemy.orm import Session
    entry = {
        "id": secrets.token_hex(4),
        "name": name,
        "price": body.price.strip(),
        "details": body.details.strip(),
        "pricing_type": pricing_type,
        "billing_period": billing_period if pricing_type == "subscription" else "",
        "term_length": body.term_length.strip() if pricing_type == "subscription" else "",
        "trial_days": body.trial_days.strip() if pricing_type == "subscription" else "",
    }
    with Session(get_engine()) as s:
        data = _load_org_profile_data(s, org_id)
        entries = list(data.get("services") or [])
        entries.append(entry)
        data["services"] = entries
        _save_org_profile_data(s, org_id, data)
        s.commit()
    return {"ok": True, "entry": entry}


@router.delete("/api/v1/services/{entry_id}")
def api_services_delete(entry_id: str, org_id: str = Depends(verify_admin)):
    from ..db import get_engine
    from sqlalchemy.orm import Session
    with Session(get_engine()) as s:
        data = _load_org_profile_data(s, org_id)
        entries = [e for e in (data.get("services") or []) if e.get("id") != entry_id]
        data["services"] = entries
        _save_org_profile_data(s, org_id, data)
        s.commit()
    return {"ok": True}


# ---------------------------------------------------------------------------
# SMS & Email tab — one place to see every SMS/email (sent or received) and
# to compose a new one, either sending immediately or queuing it into the
# SAME pending-approval flow the Inbox already uses. Deliberately does not
# reinvent sending: it reuses ApprovalManager.approve_and_send, so a compose
# here goes through the exact tested path (org's connected Gmail/Outlook/SMTP
# preferred over the shared Resend sender, real Twilio SMS).
# ---------------------------------------------------------------------------
@router.get("/api/v1/messages")
def api_messages_list(
    channel: str = "sms,email",
    limit: int = 50,
    patient_id: int | None = None,
    org_id: str = Depends(verify_admin),
):
    """Flat message feed, newest first — or, with patient_id, the FULL
    conversation with one contact across every channel and every campaign
    they've ever had, oldest first (a readable thread)."""
    from sqlalchemy import asc, desc
    from sqlalchemy.orm import Session
    from ..db import get_engine

    wanted = {c.strip() for c in channel.split(",") if c.strip()}
    with Session(get_engine()) as s:
        if patient_id is not None:
            patient = s.query(Patient).filter(
                Patient.id == patient_id, Patient.practice_id == org_id
            ).first()
            if patient is None:
                raise HTTPException(status_code=404, detail="Contact not found")
            campaign_ids = [c.id for c in s.query(Campaign.id).filter(
                Campaign.patient_id == patient_id, Campaign.practice_id == org_id
            ).all()]
            if not campaign_ids:
                return {"messages": []}
            q = (
                s.query(Message)
                .filter(Message.campaign_id.in_(campaign_ids))
                .filter(Message.channel.in_({"sms", "email", "call"}))
                .order_by(asc(Message.created_at))
            )
            out = []
            for m in q.all():
                out.append({
                    "id": m.id,
                    "channel": m.channel,
                    "direction": m.direction,
                    "status": m.status,
                    "subject": m.subject,
                    "body": m.body,
                    "recording_url": (m.metadata_json or {}).get("recording_url"),
                    "send_error": (m.metadata_json or {}).get("send_error"),
                    "contact_name": f"{patient.first_name} {patient.last_name}".strip(),
                    "contact_phone": patient.phone,
                    "contact_email": patient.email,
                    "created_at": m.created_at.isoformat() if m.created_at else None,
                })
            return {"messages": out}

        campaign_ids = [c.id for c in s.query(Campaign.id).filter(Campaign.practice_id == org_id).all()]
        if not campaign_ids:
            return {"messages": []}
        q = (
            s.query(Message)
            .filter(Message.campaign_id.in_(campaign_ids))
            .filter(Message.channel.in_(wanted))
            .order_by(desc(Message.created_at))
            .limit(max(1, min(limit, 200)))
        )
        out = []
        for m in q.all():
            campaign = s.get(Campaign, m.campaign_id)
            patient = s.get(Patient, campaign.patient_id) if campaign else None
            out.append({
                "id": m.id,
                "channel": m.channel,
                "direction": m.direction,
                "status": m.status,
                "subject": m.subject,
                "body": m.body,
                "contact_name": f"{patient.first_name} {patient.last_name}".strip() if patient else "Unknown",
                "contact_phone": patient.phone if patient else None,
                "contact_email": patient.email if patient else None,
                "created_at": m.created_at.isoformat() if m.created_at else None,
            })
        return {"messages": out}


class ComposeMessageBody(BaseModel):
    channel: str  # "sms" | "email"
    to: str        # phone number (sms) or email address (email)
    contact_name: str = ""
    subject: str = ""
    body: str
    queue: bool = False  # True = drop into the pending-approval queue; False = send now
    # ISO datetime (no offset — interpreted as America/New_York, same as the
    # quiet-hours window everywhere else). If set, overrides `queue`: the
    # message is pre-approved (the owner wrote and scheduled it themselves)
    # and waits for send_approved()'s background sweep to fire at that time.
    scheduled_at: str | None = None


@router.post("/api/v1/messages/compose")
def api_messages_compose(body: ComposeMessageBody, org_id: str = Depends(verify_admin)):
    channel = body.channel.strip().lower()
    if channel not in ("sms", "email"):
        raise HTTPException(status_code=400, detail="channel must be 'sms' or 'email'")
    to = body.to.strip()
    if not to:
        raise HTTPException(status_code=400, detail="Enter a recipient")
    if not body.body.strip():
        raise HTTPException(status_code=400, detail="Enter a message")

    scheduled_dt = None
    if body.scheduled_at:
        from datetime import datetime as _dt
        from zoneinfo import ZoneInfo as _ZoneInfo
        try:
            naive = _dt.fromisoformat(body.scheduled_at)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid scheduled time")
        local = naive.replace(tzinfo=_ZoneInfo("America/New_York"))
        scheduled_dt = local.astimezone(_ZoneInfo("UTC")).replace(tzinfo=None)
        if scheduled_dt <= _dt.utcnow():
            raise HTTPException(status_code=400, detail="Pick a time in the future")

    from sqlalchemy.orm import Session
    from ..db import get_engine

    with Session(get_engine()) as s:
        patient = None
        if channel == "sms":
            patient = (
                s.query(Patient)
                .filter(Patient.practice_id == org_id, Patient.phone == to)
                .first()
            )
        else:
            patient = (
                s.query(Patient)
                .filter(Patient.practice_id == org_id, Patient.email == to)
                .first()
            )
        if patient is None:
            name = body.contact_name.strip() or to
            first, _, last = name.partition(" ")
            patient = Patient(
                practice_id=org_id,
                first_name=first or name,
                last_name=last,
                phone=to if channel == "sms" else None,
                email=to if channel == "email" else None,
                preferred_channel=channel,
            )
            s.add(patient)
            s.flush()

        campaign = Campaign(practice_id=org_id, workflow_id="manual", patient_id=patient.id)
        s.add(campaign)
        s.flush()

        message = Message(
            campaign_id=campaign.id,
            direction="outbound",
            channel=channel,
            subject=(body.subject.strip() or None) if channel == "email" else None,
            body=body.body.strip(),
            status="pending_approval",
        )
        s.add(message)
        s.flush()
        message_id = message.id
        s.commit()

    if scheduled_dt is not None:
        mgr = ApprovalManager()
        mgr.approve(message_id)
        with Session(get_engine()) as s2:
            m = s2.get(Message, message_id)
            m.scheduled_at = scheduled_dt
            s2.commit()
        return {"ok": True, "status": "scheduled", "scheduled_at": scheduled_dt.isoformat(), "message_id": message_id}

    if body.queue:
        return {"ok": True, "status": "pending_approval", "message_id": message_id}

    status = ApprovalManager().approve_and_send(message_id)
    return {"ok": status in ("sent",), "status": status, "message_id": message_id}


# ---------------------------------------------------------------------------
# Follow-up rules — the Settings page that actually governs the engine.
# first_followup_days gates when composing starts, max_touches caps outbound
# messages without a reply (both enforced in CampaignRunner.run_due_messages),
# auto_approve flips the org's approval_mode (via load_practice's DB-org
# fallback), and tone feeds the composer's prompt.
# ---------------------------------------------------------------------------
class RulesBody(BaseModel):
    first_followup_days: int = 0
    max_touches: int = 0
    auto_approve: bool = False
    tone: str = ""


@router.get("/api/v1/rules")
def api_rules_get(org_id: str = Depends(verify_admin)):
    from ..db import get_engine
    from sqlalchemy.orm import Session
    with Session(get_engine()) as s:
        data = _load_org_profile_data(s, org_id)
    rules = data.get("rules") or {}
    return {
        "first_followup_days": int(rules.get("first_followup_days") or 0),
        "max_touches": int(rules.get("max_touches") or 0),
        "auto_approve": bool(rules.get("auto_approve")),
        "tone": data.get("tone") or "warm_professional",
    }


@router.post("/api/v1/rules")
def api_rules_save(body: RulesBody, org_id: str = Depends(verify_admin)):
    from ..db import get_engine
    from sqlalchemy.orm import Session
    if body.first_followup_days not in (0, 1, 2, 3, 7):
        raise HTTPException(status_code=400, detail="invalid first_followup_days")
    if body.max_touches not in (0, 1, 2, 3):
        raise HTTPException(status_code=400, detail="invalid max_touches")
    with Session(get_engine()) as s:
        data = _load_org_profile_data(s, org_id)
        data["rules"] = {
            "first_followup_days": body.first_followup_days,
            "max_touches": body.max_touches,
            "auto_approve": bool(body.auto_approve),
        }
        if body.tone in ("warm_professional", "casual_friendly", "clinical_formal"):
            data["tone"] = body.tone
        _save_org_profile_data(s, org_id, data)
        s.commit()
    return {"ok": True}


class PauseBody(BaseModel):
    paused: bool


@router.post("/api/v1/org/pause")
def api_org_pause(body: PauseBody, org_id: str = Depends(verify_admin)):
    """Master switch: pause/resume ALL automated follow-up composition for
    this org. Paused orgs are skipped by the campaign scheduler entirely —
    nothing new gets drafted until resumed. Already-pending drafts stay in
    the Inbox (you can still send or reject them by hand)."""
    from ..db import get_engine
    from sqlalchemy.orm import Session
    with Session(get_engine()) as s:
        data = _load_org_profile_data(s, org_id)
        data["paused"] = bool(body.paused)
        _save_org_profile_data(s, org_id, data)
        s.commit()
    return {"ok": True, "paused": bool(body.paused)}


@router.post("/api/v1/campaigns/{campaign_id}/stop")
def api_campaign_stop(campaign_id: int, org_id: str = Depends(verify_admin)):
    """Stop one contact's follow-up campaign — no further steps will be
    composed for them. Org-scoped: you can only stop your own campaigns."""
    with get_session() as s:
        c = s.get(Campaign, campaign_id)
        if c is None or c.practice_id != org_id:
            raise HTTPException(status_code=404, detail="campaign not found")
        c.status = "stopped"
    return {"ok": True, "id": campaign_id}


@router.get("/api/v1/station/queue")
def api_station_queue(org_id: str = Depends(verify_admin)):
    """What the campaign engine is ABOUT to do — the Station's 'in queue'
    board. For every active campaign, the next cadence step that hasn't been
    composed yet: who it's for, which channel, and when it comes due. Steps
    already due compose on the scheduler's next pass (runs every 5 minutes)."""
    from ..config import load_workflow

    out = []
    workflows: dict[str, Any] = {}
    with get_session() as s:
        campaigns = (
            s.query(Campaign)
            .filter(Campaign.practice_id == org_id, Campaign.status == "active")
            .all()
        )
        now = datetime.utcnow()
        for c in campaigns:
            wf = workflows.get(c.workflow_id)
            if wf is None:
                try:
                    wf = load_workflow(c.workflow_id)
                except Exception:
                    wf = False  # manual / unknown workflow — nothing scheduled
                workflows[c.workflow_id] = wf
            if not wf:
                continue
            next_step = next(
                (st for st in sorted(wf.cadence, key=lambda x: x.day) if st.day > c.last_step_completed),
                None,
            )
            if next_step is None:
                continue
            patient = s.get(Patient, c.patient_id)
            if patient is None:
                continue
            due_at = c.started_at + timedelta(days=next_step.day)
            out.append({
                "campaign_id": c.id,
                "contact_name": f"{patient.first_name} {patient.last_name}".strip(),
                "contact_phone": patient.phone,
                "contact_email": patient.email,
                "workflow": c.workflow_id,
                "channel": next_step.channel,
                "intent": getattr(next_step, "intent", "") or "",
                "day": next_step.day,
                "due_at": due_at.isoformat(),
                "due_now": now >= due_at,
            })
    out.sort(key=lambda e: e["due_at"])
    return {"queue": out}


@router.get("/app/manifest.json")
def manifest():
    return JSONResponse(
        {
            "name": "Adapix",
            "short_name": "Adapix",
            "description": "See what needs you and approve messages in one tap.",
            "start_url": "/app",
            "scope": "/app",
            "display": "standalone",
            "orientation": "portrait",
            "background_color": "#0d2c5d",
            "theme_color": "#0d2c5d",
            "icons": [
                {"src": "/app/icon-192.png", "sizes": "192x192", "type": "image/png"},
                {"src": "/app/icon-512.png", "sizes": "512x512", "type": "image/png"},
            ],
        }
    )


@router.get("/app/sw.js")
def service_worker():
    """Minimal service worker — caches the shell so 'Add to Home Screen' works
    and the app opens fullscreen even on flaky office Wi-Fi.

    We do NOT cache the JSON API — escalations must always be fresh.
    """
    sw = """
const CACHE = 'adapix-shell-v4';
const SHELL = ['/app', '/app/manifest.json', '/app/icon-192.png', '/app/icon-512.png'];

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)));
  self.skipWaiting();
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
    )
  );
  self.clients.claim();
});

self.addEventListener('fetch', (e) => {
  const url = new URL(e.request.url);
  if (url.pathname.startsWith('/api/')) return; // never cache API
  if (e.request.method !== 'GET') return;
  // Network-first for the HTML shell so UI updates show immediately when
  // online; fall back to the cached shell only when offline (keeps PWA).
  if (e.request.mode === 'navigate' || url.pathname === '/app') {
    e.respondWith(
      fetch(e.request)
        .then((res) => {
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put('/app', copy));
          return res;
        })
        .catch(() => caches.match('/app'))
    );
    return;
  }
  // Cache-first for static assets (icons, manifest)
  e.respondWith(
    caches.match(e.request).then((cached) => cached || fetch(e.request))
  );
});

self.addEventListener('push', (e) => {
  const data = (() => { try { return e.data.json(); } catch (_) { return {}; } })();
  const title = data.title || 'Adapix';
  const body  = data.body  || 'New activity in your queue.';
  const url   = data.url   || '/app';
  const tag   = data.tag   || 'adapix';
  e.waitUntil(self.registration.showNotification(title, {
    body,
    icon:  '/app/icon-192.png',
    badge: '/app/icon-192.png',
    tag,
    data: { url },
    vibrate: [50, 30, 50],
  }));
});

// Tapping a notification: focus an existing app tab if one is open,
// otherwise open a new one at the deep-link target stored on the push.
self.addEventListener('notificationclick', (e) => {
  e.notification.close();
  const url = (e.notification.data && e.notification.data.url) || '/app';
  e.waitUntil((async () => {
    const all = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
    for (const c of all) {
      if (c.url.includes('/app')) { await c.focus(); return; }
    }
    await self.clients.openWindow(url);
  })());
});
""".strip()
    return PlainTextResponse(content=sw, media_type="application/javascript")


def _icon_response(filename: str):
    p = BRANDING_DIR / filename
    if not p.exists():
        # Fall back to the mark-only PNG if a sized icon hasn't been generated yet
        fallback = BRANDING_DIR / "adapix_mark_only.png"
        if fallback.exists():
            return FileResponse(fallback, media_type="image/png")
        raise HTTPException(status_code=404, detail="icon not found")
    return FileResponse(p, media_type="image/png")


@router.get("/app/icon-192.png")
def icon_192():
    return _icon_response("adapix_icon_192.png")


@router.get("/app/icon-512.png")
def icon_512():
    return _icon_response("adapix_icon_512.png")


# ---------------------------------------------------------------------------
# JSON API for the PWA
# ---------------------------------------------------------------------------
def _patient_label(patient: Patient | None) -> str:
    if not patient:
        return "(unknown)"
    name = f"{patient.first_name} {patient.last_name[:1]}." if patient.last_name else patient.first_name
    treatment = f" — {patient.treatment_type}" if patient.treatment_type else ""
    return f"{name}{treatment}"


def _ago(dt: datetime | None) -> str:
    if not dt:
        return ""
    delta = datetime.utcnow() - dt
    s = int(delta.total_seconds())
    if s < 60:
        return "just now"
    if s < 3600:
        return f"{s // 60}m"
    if s < 86400:
        return f"{s // 3600}h"
    return f"{s // 86400}d"


@router.get("/api/v1/activity")
def activity(limit: int = 50, _user: str = Depends(verify_admin)) -> dict[str, Any]:
    """Chronological activity feed for the dashboard — everything Adapix has
    been doing across messages, drafts, replies, and escalations."""
    limit = max(1, min(limit, 200))
    events: list[dict[str, Any]] = []

    with get_session() as s:
        # ---- Messages: outbound sent, drafts, inbound replies ---------------
        msg_rows = (
            s.query(Message, Campaign, Patient)
            .join(Campaign, Message.campaign_id == Campaign.id)
            .join(Patient, Campaign.patient_id == Patient.id)
            .filter(Campaign.practice_id == _user)
            .order_by(Message.created_at.desc())
            .limit(limit)
            .all()
        )
        for m, c, p in msg_rows:
            patient_name = f"{p.first_name} {p.last_name}".strip()
            body_preview = (m.body or "").strip().replace("\n", " ")
            if len(body_preview) > 140:
                body_preview = body_preview[:140] + "…"

            if m.direction == "outbound":
                if m.channel == "call" and m.status == "sent":
                    kind, label = "call_placed", f"Called {patient_name}"
                elif m.status == "sent":
                    kind, label = "sent", f"Sent {m.channel} to {patient_name}"
                elif m.channel == "call" and m.status == "pending_approval":
                    kind, label = "call_plan", f"Call plan ready for {patient_name}"
                elif m.status in ("pending_approval", "composed"):
                    kind, label = "draft", f"Drafted {m.channel} for {patient_name}"
                elif m.channel == "call" and m.status == "failed":
                    kind, label = "call_failed", f"Couldn't reach {patient_name} by phone"
                elif m.status == "rejected":
                    kind, label = "rejected", f"Rejected draft for {patient_name}"
                else:
                    kind, label = "outbound", f"{m.status} {m.channel} → {patient_name}"
            elif m.channel == "call":
                kind = "call_outcome"
                label = (m.subject or f"Call with {patient_name} ended").strip()
                body_preview = ""  # subject already carries the summary; skip raw transcript
            else:
                kind, label = "reply", f"{patient_name} replied"

            events.append({
                "kind": kind,
                "label": label,
                "preview": body_preview,
                "patient": patient_name,
                "channel": m.channel,
                "ts": m.created_at.isoformat() if m.created_at else None,
                "message_id": m.id,
                "campaign_id": c.id,
                "workflow": c.workflow_id,
            })

        # ---- Escalations: anything Adapix flagged ----------------------------
        esc_rows = (
            s.query(EscalationEvent, Campaign, Patient)
            .join(Campaign, EscalationEvent.campaign_id == Campaign.id)
            .join(Patient, Campaign.patient_id == Patient.id)
            .filter(Campaign.practice_id == _user)
            .order_by(EscalationEvent.created_at.desc())
            .limit(limit)
            .all()
        )
        for e, c, p in esc_rows:
            patient_name = f"{p.first_name} {p.last_name}".strip()
            cat = (e.category or "").replace("_", " ")
            events.append({
                "kind": "emergency" if e.category == "emergency" else "escalation",
                "label": f"Flagged {patient_name} — {cat}",
                "preview": e.reasoning or e.suggested_action or "",
                "patient": patient_name,
                "channel": None,
                "ts": e.created_at.isoformat() if e.created_at else None,
                "message_id": e.triggered_by_message_id,
                "campaign_id": c.id,
                "workflow": c.workflow_id,
                "resolved": bool(e.resolved),
            })

    # Sort all events newest-first across both sources, then trim
    events.sort(key=lambda x: x["ts"] or "", reverse=True)
    events = events[:limit]

    # Today's counts for the header strip
    today = datetime.utcnow().date().isoformat()
    today_count = sum(1 for e in events if (e.get("ts") or "")[:10] == today)
    last_ts = events[0]["ts"] if events else None

    return {
        "events": events,
        "today_count": today_count,
        "last_action_ts": last_ts,
        "now": datetime.utcnow().isoformat(),
    }


class EscalationReplyBody(BaseModel):
    body: str = ""


@router.post("/api/v1/escalations/{escalation_id}/resolve")
def api_escalation_resolve(escalation_id: int, org_id: str = Depends(verify_admin)):
    """Mark an escalation handled — with no reply (owner called them, texted
    from their own phone, or it just needed acknowledging)."""
    from ..db import get_engine
    from sqlalchemy.orm import Session
    with Session(get_engine()) as s:
        e = s.get(EscalationEvent, escalation_id)
        if not e:
            raise HTTPException(status_code=404, detail="Escalation not found")
        campaign = s.get(Campaign, e.campaign_id)
        if not campaign or campaign.practice_id != org_id:
            raise HTTPException(status_code=404, detail="Escalation not found")
        e.resolved = True
        s.commit()
    return {"ok": True}


@router.post("/api/v1/escalations/{escalation_id}/reply")
def api_escalation_reply(escalation_id: int, body: EscalationReplyBody, org_id: str = Depends(verify_admin)):
    """Reply directly from the escalation card — sends the owner's own words
    on the same channel it came in on, logs it, and resolves in one action."""
    text = body.body.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Write a reply first")
    from ..db import get_engine
    from ..channels import SmsChannel
    from ..config import Settings
    from ..models import Organization
    from ..oauth import send_email_for_org
    from sqlalchemy.orm import Session
    with Session(get_engine()) as s:
        e = s.get(EscalationEvent, escalation_id)
        if not e:
            raise HTTPException(status_code=404, detail="Escalation not found")
        campaign = s.get(Campaign, e.campaign_id)
        if not campaign or campaign.practice_id != org_id:
            raise HTTPException(status_code=404, detail="Escalation not found")
        patient = s.get(Patient, campaign.patient_id)
        if not patient:
            raise HTTPException(status_code=404, detail="Contact not found")
        if patient.opted_out:
            raise HTTPException(status_code=409, detail="This contact opted out — nothing can be sent to them.")

        triggering_channel = None
        if e.triggered_by_message_id:
            m = s.get(Message, e.triggered_by_message_id)
            triggering_channel = m.channel if m else None
        channel = triggering_channel if triggering_channel in ("sms", "email") else (patient.preferred_channel or "sms")

        settings = Settings()
        org = s.get(Organization, org_id)
        if channel == "email":
            if not patient.email:
                raise HTTPException(status_code=400, detail="This contact has no email on file")
            r = send_email_for_org(org_id, patient.email, f"Re: your message to {org.name if org else 'us'}",
                                   text, org.name if org else None, settings)
            status = "sent" if r.get("ok") else "failed"
            provider_id = r.get("provider_id")
            error = r.get("error")
        else:
            if not patient.phone:
                raise HTTPException(status_code=400, detail="This contact has no phone on file")
            r = SmsChannel(settings).send(patient.phone, text)
            status, provider_id, error = r.status, r.provider_id, r.error

        s.add(Message(
            campaign_id=campaign.id,
            direction="outbound",
            channel=channel,
            body=text,
            status=status,
            provider_id=provider_id,
            metadata_json={"kind": "owner_reply", "error": error} if error else {"kind": "owner_reply"},
        ))
        e.resolved = True
        s.commit()
    if status == "failed":
        raise HTTPException(status_code=502, detail=f"Send failed: {error}")
    return {"ok": True, "status": status}


@router.get("/api/v1/feed")
def feed(_user: str = Depends(verify_admin)) -> dict[str, Any]:
    """Everything the home screen needs: escalations, pending approvals, today's digest.

    Every query here joins through Campaign.practice_id — this endpoint serves
    multiple tenants and must never show one org another org's traffic."""
    with get_session() as s:
        # Open escalations (newest first)
        escalations = (
            s.query(EscalationEvent)
            .join(Campaign, EscalationEvent.campaign_id == Campaign.id)
            .filter(Campaign.practice_id == _user)
            .filter(EscalationEvent.resolved == False)  # noqa: E712
            .order_by(EscalationEvent.created_at.desc())
            .limit(25)
            .all()
        )

        # Severity ranking so a burst pipe sorts above a pricing question,
        # not just whichever came in most recently.
        SEVERITY_RANK = {"emergency": 0, "stop": 1, "callback_request": 2,
                         "clinical_question": 2, "decline": 3, "other": 4}
        esc_payload = []
        for e in escalations:
            campaign = s.get(Campaign, e.campaign_id)
            patient = s.get(Patient, campaign.patient_id) if campaign else None
            triggering_body = ""
            channel = None
            call_summary = None
            if e.triggered_by_message_id:
                m = s.get(Message, e.triggered_by_message_id)
                if m:
                    channel = m.channel
                    triggering_body = m.body[:280]
                    # For calls, lead with the AI's summary (stored as subject)
                    # rather than the raw transcript — much more scannable.
                    if channel == "call" and m.subject:
                        call_summary = m.subject
            esc_payload.append(
                {
                    "id": e.id,
                    "patient_id": patient.id if patient else None,
                    "patient": _patient_label(patient),
                    "phone_last4": (patient.phone or "")[-4:] if patient else "",
                    "has_phone": bool(patient and patient.phone),
                    "has_email": bool(patient and patient.email),
                    "category": e.category,
                    "severity_rank": SEVERITY_RANK.get(e.category, 4),
                    "confidence": e.confidence,
                    "reasoning": e.reasoning,
                    "suggested_action": e.suggested_action,
                    "triggering_body": triggering_body,
                    "channel": channel,
                    "call_summary": call_summary,
                    "ago": _ago(e.created_at),
                    "created_at": e.created_at.isoformat() if e.created_at else None,
                }
            )
        esc_payload.sort(key=lambda x: (x["severity_rank"], x["created_at"] or ""), reverse=False)

        # Pending-approval messages
        pending = (
            s.query(Message)
            .join(Campaign, Message.campaign_id == Campaign.id)
            .filter(Campaign.practice_id == _user)
            .filter(Message.status == "pending_approval")
            .order_by(Message.created_at.desc())
            .limit(25)
            .all()
        )
        appr_payload = []
        for m in pending:
            campaign = s.get(Campaign, m.campaign_id)
            patient = s.get(Patient, campaign.patient_id) if campaign else None
            # Touch context: which follow-up this is and whether they've replied,
            # so a legitimate 2nd/3rd touch doesn't read as a duplicate.
            prior = (
                s.query(Message)
                .filter(Message.campaign_id == m.campaign_id, Message.id != m.id)
                .order_by(Message.created_at.asc())
                .all()
            )
            sent_before = [x for x in prior if x.direction == "outbound" and x.status in ("sent", "delivered", "replied")]
            last_reply = next((x for x in reversed(prior) if x.direction == "inbound"), None)
            touch_n = len(sent_before) + 1
            if last_reply is not None:
                reply_note = f"they replied {_ago(last_reply.created_at)}"
            elif sent_before:
                reply_note = f"no reply since the last one {_ago(sent_before[-1].created_at)}"
            else:
                reply_note = "first message to them"
            ordinal = {1: "1st", 2: "2nd", 3: "3rd"}.get(touch_n, f"{touch_n}th")
            appr_payload.append(
                {
                    "id": m.id,
                    "patient": _patient_label(patient),
                    "channel": m.channel,
                    "day": m.day_in_campaign,
                    "body": m.body,
                    "subject": m.subject,
                    "ago": _ago(m.created_at),
                    "created_at": m.created_at.isoformat() if m.created_at else None,
                    "touch": touch_n,
                    "touch_label": f"{ordinal} follow-up" + (f" · {reply_note}" if touch_n > 1 or last_reply else ""),
                }
            )

        # Today's digest
        today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        sent_today = (
            s.query(Message)
            .join(Campaign, Message.campaign_id == Campaign.id)
            .filter(Campaign.practice_id == _user)
            .filter(Message.direction == "outbound", Message.status.in_(["sent", "delivered"]))
            .filter(Message.created_at >= today_start)
            .count()
        )
        booked_today = (
            s.query(Patient)
            .filter(Patient.practice_id == _user)
            .filter(Patient.status == "treatment_started")
            .filter(Patient.created_at >= today_start - timedelta(days=14))
            .count()
        )
        open_escalations = (
            s.query(EscalationEvent)
            .join(Campaign, EscalationEvent.campaign_id == Campaign.id)
            .filter(Campaign.practice_id == _user)
            .filter(EscalationEvent.resolved == False)  # noqa: E712
            .count()
        )

        return {
            "as_of": datetime.utcnow().isoformat() + "Z",
            "escalations": esc_payload,
            "approvals": appr_payload,
            "digest": {
                "sent_today": sent_today,
                "booked_recent": booked_today,
                "open_escalations": open_escalations,
                "pending_approvals": len(appr_payload),
            },
        }


class ApproveBody(BaseModel):
    edited_body: str | None = None


class RejectBody(BaseModel):
    reason: str | None = None



def _require_message_in_org(message_id: int, org_id: str) -> None:
    """404 unless the message belongs to a campaign owned by the caller's org.

    Approve/reject act on raw message ids — without this check any logged-in
    org could approve-and-send (or kill) another org's queued messages."""
    with get_session() as s:
        m = s.get(Message, message_id)
        c = s.get(Campaign, m.campaign_id) if m else None
        if not c or c.practice_id != org_id:
            raise HTTPException(status_code=404, detail="Message not found")


def _send_failure_detail(message_id: int, status: str) -> str:
    """'Send failed: failed' told the owner nothing — _send_one() always
    writes the REAL reason (a Vapi HTTP error, 'no calling number
    provisioned', etc.) to metadata_json.send_error, but the approve/
    send-now endpoints only ever echoed back the terminal status string,
    which for a failure is always just the literal word 'failed'. Pull the
    real reason back out for the error response."""
    with get_session() as s:
        m = s.get(Message, message_id)
        err = (m.metadata_json or {}).get("send_error") if m else None
    return f"Send failed: {err}" if err else f"Send failed: {status}"


@router.post("/api/v1/approvals/{message_id}/approve")
def api_approve(message_id: int, body: ApproveBody, _user: str = Depends(verify_admin)):
    _require_message_in_org(message_id, _user)
    mgr = ApprovalManager()

    # Scheduled for later (set at queue time — e.g. Calls tab "Call at"):
    # approve the PLAN now, but don't place the call / send the message
    # until send_approved()'s background sweep says it's due.
    from datetime import datetime as _dt
    with get_session() as s:
        m = s.get(Message, message_id)
        is_future_scheduled = bool(m and m.scheduled_at and m.scheduled_at > _dt.utcnow())
    if is_future_scheduled:
        if not mgr.approve(message_id, edited_body=body.edited_body or None):
            raise HTTPException(status_code=400, detail="Could not approve — already handled")
        return {"ok": True, "id": message_id, "status": "scheduled"}

    try:
        status = mgr.approve_and_send(message_id, edited_body=body.edited_body or None)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    if status == "opted_out":
        raise HTTPException(status_code=409, detail="This contact opted out — nothing can be sent to them.")
    if status not in ("sent", "queued"):
        raise HTTPException(status_code=502, detail=_send_failure_detail(message_id, status))
    return {"ok": True, "id": message_id, "status": status}


@router.post("/api/v1/approvals/{message_id}/send-now")
def api_send_now(message_id: int, _user: str = Depends(verify_admin)):
    """Place a scheduled call (or send a scheduled message) right now instead
    of waiting for its scheduled_at — the Calls tab's 'Call now' override on
    a Scheduled entry. Requires the message to already be approved (i.e.
    already past the plan-approval step, whether scheduled or not)."""
    _require_message_in_org(message_id, _user)
    mgr = ApprovalManager()
    try:
        status = mgr.send_now(message_id)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    if status == "opted_out":
        raise HTTPException(status_code=409, detail="This contact opted out — nothing can be sent to them.")
    if status == "not_found_or_not_approved":
        raise HTTPException(status_code=400, detail="Nothing to send — it may have already gone out or been cancelled.")
    if status not in ("sent", "queued"):
        raise HTTPException(status_code=502, detail=_send_failure_detail(message_id, status))
    return {"ok": True, "id": message_id, "status": status}


@router.post("/api/v1/approvals/{message_id}/reject")
def api_reject(message_id: int, body: RejectBody, _user: str = Depends(verify_admin)):
    _require_message_in_org(message_id, _user)
    mgr = ApprovalManager()
    try:
        mgr.reject(message_id, reason=body.reason or None)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "id": message_id}


# ---------------------------------------------------------------------------
# Calls — the dashboard's home for queuing + reviewing AI phone calls.
# Web equivalent of the `queue-call` / `approve` / `pending-approvals` CLI.
# ---------------------------------------------------------------------------

@router.get("/api/v1/calls")
def api_calls_list(org_id: str = Depends(verify_admin)):
    """Everything the Calls tab needs: pending call plans + recent call history."""
    with get_session() as s:
        pending_rows = (
            s.query(Message, Campaign, Patient)
            .join(Campaign, Message.campaign_id == Campaign.id)
            .join(Patient, Campaign.patient_id == Patient.id)
            .filter(
                Campaign.practice_id == org_id,
                Message.channel == "call",
                Message.status == "pending_approval",
            )
            .order_by(Message.created_at.asc())
            .all()
        )
        pending = [
            {
                "id": m.id,
                "patient": _patient_label(p),
                "patient_id": p.id,
                "phone": p.phone,
                "goal": m.body,
                "scheduled_at": m.scheduled_at.isoformat() if m.scheduled_at else None,
                "created_at": m.created_at.isoformat() if m.created_at else None,
            }
            for m, c, p in pending_rows
        ]

        # Approved-but-not-yet-placed scheduled calls: real, but not "placed"
        # or "waiting on you" — a distinct third bucket so neither pending
        # nor history mislabels them.
        from datetime import datetime as _dt
        now = _dt.utcnow()
        scheduled_rows = (
            s.query(Message, Campaign, Patient)
            .join(Campaign, Message.campaign_id == Campaign.id)
            .join(Patient, Campaign.patient_id == Patient.id)
            .filter(
                Campaign.practice_id == org_id,
                Message.channel == "call",
                Message.status == "approved",
                Message.scheduled_at.isnot(None),
                Message.scheduled_at > now,
            )
            .order_by(Message.scheduled_at.asc())
            .all()
        )
        scheduled = [
            {
                "id": m.id,
                "patient": _patient_label(p),
                "patient_id": p.id,
                "phone": p.phone,
                "goal": m.body,
                "scheduled_at": m.scheduled_at.isoformat(),
                "created_at": m.created_at.isoformat() if m.created_at else None,
            }
            for m, c, p in scheduled_rows
        ]

        history_rows = (
            s.query(Message, Campaign, Patient)
            .join(Campaign, Message.campaign_id == Campaign.id)
            .join(Patient, Campaign.patient_id == Patient.id)
            .filter(Campaign.practice_id == org_id, Message.channel == "call")
            .filter(
                (Message.direction == "inbound")
                | Message.status.in_(("sent", "failed", "rejected"))
            )
            .order_by(Message.created_at.desc())
            .limit(50)
            .all()
        )
        history = []
        for m, c, p in history_rows:
            history.append({
                "id": m.id,
                "patient": _patient_label(p),
                "direction": m.direction,   # outbound = placed; inbound = outcome/transcript
                "status": m.status,
                "goal": m.body if m.direction == "outbound" else None,
                "summary": m.subject if m.direction == "inbound" else None,
                "transcript": m.body if m.direction == "inbound" else None,
                "recording_url": (m.metadata_json or {}).get("recording_url"),
                "send_error": (m.metadata_json or {}).get("send_error"),
                "created_at": m.created_at.isoformat() if m.created_at else None,
            })

        return {"pending": pending, "scheduled": scheduled, "history": history}


class QueueCallBody(BaseModel):
    patient_id: int
    goal: str
    # ISO datetime (no offset — America/New_York, same as Settings ->
    # Follow-up rules / the quiet-hours window). Optional: leave unset and
    # the call places as soon as it's approved, same as today.
    scheduled_at: str | None = None


@router.post("/api/v1/calls/queue")
def api_calls_queue(body: QueueCallBody, org_id: str = Depends(verify_admin)):
    """Queue an AI call for a contact — the human approves the GOAL up front
    (a live call can't be approved word-by-word); approving it places the call."""
    from ..billing import engine_allowed
    from ..models import Organization as _Org
    with get_session() as _s:
        _org = _s.get(_Org, org_id)
    allowed, why = engine_allowed(org_id, _org.created_at if _org else None)
    if not allowed:
        raise HTTPException(status_code=402, detail=f"Calls are paused ({why}) — check Settings → Account & billing.")
    goal = body.goal.strip()
    if not goal:
        raise HTTPException(status_code=400, detail="Describe what the call should accomplish.")

    scheduled_dt = None
    if body.scheduled_at:
        from datetime import datetime as _dt
        from zoneinfo import ZoneInfo as _ZoneInfo
        try:
            naive = _dt.fromisoformat(body.scheduled_at)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid scheduled time")
        local = naive.replace(tzinfo=_ZoneInfo("America/New_York"))
        scheduled_dt = local.astimezone(_ZoneInfo("UTC")).replace(tzinfo=None)
        if scheduled_dt <= _dt.utcnow():
            raise HTTPException(status_code=400, detail="Pick a time in the future")

    with get_session() as s:
        patient = s.query(Patient).filter(
            Patient.id == body.patient_id, Patient.practice_id == org_id
        ).first()
        if patient is None:
            raise HTTPException(status_code=404, detail="Contact not found")
        if not patient.phone:
            raise HTTPException(status_code=400, detail="This contact has no phone number on file.")

        camp = Campaign(practice_id=org_id, workflow_id="voice_call", patient_id=patient.id)
        s.add(camp)
        s.flush()
        msg = Message(
            campaign_id=camp.id,
            direction="outbound",
            channel="call",
            body=goal,
            status="pending_approval",
            scheduled_at=scheduled_dt,
        )
        s.add(msg)
        s.flush()
        return {"ok": True, "id": msg.id}


# ---------------------------------------------------------------------------
# Dynamic dashboard — the widgets Adapix has decided to show.
# ---------------------------------------------------------------------------
@router.get("/api/v1/dashboard")
def api_dashboard_get(org_id: str = Depends(verify_admin)):
    """Return the current dashboard layout + the data for each widget.
    The UI uses this to render the Home view dynamically."""
    from ..dashboard import load_config, catalog_index, get_widget_data
    cfg = load_config()
    catalog = catalog_index()
    widgets_with_data: list[dict] = []
    for w in cfg.get("widgets", []):
        wid = w.get("id")
        entry = catalog.get(wid)
        if not entry:
            continue   # widget id removed from catalog — skip
        widgets_with_data.append({
            **w,
            "label":       entry["label"],
            "description": entry["description"],
            "data":        get_widget_data(wid, org_id),
        })
    return {
        "version":   cfg.get("version", 0),
        "updated_at": cfg.get("updated_at", 0),
        "widgets":   widgets_with_data,
        "catalog":   [
            {"id": w["id"], "label": w["label"], "description": w["description"]}
            for w in catalog.values()
        ],
    }


class WidgetMutationBody(BaseModel):
    widget_id: str
    position: str = "main"     # "top" | "main" | "right"
    reason: str = ""


@router.post("/api/v1/dashboard/add")
def api_dashboard_add(body: WidgetMutationBody, _user: str = Depends(verify_admin)):
    from ..dashboard import add_widget
    if not add_widget(body.widget_id, position=body.position, reason=body.reason):
        raise HTTPException(status_code=400, detail="unknown widget id")
    return {"ok": True}


@router.post("/api/v1/dashboard/remove")
def api_dashboard_remove(body: WidgetMutationBody, _user: str = Depends(verify_admin)):
    from ..dashboard import remove_widget
    if not remove_widget(body.widget_id, reason=body.reason):
        raise HTTPException(status_code=404, detail="widget not on dashboard")
    return {"ok": True}


class WidgetPinBody(BaseModel):
    widget_id: str
    pinned: bool = True
    reason: str = ""


@router.post("/api/v1/dashboard/pin")
def api_dashboard_pin(body: WidgetPinBody, _user: str = Depends(verify_admin)):
    from ..dashboard import pin_widget
    if not pin_widget(body.widget_id, body.pinned, reason=body.reason):
        raise HTTPException(status_code=404, detail="widget not on dashboard")
    return {"ok": True}


@router.post("/api/v1/dashboard/reset")
def api_dashboard_reset(_user: str = Depends(verify_admin)):
    from ..dashboard import reset_to_default
    return reset_to_default()


# ---------------------------------------------------------------------------
# Email OAuth — each org connects its OWN Gmail/Outlook so Adapix sends
# follow-ups as them (their real address), not a shared sender. OAuth login
# IS the ownership verification; tokens are stored per-org (see EmailConnection).
# ---------------------------------------------------------------------------
def _oauth_redirect_uri(request: Request, provider: str) -> str:
    """Build the redirect URI Google/Microsoft sends the user back to.
    Uses public_base_url if configured (production), otherwise inferred
    from the request (dev with localhost or ngrok)."""
    from ..config import Settings
    s = Settings()
    base = (s.public_base_url or str(request.base_url)).rstrip("/")
    return f"{base}/oauth/{provider}/callback"


@router.get("/oauth/google/start")
def oauth_google_start(request: Request, org_id: str = Depends(verify_admin)):
    """Kick off the Google consent flow for this org. The org id rides in the
    server-side state record, NOT the session — the callback may land on a
    different origin (public tunnel/domain) where the session cookie doesn't
    exist, so it must be able to identify the org without one."""
    from fastapi.responses import RedirectResponse
    from ..oauth import google_auth_url, new_state
    try:
        state = new_state("google", org_id)
        url = google_auth_url(_oauth_redirect_uri(request, "google"), state)
    except ValueError as e:
        raise HTTPException(status_code=503, detail=str(e))
    return RedirectResponse(url=url, status_code=302)


@router.get("/oauth/google/callback")
def oauth_google_callback(request: Request, code: str = "", state: str = ""):
    """Google sends the user back here after consent. No session required —
    the org comes from the one-time state record created at /start."""
    from fastapi.responses import RedirectResponse
    from ..oauth import consume_state, google_complete_connection
    meta = consume_state(state, "google") if code else None
    if not meta or not meta.get("org_id"):
        raise HTTPException(status_code=400, detail="invalid or expired auth state")
    try:
        google_complete_connection(meta["org_id"], code, _oauth_redirect_uri(request, "google"))
    except Exception as e:
        return HTMLResponse(f"<h1>Connection failed</h1><pre>{e}</pre>")
    return RedirectResponse(url="/app?tab=settings", status_code=302)


@router.get("/oauth/microsoft/start")
def oauth_microsoft_start(request: Request, org_id: str = Depends(verify_admin)):
    from fastapi.responses import RedirectResponse
    from ..oauth import microsoft_auth_url, new_state
    try:
        state = new_state("microsoft", org_id)
        url = microsoft_auth_url(_oauth_redirect_uri(request, "microsoft"), state)
    except ValueError as e:
        raise HTTPException(status_code=503, detail=str(e))
    return RedirectResponse(url=url, status_code=302)


@router.get("/oauth/microsoft/callback")
def oauth_microsoft_callback(request: Request, code: str = "", state: str = ""):
    from fastapi.responses import RedirectResponse
    from ..oauth import consume_state, microsoft_complete_connection
    meta = consume_state(state, "microsoft") if code else None
    if not meta or not meta.get("org_id"):
        raise HTTPException(status_code=400, detail="invalid or expired auth state")
    try:
        microsoft_complete_connection(meta["org_id"], code, _oauth_redirect_uri(request, "microsoft"))
    except Exception as e:
        return HTMLResponse(f"<h1>Connection failed</h1><pre>{e}</pre>")
    return RedirectResponse(url="/app?tab=settings", status_code=302)


@router.get("/api/v1/email/status")
def api_email_status(org_id: str = Depends(verify_admin)):
    """This org's connected email provider (if any) — used by the Settings
    channels card, mirroring the /api/v1/phone status shape."""
    from ..config import Settings
    from ..oauth import status
    s = Settings()
    # SMTP needs no app credentials, so email connect is always available —
    # `oauth_configured` tells the UI whether the Gmail/Outlook buttons work.
    oauth_configured = bool(s.google_client_id or s.microsoft_client_id)
    st = status(org_id)
    connected_provider = None
    for prov in ("google", "microsoft", "smtp"):
        if st.get(prov, {}).get("connected"):
            connected_provider = prov
            break
    return {
        "configured": True,
        "oauth_configured": oauth_configured,
        "connected": connected_provider is not None,
        "provider": connected_provider,
        "email": st.get(connected_provider, {}).get("email") if connected_provider else None,
    }


@router.get("/api/v1/email/smtp/detect")
def api_smtp_detect(email: str = "", _user: str = Depends(verify_admin)):
    """Best-guess SMTP server/port for an address — prefills the connect form."""
    from ..oauth import detect_smtp_settings
    return detect_smtp_settings(email)


class SmtpConnectBody(BaseModel):
    email: str
    password: str          # app-specific password
    host: str = ""
    port: int = 587
    name: str = ""


@router.post("/api/v1/email/smtp/connect")
def api_smtp_connect(body: SmtpConnectBody, org_id: str = Depends(verify_admin)):
    """Connect any email account over SMTP. Verifies the login actually works
    before saving — a bad app password fails here, not on the first customer."""
    from ..oauth import detect_smtp_settings, save_smtp_connection

    email = body.email.strip().lower()
    if "@" not in email:
        raise HTTPException(status_code=400, detail="Enter a valid email address")
    if not body.password.strip():
        raise HTTPException(status_code=400, detail="Enter the app password for this account")

    host = body.host.strip() or detect_smtp_settings(email)["host"]
    result = save_smtp_connection(
        org_id, email=email, password=body.password.strip(),
        host=host, port=body.port, name=body.name.strip() or None,
    )
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error") or "Could not connect")
    return result


@router.post("/api/v1/email/disconnect")
def api_email_disconnect(org_id: str = Depends(verify_admin)):
    from ..oauth import disconnect
    if not disconnect(org_id):
        raise HTTPException(status_code=404, detail="not connected")
    return {"ok": True}


class TestEmailBody(BaseModel):
    to: str
    subject: str | None = None
    body: str | None = None


@router.post("/api/v1/email/test")
def api_email_test(body: TestEmailBody, org_id: str = Depends(verify_admin)):
    """Send a test email via this org's connected provider — verifies the
    OAuth setup is working before pointing it at a real customer."""
    from ..oauth import send_email
    result = send_email(
        org_id,
        to=body.to,
        subject=body.subject or "Hello from Adapix",
        body=body.body or "This is a test email sent by Adapix on your behalf.\n\nIf you got this, the connection is working.",
    )
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error") or "send failed")
    return result


class TestSmsBody(BaseModel):
    to: str
    body: str | None = None


@router.post("/api/v1/sms/test")
def api_sms_test(body: TestSmsBody, _user: str = Depends(verify_admin)):
    from ..channels import SmsChannel
    sms = SmsChannel()
    result = sms.send(body.to, body.body or "Hello from Adapix. This is a test.")
    return {
        "status":      result.status,
        "provider_id": result.provider_id,
        "error":       result.error,
    }


@router.post("/api/v1/campaigns/run")
def api_run_campaigns(_user: str = Depends(verify_admin)) -> dict[str, Any]:
    """Manually trigger the campaign runner — for the caller's org only.
    (The background scheduler is what runs every org; letting one tenant
    trigger all tenants' runs invites resource abuse.)"""
    return run_all_campaigns(only_org=_user)


def run_all_campaigns(only_org: str | None = None) -> dict[str, Any]:
    """Run campaigns for every org that has completed setup, plus legacy YAML practices.
    Called from the background scheduler (all orgs) and the API endpoint
    (only_org = the caller)."""
    from ..campaign import CampaignRunner
    from ..config import list_practices, list_workflows
    from ..db import get_engine
    from ..models import Organization, OrgProfile
    from sqlalchemy.orm import Session

    results: list[dict[str, Any]] = []
    errors: list[str] = []

    # --- DB-backed orgs (new multi-tenant path) ---
    # Orgs with the master pause switch on are skipped entirely: no new
    # campaigns started, no new steps composed, until they resume.
    try:
        with Session(get_engine()) as s:
            rows = (
                s.query(Organization, OrgProfile)
                .join(OrgProfile, Organization.id == OrgProfile.org_id)
                .all()
            )
            org_ids = [o.id for o, prof in rows if not (prof.data or {}).get("paused")]
            # Billing gate: don't spend API money for orgs whose card failed
            # or whose trial lapsed without a subscription.
            from ..billing import engine_allowed
            created_by_org = {o.id: o.created_at for o, _ in rows}
            blocked = []
            for oid in list(org_ids):
                ok, why = engine_allowed(oid, created_by_org.get(oid))
                if not ok:
                    blocked.append(f"{oid}: {why}")
                    org_ids.remove(oid)
            if blocked:
                errors.append("billing-gated (no drafting): " + "; ".join(blocked))
            if only_org is not None:
                org_ids = [oid for oid in org_ids if oid == only_org]
    except Exception as exc:
        errors.append(f"db org query: {exc}")
        org_ids = []

    workflows = list_workflows()

    for practice_id in org_ids:
        for workflow_id in workflows:
            try:
                runner = CampaignRunner(practice_id, workflow_id)
                started = runner.start_campaigns_for_eligible_patients()
                composed = runner.run_due_messages()
                results.append({
                    "practice": practice_id,
                    "workflow": workflow_id,
                    "started": started,
                    "composed": composed,
                })
            except FileNotFoundError:
                pass  # workflow config file doesn't exist — skip
            except Exception as exc:
                errors.append(f"{practice_id}/{workflow_id}: {exc}")

    # --- Legacy YAML-backed practices (backwards compat) ---
    yaml_practices = [] if only_org is not None else [
        p for p in list_practices() if p not in org_ids]
    for practice_id in yaml_practices:
        for workflow_id in workflows:
            try:
                runner = CampaignRunner(practice_id, workflow_id)
                started = runner.start_campaigns_for_eligible_patients()
                composed = runner.run_due_messages()
                results.append({
                    "practice": practice_id,
                    "workflow": workflow_id,
                    "started": started,
                    "composed": composed,
                })
            except FileNotFoundError:
                pass
            except Exception as exc:
                errors.append(f"{practice_id}/{workflow_id}: {exc}")

    return {
        "ok": True,
        "ran_at": datetime.utcnow().isoformat(),
        "results": results,
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# Automations API
# ---------------------------------------------------------------------------

class AutomationBody(BaseModel):
    name: str
    url: str
    task: str
    schedule: str = "0 9 * * *"
    output_format: str = "docx"
    login_url: str | None = None
    login_username: str | None = None
    login_password: str | None = None
    login_email: str | None = None


@router.get("/api/v1/automations")
def api_list_automations(_user: str = Depends(verify_admin)) -> dict[str, Any]:
    from ..config import Settings
    from ..db import get_session
    from ..models import Automation, AutomationRun
    settings = Settings()
    with get_session(settings) as s:
        autos = (s.query(Automation)
                 .filter(Automation.org_id == _user)
                 .order_by(Automation.created_at.desc()).all())
        items = []
        for a in autos:
            last_run = (
                s.query(AutomationRun)
                .filter(AutomationRun.automation_id == a.id)
                .order_by(AutomationRun.started_at.desc())
                .first()
            )
            items.append({
                "id": a.id,
                "name": a.name,
                "url": a.url,
                "task": a.task,
                "schedule": a.schedule,
                "output_format": a.output_format,
                "status": a.status,
                "last_run_at": a.last_run_at.isoformat() if a.last_run_at else None,
                "last_run_status": a.last_run_status,
                "last_error": a.last_error,
                "has_result": bool(a.last_result_path),
                "run_count": s.query(AutomationRun).filter(AutomationRun.automation_id == a.id).count(),
                "login_url": a.login_url,
                "login_username": a.login_username,
                "login_email": a.login_email,
                # never return password to the client
            })
    return {"automations": items}


@router.post("/api/v1/automations")
def api_create_automation(body: AutomationBody, _user: str = Depends(verify_admin)) -> dict[str, Any]:
    from ..config import Settings
    from ..db import get_session
    from ..models import Automation
    settings = Settings()
    with get_session(settings) as s:
        a = Automation(
            org_id=_user,
            name=body.name,
            url=body.url,
            task=body.task,
            schedule=body.schedule,
            output_format=body.output_format,
            login_url=body.login_url or None,
            login_username=body.login_username or None,
            login_password=body.login_password or None,
            login_email=body.login_email or None,
        )
        s.add(a)
        s.flush()
        aid = a.id
    return {"ok": True, "id": aid}


@router.delete("/api/v1/automations/{aid}")
def api_delete_automation(aid: int, _user: str = Depends(verify_admin)) -> dict[str, Any]:
    from ..config import Settings
    from ..db import get_session
    from ..models import Automation
    settings = Settings()
    with get_session(settings) as s:
        a = s.get(Automation, aid)
        if not a or a.org_id != _user:
            raise HTTPException(status_code=404, detail="not found")
        s.delete(a)
    return {"ok": True}


@router.patch("/api/v1/automations/{aid}")
def api_update_automation(aid: int, body: AutomationBody, _user: str = Depends(verify_admin)) -> dict[str, Any]:
    from ..config import Settings
    from ..db import get_session
    from ..models import Automation
    settings = Settings()
    with get_session(settings) as s:
        a = s.get(Automation, aid)
        if not a or a.org_id != _user:
            raise HTTPException(status_code=404, detail="not found")
        a.name = body.name
        a.url = body.url
        a.task = body.task
        a.schedule = body.schedule
        a.output_format = body.output_format
        a.login_url = body.login_url or None
        a.login_username = body.login_username or None
        a.login_password = body.login_password or None
        a.login_email = body.login_email or None
    return {"ok": True}


@router.post("/api/v1/automations/{aid}/run")
def api_run_automation(aid: int, _user: str = Depends(verify_admin)) -> dict[str, Any]:
    """Trigger an automation immediately (outside its schedule)."""
    import threading
    from ..automations import run_automation
    from ..config import Settings
    from ..db import get_session
    from ..models import Automation
    with get_session(Settings()) as s:
        a = s.get(Automation, aid)
        if not a or a.org_id != _user:
            raise HTTPException(status_code=404, detail="not found")
    def _run():
        try:
            run_automation(aid)
        except Exception as exc:
            log.error(f"Automation {aid} thread error: {exc}")
    threading.Thread(target=_run, daemon=True).start()
    return {"ok": True, "message": "Automation started — check back in a moment for results."}


@router.post("/api/v1/automations/{aid}/pause")
def api_pause_automation(aid: int, _user: str = Depends(verify_admin)) -> dict[str, Any]:
    from ..config import Settings
    from ..db import get_session
    from ..models import Automation
    settings = Settings()
    with get_session(settings) as s:
        a = s.get(Automation, aid)
        if not a or a.org_id != _user:
            raise HTTPException(status_code=404, detail="not found")
        a.status = "paused" if a.status == "active" else "active"
        new_status = a.status
    return {"ok": True, "status": new_status}


@router.get("/api/v1/automations/{aid}/download")
def api_download_result(aid: int, _user: str = Depends(verify_admin)):
    from pathlib import Path as FPath
    from fastapi.responses import FileResponse
    from ..config import Settings
    from ..db import get_session
    from ..models import Automation
    settings = Settings()
    with get_session(settings) as s:
        a = s.get(Automation, aid)
        if not a or a.org_id != _user or not a.last_result_path:
            raise HTTPException(status_code=404, detail="no result yet")
        path = FPath(a.last_result_path)
        if not path.exists():
            raise HTTPException(status_code=404, detail="result file missing")
        ext = path.suffix
        media = "application/vnd.openxmlformats-officedocument.wordprocessingml.document" if ext == ".docx" else "text/plain"
        return FileResponse(str(path), media_type=media, filename=path.name)


# ---------------------------------------------------------------------------
# AI Team — specialist agent chats
# ---------------------------------------------------------------------------
from ..team_agents import (
    CATEGORY_ORDER, list_agents, get_agent,
    load_agent_history, send_agent_message, clear_agent_history,
)


@router.get("/api/v1/team-agents")
def api_team_agents(_user: str = Depends(verify_admin)):
    agents = list_agents()
    by_cat: dict[str, list] = {}
    for a in agents:
        by_cat.setdefault(a.category, []).append({
            "slug": a.slug,
            "name": a.name,
            "description": a.description,
            "emoji": a.emoji,
            "category": a.category,
        })
    return {"categories": [
        {"name": cat, "agents": by_cat[cat]}
        for cat in CATEGORY_ORDER if cat in by_cat
    ]}


@router.get("/api/v1/team-agents/{slug}/chat/history")
def api_agent_history(slug: str, _user: str = Depends(verify_admin)):
    if not get_agent(slug):
        raise HTTPException(status_code=404, detail="agent not found")
    return {"messages": load_agent_history(slug)}


class AgentMessageBody(BaseModel):
    message: str


@router.post("/api/v1/team-agents/{slug}/chat/send")
async def api_agent_send(
    slug: str,
    message: str = Form(""),
    files: List[UploadFile] = File(default=[]),
    _user: str = Depends(verify_admin),
):
    attachments = await _parse_attachments(files)
    try:
        result = send_agent_message(slug, message, attachments=attachments or None)
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/api/v1/team-agents/{slug}/chat/history")
def api_agent_clear(slug: str, _user: str = Depends(verify_admin)):
    clear_agent_history(slug)
    return {"ok": True}


@router.get("/api/v1/patients")
def api_patients_list(q: str = "", _user: str = Depends(verify_admin)):
    """All contacts for the org, with enough signal to sort by who needs
    attention — a 150-row list is useless as pure eyeball-scroll."""
    from sqlalchemy import func as _func
    with get_session() as s:
        query = s.query(Patient).filter(Patient.practice_id == _user)
        needle = q.strip().lower()
        if needle:
            like = f"%{needle}%"
            query = query.filter(
                _func.lower(Patient.first_name + " " + Patient.last_name).like(like)
                | _func.lower(Patient.phone).like(like)
                | _func.lower(Patient.email).like(like)
                | _func.lower(Patient.treatment_type).like(like)
            )
        rows = query.limit(500).all()

        # Last outbound / inbound timestamp per patient, across all their
        # campaigns — cheap enough at this scale, and it's the one thing
        # that turns a flat list into "who's actually gone quiet."
        last_out = dict(
            s.query(Campaign.patient_id, _func.max(Message.created_at))
            .join(Message, Message.campaign_id == Campaign.id)
            .filter(Campaign.practice_id == _user, Message.direction == "outbound")
            .group_by(Campaign.patient_id).all()
        )
        last_in = dict(
            s.query(Campaign.patient_id, _func.max(Message.created_at))
            .join(Message, Message.campaign_id == Campaign.id)
            .filter(Campaign.practice_id == _user, Message.direction == "inbound")
            .group_by(Campaign.patient_id).all()
        )

        now = datetime.utcnow()
        out = []
        for p in rows:
            lo, li = last_out.get(p.id), last_in.get(p.id)
            silent_since = li or lo
            silent_days = (now - silent_since).days if silent_since else None
            active = p.status not in ("treatment_started", "explicitly_declined") and not p.opted_out
            needs_attention = bool(active and lo and silent_days is not None and silent_days >= 9 and (not li or li < lo))
            out.append({
                "id": p.id,
                "first_name": p.first_name,
                "last_name": p.last_name,
                "phone": p.phone,
                "email": p.email,
                "preferred_channel": p.preferred_channel,
                "status": p.status,
                "opted_out": bool(p.opted_out),
                "treatment_type": p.treatment_type,
                "treatment_plan_amount": p.treatment_plan_amount,
                "consult_date": p.consult_date.isoformat() if p.consult_date else None,
                "silent_days": silent_days,
                "needs_attention": needs_attention,
            })
        # Needs-attention first, then longest-silent, so the top of the list
        # is always "who should I actually think about today."
        out.sort(key=lambda x: (not x["needs_attention"], -(x["silent_days"] or -1)))
        return {"total": len(out), "patients": out}


@router.get("/api/v1/phone")
def api_phone_get(org_id: str = Depends(verify_admin)):
    """The org's dedicated calling number + provisioning status (Settings UI)."""
    from ..models import Organization

    with get_session() as s:
        org = s.get(Organization, org_id)
        if org is None:
            return {"has_number": False, "status": "none", "number": None}
        return {
            "has_number": bool(org.vapi_phone_number_id),
            "status": org.phone_status,
            "number": org.phone_number,
        }


class ProvisionBody(BaseModel):
    area_code: str = ""


@router.post("/api/v1/phone/provision")
def api_phone_provision(body: ProvisionBody, org_id: str = Depends(verify_admin)):
    """On-demand: give this business its dedicated calling number (Settings button)."""
    from ..provisioning import ensure_org_number

    return ensure_org_number(org_id, area_code=(body.area_code.strip() or None))


# ---------------------------------------------------------------------------
# Wins — the ROI proof loop. One tap when a chased contact turns into a job;
# the dashboard rolls it up as "Won back $X this month". This number is the
# product's whole argument at renewal time.
# ---------------------------------------------------------------------------
class WinBody(BaseModel):
    patient_id: int
    amount: float | None = None


@router.post("/api/v1/calling-number/retry")
def api_calling_number_retry(background: BackgroundTasks, org_id: str = Depends(verify_admin)):
    """Manual retry after failed number provisioning — surfaced in Settings
    since a silent failure otherwise leaves calls permanently broken."""
    from ..provisioning import ensure_org_number
    background.add_task(ensure_org_number, org_id)
    return {"ok": True, "status": "retrying"}


@router.post("/api/v1/wins")
def api_win_mark(body: WinBody, org_id: str = Depends(verify_admin)):
    from ..db import get_engine
    from sqlalchemy.orm import Session
    with Session(get_engine()) as s:
        p = s.get(Patient, body.patient_id)
        if not p or p.practice_id != org_id:
            raise HTTPException(status_code=404, detail="Contact not found")
        amount = body.amount if body.amount is not None else (p.treatment_plan_amount or 0)
        data = _load_org_profile_data(s, org_id)
        wins = list(data.get("wins") or [])
        wins.append({
            "patient_id": p.id,
            "name": f"{p.first_name or ''} {p.last_name or ''}".strip(),
            "amount": float(amount or 0),
            "at": datetime.utcnow().isoformat() + "Z",
        })
        data["wins"] = wins
        _save_org_profile_data(s, org_id, data)
        p.status = "treatment_started"
        # A won customer stops being chased: close their active campaigns and
        # clear any drafts still waiting.
        for c in s.query(Campaign).filter(Campaign.patient_id == p.id,
                                          Campaign.status == "active").all():
            c.status = "completed"
            for m in s.query(Message).filter(Message.campaign_id == c.id,
                                             Message.status.in_(("pending_approval", "approved"))).all():
                m.status = "rejected"
        s.commit()
    return {"ok": True, "amount": float(amount or 0)}


@router.post("/api/v1/digest/test")
def api_digest_test(org_id: str = Depends(verify_admin)):
    """Send this org's digest right now, bypassing the once-daily schedule —
    lets the owner see what it looks like without waiting for 8am."""
    from ..digest import _org_digest, _digest_text
    from ..notifications import push_notification
    d = _org_digest(org_id)
    if d is None:
        d = {"drafts": 0, "escalations": 0, "stale_hours": 0, "won_this_week": 0, "won_last_week": 0}
    title, body = _digest_text(d)
    res = push_notification(title=title, body=body, url="/app", tag="adapix-digest", org_id=org_id)
    if not res.get("ok"):
        raise HTTPException(status_code=400, detail=res.get("error") or "no devices subscribed")
    return {"ok": True, "title": title, "body": body}


@router.get("/api/v1/wins/summary")
def api_wins_summary(org_id: str = Depends(verify_admin)):
    from ..db import get_engine
    from sqlalchemy.orm import Session
    with Session(get_engine()) as s:
        wins = (_load_org_profile_data(s, org_id).get("wins")) or []
    now = datetime.utcnow()
    month_start = datetime(now.year, now.month, 1).isoformat()
    month = [w for w in wins if (w.get("at") or "") >= month_start]
    week_ago = (now - timedelta(days=7)).isoformat()
    two_weeks_ago = (now - timedelta(days=14)).isoformat()
    week_total = sum(w.get("amount") or 0 for w in wins if (w.get("at") or "") >= week_ago)
    last_week_total = sum(w.get("amount") or 0 for w in wins if two_weeks_ago <= (w.get("at") or "") < week_ago)
    return {
        "month_total": sum(w.get("amount") or 0 for w in month),
        "month_count": len(month),
        "all_total": sum(w.get("amount") or 0 for w in wins),
        "all_count": len(wins),
        "week_total": week_total,
        "last_week_total": last_week_total,
        "recent": sorted(wins, key=lambda w: w.get("at") or "", reverse=True)[:5],
    }


class NotifyPrefsBody(BaseModel):
    notify_on_escalation: bool = True
    notify_on_draft: bool = True
    notify_digest: bool = True


@router.get("/api/v1/notify-prefs")
def api_notify_prefs_get(org_id: str = Depends(verify_admin)):
    from ..db import get_engine
    from sqlalchemy.orm import Session
    with Session(get_engine()) as s:
        data = _load_org_profile_data(s, org_id)
    return {
        "notify_on_escalation": bool(data.get("notify_on_escalation", True)),
        "notify_on_draft": bool(data.get("notify_on_draft", True)),
        "notify_digest": bool(data.get("notify_digest", True)),
    }


@router.post("/api/v1/notify-prefs")
def api_notify_prefs_set(body: NotifyPrefsBody, org_id: str = Depends(verify_admin)):
    from ..db import get_engine
    from sqlalchemy.orm import Session
    with Session(get_engine()) as s:
        data = _load_org_profile_data(s, org_id)
        data["notify_on_escalation"] = body.notify_on_escalation
        data["notify_on_draft"] = body.notify_on_draft
        data["notify_digest"] = body.notify_digest
        _save_org_profile_data(s, org_id, data)
        s.commit()
    return {"ok": True}


@router.get("/api/v1/contacts/template.csv", response_class=PlainTextResponse)
def api_contacts_template(_user: str = Depends(verify_admin)):
    """Download a blank CSV template pre-filled with one example row."""
    headers = "first_name,last_name,phone,email,preferred_channel,consult_date,service_type,deal_value,notes,external_id"
    example = "Jane,Smith,+14125550100,jane@email.com,sms,2024-01-15,Premium Package,6000,Interested but needs pricing info,EXT-001"
    return PlainTextResponse(
        content=f"{headers}\n{example}\n",
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=adapix_contacts_template.csv"},
    )


@router.post("/api/v1/contacts/import")
async def api_contacts_import(
    file: UploadFile = File(...),
    preview: str = Form("false"),
    chase: str = Form("all"),
    _user: str = Depends(verify_admin),
):
    """Upload a CSV of contacts. With preview=true returns first 5 rows without inserting.

    chase="all"  -> imported contacts enter the follow-up pool (capped drafting)
    chase="none" -> imported as records only; the owner opts each one in later
    Re-imports are deduped on (org, phone) and (org, external_id).
    """
    import csv as csv_mod
    import io
    from datetime import datetime as dt

    content = await file.read()
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = content.decode("latin-1")

    reader = csv_mod.DictReader(io.StringIO(text))
    rows = list(reader)
    columns = list(reader.fieldnames or [])

    if preview.lower() in ("true", "1", "yes"):
        return {"preview": rows[:5], "total": len(rows), "columns": columns}

    imported = 0
    skipped = 0
    duplicates = 0
    errors: list[str] = []
    status_for_new = "consulted_not_started" if chase != "none" else "on_hold"

    with get_session() as s:
        existing = s.query(Patient.phone, Patient.external_id).filter(Patient.practice_id == _user).all()
        seen_phones = {ph for ph, _ in existing if ph}
        seen_ext = {ext for _, ext in existing if ext}
        for i, row in enumerate(rows):
            try:
                from ..phone import normalize_phone
                row_phone = normalize_phone(row.get("phone"))
                row_ext = (row.get("external_id") or "").strip() or None
                if (row_phone and row_phone in seen_phones) or (row_ext and row_ext in seen_ext):
                    duplicates += 1
                    continue
                consult_date = None
                raw_date = row.get("consult_date", "").strip()
                if raw_date:
                    consult_date = dt.fromisoformat(raw_date)

                amount = None
                raw_amount = (row.get("deal_value") or row.get("treatment_plan_amount") or "").strip()
                if raw_amount:
                    try:
                        amount = float(raw_amount)
                    except ValueError:
                        pass

                s.add(Patient(
                    practice_id=_user,
                    external_id=(row.get("external_id") or "").strip() or None,
                    first_name=(row.get("first_name") or "").strip(),
                    last_name=(row.get("last_name") or "").strip(),
                    phone=row_phone,
                    email=(row.get("email") or "").strip() or None,
                    preferred_channel=(row.get("preferred_channel") or "sms").strip(),
                    consult_date=consult_date,
                    treatment_type=(row.get("service_type") or row.get("treatment_type") or "").strip() or None,
                    treatment_plan_amount=amount,
                    notes=(row.get("notes") or "").strip() or None,
                    status=status_for_new,
                ))
                if row_phone:
                    seen_phones.add(row_phone)
                if row_ext:
                    seen_ext.add(row_ext)
                imported += 1
            except Exception as exc:
                errors.append(f"Row {i + 2}: {exc}")
                skipped += 1

    return {"imported": imported, "skipped": skipped, "duplicates": duplicates, "errors": errors}


class ContactAddBody(BaseModel):
    first_name: str = ""
    last_name: str = ""
    phone: str = ""
    email: str = ""
    preferred_channel: str = ""
    service_type: str = ""
    deal_value: str = ""
    notes: str = ""
    status: str = ""


@router.post("/api/v1/contacts/add")
def api_contacts_add(body: ContactAddBody, _user: str = Depends(verify_admin)):
    """Add a single contact straight from the dashboard — no CSV needed."""
    first = (body.first_name or "").strip()
    last = (body.last_name or "").strip()
    from ..phone import normalize_phone
    phone = normalize_phone(body.phone)
    email = (body.email or "").strip() or None

    if not first and not last:
        raise HTTPException(status_code=400, detail="Add a name so you know who this is.")
    if not phone and not email:
        raise HTTPException(status_code=400, detail="Add a phone number or an email so Adapix can reach them.")

    amount = None
    raw_amount = (body.deal_value or "").strip()
    if raw_amount:
        try:
            amount = float(raw_amount.replace(",", "").replace("$", ""))
        except ValueError:
            pass

    channel = (body.preferred_channel or "").strip() or ("sms" if phone else "email")

    # "consulted_not_started" = a lead Adapix follows up to win.
    # "treatment_started" = an existing customer (no convert-follow-up).
    allowed_status = {
        "consulted_not_started", "treatment_started",
        "explicitly_declined", "paused",
    }
    status = (body.status or "").strip()
    if status not in allowed_status:
        status = "consulted_not_started"

    with get_session() as s:
        p = Patient(
            practice_id=_user,
            first_name=first,
            last_name=last,
            phone=phone,
            email=email,
            preferred_channel=channel,
            treatment_type=(body.service_type or "").strip() or None,
            treatment_plan_amount=amount,
            notes=(body.notes or "").strip() or None,
            status=status,
        )
        s.add(p)
        s.flush()
        return {"ok": True, "id": p.id}


class ContactEditBody(BaseModel):
    first_name: str | None = None
    last_name: str | None = None
    phone: str | None = None
    email: str | None = None
    preferred_channel: str | None = None
    service_type: str | None = None
    deal_value: str | None = None
    notes: str | None = None
    status: str | None = None


@router.patch("/api/v1/patients/{patient_id}")
def api_patient_edit(patient_id: int, body: ContactEditBody, _user: str = Depends(verify_admin)):
    """Edit a contact — a typo'd phone or an out-of-date note otherwise had
    no fix short of deleting and re-adding the whole person."""
    from ..phone import normalize_phone
    with get_session() as s:
        p = s.query(Patient).filter(Patient.id == patient_id, Patient.practice_id == _user).first()
        if not p:
            raise HTTPException(status_code=404, detail="Contact not found")

        if body.first_name is not None:
            p.first_name = body.first_name.strip()
        if body.last_name is not None:
            p.last_name = body.last_name.strip()
        if body.phone is not None:
            p.phone = normalize_phone(body.phone) if body.phone.strip() else None
        if body.email is not None:
            p.email = body.email.strip() or None
        if body.preferred_channel is not None and body.preferred_channel in ("sms", "email"):
            p.preferred_channel = body.preferred_channel
        if body.service_type is not None:
            p.treatment_type = body.service_type.strip() or None
        if body.deal_value is not None:
            raw = body.deal_value.strip().replace(",", "").replace("$", "")
            p.treatment_plan_amount = float(raw) if raw else None
        if body.notes is not None:
            p.notes = body.notes.strip() or None
        if body.status is not None and body.status in (
            "consulted_not_started", "treatment_started", "explicitly_declined", "paused",
        ):
            p.status = body.status
        if not p.first_name.strip() and not p.last_name.strip():
            raise HTTPException(status_code=400, detail="A contact needs a name.")
        if not p.phone and not p.email:
            raise HTTPException(status_code=400, detail="A contact needs a phone number or an email.")
        s.commit()
        return {
            "ok": True, "id": p.id, "first_name": p.first_name, "last_name": p.last_name,
            "phone": p.phone, "email": p.email, "preferred_channel": p.preferred_channel,
            "status": p.status, "treatment_type": p.treatment_type,
            "treatment_plan_amount": p.treatment_plan_amount, "notes": p.notes,
        }


@router.delete("/api/v1/patients/{patient_id}")
def api_patient_delete(patient_id: int, _user: str = Depends(verify_admin)):
    """Remove a contact and everything attached to them (campaigns, message
    history) — for a bad import row or a deletion request."""
    with get_session() as s:
        p = s.query(Patient).filter(Patient.id == patient_id, Patient.practice_id == _user).first()
        if not p:
            raise HTTPException(status_code=404, detail="Contact not found")
        s.delete(p)
        s.commit()
    return {"ok": True}


@router.get("/api/v1/team-agents/{slug}/documents/{filename}")
def api_agent_document(slug: str, filename: str, _user: str = Depends(verify_admin)):
    import os
    from pathlib import Path
    doc_dir = Path(os.environ.get("ADAPIX_VAR", ".")) / "agent_documents"
    # Strip any path traversal attempts
    safe_name = Path(filename).name
    path = doc_dir / safe_name
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="Document not found")
    return FileResponse(str(path), media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document", filename=safe_name)
