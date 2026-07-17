"""Guards around every Claude-powered path — keeps one hostile (or broken)
actor from burning the platform's AI budget.

Three layers, all invisible to normal use:

1. Per-contact inbound cooldown — after MAX_REPLIES_PER_HOUR AI replies to
   the same contact within an hour, Adapix stops replying to them and the
   owner gets one push notification. Inbound is still logged for audit.

2. Per-org request rate limit — the interactive AI endpoints (Teach-Adapix
   chat, draft suggestions) allow RATE_LIMIT_PER_MIN calls per org per
   minute. In-memory sliding window; resets on restart, which is fine —
   it's a burst brake, not an accounting system.

3. Per-org daily AI budget — every AI-triggering event increments a daily
   counter (JSON on the persistent volume). At 80% the founder gets one
   alert email for that org that day; at 100% AI paths degrade (inbound
   stops getting AI replies, suggestions fall back to templates) until
   midnight UTC. The cap is set far above anything legitimate: a maxed-out
   day of campaigns + replies + chat is well under 150.
"""
from __future__ import annotations

import json
import os
import threading
import time
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path

MAX_REPLIES_PER_HOUR = 10          # AI replies to ONE contact in one hour
RATE_LIMIT_PER_MIN = 20            # interactive AI requests per org per minute
DAILY_AI_CAP = 500                 # AI events per org per day
ALERT_AT = 0.8                     # founder email at 80% of the cap

_lock = threading.Lock()
_rate_windows: dict[str, deque] = {}


# ---------------------------------------------------------------------------
# 1. Per-contact inbound cooldown
# ---------------------------------------------------------------------------

def contact_reply_throttled(session, patient_id: int) -> bool:
    """True when this contact has already received MAX_REPLIES_PER_HOUR
    outbound texts in the last hour — at that point they're either a bot,
    a loop, or a conversation a human should be having."""
    from .models import Campaign, Message
    cutoff = datetime.utcnow() - timedelta(hours=1)
    n = (
        session.query(Message)
        .join(Campaign, Message.campaign_id == Campaign.id)
        .filter(
            Campaign.patient_id == patient_id,
            Message.direction == "outbound",
            Message.channel == "sms",
            Message.created_at >= cutoff,
        )
        .count()
    )
    return n >= MAX_REPLIES_PER_HOUR


# ---------------------------------------------------------------------------
# 2. Per-org interactive rate limit
# ---------------------------------------------------------------------------

def allow_request(org_id: str) -> bool:
    """Sliding-window burst brake for the interactive AI endpoints."""
    now = time.monotonic()
    with _lock:
        win = _rate_windows.setdefault(org_id, deque())
        while win and now - win[0] > 60:
            win.popleft()
        if len(win) >= RATE_LIMIT_PER_MIN:
            return False
        win.append(now)
        return True


# ---------------------------------------------------------------------------
# 3. Per-org daily AI budget + founder alert
# ---------------------------------------------------------------------------

def _usage_path() -> Path:
    return Path(os.environ.get("ADAPIX_VAR", ".")) / "ai_usage.json"


def _load_usage() -> dict:
    try:
        return json.loads(_usage_path().read_text(encoding="utf-8"))
    except Exception:
        return {}


def record_ai_event(org_id: str, kind: str = "") -> bool:
    """Count one AI-triggering event for this org today. Returns False when
    the org is OVER its daily cap (callers should degrade gracefully).
    Sends the founder one alert email per org per day at 80%."""
    today = datetime.utcnow().strftime("%Y-%m-%d")
    alert_needed = False
    used = 0
    with _lock:
        data = _load_usage()
        # keep the file small — today only survives the prune
        data = {d: v for d, v in data.items() if d == today}
        day = data.setdefault(today, {})
        entry = day.setdefault(org_id, {"n": 0, "alerted": False})
        entry["n"] += 1
        used = entry["n"]
        if used >= int(DAILY_AI_CAP * ALERT_AT) and not entry["alerted"]:
            entry["alerted"] = True
            alert_needed = True
        try:
            _usage_path().write_text(json.dumps(data), encoding="utf-8")
        except Exception:
            pass

    if alert_needed:
        _send_founder_alert(org_id, used)
    return used <= DAILY_AI_CAP


def over_budget(org_id: str) -> bool:
    today = datetime.utcnow().strftime("%Y-%m-%d")
    with _lock:
        n = ((_load_usage().get(today) or {}).get(org_id) or {}).get("n", 0)
    return n > DAILY_AI_CAP


def _send_founder_alert(org_id: str, used: int) -> None:
    """One heads-up email to the founder — abuse should be an email, not a
    surprise invoice. Best-effort; never breaks the calling path."""
    try:
        from .channels import EmailChannel
        from .config import Settings
        from .db import get_session
        from .models import Organization
        to = os.environ.get("ADAPIX_FOUNDER_EMAIL", "roccochenet95@gmail.com")
        with get_session() as s:
            org = s.get(Organization, org_id)
            org_name = org.name if org else org_id
        EmailChannel(Settings()).send(
            to,
            f"Adapix AI usage alert: {org_name} at {used}/{DAILY_AI_CAP} today",
            (
                f"The business \"{org_name}\" (org {org_id}) has used {used} AI calls "
                f"today — {int(used * 100 / DAILY_AI_CAP)}% of its {DAILY_AI_CAP}/day cap.\n\n"
                f"Normal usage rarely passes 150/day, so this is worth a look: it could "
                f"be a genuinely busy day, a customer-side bot texting the line, or a "
                f"stuck loop.\n\n"
                f"At {DAILY_AI_CAP} the org's AI paths degrade automatically until "
                f"midnight UTC (inbound texts stop getting AI replies, draft "
                f"suggestions fall back to templates). Nothing needs to be done to "
                f"stop the spend — this email is so you know it happened."
            ),
            from_name="Adapix",
        )
        print(f"[ai_guard] founder alert sent for org {org_id} at {used} events")
    except Exception as e:
        print(f"[ai_guard] founder alert failed: {e}")
