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
import re
from dataclasses import dataclass
from urllib import request as urlrequest
from urllib.error import HTTPError, URLError

from ..config import Settings

VAPI_CALL_URL = "https://api.vapi.ai/call/phone"
VAPI_NUMBER_URL = "https://api.vapi.ai/phone-number"

# Vapi's API sits behind Cloudflare, which blocks the default Python-urllib
# User-Agent. Send these on every request.
_VAPI_HEADERS_BASE = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "User-Agent": "Adapix/1.0 (+https://adapixai.com)",
}


def fetch_vapi_call(settings: Settings, call_id: str) -> dict | None:
    """GET the full call record from Vapi by id — used as a fallback to find
    the recording when the end-of-call-report webhook payload doesn't carry it
    directly (schema has varied across Vapi versions)."""
    if not (settings.vapi_api_key and call_id):
        return None
    try:
        req = urlrequest.Request(
            f"https://api.vapi.ai/call/{call_id}",
            method="GET",
            headers={"Authorization": f"Bearer {settings.vapi_api_key}", **_VAPI_HEADERS_BASE},
        )
        with urlrequest.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode())
    except Exception:
        return None


def extract_recording_url(call_or_report: dict) -> str:
    """Pull the recording URL out of a Vapi call/report object, checking every
    shape Vapi has used: top-level recordingUrl, artifact.recordingUrl, and
    the nested artifact.recording.mono.combinedUrl. Falls back to stereo."""
    if not call_or_report:
        return ""
    artifact = call_or_report.get("artifact") or {}
    recording = artifact.get("recording") or {}
    mono = recording.get("mono") or {}
    return (
        call_or_report.get("recordingUrl")
        or artifact.get("recordingUrl")
        or mono.get("combinedUrl")
        or call_or_report.get("stereoRecordingUrl")
        or artifact.get("stereoRecordingUrl")
        or recording.get("stereoUrl")
        or ""
    )


@dataclass
class NumberProvisionResult:
    phone_number_id: str | None
    number: str | None
    error: str | None = None
    twilio_sid: str | None = None  # set only by buy_and_import_twilio_number()


def create_vapi_number(
    settings: Settings, *, area_code: str | None = None, name: str | None = None,
    fallback_number: str | None = None,
) -> NumberProvisionResult:
    """Buy a free Vapi US number for a business. Swap this for a Twilio
    buy+import when you productionize reputation management (Twilio gives
    A-level attestation + CNAM control).

    fallback_number: where an INBOUND call to this number goes — without it,
    a customer calling back after a missed-call text hits dead air. Route to
    the owner's real office/cell phone if we have one on file."""
    if not settings.vapi_api_key:
        return NumberProvisionResult(None, None, "Vapi is not configured (VAPI_API_KEY missing)")

    def _post(body: dict) -> NumberProvisionResult:
        req = urlrequest.Request(
            VAPI_NUMBER_URL,
            data=json.dumps(body).encode(),
            method="POST",
            headers={"Authorization": f"Bearer {settings.vapi_api_key}", **_VAPI_HEADERS_BASE},
        )
        with urlrequest.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
        pid = data.get("id")
        if pid:
            return NumberProvisionResult(pid, data.get("number") or "")
        return NumberProvisionResult(None, None, f"Vapi accepted the request but returned no number id: {data!r}"[:300])

    body: dict = {"provider": "vapi"}
    if area_code:
        body["numberDesiredAreaCode"] = str(area_code)[:3]
    if name:
        body["name"] = name[:40]
    if fallback_number:
        body["fallbackDestination"] = {"type": "number", "number": fallback_number}

    # Two known-recoverable rejections, neither of which should ever block
    # getting a working calling line: (1) a bad/unparseable owner phone
    # slipping past our own validation as the fallback destination — a
    # missing callback route is a minor gap, not worth failing over; (2)
    # Vapi has no free-tier inventory in the requested area code — it
    # helpfully names real alternatives in the error text ("Try one of 573,
    # 719, 458"), so use the first one instead of just giving up. Bounded to
    # a few attempts so a genuinely bad request can't loop.
    attempts_left = 4
    last_error = ""
    while attempts_left > 0:
        attempts_left -= 1
        try:
            return _post(body)
        except HTTPError as e:
            detail = e.read().decode(errors="replace")[:300]
            last_error = f"Vapi HTTP {e.code}: {detail}"
            if "fallbackDestination" in detail and "fallbackDestination" in body:
                del body["fallbackDestination"]
                continue
            alt_match = re.search(r"[Tt]ry one of ([\d, ]+)", detail)
            if "area code" in detail.lower() and alt_match:
                alt = alt_match.group(1).split(",")[0].strip()
                if alt and alt != body.get("numberDesiredAreaCode"):
                    body["numberDesiredAreaCode"] = alt
                    continue
            return NumberProvisionResult(None, None, last_error)
        except (URLError, TimeoutError) as e:
            return NumberProvisionResult(None, None, f"Could not reach Vapi: {e}")
        except Exception as e:  # noqa: BLE001 — surface any client error as a result
            return NumberProvisionResult(None, None, str(e))
    return NumberProvisionResult(None, None, last_error or "Vapi rejected the request repeatedly")


