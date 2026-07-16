"""Email channel adapter — Resend."""
from __future__ import annotations

from dataclasses import dataclass

from ..config import Settings


@dataclass
class EmailResult:
    provider_id: str | None
    status: str
    error: str | None = None


class EmailChannel:
    def __init__(self, settings: Settings, *, dry_run: bool = False):
        self.settings = settings
        self.dry_run = dry_run
        self._configured = False

    def _configure(self) -> None:
        if self._configured or self.dry_run:
            return
        try:
            import resend
        except ImportError as e:
            raise RuntimeError("resend package not installed; pip install resend") from e
        if not self.settings.resend_api_key:
            raise RuntimeError("RESEND_API_KEY not configured in .env")
        resend.api_key = self.settings.resend_api_key
        self._configured = True

    def send(self, to: str, subject: str, body: str, *,
             reply_to: str | None = None, from_name: str | None = None) -> EmailResult:
        """Send via the shared Resend sender. from_name puts the BUSINESS's
        name in the inbox list ("Mike's Plumbing <hello@adapix...>"); reply_to
        routes the customer's reply to the owner's real inbox instead of the
        shared address. Both are how the fallback path still reads as the
        business rather than as Adapix."""
        if not to:
            return EmailResult(provider_id=None, status="failed", error="missing recipient email")
        if self.dry_run:
            print(f"\n[DRY RUN — EMAIL to {to}]\nSubject: {subject}\n\n{body}\n")
            return EmailResult(provider_id=None, status="skipped (dry run)")
        try:
            self._configure()
            import resend
            sender = self.settings.resend_from_email
            if from_name:
                # Strip characters that would break the "Name <addr>" header.
                clean = from_name.replace('"', "").replace("<", "").replace(">", "").strip()
                if clean:
                    sender = f"{clean} <{self.settings.resend_from_email}>"
            payload = {
                "from": sender,
                "to": [to],
                "subject": subject,
                "text": body,
            }
            if reply_to:
                payload["reply_to"] = [reply_to]
            response = resend.Emails.send(payload)
            return EmailResult(provider_id=response.get("id") if isinstance(response, dict) else None, status="sent")
        except Exception as e:
            return EmailResult(provider_id=None, status="failed", error=str(e))
