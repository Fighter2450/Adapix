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

from fastapi import APIRouter, Depends, HTTPException, Request
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
    """The PWA shell. Auth happens on the JSON fetches, not on the shell.
    If the device has not yet been configured (no setup wizard completed),
    redirect users to /welcome so they don't land in an empty dashboard."""
    from fastapi.responses import RedirectResponse
    if not CONFIGURED_FLAG.exists():
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


# ---------------------------------------------------------------------------
# In-dashboard chatbot — ongoing conversation with the practice
# ---------------------------------------------------------------------------
@router.get("/chat", response_class=HTMLResponse)
def chat_page():
    return HTMLResponse((TEMPLATE_DIR / "chat.html").read_text(encoding="utf-8"))


@router.get("/api/v1/chat/history")
def api_chat_history():
    from ..chat import load_history, missing_topics, suggestions_for
    from ..practice import load_profile
    profile = load_profile()
    msgs = load_history()
    return {
        "messages": msgs,
        "suggestions": suggestions_for(missing_topics(profile, msgs)),
    }


class OpenerBody(BaseModel):
    onboarding: bool = False


@router.post("/api/v1/chat/opener")
def api_chat_opener(body: OpenerBody | None = None):
    """Generate the bot's first message — used when there's no history yet.
    Pass {"onboarding": true} the first time after the welcome wizard so
    Adapix knows she's interviewing the practice (rather than the regular
    'hi, how can I help' opener)."""
    from ..chat import generate_opener
    try:
        return generate_opener(onboarding=bool(body and body.onboarding))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"opener failed: {e}")


class ChatSendBody(BaseModel):
    message: str


@router.post("/api/v1/chat/send")
def api_chat_send(body: ChatSendBody):
    """User sent a message → return Adapix's reply + any new suggestions."""
    from ..chat import reply_to
    text = (body.message or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="empty message")
    try:
        return reply_to(text)
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
def api_connectors_status():
    from ..config import Settings
    from ..oauth import status as oauth_status
    s = Settings()
    oauth = oauth_status()
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
def api_notify_subscribe(body: SubscribeBody):
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
def api_notify_unsubscribe(body: UnsubscribeBody):
    from ..notifications import remove_subscription
    ok = remove_subscription(body.endpoint)
    return {"ok": ok}


@router.get("/api/v1/notify/status")
def api_notify_status():
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
def api_notify_test(body: TestPushBody):
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
def api_expenses_list():
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
def api_subscriptions_add(body: AddSubscriptionBody):
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
def api_subscriptions_cancel(sub_id: str):
    """Stop future auto-charges. Past charges stay in history."""
    from ..expenses import cancel_subscription
    if not cancel_subscription(sub_id):
        raise HTTPException(status_code=404, detail="subscription not found or already cancelled")
    return {"ok": True, "id": sub_id}


@router.delete("/api/v1/subscriptions/{sub_id}")
def api_subscriptions_delete(sub_id: str):
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
def api_expenses_add(body: AddExpenseBody):
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
def api_expenses_delete(expense_id: str):
    from ..expenses import remove_expense
    if not remove_expense(expense_id):
        raise HTTPException(status_code=404, detail="expense not found")
    return {"ok": True, "id": expense_id}


@router.get("/api/v1/expenses/export.csv", response_class=PlainTextResponse)
def api_expenses_csv():
    from ..expenses import to_csv
    return PlainTextResponse(content=to_csv(), media_type="text/csv")


class BulkExpenseBody(BaseModel):
    text: str


@router.post("/api/v1/expenses/bulk")
def api_expenses_bulk(body: BulkExpenseBody):
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
def api_setup_save(body: SetupBody):
    """Save the wizard's collected config + mark the device configured."""
    try:
        SETUP_DIR.mkdir(parents=True, exist_ok=True)
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
        PRACTICE_JSON.write_text(json.dumps(payload, indent=2))
        CONFIGURED_FLAG.write_text("ok\n")
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"could not save: {e}")


@router.get("/api/v1/setup/status")
def api_setup_status():
    """Read current setup state — used by other UI surfaces to decide
    whether the device has been onboarded yet."""
    if not CONFIGURED_FLAG.exists():
        return {"configured": False, "profile": None}
    try:
        profile = json.loads(PRACTICE_JSON.read_text()) if PRACTICE_JSON.exists() else None
    except Exception:
        profile = None
    return {"configured": True, "profile": profile}


