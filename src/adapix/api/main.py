"""Adapix SaaS web application (FastAPI)."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ..approval import ApprovalManager
from ..db import get_session, init_db
from ..models import Campaign, EscalationEvent, Message, Patient
from .app_routes import router as app_router, run_all_campaigns
from .auth import verify_admin
from .auth_routes import router as auth_router
from .webhooks import router as webhooks_router

log = logging.getLogger("adapix.scheduler")

TEMPLATE_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))


def create_app() -> FastAPI:
    app = FastAPI(title="Adapix Admin")

    # Static assets (logo, favicon, fonts). Served unauthenticated so the
    # logo can load on /welcome before the user has any credentials.
    STATIC_DIR.mkdir(exist_ok=True)
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    # Auth routes (signup, login, logout) — public, no auth required
    app.include_router(auth_router)

    # Webhooks — Twilio signature auth, never wrap with session auth
    app.include_router(webhooks_router)

    # PWA + JSON API
    app.include_router(app_router)

    @app.on_event("startup")
    async def _startup() -> None:
        init_db()
        asyncio.create_task(_campaign_loop())
        asyncio.create_task(_automation_loop())
        asyncio.create_task(_digest_loop())
        asyncio.create_task(_scheduled_send_loop())
        asyncio.create_task(_blooio_poll_loop())

    async def _campaign_loop() -> None:
        """Run campaigns every 5 minutes in the background."""
        await asyncio.sleep(10)
        while True:
            try:
                log.info("Running scheduled campaign pass...")
                # Blocking Claude calls must not freeze webhooks/health checks.
                result = await asyncio.to_thread(run_all_campaigns)
                total = sum(r.get("composed", 0) for r in result.get("results", []))
                log.info(f"Campaign pass complete — {total} messages composed.")
            except Exception as exc:
                log.error(f"Campaign scheduler error: {exc}")
            await asyncio.sleep(300)

    async def _digest_loop() -> None:
        """Once-daily push per org with what's waiting and what's been won —
        checked hourly, dedup'd by a date stamp so it only actually sends once."""
        await asyncio.sleep(45)  # offset from the other two loops
        while True:
            try:
                from ..digest import run_daily_digests
                sent = await asyncio.to_thread(run_daily_digests)
                if sent:
                    log.info(f"Daily digest: sent to {sent} org(s).")
            except Exception as exc:
                log.error(f"Digest scheduler error: {exc}")
            await asyncio.sleep(3600)

    async def _scheduled_send_loop() -> None:
        """Places scheduled calls and sends scheduled messages once they're
        due — the manual-scheduling feature (Write a message "Send at",
        Queue a call "Call at"). Checked every 2 minutes across every org;
        send_approved() itself skips anything not yet due or outside quiet
        hours, so an early pass is a harmless no-op."""
        await asyncio.sleep(20)  # offset from the other loops
        while True:
            try:
                sent = await asyncio.to_thread(ApprovalManager().send_approved)
                if sent:
                    log.info(f"Scheduled-send sweep: {sent} message(s)/call(s) dispatched.")
            except Exception as exc:
                log.error(f"Scheduled-send scheduler error: {exc}")
            await asyncio.sleep(120)

    async def _blooio_poll_loop() -> None:
        """Poll Blooio for inbound texts every 2 minutes — the safety net
        under their webhooks, which were observed (7/16) not delivering
        real inbound events at all. Idempotent vs. the webhook path via
        Message.provider_id dedupe."""
        await asyncio.sleep(60)  # offset from the other loops
        while True:
            try:
                from ..blooio_poll import poll_blooio_inbound
                n = await asyncio.to_thread(poll_blooio_inbound)
                if n:
                    log.info(f"Blooio poll: {n} inbound message(s) processed.")
            except Exception as exc:
                log.error(f"Blooio poll error: {exc}")
            await asyncio.sleep(120)

    async def _automation_loop() -> None:
        """Check every 5 minutes for automations whose cron schedule is due."""
        await asyncio.sleep(30)  # slight offset from campaign loop
        while True:
            try:
                from ..automations import get_due_automations, run_automation
                import threading
                due = get_due_automations()
                for aid in due:
                    log.info(f"Automation {aid} is due — launching...")
                    threading.Thread(target=run_automation, args=(aid,), daemon=True).start()
            except Exception as exc:
                log.error(f"Automation scheduler error: {exc}")
            await asyncio.sleep(300)

    # ------------------------------------------------------------------
    # Root — redirect to app (login redirect handled by app_routes)
    # ------------------------------------------------------------------
    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        return RedirectResponse(url="/app", status_code=302)

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

    @app.get("/health")
    def health():
        """Railway healthcheck endpoint — returns 200 when the server is ready."""
        return {"ok": True}

    return app


app = create_app()
