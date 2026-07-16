"""CNAM registration — getting the business's actual NAME (not just a number)
to show on a customer's caller ID screen.

This is a real Twilio Trust Hub compliance submission, not a simple API call:
the business's real legal identity has to be verified before Twilio will let
its name appear on caller ID nationwide. The exact resource shapes and policy
SIDs below were pulled live from Twilio's own Trust Hub Policies API (GET
/v1/Policies + /v1/Policies/{sid}), not guessed from docs — see the policy
requirements this mirrors:

  - Primary Customer Profile of type Business (RN6433...bdd0): needs a
    business-information end-user, at least one authorized-representative
    end-user, and a physical address as a supporting document.
  - CNAM (RNf3db...ea53): needs a cnam_information end-user (just the display
    name) plus a reference to an APPROVED primary business customer profile.

Like the A2P 10DLC texting registration this project already went through,
Twilio's real review can reject a submission for reasons that only show up
once you try — expect this to need at least one iteration round once real
rejection text comes back, the same as A2P did.
"""
from __future__ import annotations

from typing import Any

from .config import Settings
from .db import get_session
from .models import Organization

POLICY_PRIMARY_BUSINESS = "RN6433641899984f951173ef1738c3bdd0"
POLICY_CNAM = "RNf3db3cd1fe25fcfd3c3ded065c8fea53"

# CNAM display names longer than 15 characters get silently truncated by
# most US carriers' CNAM lookup systems — enforce it here so the business
# sees the real constraint instead of a name that gets cut off on someone's
# phone.
CNAM_MAX_LEN = 15


def _client(settings: Settings):
    from twilio.rest import Client
    return Client(settings.twilio_account_sid, settings.twilio_auth_token)


