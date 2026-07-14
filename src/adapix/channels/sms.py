"""SMS channel adapter — Twilio."""
from __future__ import annotations

from dataclasses import dataclass

from ..config import Settings


@dataclass
class SmsResult:
    provider_id: str | None
    status: str           # sent | failed | skipped
    error: str | None = None


class SmsChannel:
    def __init__(self, settings: Settings, *, dry_run: bool = False):
        self.settings = settings
        self.dry_run = dry_run
        self._client = None

    @property
    def client(self):
        if self._client is None and not self.dry_run:
            try:
                from twilio.rest import Client
            except ImportError as e:
                raise RuntimeError("twilio package not installed; pip install twilio") from e
            if not (self.settings.twilio_account_sid and self.settings.twilio_auth_token):
                raise RuntimeError("Twilio credentials not configured in .env")
            self._client = Client(self.settings.twilio_account_sid, self.settings.twilio_auth_token)
        return self._client

    STOP_FOOTER = " Reply STOP to opt out."

    def send(self, to: str, body: str, *, first_touch: bool = False) -> SmsResult:
        if not to:
            return SmsResult(provider_id=None, status="failed", error="missing recipient phone")
        # A2P/CTIA: the first message of a conversation must carry the
        # opt-out disclosure (skipped when the composer already wrote one).
        if first_touch and "stop" not in body.lower():
            body = body.rstrip() + self.STOP_FOOTER
        if self.dry_run:
            print(f"\n[DRY RUN — SMS to {to}]\n{body}\n")
            return SmsResult(provider_id=None, status="skipped (dry run)")
        try:
            params = {"to": to, "body": body}
            # Route through the registered A2P messaging service when set —
            # that ties every send to the approved 10DLC campaign.
            if self.settings.twilio_messaging_service_sid:
                params["messaging_service_sid"] = self.settings.twilio_messaging_service_sid
            else:
                params["from_"] = self.settings.twilio_from_number
            msg = self.client.messages.create(**params)
            return SmsResult(provider_id=msg.sid, status="sent")
        except Exception as e:
            return SmsResult(provider_id=None, status="failed", error=str(e))
