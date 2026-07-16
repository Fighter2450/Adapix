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
    """Returns True if signature is valid, OR if verification is disabled.

    Twilio signs the HMAC against the EXACT URL it POSTed to. If the number's
    webhook in the Twilio console points at a different host than
    PUBLIC_BASE_URL (e.g. the raw *.up.railway.app domain instead of the
    custom domain — this has happened), a signature computed only against
    PUBLIC_BASE_URL never matches and every inbound reply is rejected with a
    403, silently dropping the customer's message before the AI ever sees
    it. Try every host we might plausibly be reached on and accept the
    first one that validates."""
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

    candidates: list[str] = []
    # 1) The host that actually served the request, forced to https — Railway
    #    (and most reverse proxies) terminate TLS at the edge, so the app
    #    often sees an http:// URL internally even though Twilio hit https.
    raw_url = str(request.url)
    if raw_url.startswith("http://"):
        raw_url = "https://" + raw_url[len("http://"):]
    candidates.append(raw_url)
    # 2) The configured canonical public URL, if different (covers a custom
    #    domain configured in Twilio while PUBLIC_BASE_URL differs, or vice
    #    versa).
    if settings.public_base_url:
        alt = settings.public_base_url.rstrip("/") + str(request.url.path)
        if request.url.query:
            alt += "?" + request.url.query
        candidates.append(alt)

    return any(validator.validate(u, form, sig) for u in dict.fromkeys(candidates))


@router.post("/twilio/sms")
async def twilio_inbound_sms(request: Request):
    """Inbound SMS from Twilio. Form-encoded, signed."""
    form_data = await request.form()
    form_dict = {k: str(v) for k, v in form_data.items()}

    From = form_dict.get("From", "")
    To = form_dict.get("To", "")
    Body = form_dict.get("Body", "")
    MessageSid = form_dict.get("MessageSid", "")

    # Persist the raw inbound BEFORE anything else, verification included —
    # a customer reply must never vanish with zero trace, whether it's a
    # downstream exception or a signature mismatch (misconfigured webhook
    # URL, rotated auth token) that eats it.
    if From and Body:
        try:
            import json as _json
            import os as _os
            import time as _time
            from pathlib import Path as _Path
            raw = _Path(_os.environ.get("ADAPIX_VAR", ".")) / "inbound_raw.jsonl"
            with raw.open("a", encoding="utf-8") as f:
                f.write(_json.dumps({"t": int(_time.time()), "from": From, "to": To,
                                     "body": Body, "sid": MessageSid}) + chr(10))
        except Exception:
            pass

    if not await _verify_twilio(request, form_dict):
        # Don't 401 — Twilio retries aggressively. Just no-op + log.
        print(f"[adapix] twilio webhook signature verification FAILED from={From}")
        raise HTTPException(status_code=403, detail="Invalid Twilio signature")

    if not From or not Body:
        return PlainTextResponse(content=EMPTY_TWIML, media_type="application/xml")

    try:
        processor = InboundProcessor()
        result = processor.process_sms(
            from_number=From, body=Body, provider_id=MessageSid or None, to_number=To or None
        )
        print(
            f"[adapix] inbound from={From} status={result.status} "
            f"category={result.classification.category if result.classification else 'n/a'}"
        )
    except Exception as e:
        # 500 makes Twilio retry instead of silently dropping the reply.
        print(f"[adapix] inbound webhook error: {e}")
        raise HTTPException(status_code=500, detail="inbound processing failed")

    return PlainTextResponse(content=EMPTY_TWIML, media_type="application/xml")


def _verify_blooio(raw_body: bytes, signature_header: str) -> bool:
    """Blooio signs each delivery: X-Blooio-Signature: t=<ts>,v1=<hmac>,
    where hmac = HMAC-SHA256("{ts}.{raw_body}", signing_secret). The secret
    was captured once at webhook registration (BLOOIO_WEBHOOK_SECRET)."""
    import hashlib
    import hmac as _hmac
    import os as _os

    secret = _os.environ.get("BLOOIO_WEBHOOK_SECRET", "").strip()
    if not secret:
        # Fail CLOSED — an unverifiable inbound channel would let anyone
        # forge customer replies (including fake STOPs).
        return False
    try:
        parts = dict(p.split("=", 1) for p in signature_header.split(","))
        ts, given = parts["t"], parts["v1"]
        expected = _hmac.new(secret.encode(), f"{ts}.{raw_body.decode('utf-8')}".encode(), hashlib.sha256).hexdigest()
        return _hmac.compare_digest(expected, given)
    except Exception:
        return False


