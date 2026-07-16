"""iMessage channel adapter — Blooio.

Sends texts through Blooio's API so recipients on Apple devices get a blue
iMessage bubble instead of green carrier SMS (Blooio handles its own
RCS/SMS fallback for non-Apple recipients). Reality check documented here
on purpose: every iMessage-for-business provider, Blooio included, relays
through Apple hardware signed into real Apple IDs — outside Apple's
sanctioned channels. That's the provider's operational risk to manage, but
it's why Adapix ALWAYS keeps Twilio SMS as the fallback transport: if a
Blooio send fails for any reason, the message still goes out green rather
than not at all (see ApprovalManager._send_one).

API (v4): POST https://api.blooio.com/v4/messages
  Authorization: Bearer <BLOOIO_API_KEY>
  {"channel_id": "...", "to": {"identifier": "+1555..."},
   "content": {"type": "text", "text": "..."}}
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass

from ..config import Settings

BLOOIO_MESSAGES_URL = "https://api.blooio.com/v4/messages"
BLOOIO_CHANNELS_URL = "https://api.blooio.com/v4/channels"
BLOOIO_WEBHOOKS_URL = "https://api.blooio.com/v4/webhooks"

_BLOOIO_HEADERS_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Adapix/1.0"


def _blooio_get(settings: Settings, url: str) -> dict:
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {settings.blooio_api_key}",
        "User-Agent": _BLOOIO_HEADERS_UA,
    })
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8") or "{}")


def list_channels(settings: Settings) -> list[dict]:
    """All texting lines on the platform Blooio account. Each entry has
    id (ch_...), display_address (+1...), status, capabilities."""
    if not settings.blooio_api_key:
        return []
    return _blooio_get(settings, BLOOIO_CHANNELS_URL).get("data") or []


def list_chats(settings: Settings) -> list[dict]:
    if not settings.blooio_api_key:
        return []
    return _blooio_get(settings, "https://api.blooio.com/v4/chats").get("data") or []


def list_chat_messages(settings: Settings, chat_id: str) -> list[dict]:
    return _blooio_get(settings, f"https://api.blooio.com/v4/chats/{chat_id}/messages").get("data") or []


def get_contact(settings: Settings, contact_id: str) -> dict:
    return _blooio_get(settings, f"https://api.blooio.com/v4/contacts/{contact_id}")


def register_webhook(settings: Settings, url: str) -> dict:
    """Register the inbound-events webhook (message.received) with Blooio.
    Returns the created webhook object INCLUDING its signing_secret — capture
    it and set BLOOIO_WEBHOOK_SECRET, it is not retrievable later."""
    payload = {"url": url, "event_types": ["message.received"]}
    req = urllib.request.Request(
        BLOOIO_WEBHOOKS_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {settings.blooio_api_key}",
            "Content-Type": "application/json",
            "User-Agent": _BLOOIO_HEADERS_UA,
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8") or "{}")


@dataclass
class IMessageResult:
    provider_id: str | None
    status: str           # sent | failed | skipped
    error: str | None = None


class IMessageChannel:
    """One platform Blooio account (BLOOIO_API_KEY), per-org sender lines.

    Each org texts from its OWN dedicated Blooio line — the channel_id lives
    on the Organization row (mirroring vapi_phone_number_id for calls), never
    a shared line across businesses. settings.blooio_channel_id remains only
    as a dev/test fallback when no per-org channel is passed."""

    def __init__(self, settings: Settings, *, dry_run: bool = False):
        self.settings = settings
        self.dry_run = dry_run

    def is_configured(self, channel_id: str | None = None) -> bool:
        return bool(self.settings.blooio_api_key and (channel_id or self.settings.blooio_channel_id))

    def send(self, to: str, body: str, channel_id: str | None = None) -> IMessageResult:
        channel = channel_id or self.settings.blooio_channel_id
        if not to:
            return IMessageResult(provider_id=None, status="failed", error="missing recipient phone")
        if not (self.settings.blooio_api_key and channel):
            return IMessageResult(provider_id=None, status="failed", error="Blooio not configured for this business")
        if self.dry_run:
            print(f"\n[DRY RUN — iMESSAGE to {to} via {channel}]\n{body}\n")
            return IMessageResult(provider_id=None, status="skipped (dry run)")

        payload = {
            "channel_id": channel,
            "to": {"identifier": to},
            "content": {"type": "text", "text": body},
        }
        req = urllib.request.Request(
            BLOOIO_MESSAGES_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.settings.blooio_api_key}",
                "Content-Type": "application/json",
                # Cloudflare fronts api.blooio.com and bot-blocks requests
                # with urllib's default UA (error 1010) — same lesson as the
                # Vapi adapter. A real UA string gets through.
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Adapix/1.0",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = json.loads(resp.read().decode("utf-8") or "{}")
            provider_id = data.get("message_id") or data.get("id")
            return IMessageResult(provider_id=provider_id, status="sent")
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8")[:300]
            except Exception:
                pass
            return IMessageResult(provider_id=None, status="failed", error=f"HTTP {e.code}: {detail}")
        except Exception as e:
            return IMessageResult(provider_id=None, status="failed", error=str(e))
