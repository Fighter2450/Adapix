"""The booking loop — a reply that means "yes" becomes an appointment.

Flow (all inside the normal inbound pipeline):
  1. Classifier tags a reply wants_to_book → offer_slots() computes 2-3 real
     openings from the business's own hours minus existing bookings, replies
     with them, and records an "offered" Booking row holding the ISO list.
  2. The customer answers ("Thursday at 1 works") → match_slot_choice() maps
     the text onto the offered slots (Claude, with a keyword fallback) →
     confirm_booking() marks it scheduled, replies with a confirmation, and
     queues a reminder text for the day before via the existing
     scheduled-send loop (an approved Message with scheduled_at).

No external calendar required — the business's hours in their profile ARE
the calendar. Google Calendar sync can layer on later.
"""
from __future__ import annotations

import json
import re
import logging
from datetime import datetime, timedelta
from typing import Any

log = logging.getLogger("adapix.booking")

SLOT_DURATION_MIN = 60
MIN_LEAD_HOURS = 4          # never offer a slot sooner than this
OFFER_DAYS_AHEAD = 7
OFFER_COUNT = 3

_DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


# ---------------------------------------------------------------------------
# Hours parsing + slot generation
# ---------------------------------------------------------------------------

def _parse_window(text: str) -> tuple[int, int] | None:
    """'7am-5pm' / '8:30am - 12pm' -> (start_hour, end_hour) in 24h (whole
    hours; a half-open start rounds up so we never offer before opening)."""
    if not text or "closed" in text.lower():
        return None
    m = re.findall(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)", text.lower())
    if len(m) < 2:
        return None
    def to24(h, mnt, ap):
        h = int(h) % 12
        if ap == "pm":
            h += 12
        return h + (1 if mnt and int(mnt) > 0 else 0)  # round starts up
    start = to24(*m[0])
    end_h = int(m[1][0]) % 12 + (12 if m[1][2] == "pm" else 0)  # end: floor
    return (start, end_h) if end_h > start else None


def _org_hours(org_id: str) -> dict[int, tuple[int, int]]:
    """weekday-index -> (open_hour, close_hour). Falls back to 9-5 Mon-Fri."""
    from .db import get_session
    from .models import OrgProfile
    with get_session() as s:
        prof = s.query(OrgProfile).filter(OrgProfile.org_id == org_id).first()
        practice = ((prof.data or {}).get("practice") or {}) if prof else {}
    wk = _parse_window(practice.get("hours_weekday") or "") or (9, 17)
    sat = _parse_window(practice.get("hours_saturday") or "")
    sun = _parse_window(practice.get("hours_sunday") or "")
    hours: dict[int, tuple[int, int]] = {i: wk for i in range(5)}
    if sat:
        hours[5] = sat
    if sun:
        hours[6] = sun
    return hours


