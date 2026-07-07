"""iMessage channel adapter — Claw Messenger.

Same job as the Blooio adapter (blue-bubble iMessage with the provider's
own RCS/SMS fallback), picked for cost: $5/mo for 250 messages with a
dedicated number vs Blooio's $39/mo shared. Same reality check applies —
Claw relays through Apple hardware on their side, outside Apple's
sanctioned channels, so Adapix ALWAYS keeps Twilio SMS as the fallback
transport (see ApprovalManager._send_one).

API: POST https://www.clawmessenger.com/api/agent/send-message
  Authorization: Bearer cm_live_...
  {"phone_number": "+1555...", "text": "..."}

One Claw account = one dedicated number today, so this is a platform-level
channel (like TWILIO_FROM_NUMBER). When per-business lines matter, Claw
sells extra numbers per account — revisit then.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

from ..config import Settings
from .imessage import IMessageResult

CLAW_SEND_URL = "https://www.clawmessenger.com/api/agent/send-message"


class ClawChannel:
    def __init__(self, settings: Settings, *, dry_run: bool = False):
        self.settings = settings
        self.dry_run = dry_run

    def is_configured(self) -> bool:
        return bool(self.settings.claw_api_key)

    def send(self, to: str, body: str) -> IMessageResult:
        if not to:
            return IMessageResult(provider_id=None, status="failed", error="missing recipient phone")
        if not self.settings.claw_api_key:
            return IMessageResult(provider_id=None, status="failed", error="Claw Messenger not configured")
        if self.dry_run:
            print(f"\n[DRY RUN — iMESSAGE (Claw) to {to}]\n{body}\n")
            return IMessageResult(provider_id=None, status="skipped (dry run)")

        payload = json.dumps({"phone_number": to, "text": body}).encode()
        req = urllib.request.Request(
            CLAW_SEND_URL,
            data=payload,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.settings.claw_api_key}",
                "Content-Type": "application/json",
                # Real UA — Cloudflare-fronted APIs block urllib's default
                # (same lesson as Vapi and Blooio).
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Adapix/1.0",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode() or "{}")
            msg_id = data.get("id") or data.get("message_id")
            return IMessageResult(provider_id=str(msg_id) if msg_id else None, status="sent")
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode()[:200]
            except Exception:
                pass
            return IMessageResult(provider_id=None, status="failed",
                                  error=f"HTTP {e.code}: {detail or e.reason}")
        except Exception as e:
            return IMessageResult(provider_id=None, status="failed", error=str(e))
