"""Missed-call text-back — the instant "sorry we missed you" text.

When an INBOUND call to a business's Adapix line ends without a real
conversation — the caller hung up right away, hit voicemail, or a pipeline
error meant the assistant never got a word in — the caller was a live lead
who reached out and got nothing. This drafts a text back to that number so
the lead isn't lost. The draft waits in the Inbox like everything else
Adapix writes: nothing sends without the owner's OK.

Unknown callers become a new lead automatically ("Caller (…1140)") so the
reply conversation has somewhere to land.

Detection is deliberately conservative (empty/near-empty transcript, or an
ended-reason that means the call never really happened). Tuning against
real live calls is still pending with Rocco — until then this can only ever
create a pending-approval draft, never send anything on its own.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from .db import get_session
from .models import Campaign, Message, Organization, Patient
from .phone import normalize_phone

WORKFLOW_ID = "missed_call_textback"

# endedReason prefixes that mean the caller never got a conversation even if
# some transcript text exists (assistant greeting into dead air, etc.).
_FAILURE_REASON_PREFIXES = (
    "pipeline-error",
    "assistant-error",
    "vonage-",            # provider-side failures
    "twilio-failed",
    "voicemail",
    "customer-did-not-answer",
)

# A real conversation produces well over this much transcript; an immediate
# hangup produces nothing or just the assistant's first words.
_MIN_REAL_TRANSCRIPT_CHARS = 40


def _is_missed(transcript: str, ended_reason: str) -> bool:
    if any(ended_reason.startswith(p) for p in _FAILURE_REASON_PREFIXES):
        return True
    return len((transcript or "").strip()) < _MIN_REAL_TRANSCRIPT_CHARS


def maybe_textback(
    *,
    call: dict,
    caller_number: str | None,
    transcript: str,
    ended_reason: str,
) -> str:
    """Called from the Vapi end-of-call webhook for every finished call.
    Returns a short status string for logging. Never raises."""
    try:
        if (call.get("type") or "") != "inboundPhoneCall":
            return "skipped: not an inbound call"
        if not _is_missed(transcript, ended_reason or ""):
            return "skipped: real conversation happened"
        caller = normalize_phone(caller_number or "") or (caller_number or "").strip()
        if not caller:
            return "skipped: no caller number"

        with get_session() as s:
            # Which business was called — Vapi tells us its phone-number id;
            # the dialed number itself is the fallback match.
            vapi_num_id = call.get("phoneNumberId") or ""
            dialed = ((call.get("phoneNumber") or {}).get("number")) or ""
            org = (
                s.query(Organization)
                .filter(
                    (Organization.vapi_phone_number_id == vapi_num_id)
                    if vapi_num_id
                    else (Organization.phone_number == dialed)
                )
                .first()
            )
            if org is None and dialed:
                org = s.query(Organization).filter(Organization.phone_number == dialed).first()
            if org is None:
                return "skipped: no org for called number"

            from .billing import engine_allowed
            allowed, why = engine_allowed(org.id, org.created_at)
            if not allowed:
                return f"skipped: engine paused ({why})"

            patient = (
                s.query(Patient)
                .filter(Patient.practice_id == org.id, Patient.phone == caller)
                .first()
            )
            if patient is not None and patient.opted_out:
                return "skipped: caller opted out"
            if patient is None:
                # A missed call from an unknown number IS a new lead.
                patient = Patient(
                    practice_id=org.id,
                    first_name="Caller",
                    last_name=f"(…{caller[-4:]})",
                    phone=caller,
                    notes="Created automatically from a missed inbound call.",
                )
                s.add(patient)
                s.flush()

            # One draft per caller per day — a retry-dialer must not stack
            # five identical apologies in the Inbox.
            cutoff = datetime.utcnow() - timedelta(hours=24)
            recent = (
                s.query(Message)
                .join(Campaign, Message.campaign_id == Campaign.id)
                .filter(
                    Campaign.patient_id == patient.id,
                    Campaign.workflow_id == WORKFLOW_ID,
                    Message.created_at >= cutoff,
                )
                .first()
            )
            if recent is not None:
                return "skipped: already drafted for this caller today"

            from .practice import load_profile
            prof = load_profile(org.id)
            business = (prof.practice_name or "").strip()
            owner = (prof.doctor or "").strip()
            # PracticeProfile ships placeholder defaults for unconfigured
            # orgs — never let "Dr. at your practice" reach a customer.
            if business.lower() in ("", "your practice"):
                business = (org.name or "").strip()
            if owner.lower() in ("", "dr.", "dr"):
                owner = ""
            intro = f"Hi, it's {owner} at {business}" if owner and business else (
                f"Hi, it's {business}" if business else "Hi"
            )
            body = (
                f"{intro} — sorry we missed your call just now. "
                f"What can we help you with? Reply here and we'll get right back to you."
            )

            camp = Campaign(practice_id=org.id, workflow_id=WORKFLOW_ID, patient_id=patient.id)
            s.add(camp)
            s.flush()
            msg = Message(
                campaign_id=camp.id,
                direction="outbound",
                channel="sms",
                body=body,
                status="pending_approval",
            )
            s.add(msg)
            s.flush()
            return f"drafted text-back (message {msg.id}) for {caller}"
    except Exception as e:
        return f"error: {e}"
