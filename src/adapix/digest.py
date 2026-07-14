"""Daily digest push — the one thing that makes Adapix tell the owner
something instead of waiting to be asked.

Runs once a day per org (default 8am America/New_York, until per-org
timezones exist — matches the quiet-hours default already used for sends).
Dedup is a date stamp in the org profile blob so an hourly scheduler tick
can safely call this without double-sending.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

DIGEST_HOUR_LOCAL = 8


def _due_orgs(now_utc: datetime) -> list[tuple[str, dict]]:
    from sqlalchemy.orm import Session
    from .db import get_engine
    from .models import Organization
    from .api.app_routes import _load_org_profile_data

    local_hour = datetime.now(ZoneInfo("America/New_York")).hour
    if local_hour != DIGEST_HOUR_LOCAL:
        return []

    today = now_utc.date().isoformat()
    out = []
    with Session(get_engine()) as s:
        for org in s.query(Organization).all():
            data = _load_org_profile_data(s, org.id)
            if data.get("paused"):
                continue
            if data.get("last_digest_date") == today:
                continue
            out.append((org.id, data))
    return out


def _org_digest(org_id: str) -> dict | None:
    """The numbers for one org's digest. None if there's nothing worth saying."""
    from sqlalchemy.orm import Session
    from sqlalchemy import func
    from .db import get_engine
    from .models import Campaign, EscalationEvent, Message

    now = datetime.utcnow()
    week_ago = now - timedelta(days=7)
    two_weeks_ago = now - timedelta(days=14)

    with Session(get_engine()) as s:
        drafts = (
            s.query(Message)
            .join(Campaign, Message.campaign_id == Campaign.id)
            .filter(Campaign.practice_id == org_id, Message.status == "pending_approval")
            .count()
        )
        escalations = (
            s.query(EscalationEvent)
            .join(Campaign, EscalationEvent.campaign_id == Campaign.id)
            .filter(Campaign.practice_id == org_id, EscalationEvent.resolved == False)  # noqa: E712
            .count()
        )
        oldest_draft = (
            s.query(func.min(Message.created_at))
            .join(Campaign, Message.campaign_id == Campaign.id)
            .filter(Campaign.practice_id == org_id, Message.status == "pending_approval")
            .scalar()
        )
        from .api.app_routes import _load_org_profile_data
        data = _load_org_profile_data(s, org_id)
        wins = data.get("wins") or []
        this_week = sum(w.get("amount") or 0 for w in wins if (w.get("at") or "") >= week_ago.isoformat())
        last_week = sum(
            w.get("amount") or 0 for w in wins
            if two_weeks_ago.isoformat() <= (w.get("at") or "") < week_ago.isoformat()
        )

    if not (drafts or escalations or this_week):
        return None  # nothing worth pinging about

    stale_hours = int((now - oldest_draft).total_seconds() / 3600) if oldest_draft else 0
    return {
        "drafts": drafts, "escalations": escalations,
        "stale_hours": stale_hours,
        "won_this_week": this_week, "won_last_week": last_week,
    }


def _digest_text(d: dict) -> tuple[str, str]:
    parts = []
    if d["escalations"]:
        parts.append(f"{d['escalations']} waiting on you")
    if d["drafts"]:
        stale = f" ({d['stale_hours']}h old)" if d["stale_hours"] >= 24 else ""
        parts.append(f"{d['drafts']} draft{'s' if d['drafts'] != 1 else ''} to review{stale}")
    if d["won_this_week"]:
        trend = ""
        if d["won_last_week"]:
            trend = " (up)" if d["won_this_week"] > d["won_last_week"] else (" (down)" if d["won_this_week"] < d["won_last_week"] else "")
        parts.append(f"${d['won_this_week']:,.0f} won back this week{trend}")
    title = "Your Adapix digest"
    body = " · ".join(parts) if parts else "All caught up."
    return title, body


def run_daily_digests() -> int:
    """Called from the scheduler loop. Returns how many digests were sent."""
    from .notifications import push_notification
    from sqlalchemy.orm import Session
    from .db import get_engine
    from .api.app_routes import _load_org_profile_data, _save_org_profile_data

    now = datetime.utcnow()
    sent = 0
    for org_id, _profile in _due_orgs(now):
        d = _org_digest(org_id)
        with Session(get_engine()) as s:
            data = _load_org_profile_data(s, org_id)
            data["last_digest_date"] = now.date().isoformat()
            _save_org_profile_data(s, org_id, data)
            s.commit()
        if d is None:
            continue
        title, body = _digest_text(d)
        try:
            push_notification(title=title, body=body, url="/app", tag="adapix-digest", org_id=org_id)
            sent += 1
        except Exception:
            pass
    return sent
