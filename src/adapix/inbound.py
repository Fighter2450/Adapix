"""Inbound message processor.

When a patient/parent replies (currently SMS only), this module:
  1. Looks up the patient by phone, finds the active campaign.
  2. Logs the inbound message.
  3. Classifies it via the Escalator.
  4. Dispatches the right action:
       - stop / decline       → close the campaign, send (or don't) acknowledgment
       - clinical_question    → escalate to doctor, send acknowledgment
       - callback_request     → escalate to office manager, send acknowledgment
       - emergency            → urgent escalation, redirect to office phone
       - other (continue)     → agent composes a normal SMS reply

Dispatch results are returned so the webhook handler can log + respond.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .agent import AdapixAgent
from .channels import SmsChannel
from .config import Settings, load_practice, load_workflow
from .db import get_session
from .escalation import Classification, Escalator
from .models import (
    Campaign,
    CampaignStatus,
    EscalationEvent,
    Message,
    Patient,
)


@dataclass
class InboundResult:
    status: str                        # responded | escalated | declined | stopped | emergency | ignored
    classification: Classification | None = None
    response_body: str | None = None
    reason: str | None = None


class InboundProcessor:
    """Handles inbound replies. Construct once, call .process_sms() per inbound."""

    def __init__(self, *, dry_run: bool = False):
        self.settings = Settings()
        self.dry_run = dry_run
        self.escalator = Escalator(self.settings)
        self.sms = SmsChannel(self.settings, dry_run=dry_run)

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    def process_sms(
        self,
        from_number: str,
        body: str,
        provider_id: str | None = None,
    ) -> InboundResult:
        with get_session(self.settings) as s:
            patient = (
                s.query(Patient).filter(Patient.phone == from_number).first()
            )
            if patient is None:
                return InboundResult(status="ignored", reason="no patient match for phone")

            campaign = (
                s.query(Campaign)
                .filter(
                    Campaign.patient_id == patient.id,
                    Campaign.status == CampaignStatus.active.value,
                )
                .order_by(Campaign.started_at.desc())
                .first()
            )
            if campaign is None:
                # Still log inbound for audit, but no action
                s.add(
                    Message(
                        campaign_id=0,
                        direction="inbound",
                        channel="sms",
                        body=body,
                        status="received_no_campaign",
                        provider_id=provider_id,
                    )
                )
                return InboundResult(status="ignored", reason="no active campaign")

            # Log the inbound message
            inbound = Message(
                campaign_id=campaign.id,
                direction="inbound",
                channel="sms",
                body=body,
                status="received",
                provider_id=provider_id,
            )
            s.add(inbound)
            s.flush()  # so inbound.id is populated

            history = self._conversation_history(s, campaign.id, exclude_message_id=inbound.id)
            from .practice import load_profile
            profile = load_profile(patient.practice_id)
            classification = self.escalator.classify(
                body, history, business_context=profile.classifier_context_fragment()
            )

            # Log escalation event for any non-"other" category
            if classification.category != "other":
                s.add(
                    EscalationEvent(
                        campaign_id=campaign.id,
                        triggered_by_message_id=inbound.id,
                        category=classification.category,
                        confidence=classification.confidence,
                        reasoning=classification.reasoning,
                        suggested_action=classification.suggested_action,
                    )
                )

            # Dispatch
            return self._dispatch(s, campaign, patient, classification, history, body)

    # ------------------------------------------------------------------
    # Call outcomes (from the Vapi end-of-call-report webhook)
    # ------------------------------------------------------------------

    def process_call_outcome(
        self,
        *,
        transcript: str,
        summary: str = "",
        ended_reason: str = "",
        patient_id: int | None = None,
        campaign_id: int | None = None,
        from_number: str | None = None,
        provider_id: str | None = None,
        recording_url: str | None = None,
    ) -> InboundResult:
        """Store a finished AI call's transcript, classify what happened, and
        raise an escalation into the Inbox when it needs a human — the voice
        analog of process_sms(). Contact/campaign come from the call metadata
        we attached when placing it, falling back to the customer's number."""
        text = (summary or transcript or "").strip()
        with get_session(self.settings) as s:
            patient = None
            if patient_id:
                patient = s.get(Patient, int(patient_id))
            if patient is None and from_number:
                patient = s.query(Patient).filter(Patient.phone == from_number).first()
            if patient is None:
                return InboundResult(status="ignored", reason="no contact match for call")

            campaign = None
            if campaign_id:
                campaign = s.get(Campaign, int(campaign_id))
            if campaign is None:
                campaign = (
                    s.query(Campaign)
                    .filter(Campaign.patient_id == patient.id)
                    .order_by(Campaign.started_at.desc())
                    .first()
                )

            # Log the call transcript as an inbound record on the contact.
            rec = Message(
                campaign_id=campaign.id if campaign else 0,
                direction="inbound",
                channel="call",
                subject=(summary[:250] or None) if summary else None,
                body=(transcript or summary or "(call ended — no transcript)"),
                status="received",
                provider_id=provider_id,
                metadata_json={
                    "kind": "call_outcome",
                    "ended_reason": ended_reason,
                    **({"recording_url": recording_url} if recording_url else {}),
                },
            )
            s.add(rec)
            s.flush()

            # Classify what happened on the call (same engine as inbound SMS).
            from .practice import load_profile
            profile = load_profile(patient.practice_id)
            classification = self.escalator.classify(
                text or "(no transcript)", [],
                business_context=profile.classifier_context_fragment(),
            )

            if campaign and classification.category != "other":
                s.add(
                    EscalationEvent(
                        campaign_id=campaign.id,
                        triggered_by_message_id=rec.id,
                        category=classification.category,
                        confidence=classification.confidence,
                        reasoning=classification.reasoning,
                        suggested_action=classification.suggested_action,
                    )
                )
                return InboundResult(status="escalated", classification=classification)

            return InboundResult(status="logged", classification=classification)

    # ------------------------------------------------------------------
    # Dispatch by classification
    # ------------------------------------------------------------------

    def _dispatch(
        self,
        session,
        campaign: Campaign,
        patient: Patient,
        classification: Classification,
        history: list[dict[str, Any]],
        inbound_body: str,
    ) -> InboundResult:
        cat = classification.category

        if cat == "stop":
            campaign.status = CampaignStatus.stopped.value
            # TCPA: Twilio auto-blocks; we send no further messages.
            return InboundResult(status="stopped", classification=classification)

        if cat == "decline":
            campaign.status = CampaignStatus.declined.value
            body = self._decline_acknowledgment(campaign.practice_id)
            self._send_and_log(session, campaign, patient, body, "decline_ack")
            return InboundResult(
                status="declined",
                classification=classification,
                response_body=body,
            )

        if cat == "emergency":
            campaign.status = CampaignStatus.escalated.value
            body = self._emergency_redirect(campaign.practice_id)
            self._send_and_log(session, campaign, patient, body, "emergency_redirect")
            return InboundResult(
                status="emergency",
                classification=classification,
                response_body=body,
            )

        if cat in ("clinical_question", "callback_request"):
            campaign.status = CampaignStatus.escalated.value
            body = self._escalation_acknowledgment(campaign.practice_id, cat)
            self._send_and_log(session, campaign, patient, body, f"escalation_ack:{cat}")
            return InboundResult(
                status="escalated",
                classification=classification,
                response_body=body,
            )

        # cat == "other" — let the agent reply
        workflow = load_workflow(campaign.workflow_id)
        practice = load_practice(campaign.practice_id)
        agent = AdapixAgent(workflow=workflow, practice=practice, settings=self.settings)
        plan = agent.respond_to_inbound(inbound_body, history)
        self._send_and_log(session, campaign, patient, plan.body, "agent_reply")
        return InboundResult(
            status="responded",
            classification=classification,
            response_body=plan.body,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _send_and_log(
        self,
        session,
        campaign: Campaign,
        patient: Patient,
        body: str,
        kind: str,
    ) -> None:
        result = self.sms.send(patient.phone or "", body)
        session.add(
            Message(
                campaign_id=campaign.id,
                direction="outbound",
                channel="sms",
                body=body,
                status=result.status,
                provider_id=result.provider_id,
                metadata_json={"kind": kind, "error": result.error}
                if result.error
                else {"kind": kind},
            )
        )

    def _decline_acknowledgment(self, practice_id: str) -> str:
        practice = load_practice(practice_id)
        return (
            f"Totally understand — thank you for letting us know. We'll pause "
            f"the follow-up. If anything changes down the road, you can always "
            f"reach the {practice.name} office at {practice.office_phone}."
        )

    def _emergency_redirect(self, practice_id: str) -> str:
        practice = load_practice(practice_id)
        return (
            f"That sounds urgent — please call our office right now at "
            f"{practice.office_phone}. If after hours and severe, please go to "
            f"the nearest urgent care."
        )

    def _escalation_acknowledgment(self, practice_id: str, category: str) -> str:
        practice = load_practice(practice_id)
        if category == "clinical_question":
            return (
                f"Great question — I want to make sure you get the right answer. "
                f"I'm flagging this for {practice.doctor_name} to follow up with "
                f"you directly. Office hours: {practice.office_hours}."
            )
        return (
            f"Got it — I'll have someone from {practice.name} call you back. "
            f"Our office hours are {practice.office_hours}, and the direct "
            f"line is {practice.office_phone}."
        )

    @staticmethod
    def _conversation_history(
        session,
        campaign_id: int,
        exclude_message_id: int | None = None,
    ) -> list[dict[str, Any]]:
        prior = (
            session.query(Message)
            .filter(Message.campaign_id == campaign_id)
            .order_by(Message.created_at.asc())
            .all()
        )
        history: list[dict[str, Any]] = []
        for m in prior:
            if exclude_message_id is not None and m.id == exclude_message_id:
                continue
            role = "assistant" if m.direction == "outbound" else "user"
            content = (
                f"Subject: {m.subject}\n\n{m.body}"
                if m.subject and m.channel == "email"
                else m.body
            )
            history.append({"role": role, "content": content})
        return history
