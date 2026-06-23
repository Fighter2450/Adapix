"""Adapix admin web interface (FastAPI).

Run with:
    uvicorn adapix.api.main:app --reload --port 8000

Mounts:
  GET  /                           - admin dashboard         (auth required)
  GET  /approvals                  - pending-approval queue  (auth required)
  POST /approvals/{id}/approve     - approve & send          (auth required)
  POST /approvals/{id}/reject      - reject                  (auth required)
  POST /webhooks/twilio/sms        - inbound SMS (Twilio signed, NO admin auth)
  POST /webhooks/dev/sms           - dev simulator           (NO admin auth)

Auth is HTTP Basic. Admin user/pass come from .env. If both unset,
auth is disabled (dev convenience).
"""
from __future__ import annotations

from pathlib import Path

from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ..approval import ApprovalManager
from ..db import get_session, init_db
from ..models import Campaign, EscalationEvent, Message, Patient
from .app_routes import router as app_router
from .auth import verify_admin
from .webhooks import router as webhooks_router

TEMPLATE_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))


def create_app() -> FastAPI:
    app = FastAPI(title="Adapix Admin")

    # Static assets (logo, favicon, fonts). Served unauthenticated so the
    # logo can load on /welcome before the user has any credentials.
    STATIC_DIR.mkdir(exist_ok=True)
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    # Webhooks router has its own (Twilio signature) auth — DO NOT wrap it
    # with admin Basic auth, or Twilio can't reach it.
    app.include_router(webhooks_router)

    # Mobile PWA + JSON API (the surgeon-facing companion app at /app).
    # Auth is enforced inside app_routes via Depends(verify_admin) on every
    # JSON endpoint. The HTML shell loads without auth (no PHI in it).
    app.include_router(app_router)

    @app.on_event("startup")
    def _startup() -> None:
        init_db()

    # ------------------------------------------------------------------
    # Admin dashboard (auth required)
    # ------------------------------------------------------------------
    @app.get("/", response_class=HTMLResponse)
    def index(request: Request, _user: str = Depends(verify_admin)):
        with get_session() as s:
            patients = s.query(Patient).order_by(Patient.created_at.desc()).limit(100).all()
            campaigns = s.query(Campaign).order_by(Campaign.started_at.desc()).limit(100).all()
            messages = s.query(Message).order_by(Message.created_at.desc()).limit(50).all()
            escalations = (
                s.query(EscalationEvent)
                .filter(EscalationEvent.resolved == False)  # noqa: E712
                .order_by(EscalationEvent.created_at.desc())
                .limit(50)
                .all()
            )
            open_count = (
                s.query(EscalationEvent)
                .filter(EscalationEvent.resolved == False)  # noqa: E712
                .count()
            )
            pending_count = (
                s.query(Message).filter(Message.status == "pending_approval").count()
            )
            counts = {
                "patients": s.query(Patient).count(),
                "campaigns": s.query(Campaign).count(),
                "messages": s.query(Message).count(),
                "open_escalations": open_count,
                "pending_approvals": pending_count,
            }
        return templates.TemplateResponse(
            "index.html",
            {
                "request": request,
                "patients": patients,
                "campaigns": campaigns,
                "messages": messages,
                "escalations": escalations,
                "counts": counts,
            },
        )

    # ------------------------------------------------------------------
    # Approvals (auth required)
    # ------------------------------------------------------------------
    @app.get("/approvals", response_class=HTMLResponse)
    def approvals_page(request: Request, _user: str = Depends(verify_admin)):
        mgr = ApprovalManager()
        pending = mgr.list_pending()
        return templates.TemplateResponse(
            "approvals.html",
            {"request": request, "pending": pending},
        )

    @app.post("/approvals/{message_id}/approve")
    def approve(
        message_id: int,
        edited_body: str = Form(default=""),
        _user: str = Depends(verify_admin),
    ):
        mgr = ApprovalManager()
        body = edited_body if edited_body.strip() else None
        mgr.approve_and_send(message_id, edited_body=body)
        return RedirectResponse(url="/approvals", status_code=303)

    @app.post("/approvals/{message_id}/reject")
    def reject(
        message_id: int,
        reason: str = Form(default=""),
        _user: str = Depends(verify_admin),
    ):
        mgr = ApprovalManager()
        mgr.reject(message_id, reason=reason or None)
        return RedirectResponse(url="/approvals", status_code=303)

    return app


app = create_app()