@router.post("/blooio")
async def blooio_inbound(request: Request):
    """Inbound iMessage/SMS/RCS replies from the org's Blooio line — routed
    into the same pipeline as Twilio inbound so classification, STOP
    handling, and per-customer memory all just work."""
    raw = await request.body()
    if not _verify_blooio(raw, request.headers.get("X-Blooio-Signature", "")):
        print("[adapix] blooio webhook signature verification FAILED")
        raise HTTPException(status_code=403, detail="Invalid Blooio signature")

    import json as _json
    try:
        envelope = _json.loads(raw.decode("utf-8"))
    except Exception:
        raise HTTPException(status_code=400, detail="bad payload")
    data = envelope.get("data") or {}
    event_type = envelope.get("type") or envelope.get("event_type") or ""
    if event_type and event_type != "message.received":
        return {"ok": True, "ignored": f"event {event_type}"}
    if data.get("direction") and data.get("direction") != "inbound":
        return {"ok": True, "ignored": "outbound echo"}
    sender = data.get("sender") or ""
    text = data.get("text") or ""
    recipient = data.get("recipient") or ""
    if not sender or not text:
        return {"ok": True, "ignored": "empty"}

    # Same never-lose-a-reply raw persist as the Twilio route.
    try:
        import os as _os
        import time as _time
        from pathlib import Path as _Path
        rawlog = _Path(_os.environ.get("ADAPIX_VAR", ".")) / "inbound_raw.jsonl"
        with rawlog.open("a", encoding="utf-8") as f:
            f.write(_json.dumps({"t": int(_time.time()), "from": sender, "to": recipient,
                                 "body": text, "sid": data.get("message_id"), "via": "blooio"}) + chr(10))
    except Exception:
        pass

    try:
        processor = InboundProcessor()
        result = processor.process_sms(
            from_number=sender, body=text,
            provider_id=data.get("message_id"), to_number=recipient or None,
        )
        print(f"[adapix] blooio inbound from={sender} status={result.status}")
    except Exception as e:
        print(f"[adapix] blooio inbound error: {e}")
        raise HTTPException(status_code=500, detail="inbound processing failed")
    return {"ok": True}


@router.post("/vapi")
async def vapi_call_events(request: Request):
    import os as _os
    expected = _os.environ.get("VAPI_WEBHOOK_SECRET", "").strip()
    if expected and request.headers.get("x-vapi-secret", "") != expected:
        raise HTTPException(status_code=403, detail="bad vapi secret")
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
        # Persist the raw report BEFORE processing — Vapi never retries, so a
        # downstream exception must not cost us the transcript.
        try:
            import json as _json
            import os as _os
            import time as _time
            from pathlib import Path as _Path
            raw = _Path(_os.environ.get("ADAPIX_VAR", ".")) / "vapi_raw.jsonl"
            with raw.open("a", encoding="utf-8") as f:
                f.write(_json.dumps({"t": int(_time.time()), "payload": msg}, default=str) + chr(10))
        except Exception:
            pass
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


@router.post("/stripe")
async def stripe_webhook(request: Request):
    """Stripe events that change what a customer may do:
    subscription created/updated/deleted and failed payments. Signature-
    verified with the endpoint's signing secret; updates the org's billing
    record, which the engine gate reads before spending anything."""
    import hashlib
    import hmac as hmac_mod
    import json
    import os
    import time

    payload = await request.body()
    secret = os.environ.get("STRIPE_WEBHOOK_SECRET", "").strip()
    sig_header = request.headers.get("Stripe-Signature", "")
    if not secret:
        raise HTTPException(status_code=503, detail="webhook secret not configured")

    try:
        parts = dict(kv.split("=", 1) for kv in sig_header.split(","))
        ts, v1 = parts["t"], parts["v1"]
        expected = hmac_mod.new(secret.encode(), f"{ts}.".encode() + payload, hashlib.sha256).hexdigest()
        if not hmac_mod.compare_digest(expected, v1):
            raise ValueError("signature mismatch")
        if abs(time.time() - int(ts)) > 600:
            raise ValueError("stale timestamp")
    except Exception:
        raise HTTPException(status_code=400, detail="invalid signature")

    event = json.loads(payload)
    etype = event.get("type", "")
    obj = (event.get("data") or {}).get("object") or {}

    from ..billing import find_subscription_by_org, set_billing

    org_id = None
    status = None
    if etype.startswith("customer.subscription."):
        org_id = (obj.get("metadata") or {}).get("org_id")
        status = "canceled" if etype.endswith("deleted") else obj.get("status")
    elif etype == "invoice.payment_failed":
        meta = ((obj.get("subscription_details") or {}).get("metadata")) or {}
        org_id = meta.get("org_id")
        status = "past_due"
    elif etype == "checkout.session.completed":
        org_id = obj.get("client_reference_id")
        if org_id:
            find_subscription_by_org(org_id)  # records sub + status
            print(f"[adapix] stripe checkout completed for org {org_id}")
        return {"ok": True}

    if org_id and status:
        set_billing(org_id, {"status": status,
                             "subscription_id": obj.get("id") if etype.startswith("customer.subscription.") else None}
                    if etype.startswith("customer.subscription.") else {"status": status})
        print(f"[adapix] stripe {etype}: org {org_id} -> {status}")
        if status == "active":
            _maybe_reward_referrer(org_id)
    return {"ok": True}


def _maybe_reward_referrer(referred_org_id: str) -> None:
    """Give a month, get a month: the referrer's $99 credit lands the first
    time the referred business's subscription turns ACTIVE (i.e. they became
    a real paying customer, not just a trial signup). referral_rewarded makes
    webhook retries harmless."""
    try:
        from ..billing import apply_referral_credit
        from ..db import get_session
        from ..models import Organization

        with get_session() as s:
            org = s.get(Organization, referred_org_id)
            if org is None or not org.referred_by_code or org.referral_rewarded:
                return
            referrer = (
                s.query(Organization)
                .filter(Organization.referral_code == org.referred_by_code)
                .first()
            )
            if referrer is None:
                return
            if apply_referral_credit(referrer.id):
                org.referral_rewarded = True
                print(f"[adapix] referral reward: org {referrer.id} credited for referring {referred_org_id}")
            else:
                # Referrer has no Stripe customer yet — leave unrewarded so a
                # later subscription event retries the credit.
                print(f"[adapix] referral reward PENDING: referrer {referrer.id} has no Stripe customer yet")
    except Exception as e:
        print(f"[adapix] referral reward error: {e}")
