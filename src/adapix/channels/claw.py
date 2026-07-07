"""iMessage channel adapter — Claw Messenger.

Same job as the Blooio adapter (blue-bubble iMessage with the provider's
own RCS/SMS fallback), picked for cost: $5/mo for 250 messages with a
dedicated number vs Blooio's $39/mo shared. Same reality check applies —
Claw relays through Apple hardware on their side, outside Apple's
sanctioned channels, so Adapix ALWAYS keeps Twilio SMS as the fallback
transport (see ApprovalManager._send_one).

Protocol: Claw's server is WebSocket-ONLY for messaging (their blog
mentions a REST send endpoint that does not actually exist — verified
404). Real flow:

  connect  wss://claw-messenger.onrender.com/ws?key=cm_live_...
  send     {"type":"send","id":"...","to":"+1...",
            "parts":[{"type":"text","value":"..."}],"service":"iMessage"}
  await    a result/status event for our id, then close.

Inbound security: Claw only accepts messages TO pre-registered numbers —
register recipients first via POST /api/routes (REST, Bearer auth).

One Claw account = one dedicated number today, so this is a platform-level
channel (like TWILIO_FROM_NUMBER). Revisit per-business lines later.
"""
from __future__ import annotations

import json
import uuid

from ..config import Settings
from .imessage import IMessageResult

CLAW_WS_URL = "wss://claw-messenger.onrender.com/ws"


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

        try:
            import websocket  # websocket-client
        except ImportError:
            return IMessageResult(provider_id=None, status="failed",
                                  error="websocket-client not installed")

        msg_id = uuid.uuid4().hex[:12]
        try:
            ws = websocket.create_connection(
                f"{CLAW_WS_URL}?key={self.settings.claw_api_key}",
                timeout=30,
                header=["User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) Adapix/1.0"],
            )
        except Exception as e:
            return IMessageResult(provider_id=None, status="failed", error=f"ws connect: {e}")

        try:
            ws.send(json.dumps({
                "type": "send",
                "id": msg_id,
                "to": to,
                "parts": [{"type": "text", "value": body}],
                "service": "iMessage",
            }))
            # Read events until our send is acknowledged (or times out).
            # Servers interleave unrelated events (inbound messages, pings),
            # so filter for our correlation id.
            ws.settimeout(30)
            for _ in range(20):
                raw = ws.recv()
                if not raw:
                    continue
                try:
                    ev = json.loads(raw)
                except Exception:
                    continue
                ev_id = ev.get("id") or ev.get("messageId")
                if ev_id and ev_id != msg_id:
                    continue
                status = (ev.get("status") or "").lower()
                ev_type = (ev.get("type") or "").lower()
                if status in ("failed", "error") or ev_type == "error":
                    return IMessageResult(provider_id=msg_id, status="failed",
                                          error=ev.get("error") or ev.get("message") or raw[:200])
                if status in ("accepted", "sent", "delivery_confirmed", "not_confirmed") or ev_type in ("send.result", "status"):
                    return IMessageResult(provider_id=msg_id, status="sent")
            # No explicit ack — the send was written to an open socket, so
            # treat as accepted rather than failing a message that likely went.
            return IMessageResult(provider_id=msg_id, status="sent")
        except Exception as e:
            return IMessageResult(provider_id=None, status="failed", error=f"ws send: {e}")
        finally:
            try:
                ws.close()
            except Exception:
                pass