@router.get("/app/manifest.json")
def manifest():
    return JSONResponse(
        {
            "name": "Adapix",
            "short_name": "Adapix",
            "description": "Live escalations and one-tap approvals for OMS practices.",
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
const CACHE = 'adapix-shell-v1';
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
                if m.status == "sent":
                    kind, label = "sent", f"Sent {m.channel} to {patient_name}"
                elif m.status in ("pending_approval", "composed"):
                    kind, label = "draft", f"Drafted {m.channel} for {patient_name}"
                elif m.status == "rejected":
                    kind, label = "rejected", f"Rejected draft for {patient_name}"
                else:
                    kind, label = "outbound", f"{m.status} {m.channel} → {patient_name}"
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
    """Everything the home screen needs: escalations, pending approvals, today's digest."""
    with get_session() as s:
        # Open escalations (newest first)
        escalations = (
            s.query(EscalationEvent)
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
            if e.triggered_by_message_id:
                m = s.get(Message, e.triggered_by_message_id)
                if m:
                    triggering_body = m.body[:280]
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
                    "ago": _ago(e.created_at),
                    "created_at": e.created_at.isoformat() if e.created_at else None,
                }
            )

        # Pending-approval messages
        pending = (
            s.query(Message)
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
            .filter(Message.direction == "outbound", Message.status.in_(["sent", "delivered"]))
            .filter(Message.created_at >= today_start)
            .count()
        )
        booked_today = (
            s.query(Patient)
            .filter(Patient.status == "scheduled")
            .filter(Patient.created_at >= today_start - timedelta(days=14))
            .count()
        )
        open_escalations = (
            s.query(EscalationEvent).filter(EscalationEvent.resolved == False).count()  # noqa: E712
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



@router.post("/api/v1/approvals/{message_id}/approve")
def api_approve(message_id: int, body: ApproveBody, _user: str = Depends(verify_admin)):
    mgr = ApprovalManager()
    try:
        mgr.approve_and_send(message_id, edited_body=body.edited_body or None)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "id": message_id}


@router.post("/api/v1/approvals/{message_id}/reject")
def api_reject(message_id: int, body: RejectBody, _user: str = Depends(verify_admin)):
    mgr = ApprovalManager()
    try:
        mgr.reject(message_id, reason=body.reason or None)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "id": message_id}


# ---------------------------------------------------------------------------
# Dynamic dashboard — the widgets Adapix has decided to show.
# ---------------------------------------------------------------------------
@router.get("/api/v1/dashboard")
def api_dashboard_get():
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
            "data":        get_widget_data(wid),
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
def api_dashboard_add(body: WidgetMutationBody):
    from ..dashboard import add_widget
    if not add_widget(body.widget_id, position=body.position, reason=body.reason):
        raise HTTPException(status_code=400, detail="unknown widget id")
    return {"ok": True}


@router.post("/api/v1/dashboard/remove")
def api_dashboard_remove(body: WidgetMutationBody):
    from ..dashboard import remove_widget
    if not remove_widget(body.widget_id, reason=body.reason):
        raise HTTPException(status_code=404, detail="widget not on dashboard")
    return {"ok": True}


class WidgetPinBody(BaseModel):
    widget_id: str
    pinned: bool = True
    reason: str = ""


@router.post("/api/v1/dashboard/pin")
def api_dashboard_pin(body: WidgetPinBody):
    from ..dashboard import pin_widget
    if not pin_widget(body.widget_id, body.pinned, reason=body.reason):
        raise HTTPException(status_code=404, detail="widget not on dashboard")
    return {"ok": True}


@router.post("/api/v1/dashboard/reset")
def api_dashboard_reset():
    from ..dashboard import reset_to_default
    return reset_to_default()


# ---------------------------------------------------------------------------
# Email OAuth — Connect Gmail Workspace or Microsoft 365 so Adapix can
# send patient emails from the practice's actual email address.
# ---------------------------------------------------------------------------
def _oauth_redirect_uri(request: Request, provider: str) -> str:
    """Build the redirect URI Google/Microsoft sends the user back to.
    Uses public_base_url if configured (production), otherwise inferred
    from the request (dev with localhost or ngrok)."""
    from ..config import Settings
    s = Settings()
    base = (s.public_base_url or str(request.base_url)).rstrip("/")
    return f"{base}/api/v1/oauth/{provider}/callback"