def submit_cnam_registration(org_id: str, form: dict[str, Any]) -> dict:
    """Submit a business for CNAM (caller-ID-name) registration.

    `form` keys (all required unless noted):
      legal_business_name, business_type (one of: sole_proprietor, llc,
      corporation, partnership, nonprofit), ein (optional), industry,
      website_url, rep_first_name, rep_last_name, rep_email, rep_phone,
      rep_job_title, address_street, address_city, address_region,
      address_postal_code, cnam_display_name

    Real money is never spent here — this only submits compliance paperwork
    to Twilio. Takes real days for Twilio to actually approve/reject; this
    function only gets the submission INTO their review queue.
    """
    settings = Settings()
    if not (settings.twilio_account_sid and settings.twilio_auth_token):
        return {"ok": False, "reason": "not_configured"}

    display_name = (form.get("cnam_display_name") or "").strip()[:CNAM_MAX_LEN]
    if not display_name:
        return {"ok": False, "reason": "missing_field", "detail": "cnam_display_name is required"}

    with get_session(settings) as s:
        org = s.get(Organization, org_id)
        if org is None:
            return {"ok": False, "reason": "no_org"}
        if org.phone_tier != "dedicated" or not org.twilio_phone_sid:
            return {"ok": False, "reason": "needs_dedicated_line",
                     "detail": "Upgrade to a dedicated calling line first."}

        c = _client(settings)
        business_type_map = {
            "sole_proprietor": "Sole Proprietor",
            "llc": "Limited Liability Corporation",
            "corporation": "Corporation",
            "partnership": "Partnership",
            "nonprofit": "Non-profit Corporation",
        }
        business_type = business_type_map.get((form.get("business_type") or "").strip().lower(), "Sole Proprietor")

        try:
            # 1. Physical address (Twilio's core Addresses API — structured,
            # not the free-text address already in Business Profile).
            address = c.addresses.create(
                customer_name=form.get("legal_business_name") or org.name,
                street=form.get("address_street") or "",
                city=form.get("address_city") or "",
                region=form.get("address_region") or "",
                postal_code=form.get("address_postal_code") or "",
                iso_country="US",
            )

            supporting_doc = c.trusthub.v1.supporting_documents.create(
                friendly_name=f"{org.name} — business address",
                type="customer_profile_address",
                attributes={"address_sids": address.sid},
            )

            business_info_user = c.trusthub.v1.end_users.create(
                friendly_name=f"{org.name} — business information",
                type="customer_profile_business_information",
                attributes={
                    "business_name": form.get("legal_business_name") or org.name,
                    "business_type": business_type,
                    "business_registration_number": form.get("ein") or "",
                    "business_registration_identifier": "EIN" if form.get("ein") else "",
                    "business_identity": "direct_customer",
                    "business_industry": (form.get("industry") or "TECHNOLOGY").upper(),
                    "website_url": form.get("website_url") or "",
                    "business_regions_of_operation": "USA_AND_CANADA",
                },
            )

            rep_user = c.trusthub.v1.end_users.create(
                friendly_name=f"{org.name} — authorized representative",
                type="authorized_representative_1",
                attributes={
                    "first_name": form.get("rep_first_name") or "",
                    "last_name": form.get("rep_last_name") or "",
                    "email": form.get("rep_email") or "",
                    "phone_number": form.get("rep_phone") or "",
                    "business_title": form.get("rep_job_title") or "Owner",
                    "job_position": form.get("rep_job_title") or "Owner",
                },
            )

            # 2. The Primary Business Customer Profile — the umbrella
            # business-identity record CNAM attaches to.
            profile = c.trusthub.v1.customer_profiles.create(
                friendly_name=f"{org.name} — business profile",
                email=form.get("rep_email") or "",
                policy_sid=POLICY_PRIMARY_BUSINESS,
            )
            for object_sid in (business_info_user.sid, rep_user.sid, supporting_doc.sid):
                c.trusthub.v1.customer_profiles(profile.sid).customer_profiles_entity_assignments.create(
                    object_sid=object_sid,
                )
            c.trusthub.v1.customer_profiles(profile.sid).customer_profiles_evaluations.create(
                policy_sid=POLICY_PRIMARY_BUSINESS,
            )
            c.trusthub.v1.customer_profiles(profile.sid).update(status="pending-review")

            # 3. The CNAM trust product itself, referencing the (now
            # pending-review) business profile + the display name.
            cnam_user = c.trusthub.v1.end_users.create(
                friendly_name=f"{org.name} — CNAM display name",
                type="cnam_information",
                attributes={"cnam_display_name": display_name},
            )
            trust_product = c.trusthub.v1.trust_products.create(
                friendly_name=f"{org.name} — CNAM",
                email=form.get("rep_email") or "",
                policy_sid=POLICY_CNAM,
            )
            for object_sid in (cnam_user.sid, profile.sid):
                c.trusthub.v1.trust_products(trust_product.sid).trust_products_entity_assignments.create(
                    object_sid=object_sid,
                )
            c.trusthub.v1.trust_products(trust_product.sid).trust_products_evaluations.create(
                policy_sid=POLICY_CNAM,
            )
            c.trusthub.v1.trust_products(trust_product.sid).update(status="pending-review")

            # 4. Attach the org's actual phone number to the CNAM registration.
            c.trusthub.v1.trust_products(trust_product.sid).trust_products_channel_endpoint_assignment.create(
                channel_endpoint_type="phone-number",
                channel_endpoint_sid=org.twilio_phone_sid,
            )
        except Exception as e:  # noqa: BLE001 — surface Twilio's real error text
            return {"ok": False, "reason": "submit_failed", "detail": str(e)}

        org.cnam_customer_profile_sid = profile.sid
        org.cnam_trust_product_sid = trust_product.sid
        org.cnam_status = "submitted"
        return {"ok": True, "status": "submitted"}


def refresh_cnam_status(org_id: str) -> dict:
    """Re-check Twilio for a verdict on an already-submitted CNAM registration.
    Cheap read-only call — safe to run on every Settings page load."""
    settings = Settings()
    with get_session(settings) as s:
        org = s.get(Organization, org_id)
        if org is None or not org.cnam_trust_product_sid:
            return {"status": (org.cnam_status if org else "none")}
        if org.cnam_status in ("approved", "rejected"):
            return {"status": org.cnam_status}
        try:
            tp = _client(settings).trusthub.v1.trust_products(org.cnam_trust_product_sid).fetch()
            status_map = {"twilio-approved": "approved", "twilio-rejected": "rejected"}
            new_status = status_map.get(tp.status)
            if new_status and new_status != org.cnam_status:
                org.cnam_status = new_status
            return {"status": org.cnam_status}
        except Exception:
            return {"status": org.cnam_status}
