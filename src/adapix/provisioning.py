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

        result = create_vapi_number(settings, area_code=area_code, name=org.name)
        if not result:
            org.phone_status = "failed"
            return {"ok": False, "reason": "provision_failed"}

        pid, number = result
        org.vapi_phone_number_id = pid
        org.phone_number = number
        org.phone_status = "provisioned"
        return {"ok": True, "status": "provisioned", "number": number}
