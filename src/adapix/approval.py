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
                  \\
                   ---reject----> rejected (terminal)
"""
from __future__ import annotations

from dataclasses import dataclass

from .channels import EmailChannel, SmsChannel, VoiceChannel
from .config import Settings
from .db import get_session
from .models import Campaign, Message, Patient


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
            if m is None or m.status != PENDING:
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
        """Send all messages currently in 'approved' status. Returns count attempted."""
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
                campaign = s.get(Campaign, m.campaign_id)
                if campaign is None:
                    continue
                if practice_id and campaign.practice_id != practice_id:
                    continue
                patient = s.get(Patient, campaign.patient_id)
                if patient is None:
                    continue
                self._send_one(m, patient, sms, email, voice)
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
            self._send_one(m, patient, sms, email, voice)
            return m.status

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

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
    ) -> None:
        if message.channel == "sms":
            result = sms.send(patient.phone or "", message.body)
        elif message.channel == "email":
            subject = message.subject or "A note from your practice"
            result = email.send(patient.email or "", subject, message.body)
        elif message.channel == "call":
            # message.body is the human-approved CALL PLAN (goal + talking points).
            # It becomes the assistant's instructions; the opening line auto-discloses AI.
            system_prompt = (
                f"You are a warm, professional voice assistant calling on behalf of "
                f"{self.settings.business_name}. Here is what you're trying to accomplish "
                f"on this call — approved by the business:\n{message.body}\n\n"
                f"Who you're calling:\n{self._patient_context(patient)}\n\n"
                "Keep it brief and natural, listen more than you talk, and never pressure. "
                "You already disclosed you're an AI in your opening line. If they ask "
                "something you can't answer or want a person, warmly offer a callback and "
                "end politely."
            )
            result = voice.place_call(
                to=patient.phone or "",
                system_prompt=system_prompt,
                goal=message.body,
                business_name=self.settings.business_name,
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
        elif result.status in ("sent", "dialing"):
            new_status = SENT
        else:
            new_status = FAILED
            if result.error:
                md = dict(message.metadata_json or {})
                md["send_error"] = result.error
                message.metadata_json = md

        message.status = new_status
        if result.provider_id:
            message.provider_id = result.provider_id
