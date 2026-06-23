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

    def send(self, to: str, body: str) -> SmsResult:
        if not to:
            return SmsResult(provider_id=None, status="failed", error="missing recipient phone")
        if self.dry_run:
            print(f"\n[DRY RUN — SMS to {to}]\n{body}\n")
            return SmsResult(provider_id=None, status="skipped (dry run)")
        try:
            msg = self.client.messages.create(
                to=to,
                from_=self.settings.twilio_from_number,
                body=body,
            )
            return SmsResult(provider_id=msg.sid, status="sent")
        except Exception as e:
            return SmsResult(provider_id=None, status="failed", error=str(e))
