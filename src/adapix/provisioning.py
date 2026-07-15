"""Per-org calling-number provisioning.

At signup (or on demand from Settings), Adapix gives each business its OWN
dedicated calling number so they do zero telephony work. Today this buys a free
Vapi US number; when you productionize reputation management, swap
`create_vapi_number` for a Twilio buy + import (A-level STIR/SHAKEN + CNAM).
"""
from __future__ import annotations

from .channels.voice import create_vapi_number
from .config import Settings
from .db import get_session
from .models import Organization


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
        # inbound callback has somewhere to ring instead of dead air.
        fallback = None
        try:
            from .api.app_routes import _load_org_profile_data
            from sqlalchemy.orm import Session as _Session
            with _Session(s.bind) as _s2:
                fallback = ((_load_org_profile_data(_s2, org_id).get("practice") or {}).get("phone") or "").strip() or None
        except Exception:
            pass
        result = create_vapi_number(settings, area_code=area_code, name=org.name, fallback_number=fallback)
        if not result.phone_number_id:
            org.phone_status = "failed"
            return {"ok": False, "reason": "provision_failed", "detail": result.error}

        org.vapi_phone_number_id = result.phone_number_id
        org.phone_number = result.number
        org.phone_status = "provisioned"
        return {"ok": True, "status": "provisioned", "number": result.number}
