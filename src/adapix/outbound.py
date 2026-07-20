"""Single source of truth for HOW an outbound SMS leaves the building.

Every business texts from its OWN line: the org's dedicated Blooio
iMessage number first (blue bubble on Apple; Blooio does its own RCS/SMS
fallback for Android), then the shared Claw platform line, then Twilio SMS.
Any failure falls through so the message still goes out.

This ordering existed in three different places (ApprovalManager._send_one,
the inbound auto-reply, the escalation reply) and drifted — the auto-mode
campaign path and older reply paths sent via the shared Twilio number,
landing in a different thread than the one the customer texted. This
function is the one implementation they should all call.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class OrgSendResult:
    status: str
    provider_id: str | None
    error: str | None
    transport: str | None   # "imessage" | "imessage-claw" | "sms" | None


def send_org_sms(settings, org, to: str, body: str, *, first_touch: bool = False,
                 dry_run: bool = False) -> OrgSendResult:
    """Send `body` to `to` on `org`'s own line first, then fall back.
    `org` may be None (no dedicated line) — then it's just Twilio SMS."""
    from .channels import ClawChannel, IMessageChannel, SmsChannel

    blooio_channel_id = getattr(org, "blooio_channel_id", None) if org else None

    # Apply the first-touch STOP footer to the MESSAGE, not the transport,
    # so every path carries it and Twilio's own footer logic no-ops.
    body_text = body
    if first_touch and "stop" not in (body_text or "").lower():
        body_text = (body_text or "").rstrip() + SmsChannel.STOP_FOOTER

    if settings.prefer_imessage and blooio_channel_id:
        imsg = IMessageChannel(settings, dry_run=dry_run)
        if imsg.is_configured(blooio_channel_id):
            r = imsg.send(to or "", body_text, channel_id=blooio_channel_id)
            if r.status != "failed":
                return OrgSendResult(r.status, r.provider_id, r.error, "imessage")

    if settings.prefer_imessage:
        claw = ClawChannel(settings, dry_run=dry_run)
        if claw.is_configured():
            r = claw.send(to or "", body_text)
            if r.status != "failed":
                return OrgSendResult(r.status, r.provider_id, r.error, "imessage-claw")

    # first_touch=False here: the footer is already applied above, so this
    # avoids a double footer on the Twilio fallback.
    r = SmsChannel(settings, dry_run=dry_run).send(to or "", body_text, first_touch=False)
    return OrgSendResult(r.status, r.provider_id, r.error, "sms")
