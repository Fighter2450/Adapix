"""Approval workflow for outbound messages.

When a practice is configured with approval_mode="required", the campaign
runner composes outbound messages but stores them with status="pending_approval"
instead of sending. A human (the practice owner / office manager) reviews the
queue in the admin UI or CLI, optionally edits the body, then either:

  - approves   -> status becomes "approved" (will be sent on next send_approved)
  - approves+sends (one-shot for the admin UI single-click flow)
  - rejects    -> status becomes "rejected" and the message is never sent

This is critical for v1 pilots: practices want to read every message we send
on their behalf for the first few weeks before letting Adapix go fully
autonomous. Flip the YAML field once they're comfortable.

Status state machine (Message.status):

    composed
       |
       v
    pending_approval ---approve---> approved ---send---> sent | failed
                  \\                    ^
                   ---reject----> rejected (terminal, also reachable from approved)

scheduled_at is orthogonal to status: manual scheduling (Write a message's
"Send at", Queue a call's "Call at") sets it on an otherwise-normal
"approved" row. send_approved()'s background sweep (main.py's
_scheduled_send_loop) only sends rows where scheduled_at is NULL or due,
inside the same 8am-9pm ET quiet-hours window the automated cadence uses.
"""
from __future__ import annotations

from dataclasses import dataclass

from .channels import EmailChannel, EmailResult, IMessageChannel, SmsChannel, VoiceChannel
from .config import Settings
from .db import get_session
from .models import Campaign, Message, Organization, Patient


PENDING = "pending_approval"
APPROVED = "approved"
REJECTED = "rejected"
SENT = "sent"
FAILED = "failed"


@dataclass
class PendingMessage:
    """Flat view of a queued message for the approval UI / CLI."""

    id: int
    campaign_id: int
    practice_id: str
    workflow_id: str
    patient_id: int
    patient_name: str
    patient_phone: str | None
    patient_email: str | None
    day_in_campaign: int | None
    channel: str
    subject: str | None
    body: str
    created_at: str