def available_slots(org_id: str, *, count: int = OFFER_COUNT,
                    now: datetime | None = None) -> list[datetime]:
    """Next open hour-slots inside business hours, avoiding existing
    bookings, spread across different days where possible."""
    from .db import get_session
    from .models import Booking
    now = now or datetime.utcnow()
    hours = _org_hours(org_id)
    earliest = now + timedelta(hours=MIN_LEAD_HOURS)
    with get_session() as s:
        taken = {
            b.start_at.replace(minute=0, second=0, microsecond=0)
            for b in s.query(Booking).filter(
                Booking.practice_id == org_id,
                Booking.status == "scheduled",
                Booking.start_at != None,  # noqa: E711
                Booking.start_at >= now,
            ).all()
        }
    slots: list[datetime] = []
    days_used: set[str] = set()
    for d in range(OFFER_DAYS_AHEAD + 1):
        day = (now + timedelta(days=d)).date()
        win = hours.get(day.weekday())
        if not win:
            continue
        # Vary the time of day across the offer (morning / midday / late) so
        # three options don't all read "7am" — that's a robot tell.
        lo, hi = win
        preferred = [lo, (lo + hi) // 2, max(lo, hi - 2)][len(slots) % 3]
        order = sorted(range(lo, hi), key=lambda h: abs(h - preferred))
        for h in order:
            t = datetime(day.year, day.month, day.day, h)
            if t < earliest or t in taken:
                continue
            if day.isoformat() in days_used and len(slots) + (OFFER_DAYS_AHEAD - d) >= count:
                continue
            slots.append(t)
            days_used.add(day.isoformat())
            break  # next day
        if len(slots) >= count:
            break
    # not enough distinct days? fill from the first open day's later hours
    if len(slots) < count:
        for d in range(OFFER_DAYS_AHEAD + 1):
            day = (now + timedelta(days=d)).date()
            win = hours.get(day.weekday())
            if not win:
                continue
            for h in range(win[0], win[1]):
                t = datetime(day.year, day.month, day.day, h)
                if t >= earliest and t not in taken and t not in slots:
                    slots.append(t)
                    if len(slots) >= count:
                        return slots
    return slots[:count]


def fmt_slot(t: datetime) -> str:
    hour = t.strftime("%I%p").lstrip("0").lower().replace(":00", "")
    return f"{_DAYS[t.weekday()]} {t.strftime('%b')} {t.day} at {hour}"


# ---------------------------------------------------------------------------
# Offer + confirm
# ---------------------------------------------------------------------------

def get_pending_offer(session, org_id: str, patient_id: int):
    from .models import Booking
    return (
        session.query(Booking)
        .filter(Booking.practice_id == org_id,
                Booking.patient_id == patient_id,
                Booking.status == "offered")
        .order_by(Booking.created_at.desc())
        .first()
    )


def offer_slots(session, org_id: str, patient, campaign) -> str | None:
    """Create/refresh the offered-Booking row and return the reply text.
    Returns None when the business has no open slots (caller escalates)."""
    from .models import Booking
    slots = available_slots(org_id)
    if not slots:
        return None
    existing = get_pending_offer(session, org_id, patient.id)
    row = existing or Booking(practice_id=org_id, patient_id=patient.id,
                              campaign_id=campaign.id if campaign else None,
                              status="offered")
    row.note = json.dumps([t.isoformat() for t in slots])
    if existing is None:
        session.add(row)
    session.flush()
    opts = ", ".join(fmt_slot(t) for t in slots[:-1]) + (f", or {fmt_slot(slots[-1])}" if len(slots) > 1 else "")
    first = (patient.first_name or "").strip()
    return (f"{'Great news, ' + first + ' — ' if first else ''}let's get you on the schedule. "
            f"I have {opts}. Which works best? (If none do, tell me what does.)")


def match_slot_choice(inbound_text: str, offered_iso: list[str]) -> str | None:
    """Which offered slot did the reply pick? Claude first, keyword fallback.
    Returns the ISO string or None."""
    slots = [datetime.fromisoformat(x) for x in offered_iso]
    choice = _match_with_claude(inbound_text, slots)
    if choice is not None:
        return choice
    return _match_keywords(inbound_text, slots)


def _match_with_claude(text: str, slots: list[datetime]) -> str | None:
    try:
        from anthropic import Anthropic
        from .config import Settings
        s = Settings()
        listing = "\n".join(f"{i+1}. {fmt_slot(t)} (iso: {t.isoformat()})" for i, t in enumerate(slots))
        resp = Anthropic(api_key=s.anthropic_api_key).messages.create(
            model=s.adapix_model, max_tokens=120,
            system=("A customer was offered these appointment slots:\n" + listing +
                    "\nDecide which ONE slot their message picks, if any. Output ONLY one-line JSON: "
                    '{"picked_iso": "<iso or null>"} — null when they picked none, declined, or asked for different times.'),
            messages=[{"role": "user", "content": text[:500]}],
        )
        raw = resp.content[0].text.strip()
        data = json.loads(raw[raw.find("{"):raw.rfind("}") + 1])
        picked = data.get("picked_iso")
        if picked and any(t.isoformat() == picked for t in slots):
            return picked
    except Exception as e:
        log.warning("slot-choice LLM match failed, falling back: %s", e)
    return None


def _match_keywords(text: str, slots: list[datetime]) -> str | None:
    low = text.lower()
    scored = []
    for t in slots:
        score = 0
        if _DAYS[t.weekday()].lower() in low:
            score += 2
        h12 = int(t.strftime("%I"))
        if re.search(rf"\b{h12}\s*(am|pm|o'?clock)?\b", low):
            score += 1
        if score:
            scored.append((score, t))
    if not scored:
        # bare unambiguous yes with exactly one option
        if len(slots) == 1 and re.search(r"\b(yes|yep|sure|works|perfect|ok(ay)?)\b", low):
            return slots[0].isoformat()
        return None
    scored.sort(key=lambda x: -x[0])
    if len(scored) > 1 and scored[0][0] == scored[1][0]:
        return None  # ambiguous
    return scored[0][1].isoformat()


def confirm_booking(session, offer_row, picked_iso: str, org_id: str, patient) -> str:
    """Flip the offer to scheduled, queue the day-before reminder, and return
    the confirmation reply text."""
    from .models import Message
    start = datetime.fromisoformat(picked_iso)
    offer_row.status = "scheduled"
    offer_row.start_at = start
    offer_row.note = None
    # Reminder the day before (or skip if the slot is <26h away) — rides the
    # existing approved+scheduled_at send loop.
    remind_at = start - timedelta(hours=24)
    if remind_at > datetime.utcnow() + timedelta(hours=2) and offer_row.campaign_id:
        from .practice import load_profile
        biz = ""
        try:
            biz = (load_profile(org_id).practice_name or "").strip()
        except Exception:
            pass
        first = (patient.first_name or "").strip()
        session.add(Message(
            campaign_id=offer_row.campaign_id,
            direction="outbound",
            channel="sms",
            body=(f"Hi {first}, " if first else "Hi, ") +
                 f"quick reminder: {biz or 'we'}" + (" are" if not biz else " is") +
                 f" booked for you tomorrow, {fmt_slot(start)}. Reply here if anything changes!",
            status="approved",
            scheduled_at=remind_at,
            metadata_json={"booking_id": offer_row.id, "booking_reminder": True, "autopilot": True},
        ))
    session.flush()
    first = (patient.first_name or "").strip()
    return ((f"Perfect{', ' + first if first else ''} — you're booked for "
             f"{fmt_slot(start)}. ") +
            "I'll send a reminder the day before. See you then!")
