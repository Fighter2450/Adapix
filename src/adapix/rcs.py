"""RCS Business Messaging registration — business name + logo on Android
text messages (the texting counterpart to cnam.py, which is calls-only).

Three chained Twilio Trust Hub submissions, in dependency order:
  1. RCS Google Registration — Google's own approval layer.
  2. RCS Core Registration — the brand/sender profile (logo, banner,
     screenshots, policy copy).
  3. RCS US Registration — the business/use-case registration, which
     REQUIRES the (now pending) RCS Google registration as a supporting
     trust product — this is why Google's registration has to exist first.

Policy SIDs and every field name below were pulled live from Twilio's own
Trust Hub Policies API (GET /v1/Policies + /v1/Policies/{sid}), not
guessed — see docs/RCS_REGISTRATION.md for the reference dump. Like A2P
10DLC and CNAM, expect Twilio/Google's real review to need at least one
iteration round once real rejection text comes back.
"""
from __future__ import annotations

from typing import Any

from .config import Settings
from .db import get_session
from .models import Organization

POLICY_RCS_CORE = "RN0d2c11bb6006398eb9cb1ea03a71016c"
POLICY_RCS_US = "RN3038ea6be8e9792456beb40f0a18695d"
POLICY_RCS_GOOGLE = "RN285c55a933ea017bdc9c0d6e6ad1ce00"

# Hosted brand assets — real, live URLs (see docs/RCS_REGISTRATION.md).
LOGO_URL = "https://adapixai.com/assets/rcs/logo.png"
BANNER_URL = "https://adapixai.com/assets/rcs/banner.png"
SCREENSHOT_URLS = [
    "https://adapixai.com/assets/rcs/screenshots/home.png",
    "https://adapixai.com/assets/rcs/screenshots/calls.png",
    "https://adapixai.com/assets/rcs/screenshots/sms_email.png",
]
DEMO_VIDEO_URL = "https://adapixai.com/assets/rcs/demo.webm"

# Drafted product-behavior copy — real, factual, not fabricated business
# identity (see docs/RCS_REGISTRATION.md for the source of truth).
USE_CASE_DESCRIPTION = (
    "Adapix is an AI follow-up assistant for small service businesses "
    "(plumbers, contractors, salons, and similar). It drafts and sends "
    "personalized follow-up texts and emails to a business's own customers "
    "— reminding them about a quote, a missed appointment, or an upcoming "
    "visit — and every message is reviewed and approved by the business "
    "owner before it goes out."
)
TRIGGER_EVENT_DESCRIPTION = (
    "A message is triggered when a business owner adds a new customer due "
    "for a follow-up, enough time passes since a quote or appointment with "
    "no response, or a customer replies to an existing conversation."
)
OPT_IN_DESCRIPTION = (
    "Contacts are added only by the business owner, who confirms the "
    "customer already gave their contact information directly to the "
    "business (e.g. by requesting a quote or booking a service). Adapix "
    "does not purchase, scrape, or otherwise acquire contact lists."
)
OPT_OUT_DESCRIPTION = (
    "Every message honors STOP automatically and permanently — replying "
    "STOP immediately halts all future messages to that contact across "
    "every channel, with no owner action required to enforce it."
)
ACCESS_INSTRUCTION = (
    "No app or account is required to receive messages — customers simply "
    "reply to the number/thread they already have."
)
HELP_SAMPLE_MESSAGE = (
    "This is Adapix, an AI assistant for [Business Name]. For help, "
    "contact [business phone/email]. Msg & data rates may apply. Reply "
    "STOP to opt out."
)
STOP_SAMPLE_MESSAGE = (
    "You've been unsubscribed and won't receive further messages from "
    "[Business Name]. Reply START to resubscribe."
)
MESSAGING_FLOW = (
    "Business owner adds/imports a contact with consent on file -> Adapix "
    "drafts a personalized follow-up -> owner reviews and approves (or "
    "edits) the draft -> message sends -> if the customer replies, Adapix "
    "reads the reply and continues the conversation, escalating to the "
    "owner whenever it can't confidently answer."
)
CAMPAIGN_OVERVIEW = (
    "Transactional/relationship follow-up messaging for small service "
    "businesses — job quotes, appointment reminders, and post-service "
    "check-ins with existing customers, not marketing blasts to purchased "
    "lists."
)


def _client(settings: Settings):
    from twilio.rest import Client
    return Client(settings.twilio_account_sid, settings.twilio_auth_token)


def _address_and_doc(c, org: Organization, form: dict[str, Any], doc_type: str):
    """Twilio's Address resource + a SupportingDocument wrapping it —
    shared by all three RCS registrations."""
    address = c.addresses.create(
        customer_name=form.get("legal_business_name") or org.name,
        street=form.get("address_street") or "",
        city=form.get("address_city") or "",
        region=form.get("address_region") or "",
        postal_code=form.get("address_postal_code") or "",
        iso_country="US",
    )
    doc = c.trusthub.v1.supporting_documents.create(
        friendly_name=f"{org.name} — business address ({doc_type})",
        type=doc_type,
        attributes={"address_sids": address.sid},
    )
    return doc


