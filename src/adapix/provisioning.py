"""Per-org calling-number provisioning.

At signup (or on demand from Settings), Adapix gives each business its OWN
dedicated calling number so they do zero telephony work. Today this buys a free
Vapi US number; when you productionize reputation management, swap
`create_vapi_number` for a Twilio buy + import (A-level STIR/SHAKEN + CNAM).
"""
from __future__ import annotations

import re

from .channels.voice import create_vapi_number
from .config import Settings
from .db import get_session
from .models import Organization
from .phone import normalize_phone

# Vapi's own E.164 validator is stricter than normalize_phone()'s "good
# enough to dedupe on" bar — normalize_phone() will happily return
# +1XXXXXXXXXX for literally any 10-digit run it finds in a messy string,
# which passed the old startswith("+") check here but still wasn't a real
# number and still got rejected. Require the exact US shape before trusting
# it as a fallback destination.
_E164_US = re.compile(r"^\+1\d{10}$")

# Vapi requires numberDesiredAreaCode or sipUri on every create-number
# request — omitting both is a hard 400, and the app has never collected an
# area-code preference from the owner. Fall back to a real major-metro area
# code (not toll-free/premium) rather than fail provisioning outright.
_DEFAULT_AREA_CODE = "212"


def ensure_org_number(org_id: str, *, area_code: str | None = None) -> dict:
    """Provision a dedicated number for an org if it doesn't have one yet.

    Idempotent: if the org already has a number, returns it untouched. Safe to
    call from a background task at signup or from the Settings "set up" button.
    """
    settings = Settings()
    with get_session(settings) as s:
        org = s.get(Organization, org_id)
        if org is None:
            return {"ok": False, "reason": "no_org"}
        if org.vapi_phone_number_id:
            return {
                "ok": True, "already": True,
                "status": org.phone_status, "number": org.phone_number,
            }
        if not settings.vapi_api_key:
            org.phone_status = "unconfigured"
            return {"ok": False, "reason": "vapi_not_configured"}

        # Look up the owner's real phone from what they taught Adapix so an
        # inbound callback has somewhere to ring instead of dead air. Must be
        # E.164 — Vapi rejects fallbackDestination.number outright otherwise,
        # and the Business Profile phone field stores whatever raw format
        # the owner typed ("(412) 555-0100"), never normalized before now.
        fallback = None
        try:
            from .api.app_routes import _load_org_profile_data
            from sqlalchemy.orm import Session as _Session
            with _Session(s.bind) as _s2:
                raw_phone = ((_load_org_profile_data(_s2, org_id).get("practice") or {}).get("phone") or "").strip()
            fallback = normalize_phone(raw_phone) if raw_phone else None
            if fallback and not _E164_US.match(fallback):
                fallback = None  # not a real, strictly-valid US E.164 number — omit rather than send garbage
        except Exception:
            pass

        # Vapi needs an area code (or sipUri) on every request; derive one
        # from the owner's own number when we have a real E.164 one, else a
        # sane default — never send neither and let the whole call 400.
        resolved_area_code = area_code
        if not resolved_area_code:
            if fallback and len(fallback) >= 5:
                resolved_area_code = fallback[2:5]  # "+1XXXNNNNNNN" -> "XXX"
            else:
                resolved_area_code = _DEFAULT_AREA_CODE

        result = create_vapi_number(settings, area_code=resolved_area_code, name=org.name, fallback_number=fallback)
        if not result.phone_number_id:
            org.phone_status = "failed"
            return {"ok": False, "reason": "provision_failed", "detail": result.error}

        org.vapi_phone_number_id = result.phone_number_id
        org.phone_number = result.number
        org.phone_status = "provisioned"
        return {"ok": True, "status": "provisioned", "number": result.number}