class ApprovalManager:
    """Queue management for pending-approval messages."""

    def __init__(self, *, dry_run: bool = False):
        self.settings = Settings()
        self.dry_run = dry_run

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def list_pending(self, practice_id: str | None = None) -> list[PendingMessage]:
        with get_session(self.settings) as s:
            messages = (
                s.query(Message)
                .filter(Message.status == PENDING)
                .order_by(Message.created_at.asc())
                .all()
            )
            out: list[PendingMessage] = []
            for m in messages:
                campaign = s.get(Campaign, m.campaign_id)
                if campaign is None:
                    continue
                if practice_id and campaign.practice_id != practice_id:
                    continue
                patient = s.get(Patient, campaign.patient_id)
                pname = (
                    f"{patient.first_name} {patient.last_name}" if patient else "Unknown"
                )
                out.append(
                    PendingMessage(
                        id=m.id,
                        campaign_id=m.campaign_id,
                        practice_id=campaign.practice_id,
                        workflow_id=campaign.workflow_id,
                        patient_id=campaign.patient_id,
                        patient_name=pname,
                        patient_phone=patient.phone if patient else None,
                        patient_email=patient.email if patient else None,
                        day_in_campaign=m.day_in_campaign,
                        channel=m.channel,
                        subject=m.subject,
                        body=m.body,
                        created_at=m.created_at.isoformat() if m.created_at else "",
                    )
                )
            return out

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def reject(self, message_id: int, *, reason: str | None = None) -> bool:
        with get_session(self.settings) as s:
            m = s.get(Message, message_id)
            # A scheduled send/call sits in "approved" (not yet sent) until
            # its scheduled_at comes due — that's still cancellable, same as
            # a plain pending-approval draft.
            if m is None or m.status not in (PENDING, APPROVED):
                return False
            m.status = REJECTED
            md = dict(m.metadata_json or {})
            md["rejected"] = True
            if reason:
                md["reject_reason"] = reason
            m.metadata_json = md
            return True

    def approve(self, message_id: int, *, edited_body: str | None = None) -> bool:
        """Approve a pending message. Marks status='approved'. Does NOT send."""
        with get_session(self.settings) as s:
            m = s.get(Message, message_id)
            if m is None or m.status != PENDING:
                return False
            if edited_body is not None and edited_body.strip() != m.body.strip():
                md = dict(m.metadata_json or {})
                md["original_body"] = m.body
                md["edited_by_human"] = True
                m.metadata_json = md
                m.body = edited_body.strip()
            m.status = APPROVED
            return True

    def send_approved(self, practice_id: str | None = None) -> int:
        """Send every 'approved' message that's actually due: no scheduled_at
        (send/place as soon as approved — today's default), or scheduled_at
        that has arrived. Same TCPA quiet-hours window as the automated
        cadence — a message scheduled for 2am waits for the window to open
        rather than firing at the exact requested time."""
        from datetime import datetime
        from zoneinfo import ZoneInfo
        now = datetime.utcnow()
        local_hour = datetime.now(ZoneInfo("America/New_York")).hour
        in_quiet_hours_window = 8 <= local_hour < 21

        sms = SmsChannel(self.settings, dry_run=self.dry_run)
        email = EmailChannel(self.settings, dry_run=self.dry_run)
        voice = VoiceChannel(self.settings, dry_run=self.dry_run)
        attempted = 0
        with get_session(self.settings) as s:
            messages = (
                s.query(Message)
                .filter(Message.status == APPROVED)
                .order_by(Message.created_at.asc())
                .all()
            )
            for m in messages:
                if m.scheduled_at is not None:
                    if m.scheduled_at > now or not in_quiet_hours_window:
                        continue
                campaign = s.get(Campaign, m.campaign_id)
                if campaign is None:
                    continue
                if practice_id and campaign.practice_id != practice_id:
                    continue
                patient = s.get(Patient, campaign.patient_id)
                if patient is None:
                    continue
                if patient.opted_out:
                    m.status = "rejected"
                    continue
                org = s.get(Organization, campaign.practice_id)
                prior_sent = self._prior_sms_sent_to_patient(s, campaign.patient_id, exclude_message_id=m.id)
                self._send_one(
                    m, patient, sms, email, voice,
                    org.vapi_phone_number_id if org else None,
                    org.name if org else None,
                    campaign.practice_id,
                    org.blooio_channel_id if org else None,
                    first_touch=prior_sent == 0,
                )
                attempted += 1
        return attempted

    def approve_and_send(
        self, message_id: int, *, edited_body: str | None = None
    ) -> str:
        """Approve a single message and send it immediately (UI single-click flow)."""
        if not self.approve(message_id, edited_body=edited_body):
            return "not_found_or_not_pending"
        sms = SmsChannel(self.settings, dry_run=self.dry_run)
        email = EmailChannel(self.settings, dry_run=self.dry_run)
        voice = VoiceChannel(self.settings, dry_run=self.dry_run)
        with get_session(self.settings) as s:
            m = s.get(Message, message_id)
            if m is None or m.status != APPROVED:
                return "approved_but_lookup_failed"
            campaign = s.get(Campaign, m.campaign_id)
            patient = s.get(Patient, campaign.patient_id) if campaign else None
            if patient is None:
                return "no_patient"
            if patient.opted_out:
                m.status = "rejected"
                meta = dict(m.metadata_json or {})
                meta["rejected_reason"] = "contact opted out"
                m.metadata_json = meta
                return "opted_out"
            org = s.get(Organization, campaign.practice_id) if campaign else None
            prior_sent = self._prior_sms_sent_to_patient(s, campaign.patient_id, exclude_message_id=m.id) if campaign else 0
            self._send_one(
                m, patient, sms, email, voice,
                org.vapi_phone_number_id if org else None,
                org.name if org else None,
                campaign.practice_id if campaign else None,
                org.blooio_channel_id if org else None,
                first_touch=prior_sent == 0,
            )
            return m.status

    def send_now(self, message_id: int) -> str:
        """Send/place an already-APPROVED message immediately, ignoring
        scheduled_at and the quiet-hours window — the explicit-human-action
        override for a scheduled call or message someone doesn't want to
        wait on (Calls tab 'Call now' on a Scheduled entry). Same immediacy
        as approve_and_send's single-click flow, just for a message that's
        already past the approve step."""
        sms = SmsChannel(self.settings, dry_run=self.dry_run)
        email = EmailChannel(self.settings, dry_run=self.dry_run)
        voice = VoiceChannel(self.settings, dry_run=self.dry_run)
        with get_session(self.settings) as s:
            m = s.get(Message, message_id)
            if m is None or m.status != APPROVED:
                return "not_found_or_not_approved"
            campaign = s.get(Campaign, m.campaign_id)
            patient = s.get(Patient, campaign.patient_id) if campaign else None
            if patient is None:
                return "no_patient"
            if patient.opted_out:
                m.status = "rejected"
                meta = dict(m.metadata_json or {})
                meta["rejected_reason"] = "contact opted out"
                m.metadata_json = meta
                return "opted_out"
            org = s.get(Organization, campaign.practice_id) if campaign else None
            prior_sent = self._prior_sms_sent_to_patient(s, campaign.patient_id, exclude_message_id=m.id) if campaign else 0
            self._send_one(
                m, patient, sms, email, voice,
                org.vapi_phone_number_id if org else None,
                org.name if org else None,
                campaign.practice_id if campaign else None,
                org.blooio_channel_id if org else None,
                first_touch=prior_sent == 0,
            )
            return m.status

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    def _prior_sms_sent_to_patient(session, patient_id: int, *, exclude_message_id: int) -> int:
        """How many outbound SMS this PATIENT has already received, across
        every campaign they've ever had — not just the current one.

        Determines the SMS opt-out (STOP) footer: it belongs on the actual
        first text to someone, not on every message. Scoping this to a
        single campaign_id (the old behavior) was wrong the moment a
        contact could have more than one campaign — a one-off "Write a
        message" send, an ad-hoc voice-call plan, and the automated
        follow-up cadence each get their OWN Campaign row, so a
        campaign-scoped count was always 0 for anything but the automated
        cadence and put "Reply STOP to opt out" on literally every manual
        send to a contact who'd already been texted plenty."""
        return (
            session.query(Message)
            .join(Campaign, Message.campaign_id == Campaign.id)
            .filter(
                Campaign.patient_id == patient_id,
                Message.id != exclude_message_id,
                Message.channel == "sms",
                Message.direction == "outbound",
                Message.status.in_(("sent", "delivered", "replied")),
            )
            .count()
        )

    @staticmethod
    def _patient_context(patient: Patient) -> str:
        """Compact who-you're-talking-to summary for the call assistant."""
        bits = [f"Name: {patient.first_name} {patient.last_name}".strip()]
        if patient.treatment_type:
            bits.append(f"Interested in: {patient.treatment_type}")
        if patient.treatment_plan_amount:
            bits.append(f"Quote: ${patient.treatment_plan_amount:,.0f}")
        if patient.notes:
            bits.append(f"Notes: {patient.notes}")
        return "\n".join(bits)

    def _send_one(
        self,
        message: Message,
        patient: Patient,
        sms: SmsChannel,
        email: EmailChannel,
        voice: VoiceChannel,
        org_phone_number_id: str | None = None,
        org_business_name: str | None = None,
        org_id: str | None = None,
        org_blooio_channel_id: str | None = None,
        first_touch: bool = False,
    ) -> None:
        if message.channel == "sms":
            # If THIS org has its own Blooio line, try iMessage first (blue
            # bubble on Apple devices; Blooio does its own RCS/SMS fallback
            # for Android). Any Blooio failure falls back to Twilio SMS so
            # the message still goes out — same preference-then-fallback
            # shape as connected-Gmail over the shared Resend sender. Each
            # business texts from its OWN line, never a shared one.
            result = None
            # Provider order: Claw Messenger (platform line, cheapest),
            # then a per-org Blooio line if one exists, then Twilio SMS.
            from .channels import ClawChannel
            claw = ClawChannel(self.settings, dry_run=self.dry_run)
            if self.settings.prefer_imessage and claw.is_configured():
                r = claw.send(patient.phone or "", message.body)
                md = dict(message.metadata_json or {})
                if r.status != "failed":
                    result = r
                    md["transport"] = "imessage-claw"
                else:
                    md["imessage_error"] = r.error
                message.metadata_json = md
            if result is None:
                imsg = IMessageChannel(self.settings, dry_run=self.dry_run)
                if self.settings.prefer_imessage and imsg.is_configured(org_blooio_channel_id):
                    r = imsg.send(patient.phone or "", message.body, channel_id=org_blooio_channel_id)
                    md = dict(message.metadata_json or {})
                    if r.status != "failed":
                        result = r
                        md["transport"] = "imessage"
                    else:
                        md["imessage_error"] = r.error
                    message.metadata_json = md
            if result is None:
                result = sms.send(patient.phone or "", message.body, first_touch=first_touch)
        elif message.channel == "email":
            subject = message.subject or "A note from your practice"
            # If the org connected their own Gmail/Outlook, send AS them.
            # Otherwise fall back to the shared Resend sender.
            from . import oauth
            r = oauth.send_email_for_org(org_id, patient.email or "", subject,
                                        message.body, org_business_name, self.settings) if org_id else {"ok": False, "error": "no org"}
            result = EmailResult(
                provider_id=r.get("provider_id"),
                status="sent" if r.get("ok") else "failed",
                error=r.get("error"),
            )
        elif message.channel == "call":
            # Calls are the one unmetered spend — check billing right before
            # dialing, not just when the plan was queued.
            if org_id:
                from .billing import engine_allowed
                from .models import Organization as _Org
                with get_session(self.settings) as _s:
                    _org = _s.get(_Org, org_id)
                allowed, _why = engine_allowed(org_id, _org.created_at if _org else None)
                if not allowed:
                    message.status = "rejected"
                    return
            # message.body is the human-approved CALL PLAN (goal + talking points).
            # It becomes the assistant's instructions; the opening line auto-discloses AI.
            # The business name comes from the ORG (its own identity), not a global setting.
            biz = org_business_name or self.settings.business_name
            # Optional phonetic spelling (Settings -> Business profile) so the
            # TTS voice says an unusual name correctly — "Adapix" read literally
            # by a generic pronunciation model doesn't always land right.
            biz_spoken = biz
            pronunciation_note = ""
            if org_id:
                try:
                    from .api.app_routes import _load_org_profile_data
                    with get_session(self.settings) as _s3:
                        pron = (_load_org_profile_data(_s3, org_id).get("pronunciation") or "").strip()
                    if pron and pron != biz:
                        biz_spoken = pron
                        pronunciation_note = (
                            f'\n\nPRONUNCIATION: The business\'s real name is "{biz}", but write it '
                            f'phonetically as "{pron}" whenever you SAY it out loud during this call — '
                            f'that spelling is only for correct AI pronunciation, not a name change.'
                        )
                except Exception:
                    pass
            system_prompt = (
                f"You are a warm, professional voice assistant calling on behalf of "
                f"{biz}. Here is what you're trying to accomplish "
                f"on this call — approved by the business:\n{message.body}\n\n"
                f"Who you're calling:\n{self._patient_context(patient)}\n\n"
                "Keep it brief and natural, listen more than you talk, and never pressure. "
                "You already disclosed you're an AI in your opening line. If they ask "
                "something you can't answer or want a person, warmly offer a callback and "
                "end politely."
                f"{pronunciation_note}"
            )
            result = voice.place_call(
                to=patient.phone or "",
                system_prompt=system_prompt,
                goal=message.body,
                business_name=biz_spoken,
                phone_number_id=org_phone_number_id,
                # so the end-of-call-report webhook can link the outcome back
                metadata={
                    "patient_id": patient.id,
                    "campaign_id": message.campaign_id,
                    "org_id": patient.practice_id,
                    "message_id": message.id,
                },
            )
        else:
            return

        # Map result.status into our terminal states.
        # ("dialing" = a call was successfully placed; the transcript arrives
        #  later via the /webhooks/vapi end-of-call-report.)
        if result.status.startswith("skipped"):
            new_status = SENT  # treat dry-run as sent for state machine cleanliness
            md = dict(message.metadata_json or {})
            md["dry_run"] = True
            message.metadata_json = md
        elif result.status in ("sent", "dialing", "queued", "ringing", "in-progress"):
            new_status = SENT  # a call was successfully placed (Vapi returns "queued")
        else:
            new_status = FAILED
            if result.error:
                md = dict(message.metadata_json or {})
                md["send_error"] = result.error
                message.metadata_json = md

        message.status = new_status
        if result.provider_id:
            message.provider_id = result.provider_id
