"""Outbound message channels (SMS via Twilio, email via Resend).

These are the actual "send" implementations the ApprovalManager calls into.
Each channel exposes a uniform send() interface that returns a SendResult,
making the rest of the code provider-agnostic.

Both channels respect dry_run mode (used in tests + when the practice
hasn't configured credentials yet) — they return status='skipped:dry_run'
or 'skipped:no_credentials' so the message state machine still advances
cleanly without actually contacting Twilio/Resend.

The TCPA opt-out language ("Reply STOP to opt out.") is auto-appended to
EVERY outbound SMS at this layer so we cannot accidentally ship a message
without it. Twilio also auto-handles STOP/UNSTOP/HELP server-side.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .config import Settings


# ---------------------------------------------------------------------------
# Result envelope returned by every channel.send()
# ---------------------------------------------------------------------------
@dataclass
class SendResult:
    status: str                 # "sent" | "failed" | "skipped:<reason>"
    provider_id: str | None = None   # Twilio MessageSid / Resend message id
    error: str | None = None    # human-readable error if status == "failed"


# ---------------------------------------------------------------------------
# Phone number normalization — Twilio wants E.164 ("+15551234567")
# ---------------------------------------------------------------------------
def _to_e164(phone: str, default_country_code: str = "1") -> str | None:
    """Normalize a phone string into E.164. Returns None if we can't.

    Accepts:
      "(412) 555-0100"        -> "+14125550100"
      "412-555-0100"          -> "+14125550100"
      "4125550100"            -> "+14125550100"
      "+1 412 555 0100"       -> "+14125550100"
      "+44 20 7946 0958"      -> "+442079460958"
    """
    if not phone:
        return None
    digits = re.sub(r"[^\d+]", "", phone)
    if digits.startswith("+"):
        out = "+" + re.sub(r"\D", "", digits[1:])
        return out if len(out) >= 8 else None
    only_digits = re.sub(r"\D", "", digits)
    if len(only_digits) == 10:
        return f"+{default_country_code}{only_digits}"
    if len(only_digits) == 11 and only_digits.startswith(default_country_code):
        return f"+{only_digits}"
    return None  # Don't guess — better to fail than text a wrong number.


# ---------------------------------------------------------------------------
# SMS — Twilio
# ---------------------------------------------------------------------------
OPT_OUT_FOOTER = "\n\nReply STOP to opt out."


class SmsChannel:
    """Twilio-backed SMS sender."""

    def __init__(self, settings: Settings | None = None, *, dry_run: bool = False):
        self.settings = settings or Settings()
        self.dry_run = dry_run

        # Only import the SDK if we'll actually use it — keeps the rest of
        # the app importable when twilio isn't installed.
        self._client = None
        if not self.dry_run and self._has_credentials():
            try:
                from twilio.rest import Client
                self._client = Client(
                    self.settings.twilio_account_sid,
                    self.settings.twilio_auth_token,
                )
            except Exception as e:
                print(f"[sms] Twilio client init failed: {e}")
                self._client = None

    def _has_credentials(self) -> bool:
        s = self.settings
        return bool(s.twilio_account_sid and s.twilio_auth_token and s.twilio_from_number)

    def send(self, to: str, body: str) -> SendResult:
        # 1. Sanity checks
        if not body.strip():
            return SendResult(status="failed", error="empty body")

        normalized = _to_e164(to)
        if not normalized:
            return SendResult(
                status="failed",
                error=f"could not normalize phone number: {to!r}",
            )

        # 2. Always append the opt-out footer
        full_body = body.rstrip() + OPT_OUT_FOOTER

        # 3. Short-circuit modes
        if self.dry_run:
            print(f"[sms:dry_run] -> {normalized}: {full_body!r}")
            return SendResult(status="skipped:dry_run")
        if not self._client:
            print(f"[sms:no_creds] -> {normalized}: {full_body!r}")
            return SendResult(status="skipped:no_credentials")

        # 4. Real send via Twilio
        try:
            msg = self._client.messages.create(
                to=normalized,
                from_=self.settings.twilio_from_number,
                body=full_body,
            )
            return SendResult(status="sent", provider_id=msg.sid)
        except Exception as e:
            return SendResult(status="failed", error=str(e))


# ---------------------------------------------------------------------------
# Email — Resend
# ---------------------------------------------------------------------------
class EmailChannel:
    """Resend-backed email sender."""

    def __init__(self, settings: Settings | None = None, *, dry_run: bool = False):
        self.settings = settings or Settings()
        self.dry_run = dry_run
        self._client = None
        if not self.dry_run and self._has_credentials():
            try:
                import resend
                resend.api_key = self.settings.resend_api_key
                self._client = resend
            except Exception as e:
                print(f"[email] Resend client init failed: {e}")
                self._client = None

    def _has_credentials(self) -> bool:
        s = self.settings
        return bool(s.resend_api_key and s.resend_from_email)

    def send(self, to: str, subject: str, body: str) -> SendResult:
        if not body.strip():
            return SendResult(status="failed", error="empty body")
        if not to or "@" not in to:
            return SendResult(status="failed", error=f"invalid email address: {to!r}")

        if self.dry_run:
            print(f"[email:dry_run] -> {to}: {subject!r} / {body!r}")
            return SendResult(status="skipped:dry_run")

        # Prefer OAuth-connected practice email if the practice owner has
        # connected their Gmail or Microsoft account. Emails will be sent
        # FROM their actual email address (patient sees Dr. Patel, not bot).
        try:
            from .oauth import send_email as oauth_send
            r = oauth_send(to=to, subject=subject or "A note from your practice", body=body)
            if r.get("ok"):
                return SendResult(status="sent", provider_id=r.get("provider_id"))
            if r.get("provider"):
                # A provider was connected but sending failed
                return SendResult(status="failed", error=r.get("error") or "oauth send failed")
            # No provider connected — fall through to Resend below
        except Exception as e:
            print(f"[email] oauth send fell through: {e}")

        # Fallback: Resend with our own from-address (only used when no OAuth
        # is connected and Resend credentials are configured).
        if not self._client:
            print(f"[email:no_creds] -> {to}: {subject!r}")
            return SendResult(status="skipped:no_credentials")

        try:
            params = {
                "from":    self.settings.resend_from_email,
                "to":      [to],
                "subject": subject or "A note from your practice",
                "text":    body,
            }
            result = self._client.Emails.send(params)
            sid = result.get("id") if isinstance(result, dict) else None
            return SendResult(status="sent", provider_id=sid)
        except Exception as e:
            return SendResult(status="failed", error=str(e))
