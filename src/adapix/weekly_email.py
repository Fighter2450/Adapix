"""Weekly money email — Monday morning proof that the $99 is working.

"This week: 12 follow-ups, 4 replies, 1 job won ($1,850)." Sent to the
owner's login email every Monday at 9am America/New_York, but only when
the week actually had activity — a dormant trial gets silence, not spam.
Same hourly-scheduler + stamp-dedupe shape as the daily digest push, so
a scheduler tick can never double-send.

This is product email FROM Adapix TO the owner (not business email to a
customer), so it goes through the shared Resend sender directly.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

WEEKLY_DOW = 0          # Monday
WEEKLY_HOUR_LOCAL = 9   # 9am ET — after the 8am daily digest


def _week_stats(org_id: str) -> dict | None:
    """Last 7 days of real counts. None when the week was completely dead."""
    from sqlalchemy.orm import Session

    from .api.app_routes import _load_org_profile_data
    from .db import get_engine
    from .models import Campaign, Message

    week_ago = datetime.utcnow() - timedelta(days=7)
    with Session(get_engine()) as s:
        rows = (
            s.query(Message.direction, Message.status, Message.channel)
            .join(Campaign, Message.campaign_id == Campaign.id)
            .filter(Campaign.practice_id == org_id, Message.created_at >= week_ago)
            .all()
        )
        data = _load_org_profile_data(s, org_id)

    sent = sum(1 for d, st, _ in rows if d == "outbound" and st in ("sent", "delivered", "replied"))
    replies = sum(1 for d, _, _ in rows if d == "inbound")
    calls = sum(1 for d, st, ch in rows if ch == "call" and d == "outbound" and st in ("sent", "delivered", "replied"))
    wins = [w for w in (data.get("wins") or []) if (w.get("at") or "") >= week_ago.isoformat()]
    won_total = sum(w.get("amount") or 0 for w in wins)

    if not (sent or replies or wins):
        return None
    return {
        "biz": (data.get("practice") or {}).get("name") or "your business",
        "sent": sent, "replies": replies, "calls": calls,
        "wins": len(wins), "won_total": won_total,
    }


def _email_text(st: dict) -> tuple[str, str]:
    bits = [f"{st['sent']} follow-up{'s' if st['sent'] != 1 else ''}"]
    if st["replies"]:
        bits.append(f"{st['replies']} repl{'ies' if st['replies'] != 1 else 'y'}")
    if st["wins"]:
        bits.append(f"${st['won_total']:,.0f} won back")
    subject = "Your week with Adapix: " + ", ".join(bits)

    lines = [f"Here's what Adapix did for {st['biz']} this week:", ""]
    lines.append(f"  - {st['sent']} follow-up{'s' if st['sent'] != 1 else ''} sent")
    lines.append(f"  - {st['replies']} repl{'ies' if st['replies'] != 1 else 'y'} from customers")
    if st["calls"]:
        lines.append(f"  - {st['calls']} call{'s' if st['calls'] != 1 else ''} placed")
    if st["wins"]:
        lines.append(f"  - {st['wins']} job{'s' if st['wins'] != 1 else ''} won back — ${st['won_total']:,.0f}")
    lines += ["",
              "Full breakdown (by channel, reply rates, trends): https://app.adapixai.com/app — Analytics.",
              "",
              "— Adapix"]
    return subject, "\n".join(lines)


def run_weekly_emails() -> int:
    """Called from the hourly scheduler tick. Returns emails sent."""
    from sqlalchemy.orm import Session

    from .api.app_routes import _load_org_profile_data, _save_org_profile_data
    from .channels import EmailChannel
    from .config import Settings
    from .db import get_engine
    from .models import Organization, User

    local = datetime.now(ZoneInfo("America/New_York"))
    if local.weekday() != WEEKLY_DOW or local.hour != WEEKLY_HOUR_LOCAL:
        return 0
    week_stamp = local.strftime("%G-W%V")

    settings = Settings()
    if not settings.resend_api_key:
        return 0

    sent_count = 0
    with Session(get_engine()) as s:
        org_ids = [o.id for o in s.query(Organization).all()]
    for org_id in org_ids:
        with Session(get_engine()) as s:
            data = _load_org_profile_data(s, org_id)
            if data.get("paused") or data.get("weekly_email_week") == week_stamp:
                continue
            owner = s.query(User).filter(User.org_id == org_id, User.role == "owner").first()
            to = owner.email if owner else None
            # Stamp BEFORE sending — a send that errors must not retry every
            # hourly tick and spam the owner.
            data["weekly_email_week"] = week_stamp
            _save_org_profile_data(s, org_id, data)
            s.commit()
        if not to:
            continue
        st = _week_stats(org_id)
        if st is None:
            continue
        subject, body = _email_text(st)
        r = EmailChannel(settings).send(to, subject, body, from_name="Adapix")
        if r.status == "sent":
            sent_count += 1
            print(f"[adapix] weekly email sent to org {org_id}")
    return sent_count
