"""The learning loop — Adapix studies its own results and adjusts.

v1 learns one thing that compounds: WHEN this business's customers actually
reply. Reply attribution: an outbound sent message "got a reply" when an
inbound message lands in the same campaign within 48 hours after it.

Sample-size guards keep it honest — with thin data it says "still learning"
and changes nothing. With enough data (>=20 sends, >=5 replies, and a
candidate hour with >=8 sends), autopilot sends stop firing whenever the
5-minute engine pass happens to run and instead schedule themselves at the
learned best hour. Humans can't do this; that's the point.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

WINDOW_DAYS = 90
REPLY_ATTRIBUTION_HOURS = 48
MIN_SENDS_TOTAL = 20
MIN_REPLIES_TOTAL = 5
MIN_SENDS_PER_BUCKET = 8

_DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def stats(org_id: str) -> dict[str, Any]:
    """Reply-rate breakdown by send hour / channel / weekday + the learned
    recommendations (None until the data supports them)."""
    from .db import get_session
    from .models import Campaign, Message

    since = datetime.utcnow() - timedelta(days=WINDOW_DAYS)
    with get_session() as s:
        camp_ids = [c.id for c in s.query(Campaign).filter(Campaign.practice_id == org_id).all()]
        if not camp_ids:
            return _empty()
        msgs = (
            s.query(Message)
            .filter(Message.campaign_id.in_(camp_ids), Message.created_at >= since)
            .order_by(Message.created_at.asc())
            .all()
        )
    outbound = [m for m in msgs if m.direction == "outbound"
                and m.status in ("sent", "delivered", "replied")]
    inbound_by_campaign: dict[int, list[datetime]] = {}
    for m in msgs:
        if m.direction == "inbound" and m.created_at:
            inbound_by_campaign.setdefault(m.campaign_id, []).append(m.created_at)

    def replied(m) -> bool:
        if not m.created_at:
            return False
        horizon = m.created_at + timedelta(hours=REPLY_ATTRIBUTION_HOURS)
        return any(m.created_at < t <= horizon for t in inbound_by_campaign.get(m.campaign_id, []))

    by_hour: dict[int, list[int]] = {}
    by_channel: dict[str, list[int]] = {}
    by_weekday: dict[int, list[int]] = {}
    total_sends, total_replies = 0, 0
    for m in outbound:
        r = 1 if replied(m) else 0
        total_sends += 1
        total_replies += r
        if m.created_at:
            by_hour.setdefault(m.created_at.hour, []).append(r)
            by_weekday.setdefault(m.created_at.weekday(), []).append(r)
        by_channel.setdefault(m.channel or "sms", []).append(r)

    def bucketize(d: dict) -> list[dict]:
        return sorted(
            ({"key": k, "sends": len(v), "replies": sum(v),
              "rate": round(sum(v) / len(v), 3) if v else 0.0} for k, v in d.items()),
            key=lambda x: -x["rate"])

    enough = total_sends >= MIN_SENDS_TOTAL and total_replies >= MIN_REPLIES_TOTAL

    def best(buckets: list[dict]):
        qualified = [b for b in buckets if b["sends"] >= MIN_SENDS_PER_BUCKET]
        return qualified[0] if (enough and qualified) else None

    hour_buckets = bucketize(by_hour)
    chan_buckets = bucketize(by_channel)
    day_buckets = bucketize(by_weekday)
    bh, bc, bd = best(hour_buckets), best(chan_buckets), best(day_buckets)

    return {
        "sends": total_sends,
        "replies": total_replies,
        "reply_rate": round(total_replies / total_sends, 3) if total_sends else 0.0,
        "enough_data": enough,
        "by_hour": hour_buckets,
        "by_channel": chan_buckets,
        "by_weekday": day_buckets,
        "best_hour": bh["key"] if bh else None,
        "best_hour_rate": bh["rate"] if bh else None,
        "best_channel": bc["key"] if bc else None,
        "best_weekday": _DAYS[bd["key"]] if bd else None,
        "insight": _insight_line(bh, bc, bd),
    }


def _insight_line(bh, bc, bd) -> str | None:
    parts = []
    if bh:
        h = bh["key"] % 12 or 12
        ampm = "am" if bh["key"] < 12 else "pm"
        parts.append(f"messages sent around {h}{ampm} get replies {round(bh['rate']*100)}% of the time")
    if bc and bc["rate"] > 0:
        parts.append(f"{bc['key']} is your best channel ({round(bc['rate']*100)}% reply rate)")
    if bd:
        parts.append(f"{_DAYS[bd['key']]}s reply best")
    if not parts:
        return None
    return ("What I've learned about your customers: " + "; ".join(parts) +
            ". I'm timing autopilot follow-ups to match.")


def _empty() -> dict[str, Any]:
    return {"sends": 0, "replies": 0, "reply_rate": 0.0, "enough_data": False,
            "by_hour": [], "by_channel": [], "by_weekday": [],
            "best_hour": None, "best_hour_rate": None, "best_channel": None,
            "best_weekday": None, "insight": None}


def best_send_hour(org_id: str) -> int | None:
    """The single number the engine consumes. None = not enough data yet."""
    try:
        return stats(org_id)["best_hour"]
    except Exception:
        return None


def next_occurrence(hour: int, now: datetime | None = None) -> datetime:
    """The next time it's <hour>:00 — today if still ahead, else tomorrow."""
    now = now or datetime.utcnow()
    candidate = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate
