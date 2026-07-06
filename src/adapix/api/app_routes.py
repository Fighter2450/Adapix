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

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
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
    if not configured:
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
    return HTMLResponse((TEMPLATE_DIR / "chat.html").read_text(encoding="utf-8"))


@router.get("/api/v1/chat/history")
def api_chat_history(org_id: str = Depends(verify_admin)):
    from ..chat import load_history, missing_topics, suggestions_for
    from ..practice import load_profile
    profile = load_profile(org_id)
    msgs = load_history()
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
        return generate_opener(onboarding=bool(body and body.onboarding))
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
        return reply_to(text, attachments=attachments or None)
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
def api_memory_list():
    from ..memory import all_facts
    return {"facts": all_facts()}


@router.delete("/api/v1/memory/{fact_id}")
def api_memory_delete(fact_id: str):
    from ..memory import remove_fact
    ok = remove_fact(fact_id)
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
        })
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
    subs = list_subscriptions()
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
                row.data = payload
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
    from ..models import OrgProfile
    from sqlalchemy.orm import Session
    try:
        with Session(get_engine()) as s:
            row = s.get(OrgProfile, org_id)
            if row:
                return {"configured": True, "profile": row.data}
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
    from ..models import OrgProfile
    row = s.get(OrgProfile, org_id)
    return dict(row.data) if row and row.data else {}


def _save_org_profile_data(s, org_id: str, data: dict) -> None:
    from ..models import OrgProfile
    row = s.get(OrgProfile, org_id)
    if row:
        row.data = data
    else:
        s.add(OrgProfile(org_id=org_id, data=data))


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


@router.get("/api/v1/database")
def api_database(org_id: str = Depends(verify_admin)):
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
            "business_type": data.get("practice_type_label") or data.get("practice_type", "").replace("_", " "),
            "tone": data.get("tone") or "warm_professional",
            "description": data.get("description") or "",
            "services": data.get("services") or [],
            "workflows": workflows,
            "escalations": escalations,
            "knowledge_base": data.get("knowledge_base") or [],
            "configured_at": data.get("configured_at") or "",
            "calling_number": org.phone_number if org else None,
            "calling_status": org.phone_status if org else "none",
            "imessage_number": org.imessage_number if org else None,
            "imessage_connected": bool(org and org.blooio_channel_id),
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
        data["practice"] = practice
        if body.tone is not None and body.tone in ("warm_professional", "casual_friendly", "clinical_formal"):
            data["tone"] = body.tone
        if body.description is not None:
            data["description"] = body.description.strip()
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
def api_messages_list(channel: str = "sms,email", limit: int = 50, org_id: str = Depends(verify_admin)):
    from sqlalchemy import desc
    from sqlalchemy.orm import Session
    from ..db import get_engine

    wanted = {c.strip() for c in channel.split(",") if c.strip()}
    with Session(get_engine()) as s:
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
const CACHE = 'adapix-shell-v3';
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
                    "patient": _patient_label(patient),
                    "phone_last4": (patient.phone or "")[-4:] if patient else "",
                    "category": e.category,
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
            .filter(Patient.status == "scheduled")
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


@router.post("/api/v1/approvals/{message_id}/approve")
def api_approve(message_id: int, body: ApproveBody, _user: str = Depends(verify_admin)):
    _require_message_in_org(message_id, _user)
    mgr = ApprovalManager()
    try:
        mgr.approve_and_send(message_id, edited_body=body.edited_body or None)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "id": message_id}


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
                "created_at": m.created_at.isoformat() if m.created_at else None,
            }
            for m, c, p in pending_rows
        ]

        history_rows = (
            s.query(Message, Campaign, Patient)
            .join(Campaign, Message.campaign_id == Campaign.id)
            .join(Patient, Campaign.patient_id == Patient.id)
            .filter(Campaign.practice_id == org_id, Message.channel == "call")
            .filter(Message.status != "pending_approval")
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
                "created_at": m.created_at.isoformat() if m.created_at else None,
            })

        return {"pending": pending, "history": history}


class QueueCallBody(BaseModel):
    patient_id: int
    goal: str


@router.post("/api/v1/calls/queue")
def api_calls_queue(body: QueueCallBody, org_id: str = Depends(verify_admin)):
    """Queue an AI call for a contact — the human approves the GOAL up front
    (a live call can't be approved word-by-word); approving it places the call."""
    goal = body.goal.strip()
    if not goal:
        raise HTTPException(status_code=400, detail="Describe what the call should accomplish.")

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
        autos = s.query(Automation).order_by(Automation.created_at.desc()).all()
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
        if not a:
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
        if not a:
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
        if not a:
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
        if not a or not a.last_result_path:
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
def api_patients_list(_user: str = Depends(verify_admin)):
    """Return all patients/contacts for the authenticated org."""
    with get_session() as s:
        rows = (s.query(Patient)
                .filter(Patient.practice_id == _user)
                .order_by(Patient.created_at.desc())
                .limit(500)
                .all())
        return {
            "total": len(rows),
            "patients": [
                {
                    "id": p.id,
                    "first_name": p.first_name,
                    "last_name": p.last_name,
                    "phone": p.phone,
                    "email": p.email,
                    "preferred_channel": p.preferred_channel,
                    "status": p.status,
                    "treatment_type": p.treatment_type,
                    "treatment_plan_amount": p.treatment_plan_amount,
                    "consult_date": p.consult_date.isoformat() if p.consult_date else None,
                }
                for p in rows
            ],
        }


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
    _user: str = Depends(verify_admin),
):
    """Upload a CSV of contacts. With preview=true returns first 5 rows without inserting."""
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
    errors: list[str] = []

    with get_session() as s:
        for i, row in enumerate(rows):
            try:
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
                    phone=(row.get("phone") or "").strip() or None,
                    email=(row.get("email") or "").strip() or None,
                    preferred_channel=(row.get("preferred_channel") or "sms").strip(),
                    consult_date=consult_date,
                    treatment_type=(row.get("service_type") or row.get("treatment_type") or "").strip() or None,
                    treatment_plan_amount=amount,
                    notes=(row.get("notes") or "").strip() or None,
                    status="consulted_not_started",
                ))
                imported += 1
            except Exception as exc:
                errors.append(f"Row {i + 2}: {exc}")
                skipped += 1

    return {"imported": imported, "skipped": skipped, "errors": errors}


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
    phone = (body.phone or "").strip() or None
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