def submit_rcs_registration(org_id: str, form: dict[str, Any]) -> dict:
    """Submit the three chained RCS registrations. `form` keys (all
    required unless noted): legal_business_name, business_type (one of:
    sole_proprietor, llc, corporation, partnership, nonprofit), ein
    (optional), industry, website_url, rep_first_name, rep_last_name,
    rep_email, rep_phone, rep_job_title, address_street, address_city,
    address_region, address_postal_code, brand_display_name (the sender
    name shown to recipients), monthly_message_volume (a real estimate,
    integer as a string)."""
    settings = Settings()
    if not (settings.twilio_account_sid and settings.twilio_auth_token):
        return {"ok": False, "reason": "not_configured"}

    brand_name = (form.get("brand_display_name") or "").strip()
    if not brand_name:
        return {"ok": False, "reason": "missing_field", "detail": "brand_display_name is required"}

    with get_session(settings) as s:
        org = s.get(Organization, org_id)
        if org is None:
            return {"ok": False, "reason": "no_org"}
        if org.phone_tier != "dedicated" or not org.twilio_phone_sid:
            return {"ok": False, "reason": "needs_dedicated_line",
                     "detail": "Upgrade to a dedicated calling line first — RCS needs a real business number."}

        c = _client(settings)
        business_type_map = {
            "sole_proprietor": "Sole Proprietor",
            "llc": "Limited Liability Corporation",
            "corporation": "Corporation",
            "partnership": "Partnership",
            "nonprofit": "Non-profit Corporation",
        }
        business_type = business_type_map.get((form.get("business_type") or "").strip().lower(), "Sole Proprietor")
        rep_email = form.get("rep_email") or ""

        try:
            # ---- Stage 1: RCS Google Registration (no dependencies) ----
            google_rep = c.trusthub.v1.end_users.create(
                friendly_name=f"{org.name} — RCS Google rep",
                type="authorized_representative_1",
                attributes={
                    "first_name": form.get("rep_first_name") or "",
                    "last_name": form.get("rep_last_name") or "",
                    "email": rep_email,
                    "business_title": form.get("rep_job_title") or "Owner",
                    "website_url": form.get("website_url") or "",
                },
            )
            google_use_case = c.trusthub.v1.end_users.create(
                friendly_name=f"{org.name} — RCS Google use case",
                type="use_case",
                attributes={
                    "access_instruction": ACCESS_INSTRUCTION,
                    "opt_in_description": OPT_IN_DESCRIPTION,
                    "opt_out_description": OPT_OUT_DESCRIPTION,
                    "screenshot_urls": ",".join(SCREENSHOT_URLS),
                    "trigger_event_description": TRIGGER_EVENT_DESCRIPTION,
                    "use_case_description": USE_CASE_DESCRIPTION,
                    "video_urls": DEMO_VIDEO_URL,
                },
            )
            google_tp = c.trusthub.v1.trust_products.create(
                friendly_name=f"{org.name} — RCS Google Registration",
                email=rep_email,
                policy_sid=POLICY_RCS_GOOGLE,
            )
            for object_sid in (google_rep.sid, google_use_case.sid):
                c.trusthub.v1.trust_products(google_tp.sid).trust_products_entity_assignments.create(object_sid=object_sid)
            c.trusthub.v1.trust_products(google_tp.sid).trust_products_evaluations.create(policy_sid=POLICY_RCS_GOOGLE)
            c.trusthub.v1.trust_products(google_tp.sid).update(status="pending-review")

            # ---- Stage 2: RCS Core Registration (brand/sender profile) ----
            core_business = c.trusthub.v1.end_users.create(
                friendly_name=f"{org.name} — RCS Core business info",
                type="business",
                attributes={
                    "business_name": form.get("legal_business_name") or org.name,
                    "business_registration_identifier": "EIN" if form.get("ein") else "",
                    "business_registration_number": form.get("ein") or "",
                    "business_type": business_type,
                    "business_identity": "direct_customer",
                    "is_subassigned": "false",
                    "business_website": form.get("website_url") or "",
                },
            )
            core_rep = c.trusthub.v1.end_users.create(
                friendly_name=f"{org.name} — RCS Core rep",
                type="authorized_representative_1",
                attributes={
                    "first_name": form.get("rep_first_name") or "",
                    "last_name": form.get("rep_last_name") or "",
                    "business_title": form.get("rep_job_title") or "Owner",
                    "email": rep_email,
                },
            )
            core_sender = c.trusthub.v1.end_users.create(
                friendly_name=f"{org.name} — RCS sender info",
                type="rcs_sender",
                attributes={
                    "sender_requested_countries": "US",
                    "brand_name": brand_name,
                    "sender_name": brand_name,
                    "sender_description": USE_CASE_DESCRIPTION,
                    "logo_url": LOGO_URL,
                    "banner_url": BANNER_URL,
                    "phone_numbers": org.phone_number or "",
                    "phone_number_display_name": brand_name,
                    "emails": rep_email,
                    "email_display_name": brand_name,
                    "websites": form.get("website_url") or "",
                    "website_display_name": brand_name,
                    "display_color": "#7c3aed",
                    "privacy_notice_url": "https://adapixai.com/privacy",
                    "terms_conditions_url": "https://adapixai.com/terms",
                    "opt_in_description": OPT_IN_DESCRIPTION,
                    "opt_out_description": OPT_OUT_DESCRIPTION,
                    "trigger_event_description": TRIGGER_EVENT_DESCRIPTION,
                    "access_instruction": ACCESS_INSTRUCTION,
                    "video_url": DEMO_VIDEO_URL,
                    "screenshot_url": SCREENSHOT_URLS[0],
                    "use_case_description": USE_CASE_DESCRIPTION,
                },
            )
            core_doc = _address_and_doc(c, org, form, "business_address")
            core_tp = c.trusthub.v1.trust_products.create(
                friendly_name=f"{org.name} — RCS Core Registration",
                email=rep_email,
                policy_sid=POLICY_RCS_CORE,
            )
            for object_sid in (core_business.sid, core_rep.sid, core_sender.sid, core_doc.sid):
                c.trusthub.v1.trust_products(core_tp.sid).trust_products_entity_assignments.create(object_sid=object_sid)
            c.trusthub.v1.trust_products(core_tp.sid).trust_products_evaluations.create(policy_sid=POLICY_RCS_CORE)
            c.trusthub.v1.trust_products(core_tp.sid).update(status="pending-review")

            # ---- Stage 3: RCS US Registration (depends on Google's sid) ----
            us_business = c.trusthub.v1.end_users.create(
                friendly_name=f"{org.name} — RCS US business info",
                type="business",
                attributes={
                    "business_industry": (form.get("industry") or "TECHNOLOGY").upper(),
                    "business_name": form.get("legal_business_name") or org.name,
                    "business_type": business_type,
                    "business_registration_number": form.get("ein") or "",
                    "business_registration_issuing_country_iso_code": "US",
                },
            )
            us_contact = c.trusthub.v1.end_users.create(
                friendly_name=f"{org.name} — RCS US brand contact",
                type="authorized_contact",
                attributes={"mobile_phone_number": form.get("rep_phone") or ""},
            )
            volume = form.get("monthly_message_volume") or "0"
            us_use_case = c.trusthub.v1.end_users.create(
                friendly_name=f"{org.name} — RCS US use case",
                type="use_case",
                attributes={
                    "organic_website_traffic_monthly": "0",
                    "has_existing_short_code_traffic": "false",
                    "existing_short_code_number": "",
                    "existing_short_code_traffic_monthly": "0",
                    "rbm_traffic_forecast_monthly": str(volume),
                    "campaign_overview": CAMPAIGN_OVERVIEW,
                    "messaging_flow": MESSAGING_FLOW,
                    "help_sample_message": HELP_SAMPLE_MESSAGE,
                    "stop_sample_message": STOP_SAMPLE_MESSAGE,
                    "message_service_type": "customer_care",
                },
            )
            us_doc = _address_and_doc(c, org, form, "business_address")
            us_tp = c.trusthub.v1.trust_products.create(
                friendly_name=f"{org.name} — RCS US Registration",
                email=rep_email,
                policy_sid=POLICY_RCS_US,
            )
            for object_sid in (us_business.sid, us_contact.sid, us_use_case.sid, us_doc.sid, google_tp.sid):
                c.trusthub.v1.trust_products(us_tp.sid).trust_products_entity_assignments.create(object_sid=object_sid)
            c.trusthub.v1.trust_products(us_tp.sid).trust_products_evaluations.create(policy_sid=POLICY_RCS_US)
            c.trusthub.v1.trust_products(us_tp.sid).update(status="pending-review")

        except Exception as e:  # noqa: BLE001 — surface Twilio's real error text
            return {"ok": False, "reason": "submit_failed", "detail": str(e)}

        org.rcs_google_trust_product_sid = google_tp.sid
        org.rcs_core_trust_product_sid = core_tp.sid
        org.rcs_us_trust_product_sid = us_tp.sid
        org.rcs_status = "submitted"
        return {"ok": True, "status": "submitted"}


def refresh_rcs_status(org_id: str) -> dict:
    """Re-check Twilio for a verdict. All three registrations must be
    Twilio-approved for the org to count as fully approved; any one
    rejection surfaces as rejected."""
    settings = Settings()
    with get_session(settings) as s:
        org = s.get(Organization, org_id)
        if org is None or not org.rcs_us_trust_product_sid:
            return {"status": (org.rcs_status if org else "none")}
        if org.rcs_status in ("approved", "rejected"):
            return {"status": org.rcs_status}
        try:
            c = _client(settings)
            statuses = [
                c.trusthub.v1.trust_products(sid).fetch().status
                for sid in (org.rcs_google_trust_product_sid, org.rcs_core_trust_product_sid, org.rcs_us_trust_product_sid)
                if sid
            ]
            if any(st == "twilio-rejected" for st in statuses):
                org.rcs_status = "rejected"
            elif all(st == "twilio-approved" for st in statuses):
                org.rcs_status = "approved"
            return {"status": org.rcs_status}
        except Exception:
            return {"status": org.rcs_status}
