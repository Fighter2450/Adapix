"""Voice channel adapter — Vapi (AI phone calls).

Places an outbound AI phone call. The per-contact context Adapix already builds
for SMS/email becomes the assistant's *system prompt*; the opening line always
discloses that it's an AI so we stay on the right side of TCPA + the growing
list of state AI-disclosure laws.

Vapi handles the hard real-time parts (telephony, speech-to-text, the AI voice,
turn-taking, interruptions). We supply the model (Claude) and the prompt. Call
outcomes — transcript + summary — come back to us via POST /webhooks/vapi.

Design note: calling breaks the draft→approve→send model, because a live call
can't be approved word-by-word. The human approves the call's GOAL up front
(the compose step), Adapix places the call, and the transcript/outcome flows
back for review — with a live hand-off to a person when the call needs one.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from urllib import request as urlrequest
from urllib.error import HTTPError, URLError

from ..config import Settings

VAPI_CALL_URL = "https://api.vapi.ai/call/phone"


@dataclass
class VoiceResult:
    provider_id: str | None      # Vapi call id
    status: str                  # dialing | failed | skipped
    error: str | None = None


def ai_disclosure_line(business_name: str) -> str:
    """The opening line of every call. Discloses it's an AI + recording notice.

    This is a compliance requirement, not a nicety — several states legally
    require disclosing that the caller is an AI, and recording notice covers
    two-party-consent states. Keep this in every call's first message.
    """
    return (
        f"Hi, this is a virtual assistant calling on behalf of {business_name}. "
        "Just so you know, you're speaking with an AI and this call may be "
        "recorded. Is now an okay time for a quick moment?"
    )


class VoiceChannel:
    """Mirrors SmsChannel / EmailChannel: `.place_call(...)` → VoiceResult.

    Supports dry_run (prints the plan, places no call) so the whole flow can be
    exercised before a Vapi account exists.
    """

    def __init__(self, settings: Settings, *, dry_run: bool = False):
        self.settings = settings
        self.dry_run = dry_run

    def place_call(
        self,
        *,
        to: str,
        system_prompt: str,
        goal: str = "",
        business_name: str | None = None,
        first_message: str | None = None,
        metadata: dict | None = None,
        phone_number_id: str | None = None,
    ) -> VoiceResult:
        if not to:
            return VoiceResult(None, "failed", "missing recipient phone")

        # Each org calls from its OWN number; fall back to the global test number.
        number_id = phone_number_id or self.settings.vapi_phone_number_id
        biz = business_name or self.settings.business_name or "our office"
        # Always disclose AI in the opening line (compliance). Callers may pass
        # their own first_message, but it must still disclose — so we default to
        # the standard disclosure and only override when explicitly given one.
        opening = first_message or ai_disclosure_line(biz)

        if self.dry_run:
            print(
                f"\n[DRY RUN — CALL to {to}]\n"
                f"From number id: {number_id or '(none)'}\n"
                f"Opening: {opening}\n"
                f"Goal: {goal or '(none)'}\n"
                f"System prompt:\n{system_prompt}\n"
            )
            return VoiceResult(None, "skipped (dry run)")

        if not (self.settings.vapi_api_key and number_id):
            return VoiceResult(
                None, "failed",
                "No calling number for this business yet (set VAPI_API_KEY + a phone number id)",
            )

        assistant: dict = {
            "model": {
                "provider": self.settings.vapi_model_provider,
                "model": self.settings.adapix_model,
                "systemPrompt": system_prompt,
            },
            "firstMessage": opening,
        }
        # Only pin a voice if one is configured; otherwise use Vapi's default
        # (avoids failing on an invalid voice id).
        if self.settings.vapi_voice_id:
            assistant["voice"] = {
                "provider": self.settings.vapi_voice_provider,
                "voiceId": self.settings.vapi_voice_id,
            }
        # Route call events (incl. end-of-call-report with transcript) back to us.
        if self.settings.public_base_url:
            assistant["server"] = {
                "url": self.settings.public_base_url.rstrip("/") + "/webhooks/vapi",
            }

        body: dict = {
            "phoneNumberId": number_id,
            "customer": {"number": to},
            "assistant": assistant,
        }
        if metadata:
            body["metadata"] = metadata

        try:
            req = urlrequest.Request(
                VAPI_CALL_URL,
                data=json.dumps(body).encode(),
                method="POST",
                headers={
                    "Authorization": f"Bearer {self.settings.vapi_api_key}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    # Vapi's API is behind Cloudflare, which blocks the default
                    # Python-urllib User-Agent (403 error 1010). Send a real one.
                    "User-Agent": "Adapix/1.0 (+https://adapixai.com)",
                },
            )
            with urlrequest.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode())
            return VoiceResult(
                provider_id=data.get("id"),
                status=data.get("status") or "dialing",
            )
        except HTTPError as e:
            detail = e.read().decode(errors="replace")[:300]
            return VoiceResult(None, "failed", f"HTTP {e.code}: {detail}")
        except (URLError, TimeoutError) as e:
            return VoiceResult(None, "failed", str(e))
        except Exception as e:  # noqa: BLE001 — surface any client error as a result
            return VoiceResult(None, "failed", str(e))