@router.get("/api/v1/oauth/google/start")
def api_oauth_google_start(request: Request):
    """Return the Google consent URL the frontend should redirect to."""
    from ..oauth import google_auth_url, new_state
    try:
        state = new_state("google")
        url = google_auth_url(_oauth_redirect_uri(request, "google"), state)
        return {"url": url}
    except ValueError as e:
        raise HTTPException(status_code=503, detail=str(e))


@router.get("/api/v1/oauth/google/callback")
def api_oauth_google_callback(request: Request, code: str = "", state: str = ""):
    """Google sends the user back here after consent. Exchange code → tokens,
    persist them, then redirect the user back to /app Settings."""
    from fastapi.responses import RedirectResponse
    from ..oauth import consume_state, google_complete_connection
    if not code or not consume_state(state, "google"):
        raise HTTPException(status_code=400, detail="invalid or expired auth state")
    try:
        google_complete_connection(code, _oauth_redirect_uri(request, "google"))
    except Exception as e:
        return HTMLResponse(f"<h1>Connection failed</h1><pre>{e}</pre>")
    return RedirectResponse(url="/app?email_connected=google", status_code=302)


@router.get("/api/v1/oauth/microsoft/start")
def api_oauth_microsoft_start(request: Request):
    from ..oauth import microsoft_auth_url, new_state
    try:
        state = new_state("microsoft")
        url = microsoft_auth_url(_oauth_redirect_uri(request, "microsoft"), state)
        return {"url": url}
    except ValueError as e:
        raise HTTPException(status_code=503, detail=str(e))


@router.get("/api/v1/oauth/microsoft/callback")
def api_oauth_microsoft_callback(request: Request, code: str = "", state: str = ""):
    from fastapi.responses import RedirectResponse
    from ..oauth import consume_state, microsoft_complete_connection
    if not code or not consume_state(state, "microsoft"):
        raise HTTPException(status_code=400, detail="invalid or expired auth state")
    try:
        microsoft_complete_connection(code, _oauth_redirect_uri(request, "microsoft"))
    except Exception as e:
        return HTMLResponse(f"<h1>Connection failed</h1><pre>{e}</pre>")
    return RedirectResponse(url="/app?email_connected=microsoft", status_code=302)


@router.get("/api/v1/oauth/status")
def api_oauth_status():
    """Which email providers are currently connected — used by Settings UI."""
    from ..oauth import status
    return status()


class OAuthDisconnectBody(BaseModel):
    provider: str


@router.post("/api/v1/oauth/disconnect")
def api_oauth_disconnect(body: OAuthDisconnectBody):
    from ..oauth import disconnect
    if body.provider not in ("google", "microsoft"):
        raise HTTPException(status_code=400, detail="unknown provider")
    if not disconnect(body.provider):
        raise HTTPException(status_code=404, detail="not connected")
    return {"ok": True, "provider": body.provider}


class TestEmailBody(BaseModel):
    to: str
    subject: str | None = None
    body: str | None = None


@router.post("/api/v1/email/test")
def api_email_test(body: TestEmailBody, _user: str = Depends(verify_admin)):
    """Send a test email via the connected provider — verifies the OAuth
    setup is working before pointing it at a real customer."""
    from ..oauth import send_email
    result = send_email(
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
    """Manually trigger the campaign runner across all configured practices
    and workflows. Composes due messages and queues them for approval."""
    return run_all_campaigns()


def run_all_campaigns() -> dict[str, Any]:
    """Run campaigns for every practice+workflow pair that has a config file.
    Called both from the API endpoint and the background scheduler."""
    from ..campaign import CampaignRunner
    from ..config import list_practices, list_workflows

    practices = list_practices()
    workflows = list_workflows()
    results: list[dict[str, Any]] = []
    errors: list[str] = []

    for practice_id in practices:
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
                pass  # practice/workflow combo doesn't exist — skip silently
            except Exception as exc:
                errors.append(f"{practice_id}/{workflow_id}: {exc}")

    return {
        "ok": True,
        "ran_at": datetime.utcnow().isoformat(),
        "results": results,
        "errors": errors,
    }
