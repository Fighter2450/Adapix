"""HTTP webhooks.

  POST /webhooks/twilio/sms - Twilio inbound SMS (signed)
  POST /webhooks/dev/sms    - dev-only simulator (no Twilio)

Twilio signature verification:
  Twilio signs every webhook with HMAC-SHA1 of (full_url + sorted form params),
  using your Twilio auth token as the key, and puts the signature in the
  X-Twilio-Signature header. We verify with twilio.request_validator.

Skipping verification (dev only):
  Set SKIP_TWILIO_VERIFICATION=true in .env. Never do this in production.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse

from ..config import Settings
from ..inbound import InboundProcessor

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

EMPTY_TWIML = "<?xml version='1.0' encoding='UTF-8'?><Response/>"


async def _verify_twilio(request: Request, form: dict) -> bool:
    """Returns True if signature is valid, OR if verification is disabled."""
    settings = Settings()
    if settings.skip_twilio_verification:
        return True
    if not settings.twilio_auth_token:
        # No token configured — refuse rather than blindly accept
        return False
    try:
        from twilio.request_validator import RequestValidator
    except ImportError:
        return False
    validator = RequestValidator(settings.twilio_auth_token)
    sig = request.headers.get("X-Twilio-Signature", "")
    # Use public_base_url if configured (handles ngrok/proxy URL rewriting)
    if settings.public_base_url:
        url = settings.public_base_url.rstrip("/") + str(request.url.path)
        if request.url.query:
            url += "?" + request.url.query
    else:
        url = str(request.url)
    return validator.validate(url, form, sig)


@router.post("/twilio/sms")
async def twilio_inbound_sms(request: Request):
    """Inbound SMS from Twilio. Form-encoded, signed."""
    form_data = await request.form()
    form_dict = {k: str(v) for k, v in form_data.items()}

    if not await _verify_twilio(request, form_dict):
        # Don't 401 — Twilio retries aggressively. Just no-op + log.
        print("[adapix] twilio webhook signature verification FAILED")
        raise HTTPException(status_code=403, detail="Invalid Twilio signature")

    From = form_dict.get("From", "")
    Body = form_dict.get("Body", "")
    MessageSid = form_dict.get("MessageSid", "")

    if not From or not Body:
        return PlainTextResponse(content=EMPTY_TWIML, media_type="application/xml")

    try:
        processor = InboundProcessor()
        result = processor.process_sms(
            from_number=From, body=Body, provider_id=MessageSid or None
        )
        print(
            f"[adapix] inbound from={From} status={result.status} "
            f"category={result.classification.category if result.classification else 'n/a'}"
        )
    except Exception as e:
        print(f"[adapix] inbound webhook error: {e}")

    return PlainTextResponse(content=EMPTY_TWIML, media_type="application/xml")


@router.post("/vapi")
async def vapi_call_events(request: Request):
    """Vapi call events. The important one is 'end-of-call-report', which
    carries the transcript + summary once a call finishes.

    For now we log the outcome (proves the round-trip: call placed → AI talked →
    result came back). Next step: classify the transcript for booking/escalation
    and attach it to the contact's history, same as inbound SMS.
    """
    try:
        payload = await request.json()
    except Exception:
        return {"ok": True}

    msg = payload.get("message") or {}
    event = msg.get("type") or ""

    if event == "end-of-call-report":
        call = msg.get("call") or {}
        meta = call.get("metadata") or msg.get("metadata") or {}
        number = ((call.get("customer") or {}).get("number")) or ""
        ended = msg.get("endedReason") or ""
        summary = (msg.get("summary") or "").strip()
        transcript = (msg.get("transcript") or "").strip()
        # Recording location varies by Vapi payload version — check every known
        # shape (top-level, artifact, and the nested mono/stereo forms).
        from ..channels.voice import extract_recording_url, fetch_vapi_call
        from ..config import Settings as _Settings

        recording_url = extract_recording_url(msg) or extract_recording_url(call)
        call_id = call.get("id") or msg.get("id")
        if not recording_url and call_id:
            # Webhook sometimes fires before the recording finishes uploading —
            # the GET /call/{id} API reliably has it once the call has ended.
            fetched = fetch_vapi_call(_Settings(), call_id)
            recording_url = extract_recording_url(fetched or {})
        print(f"[adapix] call ended to={number or '?'} reason={ended or '?'} recording={'yes' if recording_url else 'no'}")

        try:
            result = InboundProcessor().process_call_outcome(
                transcript=transcript,
                summary=summary,
                ended_reason=ended,
                patient_id=meta.get("patient_id"),
                campaign_id=meta.get("campaign_id"),
                from_number=number or None,
                provider_id=call.get("id"),
                recording_url=recording_url or None,
            )
            cat = result.classification.category if result.classification else "n/a"
            print(f"[adapix]   call outcome: status={result.status} category={cat}")
        except Exception as e:
            print(f"[adapix]   call-outcome processing error: {e}")
    else:
        print(f"[adapix] vapi event: {event or '(unknown)'}")

    return {"ok": True}


@router.post("/dev/sms")
async def dev_inbound_sms(payload: dict):
    """Dev-only simulator. Body: {"from": "+1...", "body": "..."}"""
    from_number = payload.get("from") or ""
    body = payload.get("body") or ""
    if not from_number or not body:
        raise HTTPException(status_code=400, detail="from and body are required")
    processor = InboundProcessor()
    result = processor.process_sms(from_number=from_number, body=body)
    return {
        "status": result.status,
        "reason": result.reason,
        "response_body": result.response_body,
        "classification": (
            {
                "category": result.classification.category,
                "confidence": result.classification.confidence,
                "reasoning": result.classification.reasoning,
                "suggested_action": result.classification.suggested_action,
            }
            if result.classification
            else None
        ),
    }