def buy_and_import_twilio_number(
    settings: Settings, *, area_code: str | None = None, name: str | None = None,
) -> NumberProvisionResult:
    """Buy a REAL Twilio number (real recurring cost, ~$1.15/mo) and import it
    into Vapi for calling. Unlike create_vapi_number() (a free Vapi-owned
    number), a real carrier number gets proper STIR/SHAKEN attestation —
    meaningfully less likely to show as "Spam Likely" — and is a prerequisite
    for CNAM (business name on caller ID), which only works on numbers Twilio
    actually owns.

    This is a real-money action: only call it from a path the org owner has
    explicitly confirmed (see /api/v1/phone/upgrade), never automatically.
    """
    if not (settings.twilio_account_sid and settings.twilio_auth_token):
        return NumberProvisionResult(None, None, "Twilio is not configured (TWILIO_ACCOUNT_SID/TWILIO_AUTH_TOKEN missing)")
    try:
        from twilio.rest import Client
    except ImportError as e:
        return NumberProvisionResult(None, None, f"twilio package not installed: {e}")

    client = Client(settings.twilio_account_sid, settings.twilio_auth_token)

    # 1. Find and buy an available local number.
    try:
        candidates = client.available_phone_numbers("US").local.list(
            area_code=(area_code[:3] if area_code else None), voice_enabled=True, limit=1,
        )
        if not candidates:
            # No inventory in that area code — fall back to any US local number
            # rather than fail the upgrade outright.
            candidates = client.available_phone_numbers("US").local.list(voice_enabled=True, limit=1)
        if not candidates:
            return NumberProvisionResult(None, None, "No Twilio numbers available to purchase right now")
        bought = client.incoming_phone_numbers.create(
            phone_number=candidates[0].phone_number,
            friendly_name=(name or "Adapix business line")[:64],
        )
    except Exception as e:  # noqa: BLE001 — surface Twilio's real error text
        return NumberProvisionResult(None, None, f"Twilio purchase failed: {e}")

    bought_number = bought.phone_number
    bought_sid = bought.sid

    # 2. Import the newly-purchased number into Vapi so calls route through it.
    body = {
        "provider": "twilio",
        "number": bought_number,
        "twilioAccountSid": settings.twilio_account_sid,
        "twilioAuthToken": settings.twilio_auth_token,
    }
    if name:
        body["name"] = name[:40]
    try:
        req = urlrequest.Request(
            VAPI_NUMBER_URL,
            data=json.dumps(body).encode(),
            method="POST",
            headers={"Authorization": f"Bearer {settings.vapi_api_key}", **_VAPI_HEADERS_BASE},
        )
        with urlrequest.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
        pid = data.get("id")
        if not pid:
            # Number was bought successfully but Vapi rejected it — surface the
            # Twilio SID in the error so it can be released manually rather
            # than silently left as an orphaned recurring charge.
            return NumberProvisionResult(
                None, bought_number,
                f"Bought {bought_number} (Twilio SID {bought_sid}) but Vapi import failed: {data!r}"[:400],
            )
        return NumberProvisionResult(pid, data.get("number") or bought_number, twilio_sid=bought_sid)
    except HTTPError as e:
        detail = e.read().decode(errors="replace")[:300]
        return NumberProvisionResult(
            None, bought_number,
            f"Bought {bought_number} (Twilio SID {bought_sid}) but Vapi import failed: HTTP {e.code}: {detail}",
        )
    except (URLError, TimeoutError) as e:
        return NumberProvisionResult(None, bought_number, f"Bought {bought_number} but could not reach Vapi: {e}")
    except Exception as e:  # noqa: BLE001
        return NumberProvisionResult(None, bought_number, f"Bought {bought_number} but Vapi import failed: {e}")


def release_twilio_number(settings: Settings, twilio_sid: str) -> bool:
    """Release a purchased Twilio number back to the pool (stops the
    recurring charge). Used to roll back a number purchase if the matching
    Stripe charge to the customer fails right after — never leave them
    paying Twilio for a number the customer wasn't actually billed for."""
    if not (settings.twilio_account_sid and settings.twilio_auth_token and twilio_sid):
        return False
    try:
        from twilio.rest import Client
        Client(settings.twilio_account_sid, settings.twilio_auth_token).incoming_phone_numbers(twilio_sid).delete()
        return True
    except Exception:
        return False


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

        # Each org calls from its OWN number. A dev/global test number
        # (settings.vapi_phone_number_id) is fine in dry_run or local dev,
        # but in production a missing org number means provisioning failed —
        # placing the call anyway would silently mask that from the owner
        # and put a mystery number in the customer's caller ID.
        number_id = phone_number_id
        if not number_id:
            if self.dry_run or not self.settings.public_base_url:
                number_id = self.settings.vapi_phone_number_id  # local/dev fallback only
            else:
                return VoiceResult(None, "failed",
                                   "no calling number provisioned for this business — "
                                   "check Settings -> Messaging & channels")
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
            # Cost guardrails: calls are the one unmetered spend. Hard cap
            # the duration, hang up on dead air, and don't pitch a voicemail
            # at full rate.
            "maxDurationSeconds": 300,
            "silenceTimeoutSeconds": 20,
            "voicemailDetection": {"provider": "twilio"},
            "voicemailMessage": "",
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
            import os as _os
            _secret = _os.environ.get("VAPI_WEBHOOK_SECRET", "").strip()
            if _secret:
                assistant["server"]["secret"] = _secret

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
